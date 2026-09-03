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

from grokbot.application.generate_image import GenerateImageUseCase
from grokbot.application.generate_video import GenerateVideoUseCase
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
        self._pending: dict[int, tuple[str, int | None, int | None]] = {}
        self._owners: dict[tuple[int, int], int] = {}

    def set(
        self,
        user_id: int,
        prompt: str,
        *,
        chat_id: int | None = None,
        message_id: int | None = None,
    ) -> None:
        prev = self._pending.get(user_id)
        if prev is not None and prev[1] is not None and prev[2] is not None:
            self._owners.pop((prev[1], prev[2]), None)
        self._pending[user_id] = (prompt, chat_id, message_id)
        if chat_id is not None and message_id is not None:
            self._owners[(chat_id, message_id)] = user_id

    def get(self, user_id: int) -> str | None:
        entry = self._pending.get(user_id)
        return entry[0] if entry else None

    def pop(self, user_id: int) -> str | None:
        entry = self._pending.pop(user_id, None)
        if entry is not None and entry[1] is not None and entry[2] is not None:
            self._owners.pop((entry[1], entry[2]), None)
        return entry[0] if entry else None

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
    pending: PendingPrompts = field(default_factory=PendingPrompts)
    allowed_telegram_ids: set[int] | None = None
    variables_admin_ids: set[int] | None = None


__all__ = ["BotDeps", "PendingPrompts"]
