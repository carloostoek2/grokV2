"""Tests del JobManager (R4) — concurrencia + cancelación cooperativa + hook refine.

Cubre la API exacta de la sección QUÉ #7 del PLAN: start/None a tope,
cancel por job_id / sin id, finish idempotente, hook filtrado por user/job,
cancel_event para el teclado de item 5.
"""

from __future__ import annotations

from grokbot.application.job_manager import JobManager
from grokbot.domain.job import JobStatus

OTHER_USER = 222222222
USER = 111111111


def test_start_returns_running_job_and_tracks_active():
    jm = JobManager()
    job = jm.start(USER, "variables")

    assert job is not None
    assert job.kind == "variables"
    assert job.user_id == USER
    assert job.status is JobStatus.RUNNING
    assert jm.active_count(USER) == 1
    assert jm.active_jobs(USER) == (job,)
    assert jm.is_full(USER) is False


def test_start_none_at_max_and_does_not_affect_other_user():
    jm = JobManager()
    for _ in range(3):
        assert jm.start(USER, "variables") is not None
    assert jm.start(USER, "variables") is None
    assert jm.active_count(USER) == 3
    assert jm.is_full(USER) is True
    # Otro user con 0 activos no se ve afectado.
    assert jm.start(OTHER_USER, "var") is not None
    assert jm.active_count(OTHER_USER) == 1
    assert jm.is_full(OTHER_USER) is False


def test_cancel_by_exact_job_id_sets_event_and_calls_hook():
    calls: list[tuple[int, str | None]] = []
    jm = JobManager(refine_hook=lambda uid, jid: calls.append((uid, jid)))
    job = jm.start(USER, "variables")

    assert jm.cancel(USER, job.job_id) is True
    assert jm.is_cancelled(job) is True
    assert calls == [(USER, job.job_id)]


def test_cancel_without_id_cancels_most_recent_not_cancelled():
    jm = JobManager()
    j1 = jm.start(USER, "variables")
    j2 = jm.start(USER, "variables")
    j3 = jm.start(USER, "variables")
    jm.cancel(USER, j3.job_id)  # marca j3

    assert jm.cancel(USER) is True
    assert jm.is_cancelled(j2) is True
    assert jm.is_cancelled(j1) is False
    assert jm.is_cancelled(j3) is True


def test_cancel_unknown_job_id_returns_false():
    jm = JobManager()
    job = jm.start(USER, "variables")

    assert jm.cancel(USER, "no-existe") is False
    assert jm.is_cancelled(job) is False


def test_cancel_with_no_jobs_returns_false():
    jm = JobManager()
    assert jm.cancel(USER) is False
    assert jm.cancel(USER, "x") is False


def test_cancel_event_returns_event_and_clears_on_finish():
    jm = JobManager()
    job = jm.start(USER, "variables")
    event = jm.cancel_event(job)

    assert event is not None
    assert event.is_set() is False
    jm.cancel(USER, job.job_id)
    assert event.is_set() is True
    jm.finish(USER, job.job_id)
    assert jm.cancel_event(job) is None
    assert jm.is_cancelled(job) is False


def test_finish_removes_job_and_decrements_active():
    jm = JobManager()
    job = jm.start(USER, "variables")
    assert jm.active_count(USER) == 1

    jm.finish(USER, job.job_id)
    assert jm.active_count(USER) == 0
    assert jm.active_jobs(USER) == ()
    # finish no-op sobre job ya removido.
    jm.finish(USER, job.job_id)
    assert jm.active_count(USER) == 0


def test_finish_calls_hook_for_removed_job_and_not_for_foreign_job():
    calls: list[tuple[int, str | None]] = []
    jm = JobManager(refine_hook=lambda uid, jid: calls.append((uid, jid)))
    mine = jm.start(USER, "variables")
    theirs = jm.start(OTHER_USER, "variables")

    jm.finish(USER, mine.job_id)
    assert calls == [(USER, mine.job_id)]
    assert jm.active_count(OTHER_USER) == 1
    assert jm.is_cancelled(theirs) is False


def test_finish_with_no_job_id_removes_most_recent():
    jm = JobManager()
    j1 = jm.start(USER, "variables")
    j2 = jm.start(USER, "variables")

    jm.finish(USER)
    assert jm.active_count(USER) == 1
    assert jm.is_cancelled(j1) is False
    assert jm.active_jobs(USER) == (j1,)


def test_hook_not_called_when_no_refine_hook_configured():
    jm = JobManager()
    job = jm.start(USER, "variables")
    jm.cancel(USER, job.job_id)
    jm.finish(USER, job.job_id)
    assert jm.active_count(USER) == 0


def test_cancel_then_finish_hook_defensive_single_job():
    """Cancel + finish del MISMO job: el hook es defensivo y no toca jobs ajenos."""
    calls: list[tuple[int, str | None]] = []
    jm = JobManager(refine_hook=lambda uid, jid: calls.append((uid, jid)))
    other = jm.start(OTHER_USER, "var")
    job = jm.start(USER, "variables")

    jm.cancel(USER, job.job_id)
    jm.finish(USER, job.job_id)

    assert calls == [(USER, job.job_id), (USER, job.job_id)]
    assert jm.is_cancelled(other) is False
