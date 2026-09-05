"""Test aditivo D2 del item 5 (telegram) sobre la capa application.

Verifica que las adiciones D2 no rompen el contrato de item 4 y que viajan
hasta item 5:
1. ``BatchStarted.job_id`` propagado (random y multipose) y no vacío.
2. ``ItemResult.request`` presente (con params de comfyui en batch comfyui y en
   single ``GenerateImageUseCase``).
3. ``GenerateImageUseCase.run(cfg_override=...)`` cambia el provider resuelto.

Fakes del conftest local; IDs/prompts anonimizados (R8). Sin red ni Telegram.
"""

from __future__ import annotations

from dataclasses import replace

from grokbot.application.events import BatchStarted, ItemResult
from grokbot.application.generate_image import GenerateImageUseCase
from grokbot.application.job_manager import JobManager
from grokbot.application.run_variable_batch import (
    FixedPromptStrategy,
    RandomComboStrategy,
    RunVariableBatchUseCase,
)
from grokbot.domain.generation import MediaType
from grokbot.domain.user_config import ComfyUIConfig, UserConfig

from conftest import (
    USER_ID,
    FakeComfyuiProvider,
    FakeImageProvider,
    FakeVariablesRepo,
    make_registry,
    make_result,
)


async def _collect(uc, **kw):
    return [ev async for ev in uc.run(**kw)]


def _ok_result(i=1):
    return make_result(
        provider="kie",
        model_id="grok-imagine-image-2-0/text-to-image",
        file_path=f"/tmp/vars_{i}.jpg",
    )


# --- D2a: BatchStarted.job_id propagado y no vacío -----------------------------

async def test_batch_started_random_carries_job_id(sessions, variables_repo):
    prov = FakeImageProvider(name="kie", outcomes=[_ok_result(1)])
    reg = make_registry(kie=prov)
    jm = JobManager()
    uc = RunVariableBatchUseCase(
        sessions=sessions,
        registry=reg,
        variables=variables_repo,
        job_manager=jm,
        generate_image=GenerateImageUseCase(sessions=sessions, registry=reg),
    )
    events = await _collect(uc, user_id=USER_ID, count=1, strategy=RandomComboStrategy(variables_repo))
    started = events[0]
    assert isinstance(started, BatchStarted)
    assert started.job_id is not None and len(started.job_id) > 0
    assert started.style == "variables"


async def test_batch_started_multipose_carries_job_id(sessions, variables_repo):
    cfg = replace(
        UserConfig.defaults(),
        model="comfyui",
        comfyui=ComfyUIConfig(model="qwen", lora="multipose_batch"),
    )
    sessions.save_config(USER_ID, cfg)
    prov = FakeComfyuiProvider(outcomes=[make_result(provider="comfyui", file_path="/tmp/mp.jpg")])
    reg = make_registry(comfyui=prov)
    jm = JobManager()
    uc = RunVariableBatchUseCase(
        sessions=sessions,
        registry=reg,
        variables=variables_repo,
        job_manager=jm,
        generate_image=GenerateImageUseCase(sessions=sessions, registry=reg),
    )
    events = await _collect(
        uc,
        user_id=USER_ID,
        count=1,
        strategy=FixedPromptStrategy("x"),
        source_image=b"jpg-bytes",
    )
    started = events[0]
    assert isinstance(started, BatchStarted)
    assert started.job_id is not None and len(started.job_id) > 0
    assert started.style == "multipose"


# --- D2b: ItemResult.request presente -----------------------------------------

async def test_single_image_item_result_carries_request(sessions):
    prov = FakeImageProvider(name="kie", outcomes=[_ok_result(1)])
    reg = make_registry(kie=prov)
    uc = GenerateImageUseCase(sessions=sessions, registry=reg)
    events = await _collect(uc, user_id=USER_ID, prompt="un retrato")
    (ev,) = events
    assert isinstance(ev, ItemResult)
    assert ev.request is not None
    assert ev.request.provider == "kie"
    assert ev.request.media_type is MediaType.IMAGE
    assert ev.request.prompt == "un retrato"


async def test_comfyui_batch_item_result_request_has_params(sessions, variables_repo):
    cfg = replace(
        UserConfig.defaults(),
        model="comfyui",
        comfyui=ComfyUIConfig(model="grok_style"),
    )
    sessions.save_config(USER_ID, cfg)
    prov = FakeComfyuiProvider(outcomes=[make_result(provider="comfyui", file_path="/tmp/mp.jpg")])
    reg = make_registry(comfyui=prov)
    jm = JobManager()
    uc = RunVariableBatchUseCase(
        sessions=sessions,
        registry=reg,
        variables=variables_repo,
        job_manager=jm,
        generate_image=GenerateImageUseCase(sessions=sessions, registry=reg),
    )
    events = await _collect(
        uc,
        user_id=USER_ID,
        count=1,
        strategy=FixedPromptStrategy("x"),
        source_image=b"jpg-bytes",
    )
    results = [ev for ev in events if isinstance(ev, ItemResult)]
    assert len(results) == 1
    req = results[0].request
    assert req is not None
    assert req.provider == "comfyui"
    assert req.params.get("model") == "grok_style"
    assert "lora" not in req.params
    assert "refine" not in req.params


# --- D2c: cfg_override cambia el provider resuelto ------------------------------

async def test_cfg_override_selects_provider_without_reloading_session(sessions):
    kie_prov = FakeImageProvider(name="kie", outcomes=[_ok_result(1)])
    repl_prov = FakeImageProvider(name="replicate", outcomes=[_ok_result(1)])
    reg = make_registry(kie=kie_prov, replicate=repl_prov)
    # La sesión default es grok→kie; el override a seedream debe resolver a replicate.
    override = replace(UserConfig.defaults(), model="seedream")
    uc = GenerateImageUseCase(sessions=sessions, registry=reg)
    events = await _collect(
        uc,
        user_id=USER_ID,
        prompt="un retrato",
        cfg_override=override,
    )
    (ev,) = events
    assert isinstance(ev, ItemResult)
    assert ev.request.provider == "replicate"
    assert repl_prov.generate_count == 1
    assert kie_prov.generate_count == 0
