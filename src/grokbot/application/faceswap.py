"""Face-swap use cases: source-face management + the swap itself (R4 Item 2).

Two narrow application services behind :class:`BotDeps`:

* :class:`SourceFacesUseCase` — ephemeral FSM state for the "cara fuente" upload
  (``AWAITING_SOURCE``, parity grok ``_cmd_cambiar_source``) plus persistence of
  the source bytes into a :class:`SourceFacesRepository`; the session config
  records the source file path and returns to ``IDLE``.
* :class:`SwapFaceUseCase` — given a target image bytes, loads the user's stored
  source bytes and asks the resolved face-swap provider to swap (two-image wire
  shape, no prompt).

Both share the module helper :func:`_source_bytes` so "source configured but
file missing" resolves to a single user-safe :class:`SourceFaceMissingError`.
"""

from __future__ import annotations

from dataclasses import replace

from grokbot.domain.generation import GenerationResult
from grokbot.domain.user_config import AWAITING_SOURCE, IDLE_STATE
from grokbot.providers.registry import ProviderRegistry
from grokbot.repositories.base import SessionRepository, SourceFacesRepository


class SourceFaceError(Exception):
    """Base error of source-face management/swap (user-safe)."""

    def __init__(self, message: str, *, user_message: str) -> None:
        super().__init__(message)
        self.user_message = user_message


class SourceFaceMissingError(SourceFaceError):
    def __init__(self) -> None:
        super().__init__(
            "No source face stored.",
            user_message="Primero configura tu cara fuente con /cambiar_source.",
        )


def _source_bytes(sessions: SessionRepository, sources: SourceFacesRepository, user_id: int) -> bytes:
    """Return the user's stored source bytes or raise :class:`SourceFaceMissingError`.

    A4: a source_path that has no backing file is treated exactly like "never
    configured" — the caller should also clear the stale path (see
    :meth:`SourceFacesUseCase.clear_source`).
    """
    cfg = sessions.get_config(user_id)
    if not cfg.source_path or not sources.exists(user_id):
        raise SourceFaceMissingError()
    data = sources.read(user_id)
    if data is None:
        raise SourceFaceMissingError()
    return data


class SourceFacesUseCase:
    """Gestión de la cara fuente del usuario (estado efímero + persistencia)."""

    def __init__(self, *, sessions: SessionRepository, sources: SourceFacesRepository) -> None:
        self._sessions = sessions
        self._sources = sources

    def begin_awaiting_source(self, user_id: int) -> None:
        cfg = self._sessions.get_config(user_id)
        if cfg.state == AWAITING_SOURCE:
            return
        self._sessions.save_config(user_id, replace(cfg, state=AWAITING_SOURCE))

    def save_source(self, user_id: int, data: bytes) -> str:
        path = self._sources.save(user_id, data)
        cfg = self._sessions.get_config(user_id)
        self._sessions.save_config(user_id, replace(cfg, source_path=path, state=IDLE_STATE))
        return path

    def clear_source(self, user_id: int) -> None:
        cfg = self._sessions.get_config(user_id)
        if cfg.source_path is not None or cfg.state != IDLE_STATE:
            self._sessions.save_config(user_id, replace(cfg, source_path=None, state=IDLE_STATE))

    def source_available(self, user_id: int) -> bool:
        cfg = self._sessions.get_config(user_id)
        return bool(cfg.source_path) and self._sources.exists(user_id)


class SwapFaceUseCase:
    """Ejecuta el swap: source bytes + target bytes → :class:`GenerationResult`.

    ``provider`` es un objeto :class:`FaceSwapProvider` (el registry devuelve el
    slot ``replicate``; structural typing, sin import del transporte).
    """

    def __init__(
        self,
        *,
        sessions: SessionRepository,
        sources: SourceFacesRepository,
        registry: ProviderRegistry,
    ) -> None:
        self._sessions = sessions
        self._sources = sources
        self._registry = registry

    async def swap(self, user_id: int, *, input_image: bytes) -> GenerationResult:
        cfg = self._sessions.get_config(user_id)
        if cfg.model != "faceswap":
            raise SourceFaceError(
                "Swap requires faceswap model.",
                user_message="El modo Face Swap no está activo. Usa /config para cambiarlo.",
            )
        swap_image = _source_bytes(self._sessions, self._sources, user_id)
        resolution = self._registry.resolve_face_swap()
        provider = resolution.provider
        return await provider.swap_face(swap_image=swap_image, input_image=input_image)
