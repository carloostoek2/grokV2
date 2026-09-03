"""BotDeps — inyección por constructor de la capa telegram (item 5).

Los handlers reciben un :class:`BotDeps` (sin DI global ni settings): solo
objetos ya construidos por item 6 (o por ``make_deps`` de test). ``register_all``
arma el :class:`Dispatcher` con estos deps.

El store efímero de confirmación de prompts (:class:`PendingPrompts`) vive acá
como tipo concreto mínimo (A6): NO usa FSM de aiogram (paridad ``pending_prompt``
de grok) y un nuevo prompt pisa el pendiente anterior. En tests se inyecta uno
fresco por ``make_deps`` para aislar casos.
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
    """Prompt pendiente de confirmación por user (A6: efímero, sin FSM)."""

    def __init__(self) -> None:
        self._pending: dict[int, str] = {}

    def set(self, user_id: int, prompt: str) -> None:
        self._pending[user_id] = prompt

    def get(self, user_id: int) -> str | None:
        return self._pending.get(user_id)

    def pop(self, user_id: int) -> str | None:
        return self._pending.pop(user_id, None)

    def clear(self, user_id: int) -> None:
        self._pending.pop(user_id, None)


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
