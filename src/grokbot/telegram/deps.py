"""BotDeps — inyección por constructor de la capa telegram (item 5).

Los handlers reciben un :class:`BotDeps` (sin DI global ni settings): solo
objetos ya construidos por item 6 (o por ``make_deps`` de test). ``register_all``
arma el :class:`Dispatcher` con estos deps.

El store efímero de confirmación de prompts (:class:`PendingPrompts`) vive acá
como tipo concreto mínimo (A6): NO usa FSM de aiogram (paridad ``pending_prompt``
de grok); cada confirmación queda atada a su ``(chat_id, message_id)`` y dueño
(C4) y un nuevo prompt pisa el pendiente anterior del mismo user. En tests se
inyecta uno fresco por ``make_deps`` para aislar casos.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from grokbot.application.faceswap import SourceFacesUseCase, SwapFaceUseCase
from grokbot.application.generate_image import GenerateImageUseCase
from grokbot.application.generate_video import GenerateVideoUseCase
from grokbot.application.integrate_refs import IntegrateRefsUseCase
from grokbot.application.job_manager import JobManager
from grokbot.application.manage_config import UpdateUserConfigUseCase
from grokbot.application.manage_lists import ManageListsUseCase
from grokbot.application.refine_flow import ResolveRefineUseCase
from grokbot.application.run_variable_batch import RunVariableBatchUseCase
from grokbot.repositories.base import (
    GenerationRefsRepository,
    SessionRepository,
    VariablesRepository,
)
from grokbot.telegram.ports import MediaDownloader, TelegramGateway


class PendingPrompts:
    """Prompt pendiente de confirmación, atado a su mensaje y dueño (A6/C4).

    La confirmación queda ligada al ``(chat_id, message_id)`` del mensaje de
    confirmación y al ``user_id`` que la pidió. Un click de OTRO usuario (grupos)
    o sobre un mensaje distinto del mismo user (otro chat) NO consume ni
    reemplaza el pendiente ajeno: ``owner_of`` permite responder "la confirmación
    pertenece a otro usuario" sin editar el mensaje del dueño. Sin FSM (paridad
    grok); un nuevo prompt del mismo user pisa el pendiente anterior del mensaje
    viejo (que queda huérfano, no consumible).
    """

    def __init__(self) -> None:
        self._pending: dict[int, tuple[str, int | None, int | None, int | None]] = {}
        self._owners: dict[tuple[int, int], int] = {}

    def set(
        self,
        user_id: int,
        prompt: str,
        *,
        chat_id: int | None = None,
        message_id: int | None = None,
        source_message_id: int | None = None,
    ) -> None:
        prev = self._pending.get(user_id)
        if prev is not None and prev[1] is not None and prev[2] is not None:
            self._owners.pop((prev[1], prev[2]), None)
        self._pending[user_id] = (prompt, chat_id, message_id, source_message_id)
        if chat_id is not None and message_id is not None:
            self._owners[(chat_id, message_id)] = user_id

    def get(self, user_id: int) -> str | None:
        entry = self._pending.get(user_id)
        return entry[0] if entry else None

    def _pop(self, user_id: int) -> tuple[str, int | None] | None:
        """Extrae el pendiente y limpia su owner; devuelve (prompt, source_message_id)."""
        entry = self._pending.pop(user_id, None)
        if entry is not None and entry[1] is not None and entry[2] is not None:
            self._owners.pop((entry[1], entry[2]), None)
        if entry is None:
            return None
        return (entry[0], entry[3])

    def pop(self, user_id: int) -> str | None:
        entry = self._pop(user_id)
        return entry[0] if entry else None

    def pop_entry(self, user_id: int) -> tuple[str | None, int | None] | None:
        """Como ``pop`` pero devuelve además el ``source_message_id`` original.

        La confirmación (``handle_confirm_yes``) lo usa para que la imagen/video
        resultante responda al mensaje del usuario que invocó la generación (no al
        mensaje de confirmación del bot).
        """
        return self._pop(user_id)

    def clear(self, user_id: int) -> None:
        self.pop(user_id)

    def owns(self, chat_id: int, message_id: int, user_id: int) -> bool:
        """True si el pendiente del ``user_id`` es el de este mensaje concreto."""
        entry = self._pending.get(user_id)
        if entry is None:
            return False
        if entry[1] is None and entry[2] is None:
            return True  # entrada sin atar (tests) matchea cualquier mensaje del user
        return entry[1] == chat_id and entry[2] == message_id

    def owner_of(self, chat_id: int, message_id: int) -> int | None:
        """Dueño del pendiente publicado en este mensaje (si existe)."""
        return self._owners.get((chat_id, message_id))


class FaceswapPending:
    """file_ids de face swap pendientes de confirmar, atados a (chat, msg, uid).

    Misma semántica de ownership que :class:`PendingPrompts` (C4): cada
    confirmación queda ligada al ``(chat_id, message_id)`` del mensaje de
    confirmación y a su dueño, y un click de OTRO usuario o sobre un mensaje
    distinto no consume ni reemplaza el pendiente ajeno. Store dedicado al flujo
    face swap (``faceswap:confirm:*``) para NO colisionar con el ``confirm:yes/no``
    de prompts (pool R4 Item 2). Sin FSM (paridad grok ``pending_faceswap_file_ids``).
    """

    def __init__(self) -> None:
        self._pending: dict[int, tuple[list[str], int, int]] = {}
        self._owners: dict[tuple[int, int], int] = {}

    def set(
        self,
        user_id: int,
        file_ids: list[str],
        *,
        chat_id: int,
        message_id: int,
    ) -> None:
        prev = self._pending.get(user_id)
        if prev is not None:
            self._owners.pop((prev[1], prev[2]), None)
        self._pending[user_id] = (list(file_ids), chat_id, message_id)
        self._owners[(chat_id, message_id)] = user_id

    def get(self, user_id: int) -> list[str] | None:
        entry = self._pending.get(user_id)
        return list(entry[0]) if entry else None

    def pop(self, user_id: int) -> list[str] | None:
        entry = self._pending.pop(user_id, None)
        if entry is not None:
            self._owners.pop((entry[1], entry[2]), None)
        return list(entry[0]) if entry else None

    def clear(self, user_id: int) -> None:
        self.pop(user_id)

    def owns(self, chat_id: int, message_id: int, user_id: int) -> bool:
        """True si el pendiente del ``user_id`` es el de este mensaje concreto."""
        entry = self._pending.get(user_id)
        if entry is None:
            return False
        return entry[1] == chat_id and entry[2] == message_id

    def owner_of(self, chat_id: int, message_id: int) -> int | None:
        """Dueño del pendiente publicado en este mensaje (si existe)."""
        return self._owners.get((chat_id, message_id))


@dataclass
class LongPromptStore:
    """Colección efímera de long-prompt: user_id → {file_ids, integrate_mode, is_video}.

    Sin FSM de aiogram (paridad grok ``_set_long_prompt_collection`` bot.py
    518-531): cuando un caption supera el tope se guardan los file_ids y el
    siguiente texto del mismo user completa la edición. ``pop`` (consumo único)
    tras validar el prompt evita estados colgados (A3).
    """

    _state: dict[int, dict] = field(default_factory=dict)

    def is_awaiting(self, user_id: int) -> bool:
        return user_id in self._state

    def set(self, user_id: int, *, file_ids: list[str], integrate_mode: bool, is_video: bool) -> None:
        self._state[user_id] = {
            "file_ids": list(file_ids),
            "integrate_mode": integrate_mode,
            "is_video": is_video,
        }

    def get(self, user_id: int) -> dict | None:
        entry = self._state.get(user_id)
        return dict(entry) if entry is not None else None

    def pop(self, user_id: int) -> dict | None:
        entry = self._state.pop(user_id, None)
        return dict(entry) if entry is not None else None

    def clear(self, user_id: int) -> None:
        self._state.pop(user_id, None)


@dataclass
class AlbumStore:
    """Colección efímera de álbumes: (chat_id, media_group_id) → mensajes.

    ``delay`` es inyectable para tests (producción ALBUM_COLLECT_DELAY=1.0).
    Sin locks: asyncio single-thread da atomicidad al append síncrono dentro de
    ``handle_album``; el drenado corre en un task aparte con ``asyncio.sleep``.
    """

    delay: float = 1.0
    _pending: dict[tuple[int, str], list] = field(default_factory=dict)

    def add(self, key: tuple[int, str], message) -> bool:
        """Acumular el mensaje del media group; True cuando es el primero."""
        first = key not in self._pending
        self._pending.setdefault(key, []).append(message)
        return first

    def pop(self, key: tuple[int, str]) -> list:
        return self._pending.pop(key, [])


@dataclass
class BotDeps:
    """Dependencias de la capa telegram (use cases + seams + gates)."""

    gateway: TelegramGateway
    downloader: MediaDownloader
    refs: GenerationRefsRepository
    sessions: SessionRepository
    variables: VariablesRepository
    job_manager: JobManager
    refine_uc: ResolveRefineUseCase
    generate_image: GenerateImageUseCase
    generate_video: GenerateVideoUseCase
    run_batch: RunVariableBatchUseCase
    update_config: UpdateUserConfigUseCase
    manage_lists: ManageListsUseCase
    source_faces: SourceFacesUseCase
    swap_face: SwapFaceUseCase
    integrate_refs: IntegrateRefsUseCase
    pending: PendingPrompts = field(default_factory=PendingPrompts)
    faceswap_pending: FaceswapPending = field(default_factory=FaceswapPending)
    long_prompt: LongPromptStore = field(default_factory=LongPromptStore)
    album: AlbumStore = field(default_factory=AlbumStore)
    # Flag efímero awaiting-ref (A2): en memoria, NUNCA en cfg.state/sessions.json
    # (parity grok ``state["integrate_ref_awaiting"]``, se pierde al reiniciar).
    integrate_ref_pending: set[int] = field(default_factory=set)
    allowed_telegram_ids: set[int] | None = None
    variables_admin_ids: set[int] | None = None


__all__ = [
    "AlbumStore",
    "BotDeps",
    "FaceswapPending",
    "LongPromptStore",
    "PendingPrompts",
]
