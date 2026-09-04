"""Unit tests for grokbot.domain.job (R11)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from grokbot.domain.job import Job, JobStatus


def test_job_fields_default_frozen_and_equality():
    j1 = Job(job_id="ab12cd34", user_id=1, kind="imagine", created_at=0.0)
    assert j1.job_id == "ab12cd34"
    assert j1.user_id == 1
    assert j1.kind == "imagine"
    assert j1.created_at == 0.0
    assert j1.status is JobStatus.RUNNING

    j2 = Job(job_id="ab12cd34", user_id=1, kind="imagine", created_at=0.0)
    assert j1 == j2

    with pytest.raises(FrozenInstanceError):
        j1.kind = "video"


def test_job_status_values():
    assert JobStatus.RUNNING.value == "running"
    assert JobStatus.CANCELLED.value == "cancelled"
    assert JobStatus.COMPLETED.value == "completed"
    assert JobStatus.FAILED.value == "failed"
