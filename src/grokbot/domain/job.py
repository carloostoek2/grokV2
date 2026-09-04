"""Job domain vocabulary.

Only the descriptor/enum live here (aditive). The live job state
(``asyncio.Event`` cancellation, active registry) belongs to the ``JobManager``
in item 4. R9: sin tope de concurrencia (decisión de producto, bot privado de
un solo dueño) — el registro activo sigue existiendo para Cancelar.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
