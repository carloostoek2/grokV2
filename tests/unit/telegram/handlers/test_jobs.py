"""Tests de callbacks transaccionales (item 5, handlers/jobs.py).

Cancelación de jobs (bare / por id / sin proceso / no mata otros) y decisión de
refine (owner, mismatch, desconocido, no-op). 0 red / 0 ``unittest.mock``.
Fixtures anonimizados.
"""

from __future__ import annotations

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    callback_query,
    callback_update,
    make_deps,
    make_dispatcher,
    text_message,
)

_UID = USER_ID
_CHAT = CHAT_ID
_BOT = Bot("42:TEST")


async def _cb(deps, data: str, *, user_id: int = _UID, message_id: int = 80, text: str = "base"):
    dp, deps = make_dispatcher(deps)
    msg = text_message(text, user_id=user_id, message_id=message_id)
    await dp.feed_update(_BOT, callback_update(callback_query(data, user_id=user_id, message=msg)))
    return deps


def _last_answer(deps) -> dict:
    return deps.gateway.calls_by_method("answer_callback")[-1]


# --------------------------------------------------------------------------- #
# Cancelación de jobs
# --------------------------------------------------------------------------- #
async def test_cancel_bare_soft_signals_edit():
    deps = make_deps()
    job = deps.job_manager.start(_UID, "text")
    assert job is not None
    deps = await _cb(
        deps, "cancel_job",
        message_id=70, text="Generando imagen con Seedream 5.0...",
    )
    ans = _last_answer(deps)
    assert ans["text"] == "Cancelando…"
    assert ans["show_alert"] is False
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "Generando imagen con Seedream 5.0...\n\n⏹ Cancelando…"
    assert edits[-1]["reply_markup"] is None
    assert deps.job_manager.is_cancelled(job)
    deps.job_manager.finish(_UID, job.job_id)


async def test_cancel_with_specific_id_keeps_other_job():
    deps = make_deps()
    job_a = deps.job_manager.start(_UID, "text")
    job_b = deps.job_manager.start(_UID, "edit")
    assert job_a is not None and job_b is not None
    deps = await _cb(deps, f"cancel_job:{job_a.job_id}", message_id=71)
    ans = _last_answer(deps)
    assert ans["text"] == "Cancelando…"
    assert deps.job_manager.is_cancelled(job_a)
    assert not deps.job_manager.is_cancelled(job_b)
    deps.job_manager.finish(_UID, job_a.job_id)
    deps.job_manager.finish(_UID, job_b.job_id)


async def test_cancel_without_active_job_alerts():
    deps = make_deps()
    deps = await _cb(deps, "cancel_job:nonexistent", message_id=72)
    ans = _last_answer(deps)
    assert ans["text"] == "No hay proceso en curso."
    assert ans["show_alert"] is True
    assert not deps.gateway.calls_by_method("edit_message_text")


# --------------------------------------------------------------------------- #
# Decisión de refine
# --------------------------------------------------------------------------- #
async def test_refine_noop_answers_empty():
    deps = make_deps()
    deps = await _cb(deps, "refine_noop", message_id=73)
    ans = _last_answer(deps)
    assert ans["text"] is None
    assert ans["show_alert"] is False


async def test_refine_decision_yes():
    deps = make_deps()
    token = deps.refine_uc.register(user_id=_UID)
    assert deps.refine_uc.owner_of(token) == _UID
    deps = await _cb(deps, f"refine:{token}:yes", message_id=74)
    ans = _last_answer(deps)
    assert ans["text"] == "Refinando…"
    # Ya resuelto → idempotente y sin owner (la UI no puede re-tap).
    assert deps.refine_uc.owner_of(token) is None
    assert deps.refine_uc.decide(token, _UID, "yes") is False


async def test_refine_decision_no():
    deps = make_deps()
    token = deps.refine_uc.register(user_id=_UID)
    deps = await _cb(deps, f"refine:{token}:no", message_id=75)
    ans = _last_answer(deps)
    assert ans["text"] == "Listo, imagen final."


async def test_refine_unknown_token_already_processed():
    deps = make_deps()
    deps = await _cb(deps, "refine:deadbeef:yes", message_id=76)
    ans = _last_answer(deps)
    assert ans["text"] == "La confirmación ya se procesó."
    assert ans["show_alert"] is True


async def test_refine_not_owner():
    deps = make_deps()
    OTHER = 222222222
    token = deps.refine_uc.register(user_id=OTHER)
    deps = await _cb(deps, f"refine:{token}:yes", user_id=_UID, message_id=77)
    ans = _last_answer(deps)
    assert ans["text"] == "No es tu confirmación."
    assert ans["show_alert"] is True
    # El pending sigue vivo para su dueño real.
    assert deps.refine_uc.owner_of(token) == OTHER
    deps.refine_uc.drop(token)
