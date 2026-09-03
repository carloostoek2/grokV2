"""Capa application — casos de uso puros y testeables (item 4).

Importa solo stdlib + ``domain/*`` + ``providers/base.py`` (contratos) +
``repositories/base.py`` (Protocols). Nada de transport/telegram/settings en
import-time. Los re-exports finales se completan en la Task 4 (use cases).
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
from grokbot.application.job_manager import JobManager

__all__ = [
    "JobManager",
    "GENERATE_MAX_RETRIES",
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
