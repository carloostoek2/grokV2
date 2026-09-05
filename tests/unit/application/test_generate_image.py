"""Tests de GenerateImageUseCase (R3/R6/M2) — retry/exhausted/terminal tipados.

Escenarios por clase de error (M3): solo ``retryable=True`` se reintenta; los
terminales nunca; las excepciones genéricas nunca exponen ``repr`` (R6). El
guard M2 consulta ``supports()``/valida el modelo ComfyUI ANTES de generar.
"""

from __future__ import annotations

from dataclasses import replace

from grokbot.application.events import ItemFailed, ItemResult, RetryScheduled
from grokbot.application.generate_image import GenerateImageUseCase
from grokbot.domain.generation import MediaType
from grokbot.domain.user_config import ComfyUIConfig, UserConfig
from grokbot.providers.base import (
    ProviderInputError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
)

# Fakes comunes.
from conftest import USER_ID, FakeComfyuiProvider, FakeImageProvider, make_registry, make_result


async def _run(uc, **kw):
    return [ev async for ev in uc.run(**kw)]


def _uc(reg, sessions):
    return GenerateImageUseCase(sessions=sessions, registry=reg)


async def test_success_t2i_yields_item_result_with_text_regen_context(sessions):
    result = make_result(provider="kie", model_id="grok-imagine-image-2-0/text-to-image")
    prov = FakeImageProvider(name="kie", outcomes=[result])
    reg = make_registry(kie=prov)
    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="un retrato de prueba")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemResult)
    assert ev.result is result
    assert ev.regen_context is not None
    assert ev.regen_context["mode"] == "text"
    assert ev.regen_context["model_key"] == "grok"
    assert ev.regen_context["provider"] == "kie"
    assert prov.generate_count == 1


async def test_success_i2i_regen_context_edit_and_passes_bytes(sessions):
    result = make_result(provider="kie", model_id="grok-imagine-image-2-0/text-to-image")
    prov = FakeImageProvider(name="kie", outcomes=[result])
    reg = make_registry(kie=prov)
    events = await _run(
        _uc(reg, sessions),
        user_id=USER_ID,
        prompt="edita la imagen",
        source_image=b"jpg-bytes",
    )

    ev = events[0]
    assert isinstance(ev, ItemResult)
    assert ev.regen_context["mode"] == "edit"
    assert ev.regen_context["prompt"] == "edita la imagen"
    _request, source = prov.calls[0]
    assert source == b"jpg-bytes"


async def test_supports_false_terminates_without_generating(sessions):
    prov = FakeImageProvider(name="kie", supports_result=False)
    reg = make_registry(kie=prov)
    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="x")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert prov.generate_count == 0
    assert len(prov.supports_calls) == 1


async def test_comfyui_video_model_rejected_for_image(sessions):
    cfg = replace(
        UserConfig.defaults(),
        model="comfyui",
        comfyui=ComfyUIConfig(model="wan_i2v", lora="lightx2v"),
    )
    sessions.save_config(USER_ID, cfg)
    prov = FakeComfyuiProvider()
    reg = make_registry(comfyui=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="x")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert "genera video" in ev.reason
    assert prov.generate_count == 0


async def test_provider_input_error_terminal_no_retry(sessions):
    err = ProviderInputError("bad input", user_message="Entrada inválida.")
    prov = FakeImageProvider(name="kie", outcomes=[err])
    reg = make_registry(kie=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="x")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert ev.exhausted is False
    assert ev.reason == "Entrada inválida."
    assert prov.generate_count == 1  # sin reintentos (M3)


async def test_rate_limit_exhausted_after_5_retries(sessions, fast_sleep):
    err = ProviderRateLimitError("rate", user_message="Demasiadas peticiones.")
    prov = FakeImageProvider(name="kie", outcomes=[err] * 6)
    reg = make_registry(kie=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="x")

    retries = [ev for ev in events if isinstance(ev, RetryScheduled)]
    assert len(retries) == 5
    assert retries[0].attempt == 2
    assert retries[0].max_attempts == 6
    assert retries[-1].attempt == 6
    assert all(r.provider == "kie" for r in retries)

    failed = [ev for ev in events if isinstance(ev, ItemFailed)]
    assert len(failed) == 1
    assert failed[0].exhausted is True
    assert failed[0].terminal is False
    assert failed[0].reason == "Demasiadas peticiones."
    assert prov.generate_count == 6


async def test_rate_limit_then_success(sessions, fast_sleep):
    err = ProviderRateLimitError("rate", user_message="Demasiadas peticiones.")
    ok = make_result(provider="kie", model_id="grok-imagine-image-2-0/text-to-image")
    prov = FakeImageProvider(name="kie", outcomes=[err, err, ok])
    reg = make_registry(kie=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="x")

    retries = [ev for ev in events if isinstance(ev, RetryScheduled)]
    assert len(retries) == 2
    results = [ev for ev in events if isinstance(ev, ItemResult)]
    assert len(results) == 1
    assert results[0].result is ok
    assert prov.generate_count == 3


async def test_provider_not_configured_reason_user_safe(sessions):
    reg = make_registry(kie=None)  # usuario en kie default sin provider
    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="x")

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert ev.exhausted is False
    assert "Kie.ai no está disponible" in ev.reason


async def test_generic_exception_reason_never_reprs(sessions):
    prov = FakeImageProvider(name="kie", outcomes=[RuntimeError("secreto interno 12345")])
    reg = make_registry(kie=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="x")

    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert "secreto interno 12345" not in ev.reason
    assert "Intenta de nuevo más tarde" in ev.reason


async def test_comfyui_params_flow_id_in_request(sessions):
    cfg = replace(
        UserConfig.defaults(),
        model="comfyui",
        comfyui=ComfyUIConfig(model="grok_style"),
    )
    sessions.save_config(USER_ID, cfg)
    prov = FakeComfyuiProvider(
        outcomes=[make_result(provider="comfyui", model_id="comfyui", file_path="/tmp/a.jpg")]
    )
    reg = make_registry(comfyui=prov)

    await _run(_uc(reg, sessions), user_id=USER_ID, prompt="un retrato")

    request, _ = prov.calls[0]
    assert request.provider == "comfyui"
    assert request.media_type is MediaType.IMAGE
    # El request lleva el id de flujo; lora/refine quedaron dormidos (no viajan).
    assert request.params["model"] == "grok_style"
    assert "lora" not in request.params
    assert "refine" not in request.params


async def test_comfyui_prompts_multipose_in_request(sessions):
    cfg = replace(
        UserConfig.defaults(),
        model="comfyui",
        comfyui=ComfyUIConfig(model="qwen", lora="multipose_batch", refine="1"),
    )
    sessions.save_config(USER_ID, cfg)
    prov = FakeComfyuiProvider(
        outcomes=[make_result(provider="comfyui", model_id="comfyui", file_path="/tmp/b.jpg")]
    )
    reg = make_registry(comfyui=prov)
    ramas = ["<sks> de pie", "<sks> sentada", "<sks> saltando", "<sks> corriendo", "<sks> agachada"]

    await _run(_uc(reg, sessions), user_id=USER_ID, prompt=ramas[0], prompts=ramas)

    request, _ = prov.calls[0]
    assert request.params["prompts"] == ramas


async def test_success_sets_elapsed_sec_in_meta(sessions):
    """El use case estampa el tiempo real en el meta del resultado (paridad grok).

    Se muta el dict mutable de meta IN-PLACE: ``ev.result is result`` se conserva
    (los test previos ya dependen de esa identidad) y el sender podrá leer
    ``meta["elapsed_sec"]`` para mostrar ``<b>Tiempo:</b> Ns`` en el caption.
    """
    result = make_result(provider="kie", model_id="grok-imagine-image-2-0/text-to-image")
    prov = FakeImageProvider(name="kie", outcomes=[result])
    reg = make_registry(kie=prov)
    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="un retrato")

    ev = events[0]
    assert isinstance(ev, ItemResult)
    assert ev.result is result
    assert isinstance(ev.result.meta.get("elapsed_sec"), int)
    assert ev.result.meta["elapsed_sec"] >= 0


async def test_success_after_retry_includes_elapsed(sessions, fast_sleep):
    """El cronómetro arranca ANTES del loop: el elapsed cubre reintentos+backoff."""
    err = ProviderRateLimitError("rate", user_message="Demasiadas peticiones.")
    ok = make_result(provider="kie", model_id="grok-imagine-image-2-0/text-to-image")
    prov = FakeImageProvider(name="kie", outcomes=[err, ok])
    reg = make_registry(kie=prov)

    events = await _run(_uc(reg, sessions), user_id=USER_ID, prompt="x")

    results = [ev for ev in events if isinstance(ev, ItemResult)]
    assert len(results) == 1
    assert isinstance(results[0].result.meta.get("elapsed_sec"), int)
    assert results[0].result.meta["elapsed_sec"] >= 0
