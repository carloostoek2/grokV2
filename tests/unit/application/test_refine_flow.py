"""Tests de ResolveRefineUseCase — refine ComfyUI 2-stage (R2/R4).

Cubre la policy de ``offer`` (refine habilitado + modelo != qwen_aio + remotes),
el registro/decisión idempotente con owner, timeout, cancel_for_job por user/job
y la delegación del segundo stage al provider. Sin Telegram ni red.
"""

from __future__ import annotations

from grokbot.application.refine_flow import RefineDecision, ResolveRefineUseCase
from grokbot.domain.generation import GenerationRequest, MediaType
from grokbot.domain.user_config import ComfyUIConfig

from conftest import USER_ID, FakeComfyuiProvider, make_result

OTHER_USER = 222222222


def _remotes_result(**over):
    meta = {"comfyui_remotes": ["/workspace/a.png"]}
    meta.update(over.get("meta", {}))
    return make_result(provider="comfyui", model_id="comfyui", meta=meta)


# --- offer (policy de refinabilidad) ------------------------------------------

def test_offer_true_when_refine_enabled_not_aio_with_remotes():
    uc = ResolveRefineUseCase(provider=object())
    cfg = ComfyUIConfig(model="krea2", lora="lightning", refine="1")
    assert uc.offer(cfg, _remotes_result()) is True


def test_offer_false_when_refine_disabled():
    uc = ResolveRefineUseCase(provider=object())
    cfg = ComfyUIConfig(model="krea2", lora="lightning", refine="0")
    assert uc.offer(cfg, _remotes_result()) is False


def test_offer_false_when_qwen_aio():
    uc = ResolveRefineUseCase(provider=object())
    cfg = ComfyUIConfig(model="qwen_aio", lora="qwen_snofs", refine="1")
    assert uc.offer(cfg, _remotes_result()) is False


def test_offer_false_without_remotes():
    uc = ResolveRefineUseCase(provider=object())
    cfg = ComfyUIConfig(model="krea2", lora="lightning", refine="1")
    result = make_result(provider="comfyui", model_id="comfyui", meta={})
    assert uc.offer(cfg, result) is False


# --- register / decide / await_decision ---------------------------------------

async def test_register_decide_yes_resolves_yes():
    uc = ResolveRefineUseCase(provider=object())
    token = uc.register(user_id=USER_ID)
    assert uc.pending_count(USER_ID) == 1

    assert uc.decide(token, USER_ID, "yes") is True
    decision = await uc.await_decision(token)

    assert decision is RefineDecision.yes
    assert uc.pending_count(USER_ID) == 0


async def test_register_decide_no_resolves_no():
    uc = ResolveRefineUseCase(provider=object())
    token = uc.register(user_id=USER_ID)
    assert uc.decide(token, USER_ID, "no") is True
    assert await uc.await_decision(token) is RefineDecision.no


async def test_decide_retap_is_noop():
    uc = ResolveRefineUseCase(provider=object())
    token = uc.register(user_id=USER_ID)
    assert uc.decide(token, USER_ID, "yes") is True
    # Re-tap (yes y no) tras resolver: no-op.
    assert uc.decide(token, USER_ID, "yes") is False
    assert uc.decide(token, USER_ID, "no") is False
    assert await uc.await_decision(token) is RefineDecision.yes
    assert uc.pending_count(USER_ID) == 0


async def test_decide_wrong_owner_is_noop():
    uc = ResolveRefineUseCase(provider=object())
    token = uc.register(user_id=USER_ID)
    assert uc.decide(token, OTHER_USER, "yes") is False
    assert uc.pending_count(USER_ID) == 1
    uc.drop(token)
    assert uc.pending_count(USER_ID) == 0


async def test_decide_unknown_token_and_invalid_choice_noop():
    uc = ResolveRefineUseCase(provider=object())
    assert uc.decide("nope", USER_ID, "yes") is False
    token = uc.register(user_id=USER_ID)
    assert uc.decide(token, USER_ID, "quizas") is False
    uc.cancel_for_job(USER_ID)  # cleanup del pending


async def test_await_decision_timeout_drops():
    uc = ResolveRefineUseCase(provider=object(), timeout=0.01)
    token = uc.register(user_id=USER_ID)
    decision = await uc.await_decision(token)
    assert decision is RefineDecision.timeout
    assert uc.pending_count(USER_ID) == 0


async def test_cancel_for_job_resolves_cancelled():
    uc = ResolveRefineUseCase(provider=object())
    token_a = uc.register(user_id=USER_ID, job_id="job-a")
    token_b = uc.register(user_id=USER_ID, job_id="job-b")

    uc.cancel_for_job(USER_ID, "job-a")
    assert await uc.await_decision(token_a) is RefineDecision.cancelled
    # El pending de otro job sigue vivo.
    assert uc.pending_count(USER_ID) == 1

    uc.cancel_for_job(USER_ID)
    assert await uc.await_decision(token_b) is RefineDecision.cancelled
    assert uc.pending_count(USER_ID) == 0


async def test_cancel_for_job_other_user_is_noop():
    uc = ResolveRefineUseCase(provider=object())
    token = uc.register(user_id=USER_ID, job_id="job-a")
    uc.cancel_for_job(OTHER_USER, "job-a")
    assert uc.pending_count(USER_ID) == 1
    uc.drop(token)


async def test_await_decision_after_cancel_for_job_is_cancelled_and_idempotent():
    uc = ResolveRefineUseCase(provider=object())
    token = uc.register(user_id=USER_ID, job_id="job-a")
    uc.cancel_for_job(USER_ID, "job-a")
    uc.cancel_for_job(USER_ID, "job-a")  # idempotente
    assert await uc.await_decision(token) is RefineDecision.cancelled


# --- segundo stage ------------------------------------------------------------

async def test_refine_delegates_to_provider():
    result = _remotes_result()
    prov = FakeComfyuiProvider(refine_outcomes=[result])
    uc = ResolveRefineUseCase(provider=prov)
    request = GenerationRequest(
        provider="comfyui",
        model_id="comfyui",
        media_type=MediaType.IMAGE,
        prompt="refina",
    )

    out = await uc.refine(request, ["/workspace/a.png"])

    assert out is result
    assert len(prov.refine_calls) == 1
    assert prov.refine_calls[0][0] is request
    assert prov.refine_calls[0][1] == ["/workspace/a.png"]
