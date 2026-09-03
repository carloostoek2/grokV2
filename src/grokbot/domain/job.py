"""Job domain vocabulary.

Only the descriptor/enum live here (aditive). The live job state (concurrency
limit, ``asyncio.Event`` cancellation, active registry) belongs to the
``JobManager`` in item 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Mirrors grok bot.py:438 — max concurrent in-flight jobs per user.
MAX_ACTIVE_JOBS_PER_USER = 3


class JobStatus(str, Enum):
    RUNNING = "running"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class Job:
    """Immutable descriptor of an in-flight/terminal generation job."""

    job_id: str
    user_id: int
    kind: str
    created_at: float
    status: JobStatus = JobStatus.RUNNING
