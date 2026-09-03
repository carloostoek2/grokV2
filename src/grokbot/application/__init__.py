"""Capa application — casos de uso puros y testeables (item 4).

Importa solo stdlib + ``domain/*`` + ``providers/base.py`` (contratos) +
``repositories/base.py`` (Protocols). Nada de transport/telegram/settings en
import-time. Re-exports finales de los 4 use cases, estrategias del batch y el
vocabulario de eventos para ítems 5/6.
"""

from __future__ import annotations

from grokbot.application._retry import GENERATE_MAX_RETRIES
from grokbot.application.events import (
    BatchCancelled,
    BatchRejected,
    BatchStarted,
    BatchSummary,
    EmptyList,
    ItemFailed,
    ItemResult,
    ItemStarted,
    JobsFull,
    RetryScheduled,
)
from grokbot.application.generate_image import GenerateImageUseCase
from grokbot.application.generate_video import GenerateVideoUseCase
from grokbot.application.job_manager import JobManager
from grokbot.application.manage_config import UpdateUserConfigUseCase
from grokbot.application.manage_lists import ManageListsUseCase
from grokbot.application.refine_flow import RefineDecision, ResolveRefineUseCase
from grokbot.application.run_variable_batch import (
    FixedPromptStrategy,
    PromptStrategy,
    RandomComboStrategy,
    RunVariableBatchUseCase,
)

__all__ = [
    "JobManager",
    "GENERATE_MAX_RETRIES",
    "GenerateImageUseCase",
    "GenerateVideoUseCase",
    "RunVariableBatchUseCase",
    "UpdateUserConfigUseCase",
    "ManageListsUseCase",
    "ResolveRefineUseCase",
    "RefineDecision",
    "PromptStrategy",
    "RandomComboStrategy",
    "FixedPromptStrategy",
    "BatchStarted",
    "ItemStarted",
    "RetryScheduled",
    "ItemResult",
    "ItemFailed",
    "BatchCancelled",
    "BatchSummary",
    "EmptyList",
    "BatchRejected",
    "JobsFull",
]
