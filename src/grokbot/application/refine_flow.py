"""Refine ComfyUI 2-stage desacoplado de Telegram (D7, item 4).

Reproduce la maquinaria de confirmación de refine de grok (bot.py 4313-4527 /
716-748) sin acoplar a Telegram: ``offer`` decide si un resultado es refinable,
``register`` crea una confirmación pendiente (token opaco), ``decide``/``await_decision``
resuelven la future del usuario y ``refine`` delega el segundo stage al provider.
El caso de uso recibe el provider comfyui duck-typed (con ``.refine``) — no asume
que cualquier provider refine (D7). El timeout se inyecta (default 300 s); ítem 6
wire ``Settings.refine_confirm_timeout``.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from enum import Enum

from grokbot.domain.generation import GenerationResult
from grokbot.domain.user_config import ComfyUIConfig

_REFINE_TIMEOUT_DEFAULT = 300.0
# Centinela para resolver a `cancelled` una future pendiente (cancel_for_job).
_CANCELLED = object()


class RefineDecision(str, Enum):
    """Resolución de una confirmación de refine (ítem 5 formatea el copy)."""

    yes = "yes"
    no = "no"
    timeout = "timeout"
    cancelled = "cancelled"


@dataclass(frozen=True)
class _PendingRefine:
    """Confirmación pendiente: a quién pertenece (user/job) y su future."""

    user_id: int
    message_id: int | None
    job_id: str | None
    future: asyncio.Future


class ResolveRefineUseCase:
    """Refine ComfyUI 2-stage: oferta, espera de decisión y segundo stage.

    Los tokens son opacos (``uuid4().hex[:8]``) y nunca llevan payloads pagos;
    ``decide`` es idempotente y valida owner (R4), de modo que un re-tap o un
    token desconocido no resuelve dos veces ni ajeno.
    """

    def __init__(self, *, provider, timeout: float | None = None) -> None:
        self._provider = provider
        self._timeout = float(timeout) if timeout is not None else _REFINE_TIMEOUT_DEFAULT
        self._pending: dict[str, _PendingRefine] = {}

    # -- policy ------------------------------------------------------------

    def offer(self, cfg_comfyui: ComfyUIConfig, result: GenerationResult) -> bool:
        """True cuando el resultado del ítem es refinable (paridad 4313-4319).

        Requiere refine habilitado, modelo != ``qwen_aio`` (edición directa que no
        admite refine) y remotos ComfyUI en el resultado para el segundo stage.
        """
        return bool(
            cfg_comfyui.refine_enabled
            and cfg_comfyui.model != "qwen_aio"
            and result.meta.get("comfyui_remotes")
        )

    # -- registro / decisión -----------------------------------------------

    def register(self, *, user_id: int, message_id: int | None = None, job_id: str | None = None) -> str:
        """Registrar una confirmación pendiente y devolver su token opaco."""
        token = uuid.uuid4().hex[:8]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[token] = _PendingRefine(
            user_id=user_id,
            message_id=message_id,
            job_id=job_id,
            future=future,
        )
        return token

    def decide(self, token: str, user_id: int, choice: str) -> bool:
        """Resolver la future del token con ``choice`` in {"yes","no"}.

        Idempotente: re-tap, token desconocido o owner distinto → False sin
        efecto (paridad grok 1281-1302).
        """
        if choice not in ("yes", "no"):
            return False
        entry = self._pending.get(token)
        if entry is None or entry.future.done():
            return False
        if entry.user_id != user_id:
            return False
        entry.future.set_result(choice == "yes")
        return True

    async def await_decision(self, token: str) -> RefineDecision:
        """Esperar la decisión del usuario hasta el deadline inyectado.

        Al resolver (timeout/cancel/decidido) hace drop del pending. El shield
        evita que un timeout/cancel del waiter cancele la future pendiente, que
        puede seguir siendo resuelta por ``cancel_for_job``.
        """
        entry = self._pending.get(token)
        if entry is None:
            return RefineDecision.cancelled
        if entry.future.done():
            decision = self._decision_from_result(entry.future.result())
            self.drop(token)
            return decision
        try:
            result = await asyncio.wait_for(asyncio.shield(entry.future), timeout=self._timeout)
        except asyncio.TimeoutError:
            self.drop(token)
            return RefineDecision.timeout
        except asyncio.CancelledError:
            self.drop(token)
            return RefineDecision.cancelled
        decision = self._decision_from_result(result)
        self.drop(token)
        return decision

    def cancel_for_job(self, user_id: int, job_id: str | None = None) -> None:
        """Resolver a `cancelled` los pendientes del user/job (espejo 716-748).

        Idempotente: no-op si no hay pendientes que matcheen.
        """
        for token, entry in list(self._pending.items()):
            if entry.user_id != user_id:
                continue
            if job_id is not None and entry.job_id != job_id:
                continue
            if not entry.future.done():
                entry.future.set_result(_CANCELLED)

    def drop(self, token: str) -> None:
        """Quitar un pending (limpieza; también tras resolver/timeout/cancel)."""
        self._pending.pop(token, None)

    def pending_count(self, user_id: int) -> int:
        return sum(1 for entry in self._pending.values() if entry.user_id == user_id)

    # -- segundo stage -----------------------------------------------------

    async def refine(self, request, remote_paths: list[str]) -> GenerationResult:
        """Delegar el segundo stage al provider comfyui (ítem 5 tras decide==yes)."""
        return await self._provider.refine(request, remote_paths)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _decision_from_result(result: object) -> RefineDecision:
        if result is _CANCELLED:
            return RefineDecision.cancelled
        return RefineDecision.yes if result is True else RefineDecision.no
