"""Tests de GenerateVideoUseCase — single-attempt + guard M2 (item 4).

El video NO reintenta (paridad grok): un error del provider termina el ítem con
una sola llamada a ``generate``. El guard M2 rechaza un request VIDEO contra un
modelo ComfyUI de imagen sin llamar a ``generate``.
"""

from __future__ import annotations

from dataclasses import replace

from grokbot.application.events import ItemFailed, ItemResult
from grokbot.application.generate_video import GenerateVideoUseCase
from grokbot.domain.generation import MediaType
from grokbot.domain.user_config import ComfyUIConfig, UserConfig
from grokbot.providers.base import ProviderRateLimitError, ProviderTimeoutError

from conftest import USER_ID, FakeComfyuiProvider, FakeVideoProvider, make_registry, make_result


async def _run(uc, **kw):
    return [ev async for ev in uc.run(**kw)]


def _video_cfg(**over):
    return replace(UserConfig.defaults(), model="grok_video", **over)


def _uc(reg, sessions):
    return GenerateVideoUseCase(sessions=sessions, registry=reg)


async def test_success_video_yields_item_result(sessions):
    cfg = _video_cfg()
    sessions.save_config(USER_ID, cfg)
    result = make_result(provider="kie", model_id="grok-imagine-video", media_type=MediaType.VIDEO)
    prov = FakeVideoProvider(name="kie", outcomes=[result])
    reg = make_registry(kie=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="una escena")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemResult)
    assert ev.result is result
    assert ev.result.media_type is MediaType.VIDEO


async def test_request_carries_video_fields_from_cfg(sessions):
    cfg = _video_cfg(
        grok_imagine_provider="kie",
    )
    sessions.save_config(USER_ID, cfg)
    prov = FakeVideoProvider(
        name="kie",
        outcomes=[make_result(provider="kie", model_id="grok-imagine-video", media_type=MediaType.VIDEO)],
    )
    reg = make_registry(kie=prov)

    await _run(_uc(reg, sessions), user_id=USER_ID, prompt="escena")

    request, _ = prov.calls[0]
    assert request.media_type is MediaType.VIDEO
    assert request.model_id == "grok-imagine-video"
    assert request.aspect_ratio == "16:9"
    assert request.video_duration == 5
    assert request.video_resolution == "720p"
    assert request.video_mode == "normal"


async def test_i2v_passes_source_image(sessions):
    cfg = _video_cfg()
    sessions.save_config(USER_ID, cfg)
    prov = FakeVideoProvider(
        name="kie",
        outcomes=[make_result(provider="kie", model_id="grok-imagine-video", media_type=MediaType.VIDEO)],
    )
    reg = make_registry(kie=prov)

    await _run(_uc(reg, sessions), user_id=USER_ID, prompt="anima", source_image=b"jpg")

    _request, source = prov.calls[0]
    assert source == b"jpg"


async def test_timeout_is_terminal_single_attempt(sessions):
    cfg = _video_cfg()
    sessions.save_config(USER_ID, cfg)
    err = ProviderTimeoutError("poll timeout", user_message="El video tardó demasiado.")
    prov = FakeVideoProvider(name="kie", outcomes=[err])
    reg = make_registry(kie=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="escena")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert ev.reason == "El video tardó demasiado."
    assert prov.generate_count == 1  # single-attempt (sin retry)


async def test_comfyui_image_model_rejected_for_video(sessions):
    cfg = replace(UserConfig.defaults(), model="comfyui", comfyui=ComfyUIConfig(model="krea2"))
    sessions.save_config(USER_ID, cfg)
    prov = FakeComfyuiProvider()
    reg = make_registry(comfyui=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="escena")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert "no genera video" in ev.reason
    assert prov.generate_count == 0


async def test_comfyui_video_model_allowed(sessions):
    cfg = replace(
        UserConfig.defaults(),
        model="comfyui",
        comfyui=ComfyUIConfig(model="wan_i2v", lora="lightx2v", refine="1"),
    )
    sessions.save_config(USER_ID, cfg)
    result = make_result(
        provider="comfyui",
        model_id="comfyui",
        media_type=MediaType.VIDEO,
        file_path="/tmp/v.mp4",
        meta={"file_paths": ["/tmp/v.mp4"], "comfyui_remotes": ["/workspace/v.mp4"]},
    )
    prov = FakeComfyuiProvider(outcomes=[result])
    reg = make_registry(comfyui=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="escena")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemResult)
    assert ev.result is result
    request, _ = prov.calls[0]
    assert request.params["model"] == "wan_i2v"


async def test_resolve_video_not_configured_is_terminal(sessions):
    cfg = _video_cfg(grok_imagine_provider="kie")
    sessions.save_config(USER_ID, cfg)
    reg = make_registry(kie=None)  # usuario en kie video sin provider disponible

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="escena")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert ev.exhausted is False
    assert "Kie.ai no está disponible" in ev.reason


async def test_transient_error_video_single_attempt_no_retry(sessions):
    """Un transitorio (rate-limit) en video NO reintenta (paridad single-attempt).

    Documenta la invariante O3 del arch-enforcer: el video emite UN SOLO
    ItemFailed (nunca RetryScheduled) y no relanza; ítem 5 debe tratarlo como
    final aunque el flag terminal sea False para un error retryable.
    """
    cfg = _video_cfg(grok_imagine_provider="kie")
    sessions.save_config(USER_ID, cfg)
    err = ProviderRateLimitError("rate", user_message="Demasiadas peticiones.")
    prov = FakeVideoProvider(name="kie", outcomes=[err])
    reg = make_registry(kie=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="escena")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.reason == "Demasiadas peticiones."
    assert prov.generate_count == 1  # single-attempt: el transitorio no se reintenta
