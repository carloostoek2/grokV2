"""Tests de RunVariableBatchUseCase — /variables (random) y /var (fixed) (R1).

Escenarios del PLAN §Task 4: orden normativo, skip-on-fail, shuffle+blacklist
solo random y solo exhausted, JobsFull, cancel entre items, lista vacía,
BatchRejected, multipose (single round-trip / sin foto / cancel) y qwen_aio.
Sin red ni Telegram; fakes del conftest local. IDs/prompts anonimizados (R8).
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import replace

from grokbot.application.events import (
    BatchCancelled,
    BatchRejected,
    BatchStarted,
    BatchSummary,
    EmptyList,
    ItemFailed,
    ItemResult,
    ItemStarted,
    JobsFull,
    RetryScheduled,
)
from grokbot.application.generate_image import GenerateImageUseCase
from grokbot.application.job_manager import JobManager
from grokbot.application.run_variable_batch import (
    FixedPromptStrategy,
    RandomComboStrategy,
    RunVariableBatchUseCase,
)
from grokbot.domain.user_config import ComfyUIConfig, UserConfig
from grokbot.providers.base import ProviderInputError, ProviderRateLimitError

from conftest import (
    USER_ID,
    FakeComfyuiProvider,
    FakeImageProvider,
    FakeVariablesRepo,
    make_registry,
    make_result,
)


def _uc(sessions, reg, variables, jm=None, gen=None):
    if jm is None:
        jm = JobManager()
    if gen is None:
        gen = GenerateImageUseCase(sessions=sessions, registry=reg)
    return (
        RunVariableBatchUseCase(
            sessions=sessions,
            registry=reg,
            variables=variables,
            job_manager=jm,
            generate_image=gen,
        ),
        jm,
    )


async def _collect(uc, **kw):
    return [ev async for ev in uc.run(**kw)]


def _ok_result(i):
    return make_result(
        provider="kie",
        model_id="grok-imagine-image-2-0/text-to-image",
        file_path=f"/tmp/vars_{i}.jpg",
    )


# --- random: éxito / orden / combos únicos ------------------------------------

async def test_random_success_event_order_and_unique_combos(sessions, variables_repo, fast_sleep):
    prov = FakeImageProvider(name="kie", outcomes=[_ok_result(1), _ok_result(2), _ok_result(3)])
    reg = make_registry(kie=prov)
    uc, jm = _uc(sessions, reg, variables_repo)

    events = await _collect(uc, user_id=USER_ID, count=3, strategy=RandomComboStrategy(variables_repo))

    assert [type(ev).__name__ for ev in events] == [
        "BatchStarted",
        "ItemStarted",
        "ItemResult",
        "ItemStarted",
        "ItemResult",
        "ItemStarted",
        "ItemResult",
        "BatchSummary",
    ]
    started = events[0]
    assert isinstance(started, BatchStarted)
    assert started.style == "variables" and started.total == 3
    results = [ev for ev in events if isinstance(ev, ItemResult)]
    assert len(results) == 3
    assert {r.index for r in results} == {1, 2, 3}
    assert len({r.prompt for r in results}) == 3  # `used` acumula → sin repetidos
    summary = events[-1]
    assert isinstance(summary, BatchSummary)
    assert (summary.completed, summary.failed, summary.total) == (3, 0, 3)
    assert jm.active_count(USER_ID) == 0


# --- random: skip-on-fail (un ítem falla y el siguiente corre) ----------------

async def test_random_partial_skip_on_fail(sessions, variables_repo, fast_sleep):
    err = ProviderInputError("bad", user_message="Entrada inválida.")
    prov = FakeImageProvider(name="kie", outcomes=[_ok_result(1), err, _ok_result(3)])
    reg = make_registry(kie=prov)
    uc, _ = _uc(sessions, reg, variables_repo)

    events = await _collect(uc, user_id=USER_ID, count=3, strategy=RandomComboStrategy(variables_repo))

    results = [ev for ev in events if isinstance(ev, ItemResult)]
    failed = [ev for ev in events if isinstance(ev, ItemFailed)]
    assert len(results) == 2
    assert len(failed) == 1
    assert failed[0].terminal is True and failed[0].exhausted is False
    assert failed[0].index == 2
    assert {r.index for r in results} == {1, 3}
    summary = events[-1]
    assert isinstance(summary, BatchSummary)
    assert (summary.completed, summary.failed, summary.total) == (2, 1, 3)


# --- exhausted random: supresión del 1er exhausted + shuffle + blacklist ------

async def test_random_exhausted_suppresses_first_and_blacklists(sessions, variables_repo, fast_sleep):
    expected = RandomComboStrategy(variables_repo, rng=random.Random(1234)).draw(exclude=set())
    assert expected is not None
    strategy = RandomComboStrategy(variables_repo, rng=random.Random(1234))
    err = ProviderRateLimitError("rate", user_message="Demasiadas peticiones.")
    prov = FakeImageProvider(name="kie", outcomes=[err] * 12)  # 6 primera corrida + 6 shuffle
    reg = make_registry(kie=prov)
    uc, _ = _uc(sessions, reg, variables_repo)

    events = await _collect(uc, user_id=USER_ID, count=1, strategy=strategy)

    retries = [ev for ev in events if isinstance(ev, RetryScheduled)]
    assert len(retries) == 10
    assert retries[0].attempt == 2
    failed = [ev for ev in events if isinstance(ev, ItemFailed)]
    assert len(failed) == 1  # el primer exhausted se SUPRIMIÓ
    assert failed[0].exhausted is True
    assert failed[0].index == 1
    assert prov.generate_count == 12  # primera corrida + shuffle (6+6)
    assert variables_repo.blacklist_add_calls == [expected.key]


# --- exhausted fixed: NO shuffle, NO blacklist ---------------------------------

async def test_fixed_exhausted_no_shuffle_no_blacklist(sessions, variables_repo, fast_sleep):
    err = ProviderRateLimitError("rate", user_message="Demasiadas peticiones.")
    prov = FakeImageProvider(name="kie", outcomes=[err] * 6)
    reg = make_registry(kie=prov)
    uc, _ = _uc(sessions, reg, variables_repo)

    events = await _collect(uc, user_id=USER_ID, count=1, strategy=FixedPromptStrategy("retrato fijo"))

    retries = [ev for ev in events if isinstance(ev, RetryScheduled)]
    assert len(retries) == 5
    failed = [ev for ev in events if isinstance(ev, ItemFailed)]
    assert len(failed) == 1 and failed[0].exhausted is True
    assert prov.generate_count == 6  # 1 sola corrida (sin shuffle)
    assert variables_repo.blacklist_add_calls == []


# --- JobsFull / cancel / EmptyList / BatchRejected ----------------------------

async def test_jobs_full_when_three_active(sessions, variables_repo, fast_sleep):
    jm = JobManager()  # max_active = 3
    for _ in range(3):
        assert jm.start(USER_ID, "x") is not None
    reg = make_registry(kie=FakeImageProvider(name="kie"))
    uc, _ = _uc(sessions, reg, variables_repo, jm=jm)

    events = await _collect(uc, user_id=USER_ID, count=3, strategy=RandomComboStrategy(variables_repo))

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, JobsFull)
    assert ev.active == 3 and ev.max_active == 3


class _GatedKieProvider(FakeImageProvider):
    """Kie fake cuyo generate se bloquea hasta que el test lo libera.

    ``block_calls=None`` (default) bloquea TODAS las llamadas; con un set
    (1-based) solo bloquea las llamadas indicadas y deja pasar las demás —
    permite escenarios "ítem i completo, ítem i+1 en vuelo".
    """

    def __init__(self, gate, entered, *, outcomes=None, block_calls=None) -> None:
        super().__init__(name="kie", outcomes=outcomes)
        self._gate = gate
        self.entered = entered
        self._calls = 0
        self._block_calls = block_calls

    async def generate(self, request, *, source_image=None):
        self._calls += 1
        if self._block_calls is None or self._calls in self._block_calls:
            self.entered.set()
            await self._gate.wait()
        return await super().generate(request, source_image=source_image)


async def test_cancel_during_generation_suppresses_inflight_item(sessions, variables_repo, fast_sleep):
    gate = asyncio.Event()
    entered = asyncio.Event()
    prov = _GatedKieProvider(gate, entered, outcomes=[_ok_result(1)])
    reg = make_registry(kie=prov)
    jm = JobManager()
    uc, _ = _uc(sessions, reg, variables_repo, jm=jm)

    events: list = []

    async def _consume():
        async for ev in uc.run(user_id=USER_ID, count=3, strategy=RandomComboStrategy(variables_repo)):
            events.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.wait_for(entered.wait(), timeout=2)  # item 1 en vuelo (generando)
    job_id = jm.active_jobs(USER_ID)[0].job_id
    jm.cancel(USER_ID, job_id)
    gate.set()
    await asyncio.wait_for(task, timeout=2)

    # El item en vuelo se SUPRIME: su ItemResult no se relayea ni se cuenta y el
    # batch termina en BatchCancelled — paridad grok bot.py 2387-2399.
    cancelled = [ev for ev in events if isinstance(ev, BatchCancelled)]
    assert len(cancelled) == 1
    assert (cancelled[0].completed, cancelled[0].failed, cancelled[0].total) == (0, 0, 3)
    assert not any(isinstance(ev, (ItemResult, ItemFailed)) for ev in events)
    assert not any(isinstance(ev, BatchSummary) for ev in events)
    assert [type(ev).__name__ for ev in events] == ["BatchStarted", "ItemStarted", "BatchCancelled"]
    assert prov.generate_count == 1  # la generación en vuelo corrió pero no se entregó
    assert jm.active_count(USER_ID) == 0


async def test_cancel_during_last_item_yields_batch_cancelled_not_summary(sessions, variables_repo, fast_sleep):
    gate = asyncio.Event()
    entered = asyncio.Event()
    prov = _GatedKieProvider(gate, entered, outcomes=[_ok_result(1)])
    reg = make_registry(kie=prov)
    jm = JobManager()
    uc, _ = _uc(sessions, reg, variables_repo, jm=jm)

    events: list = []

    async def _consume():
        async for ev in uc.run(user_id=USER_ID, count=1, strategy=RandomComboStrategy(variables_repo)):
            events.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.wait_for(entered.wait(), timeout=2)  # ÚLTIMO item en vuelo
    job_id = jm.active_jobs(USER_ID)[0].job_id
    jm.cancel(USER_ID, job_id)
    gate.set()
    await asyncio.wait_for(task, timeout=2)

    # Cancel durante el último item: NUNCA BatchSummary con un item en vuelo.
    cancelled = [ev for ev in events if isinstance(ev, BatchCancelled)]
    assert len(cancelled) == 1
    assert (cancelled[0].completed, cancelled[0].failed, cancelled[0].total) == (0, 0, 1)
    assert not any(isinstance(ev, (ItemResult, ItemFailed)) for ev in events)
    assert not any(isinstance(ev, BatchSummary) for ev in events)
    assert [type(ev).__name__ for ev in events] == ["BatchStarted", "ItemStarted", "BatchCancelled"]
    assert prov.generate_count == 1
    assert jm.active_count(USER_ID) == 0


async def test_cancel_between_items_counts_finished_suppresses_next(sessions, variables_repo, fast_sleep):
    gate = asyncio.Event()
    entered = asyncio.Event()
    # Item 1 pasa libre; item 2 queda en vuelo (gate).
    prov = _GatedKieProvider(gate, entered, outcomes=[_ok_result(1), _ok_result(2)], block_calls={2})
    reg = make_registry(kie=prov)
    jm = JobManager()
    uc, _ = _uc(sessions, reg, variables_repo, jm=jm)

    events: list = []

    async def _consume():
        async for ev in uc.run(user_id=USER_ID, count=3, strategy=RandomComboStrategy(variables_repo)):
            events.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.wait_for(entered.wait(), timeout=2)  # item 1 completado, item 2 en vuelo
    assert sum(1 for ev in events if isinstance(ev, ItemResult)) == 1
    job_id = jm.active_jobs(USER_ID)[0].job_id
    jm.cancel(USER_ID, job_id)
    gate.set()
    await asyncio.wait_for(task, timeout=2)

    # Lo ya completado se cuenta (completed=1); el item en vuelo se suprime.
    cancelled = [ev for ev in events if isinstance(ev, BatchCancelled)]
    assert len(cancelled) == 1
    assert (cancelled[0].completed, cancelled[0].failed, cancelled[0].total) == (1, 0, 3)
    assert sum(1 for ev in events if isinstance(ev, ItemResult)) == 1  # solo item 1
    assert not any(isinstance(ev, ItemFailed) for ev in events)
    assert not any(isinstance(ev, BatchSummary) for ev in events)
    assert prov.generate_count == 2
    assert jm.active_count(USER_ID) == 0


async def test_empty_list_before_batch_start_and_no_job(sessions, fast_sleep):
    variables = FakeVariablesRepo(
        lists={"poses": [], "angles": ["frontal"], "actions": ["mirando a cámara"]}
    )
    reg = make_registry(kie=FakeImageProvider(name="kie"))
    jm = JobManager()
    uc, _ = _uc(sessions, reg, variables, jm=jm)

    events = await _collect(uc, user_id=USER_ID, count=3, strategy=RandomComboStrategy(variables))

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, EmptyList)
    assert ev.name == "poses"
    assert jm.active_count(USER_ID) == 0
    assert not any(isinstance(e, BatchStarted) for e in events)


async def test_batch_rejected_grok_video(sessions, variables_repo, fast_sleep):
    cfg = replace(UserConfig.defaults(), model="grok_video")
    sessions.save_config(USER_ID, cfg)
    reg = make_registry(kie=FakeImageProvider(name="kie"))
    jm = JobManager()
    uc, _ = _uc(sessions, reg, variables_repo, jm=jm)

    events = await _collect(uc, user_id=USER_ID, count=3, strategy=RandomComboStrategy(variables_repo))

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, BatchRejected)
    assert "video" in ev.reason
    assert jm.active_count(USER_ID) == 0


async def test_batch_rejected_comfyui_video_model(sessions, variables_repo, fast_sleep):
    cfg = replace(
        UserConfig.defaults(),
        model="comfyui",
        comfyui=ComfyUIConfig(model="wan_i2v", lora="lightx2v", refine="0"),
    )
    sessions.save_config(USER_ID, cfg)
    prov = FakeComfyuiProvider()
    reg = make_registry(comfyui=prov)
    jm = JobManager()
    uc, _ = _uc(sessions, reg, variables_repo, jm=jm)

    events = await _collect(uc, user_id=USER_ID, count=3, strategy=RandomComboStrategy(variables_repo))

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, BatchRejected)
    assert ev.reason == "El modelo ComfyUI configurado genera video, no imágenes."
    assert prov.generate_count == 0
    assert jm.active_count(USER_ID) == 0


# --- multipose ----------------------------------------------------------------

def _multipose_cfg():
    return replace(
        UserConfig.defaults(),
        model="comfyui",
        comfyui=ComfyUIConfig(model="qwen", lora="multipose_batch", refine="1"),
    )


async def test_multipose_single_roundtrip_and_summary(sessions, variables_repo, fast_sleep):
    sessions.save_config(USER_ID, _multipose_cfg())
    result = make_result(
        provider="comfyui",
        model_id="comfyui",
        file_path="/tmp/mp.jpg",
        meta={"file_paths": ["/tmp/mp.jpg"], "comfyui_remotes": ["/workspace/mp.png"]},
    )
    prov = FakeComfyuiProvider(outcomes=[result])
    reg = make_registry(comfyui=prov)
    uc, jm = _uc(sessions, reg, variables_repo)

    events = await _collect(
        uc,
        user_id=USER_ID,
        count=5,
        strategy=RandomComboStrategy(variables_repo),
        source_image=b"jpg-bytes",
    )

    started = events[0]
    assert isinstance(started, BatchStarted)
    assert started.style == "multipose" and started.total == 5
    results = [ev for ev in events if isinstance(ev, ItemResult)]
    assert len(results) == 1
    assert results[0].combos is not None and len(results[0].combos) == 5
    summaries = [ev for ev in events if isinstance(ev, BatchSummary)]
    assert len(summaries) == 1
    assert summaries[0].combos is not None and len(summaries[0].combos) == 5
    assert prov.generate_count == 1  # UN round-trip al box
    request, source = prov.calls[0]
    assert source == b"jpg-bytes"
    ramas = request.params["prompts"]
    assert len(ramas) == 5
    assert all(r.startswith("<sks> ") for r in ramas)
    assert jm.active_count(USER_ID) == 0


async def test_multipose_requires_photo(sessions, variables_repo):
    sessions.save_config(USER_ID, _multipose_cfg())
    prov = FakeComfyuiProvider()
    reg = make_registry(comfyui=prov)
    jm = JobManager()
    uc, _ = _uc(sessions, reg, variables_repo, jm=jm)

    events = await _collect(
        uc,
        user_id=USER_ID,
        count=5,
        strategy=RandomComboStrategy(variables_repo),
        source_image=None,
    )

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert "foto" in ev.reason
    assert prov.generate_count == 0
    assert jm.active_count(USER_ID) == 0


async def test_multipose_empty_list_precheck_carries_multipose_style(sessions, fast_sleep):
    """C15: el precheck de lista vacía multipose emite ``EmptyList(style="multipose")``.

    El precheck corre ANTES del ``BatchStarted`` (sin job aún); el evento
    transporta el estilo para que el presenter diga "modo Multi-pose" y no
    "/variables" (el handler de /variables pasa su propio style por default).
    """
    sessions.save_config(USER_ID, _multipose_cfg())
    variables = FakeVariablesRepo(
        lists={"poses": [], "angles": ["frontal"], "actions": ["mirando a cámara"]}
    )
    prov = FakeComfyuiProvider()
    reg = make_registry(comfyui=prov)
    jm = JobManager()
    uc, _ = _uc(sessions, reg, variables, jm=jm)

    events = await _collect(
        uc,
        user_id=USER_ID,
        count=5,
        strategy=RandomComboStrategy(variables),
        source_image=b"jpg-bytes",
    )

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, EmptyList)
    assert ev.name == "poses"
    assert ev.style == "multipose"
    assert jm.active_count(USER_ID) == 0
    assert prov.generate_count == 0


class _GateComfyuiProvider(FakeComfyuiProvider):
    def __init__(self, gate: asyncio.Event, entered: asyncio.Event, *, outcomes=None) -> None:
        super().__init__(outcomes=outcomes)
        self._gate = gate
        self.entered = entered

    async def generate(self, request, *, source_image=None):
        self.entered.set()
        await self._gate.wait()
        return await super().generate(request, source_image=source_image)


async def test_multipose_cancel_during_poll(sessions, variables_repo, fast_sleep):
    sessions.save_config(USER_ID, _multipose_cfg())
    gate = asyncio.Event()
    entered = asyncio.Event()
    result = make_result(
        provider="comfyui",
        model_id="comfyui",
        file_path="/tmp/mp.jpg",
        meta={"file_paths": ["/tmp/mp.jpg"], "comfyui_remotes": ["/workspace/mp.png"]},
    )
    prov = _GateComfyuiProvider(gate, entered, outcomes=[result])
    reg = make_registry(comfyui=prov)
    jm = JobManager()
    uc, _ = _uc(sessions, reg, variables_repo, jm=jm)

    events: list = []

    async def _consume():
        async for ev in uc.run(
            user_id=USER_ID,
            count=5,
            strategy=RandomComboStrategy(variables_repo),
            source_image=b"jpg-bytes",
        ):
            events.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.wait_for(entered.wait(), timeout=2)
    job_id = jm.active_jobs(USER_ID)[0].job_id
    jm.cancel(USER_ID, job_id)
    gate.set()
    await asyncio.wait_for(task, timeout=2)

    cancelled = [ev for ev in events if isinstance(ev, BatchCancelled)]
    assert len(cancelled) == 1
    assert not any(isinstance(ev, BatchSummary) for ev in events)
    assert jm.active_count(USER_ID) == 0


# --- qwen_aio -----------------------------------------------------------------

async def test_qwen_aio_rewrites_prompt(sessions, variables_repo, fast_sleep):
    cfg = replace(
        UserConfig.defaults(),
        model="comfyui",
        comfyui=ComfyUIConfig(model="qwen_aio", lora="qwen_snofs", refine="0"),
    )
    sessions.save_config(USER_ID, cfg)
    result = make_result(
        provider="comfyui",
        model_id="comfyui",
        file_path="/tmp/aio.jpg",
        meta={"file_paths": ["/tmp/aio.jpg"], "comfyui_remotes": ["/workspace/aio.png"]},
    )
    prov = FakeComfyuiProvider(outcomes=[result])
    reg = make_registry(comfyui=prov)
    uc, _ = _uc(sessions, reg, variables_repo)

    events = await _collect(
        uc,
        user_id=USER_ID,
        count=1,
        strategy=RandomComboStrategy(variables_repo),
        source_image=b"jpg-bytes",
    )

    results = [ev for ev in events if isinstance(ev, ItemResult)]
    assert len(results) == 1
    prompt = results[0].prompt
    assert prompt.startswith("she is ")
    assert "same person, same room" in prompt
    assert "make the subjects skin details more prominent and natural" in prompt
    assert "{pose}" not in prompt  # NO es el template crudo del draw
    request, _ = prov.calls[0]
    assert request.prompt == prompt
