"""Batch unificado de variables — `/variables` (random) y `/var` (fijo) (item 4).

Reproduce el orquestador de grok (bot.py 2256-2468 random, 2691-2830 var,
2145-2253 multipose) como eventos tipados. Reusa ``GenerateImageUseCase`` por
ítem (D3) — la policy de retry/exhausted vive en UN solo lugar. Estrategias
puras (RNG inyectable), ramas multipose/qwen_aio (D9) y cancelación
cooperativa vía JobManager (D6). No persiste ``generation_refs`` (item 5).
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

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
from grokbot.domain.job import Job
from grokbot.domain.user_config import UserConfig, is_comfy_video_model
from grokbot.domain.variables import (
    MAX_COMBO_ATTEMPTS,
    MULTIPOSE_BATCH_SIZE,
    PromptTemplate,
    build_shuffled_prompt,
    combo_key,
    combo_label,
    list_for_placeholder,
)
from grokbot.providers.base import ProviderError
from grokbot.providers.registry import ProviderRegistry
from grokbot.repositories.base import SessionRepository, VariablesRepository

_MULTIPOSE_PHOTO_MSG = (
    "El modo Multi-pose necesita una foto de entrada: "
    "envía la foto con /variables (o responde a una foto)."
)
_COMFY_VIDEO_NOT_IMAGE_MSG = "El modelo ComfyUI configurado genera video, no imágenes."


# --- PromptStrategy (SPEC §5.4) ----------------------------------------------
@dataclass(frozen=True)
class DrawnCombo:
    """Combinación dibujada por una estrategia: prompt renderizado + metadata."""

    prompt: str  # template renderizado
    values: dict[str, str]  # {placeholder: value}
    key: tuple[str, ...]  # domain.combo_key(template, values)
    label: str  # domain.combo_label(values)


class PromptStrategy(Protocol):
    """Estrategia de dibujo de prompts para el batch."""

    style: str  # "variables" (random) | "var" (fixed)

    def draw(self, *, exclude: set[tuple[str, ...]]) -> DrawnCombo | None: ...


class RandomComboStrategy:
    """Estrategia random de `/variables` (espejo variables_store.random_combination).

    Cada ``draw`` relee listas/template/blacklist frescos del repo y evita
    ``exclude`` ∪ blacklist. Devuelve None solo cuando ningún campo del template
    tiene lista usable.
    """

    style = "variables"

    def __init__(self, variables: VariablesRepository, *, rng: random.Random | None = None) -> None:
        self._variables = variables
        self._rng = rng if rng is not None else random.Random()

    def draw(self, *, exclude: set[tuple[str, ...]]) -> DrawnCombo | None:
        lists = self._variables.get_lists()
        usable = {name: items for name, items in lists.items() if items}
        if not usable:
            return None
        template = self._variables.get_template()
        pt = PromptTemplate(template)
        placeholders = [] if pt.has_format_expr() else pt.fields()
        blacklist = self._variables.get_blacklist()
        exclude = exclude or set()

        if placeholders:
            field_map: dict[str, str] = {}
            for placeholder in placeholders:
                resolved = list_for_placeholder(placeholder, usable)
                if resolved is not None:
                    field_map[placeholder] = resolved
            if not field_map:
                return None
            order = list(field_map)

            def _draw() -> dict[str, str]:
                return {p: self._rng.choice(usable[field_map[p]]) for p in order}

        else:
            order = list(usable)

            def _draw() -> dict[str, str]:
                return {name: self._rng.choice(usable[name]) for name in order}

        for _ in range(MAX_COMBO_ATTEMPTS):
            values = _draw()
            key = combo_key(template, values)
            if key not in exclude and key not in blacklist:
                break
        else:
            values = _draw()

        prompt = pt.render(values) if placeholders else ", ".join(values.values())
        return DrawnCombo(
            prompt=prompt,
            values=values,
            key=combo_key(template, values),
            label=combo_label(values),
        )


class FixedPromptStrategy:
    """Estrategia fija de `/var`: siempre el mismo prompt (paridad grok 2716)."""

    style = "var"

    def __init__(self, prompt: str) -> None:
        self._prompt = prompt

    def draw(self, *, exclude: set[tuple[str, ...]]) -> DrawnCombo | None:
        return DrawnCombo(prompt=self._prompt, values={}, key=(), label=self._prompt)


# --- Helpers puros -----------------------------------------------------------

def _batch_model_reject(cfg: UserConfig, registry: ProviderRegistry) -> str | None:
    """Razón de rechazo del batch, o None si el modelo puede correr imágenes.

    Espejo de grok ``_variables_model_or_reject`` (2083-2108): los modelos de
    video no aplican. El error de resolución se traduce a su mensaje user-safe.
    """
    try:
        registry.resolve_image(cfg)
    except ProviderError as err:
        return str(err.user_message)
    if cfg.model == "comfyui" and is_comfy_video_model(cfg.comfyui.model):
        return _COMFY_VIDEO_NOT_IMAGE_MSG
    return None


def _qwen_aio_prompt(draw: DrawnCombo) -> str:
    """Instrucción directa de Qwen AIO (grok bot.py 2355-2364).

    Qwen-Edit responde a verbos de edición + fidelidad; el skin prompt activa la
    LoRA qwen-edit-skin. Sin ángulos extremos (cámara fija).
    """
    pose = draw.values.get("pose", "")
    angle = draw.values.get("angle", "")
    action = draw.values.get("action", "")
    extra = f", {action}" if action else ""
    return (
        f"she is {pose}, {angle}{extra}, same person, same room, "
        "make the subjects skin details more prominent and natural"
    )


class RunVariableBatchUseCase:
    """Batch unificado random/fijo con ramas multipose/qwen_aio y cancelación."""

    def __init__(
        self,
        *,
        sessions: SessionRepository,
        registry: ProviderRegistry,
        variables: VariablesRepository,
        job_manager: JobManager,
        generate_image: GenerateImageUseCase,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._variables = variables
        self._job_manager = job_manager
        self._generate_image = generate_image

    # -- orquestador -------------------------------------------------------
    async def run(
        self,
        *,
        user_id: int,
        count: int,
        strategy: PromptStrategy,
        source_image: bytes | None = None,
        source_file_id: str | None = None,
        mode: str = "edit",
    ) -> AsyncIterator[BatchStarted | ItemStarted | RetryScheduled | ItemResult | ItemFailed | BatchCancelled | BatchSummary | EmptyList | BatchRejected | JobsFull]:
        cfg = self._sessions.get_config(user_id)

        # 1. Rechazo temprano de modelos que no generan imágenes.
        reject = _batch_model_reject(cfg, self._registry)
        if reject:
            yield BatchRejected(reason=reject)
            return

        use_comfyui = cfg.model == "comfyui"
        multipose = use_comfyui and cfg.comfyui.model == "qwen" and cfg.comfyui.lora == "multipose_batch"
        use_qwen_aio = use_comfyui and cfg.comfyui.model == "qwen_aio"

        # 2. Rama multipose: batch de 5 poses en UN round-trip al box.
        if multipose:
            async for ev in self._run_multipose(
                user_id=user_id,
                source_image=source_image,
                source_file_id=source_file_id,
                mode=mode,
            ):
                yield ev
            return

        # 3. Pre-check de listas vacías SOLO random (paridad 2301-2310).
        if strategy.style == "variables":
            first_empty = next(
                (name for name, items in self._variables.get_lists().items() if not items),
                None,
            )
            if first_empty is not None:
                yield EmptyList(name=first_empty)
                return

        # 4. Registrar job (o JobsFull).
        job = self._job_manager.start(user_id, kind=strategy.style)
        if job is None:
            yield JobsFull(
                active=self._job_manager.active_count(user_id),
                max_active=self._job_manager.max_active,
            )
            return

        completed = 0
        failed = 0
        try:
            yield BatchStarted(style=strategy.style, total=count)
            used: set[tuple[str, ...]] = set()
            for i in range(1, count + 1):
                if self._job_manager.is_cancelled(job):
                    yield BatchCancelled(completed=completed, failed=failed, total=count)
                    return

                if strategy.style == "variables":
                    draw = strategy.draw(exclude=used)
                    if draw is None:
                        yield EmptyList(name="")
                        return
                    used.add(draw.key)
                    prompt = _qwen_aio_prompt(draw) if use_qwen_aio else draw.prompt
                else:
                    draw = strategy.draw(exclude=set())
                    prompt = draw.prompt

                yield ItemStarted(index=i, total=count)
                async for ev in self._drive_item(
                    job=job,
                    i=i,
                    count=count,
                    prompt=prompt,
                    draw=draw,
                    source_image=source_image,
                    source_file_id=source_file_id,
                    allow_shuffle=(strategy.style == "variables"),
                ):
                    # Terminal del item en vuelo. Paridad grok 2387-2399 / 2736-2741:
                    # si el usuario canceló mientras este item generaba (o durante el
                    # shuffle-retry), se SUPRIME el terminal (no se envía media/error,
                    # no se cuenta) y se emite BatchCancelled — incluso si es el último
                    # item (nunca BatchSummary con un item en vuelo cancelado).
                    if isinstance(ev, (ItemResult, ItemFailed)):
                        if self._job_manager.is_cancelled(job):
                            yield BatchCancelled(completed=completed, failed=failed, total=count)
                            return
                        if isinstance(ev, ItemFailed) and ev.exhausted and strategy.style == "variables":
                            self._variables.blacklist_add(draw.key)
                            failed += 1
                        elif isinstance(ev, ItemResult):
                            completed += 1
                        elif isinstance(ev, ItemFailed):
                            failed += 1
                    yield ev
            yield BatchSummary(completed=completed, failed=failed, total=count)
        finally:
            self._job_manager.finish(job.user_id, job.job_id)

    # -- driver por ítem -----------------------------------------------------
    async def _drive_item(
        self,
        *,
        job: Job,
        i: int,
        count: int,
        prompt: str,
        draw: DrawnCombo,
        source_image: bytes | None,
        source_file_id: str | None,
        allow_shuffle: bool,
    ) -> AsyncIterator[RetryScheduled | ItemResult | ItemFailed]:
        """Corre el inner GenerateImageUseCase, relaya su progreso y decide el shuffle-retry.

        Paridad grok 2386-2411: en random, un ``ItemFailed(exhausted=True)`` de
        la primera corrida se SUPRIME y se reintenta con el prompt derangement;
        el terminal que se propaga es el de la segunda corrida. El caller hace
        ``blacklist_add`` si esa segunda también agota. La cancel se chequea en
        el caller al recibir el terminal, y ACÁ antes del shuffle (grok 2389-2391:
        un cancel tras el primer generate aborta el item sin reintentar).
        """
        shuffled = False
        async for ev in self._generate_image.run(
            user_id=job.user_id,
            prompt=prompt,
            source_image=source_image,
            source_file_id=source_file_id,
            index=i,
            total=count,
        ):
            if isinstance(ev, ItemResult):
                yield ev
                return
            if isinstance(ev, ItemFailed) and ev.exhausted and allow_shuffle and not shuffled:
                # Cancel tras la primera corrida → se aborta el shuffle: se
                # propaga el terminal exhausted para que el caller (que ve el
                # cancel seteado) emita BatchCancelled sin blacklistear ni contar.
                if self._job_manager.is_cancelled(job):
                    yield ev
                    return
                shuffled = True
                shuffled_prompt = build_shuffled_prompt(self._variables.get_template(), draw.values)
                async for ev2 in self._generate_image.run(
                    user_id=job.user_id,
                    prompt=shuffled_prompt,
                    source_image=source_image,
                    source_file_id=source_file_id,
                    index=i,
                    total=count,
                ):
                    yield ev2
                return
            # ItemFailed terminal (o fixed-exhausted) y RetryScheduled se relayean.
            yield ev
            if isinstance(ev, ItemFailed):
                return

    # -- multipose (single-shot) ---------------------------------------------
    async def _run_multipose(
        self,
        *,
        user_id: int,
        source_image: bytes | None,
        source_file_id: str | None,
        mode: str,
    ) -> AsyncIterator[BatchStarted | BatchCancelled | BatchSummary | ItemResult | ItemFailed | EmptyList | JobsFull]:
        """Reproduce grok ``_run_multipose_batch`` (2145-2253) como eventos.

        Requiere foto de entrada y listas no vacías (paridad grok: precondiciones
        ANTES de registrar el job); corre UNA generación con ``prompts`` = 5 ramas
        ``<sks> ...`` y resume las poses usadas.
        """
        if mode != "edit" or source_image is None:
            yield ItemFailed(reason=_MULTIPOSE_PHOTO_MSG, prompt="", terminal=True)
            return

        # Paridad grok 2165-2174: lista vacía aborta antes de registrar job.
        first_empty = next(
            (name for name, items in self._variables.get_lists().items() if not items),
            None,
        )
        if first_empty is not None:
            yield EmptyList(name=first_empty)
            return

        job = self._job_manager.start(user_id, "variables")
        if job is None:
            yield JobsFull(
                active=self._job_manager.active_count(user_id),
                max_active=self._job_manager.max_active,
            )
            return

        try:
            yield BatchStarted(style="multipose", total=MULTIPOSE_BATCH_SIZE)
            strategy = RandomComboStrategy(self._variables)
            used: set[tuple[str, ...]] = set()
            combos: list[DrawnCombo] = []
            for _ in range(MULTIPOSE_BATCH_SIZE):
                draw = strategy.draw(exclude=used)
                if draw is None:
                    yield EmptyList(name="")
                    return
                used.add(draw.key)
                combos.append(draw)

            labels = tuple(draw.label for draw in combos)
            rama_prompts = [f"<sks> {draw.prompt}" for draw in combos]
            prompt = rama_prompts[0]

            ok_result: ItemResult | None = None
            async for ev in self._generate_image.run(
                user_id=user_id,
                prompt=prompt,
                prompts=rama_prompts,
                source_image=source_image,
                source_file_id=source_file_id,
            ):
                if self._job_manager.is_cancelled(job):
                    yield BatchCancelled(completed=0, failed=0, total=1)
                    return
                if isinstance(ev, ItemResult):
                    ok_result = ev
                else:
                    yield ev
                    if isinstance(ev, ItemFailed):
                        return

            if self._job_manager.is_cancelled(job):
                yield BatchCancelled(completed=0, failed=0, total=1)
                return
            if ok_result is not None:
                yield ItemResult(
                    result=ok_result.result,
                    prompt=ok_result.prompt,
                    combos=labels,
                    regen_context=ok_result.regen_context,
                )
                yield BatchSummary(completed=1, failed=0, total=1, combos=labels)
        finally:
            self._job_manager.finish(job.user_id, job.job_id)
