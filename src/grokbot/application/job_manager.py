"""Job manager — registro activo + cancelación cooperativa por usuario (D6).

Reproduce la semántica de jobs de grok (bot.py 435-515) para la capa
application: cada batch registra un job activo y consulta cancelación entre
ítems vía :class:`asyncio.Event`. El ``Job`` (domain) es un descriptor inmutable;
el estado vivo (activos por user, eventos de cancel, hook de refine) vive acá.

R9: SIN tope de concurrencia (decisión de producto — bot privado de un solo
dueño, sin límites de uso): ``start`` siempre registra el job. El registro
activo se conserva para el botón Cancelar y para resolver confirmaciones de
refine pendientes del job.

El ``refine_hook`` opcional se invoca al cancelar y al finalizar con
``(user_id, job_id)``; en item 5/6 se wire con
``ResolveRefineUseCase.cancel_for_job`` para resolver confirmaciones de refine
pendientes del job (paridad ``_finish_job``/``_cancel_pending_refines_for_user``).
El hook es idempotente y filtra por user/job (R4), de modo que cancel+finish no
resuelven dos veces.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable

from grokbot.domain.job import Job, JobStatus


class JobManager:
    """Jobs activos por usuario con cancelación cooperativa (sin tope, R9).

    Métodos sync sin locks: asyncio single-thread da atomicidad por turno del
    loop. ``_active`` es ``{user_id: [Job, ...]}`` y ``_events`` mapea
    ``job_id`` → su evento de cancelación.
    """

    def __init__(
        self,
        *,
        refine_hook: Callable[[int, str | None], None] | None = None,
    ) -> None:
        self._refine_hook = refine_hook
        self._active: dict[int, list[Job]] = {}
        self._events: dict[str, asyncio.Event] = {}

    # -- lifecycle --------------------------------------------------------

    def start(self, user_id: int, kind: str) -> Job:
        """Registrar un job ``kind`` para el user (siempre, sin tope; R9)."""
        jobs = self._active.setdefault(user_id, [])
        job = Job(
            job_id=uuid.uuid4().hex[:8],
            user_id=user_id,
            kind=kind,
            created_at=time.time(),
            status=JobStatus.RUNNING,
        )
        jobs.append(job)
        self._events[job.job_id] = asyncio.Event()
        return job

    def cancel(self, user_id: int, job_id: str | None = None) -> bool:
        """Solicitar cancelación de un job (set del evento + refine_hook).

        ``job_id`` dado: cancela ese job exacto (False si no está activo).
        ``job_id`` None: cancela el más reciente NO cancelado (reversed), o el
        último activo si todos ya están cancelados (espejo 474-489). False solo
        cuando el user no tiene jobs.
        """
        jobs = self._active.get(user_id)
        if not jobs:
            return False
        if job_id is not None:
            for job in jobs:
                if job.job_id == job_id:
                    self._events[job.job_id].set()
                    self._call_refine_hook(user_id, job_id)
                    return True
            return False
        for job in reversed(jobs):
            if not self._events[job.job_id].is_set():
                self._events[job.job_id].set()
                self._call_refine_hook(user_id, job.job_id)
                return True
        last = jobs[-1]
        self._events[last.job_id].set()
        self._call_refine_hook(user_id, last.job_id)
        return True

    def finish(self, user_id: int, job_id: str | None = None) -> None:
        """Remover un job de los activos. No-op si no existe.

        ``job_id`` None remueve el job más reciente del user. El refine_hook se
        llama de forma defensiva para el job removido (idempotente; no resuelve
        refines ajenos ni dos veces, R4).
        """
        jobs = self._active.get(user_id)
        if not jobs:
            return
        removed: Job | None = None
        if job_id is not None:
            for job in jobs:
                if job.job_id == job_id:
                    jobs.remove(job)
                    removed = job
                    break
        else:
            removed = jobs.pop()
        if removed is not None:
            self._events.pop(removed.job_id, None)
            self._call_refine_hook(user_id, removed.job_id)
        if not jobs:
            self._active.pop(user_id, None)

    # -- queries ----------------------------------------------------------

    def is_cancelled(self, job: Job) -> bool:
        """True cuando el job está activo y su evento de cancelación fue seteado."""
        event = self._events.get(job.job_id)
        return bool(event is not None and event.is_set())

    def cancel_event(self, job: Job) -> asyncio.Event | None:
        """Evento de cancelación del job (item 5 lo usa para el teclado), o None."""
        return self._events.get(job.job_id)

    def active_count(self, user_id: int) -> int:
        return len(self._active.get(user_id, []))

    def active_jobs(self, user_id: int) -> tuple[Job, ...]:
        return tuple(self._active.get(user_id, ()))

    # -- helpers ----------------------------------------------------------

    def _call_refine_hook(self, user_id: int, job_id: str) -> None:
        hook = self._refine_hook
        if hook is not None:
            hook(user_id, job_id)
