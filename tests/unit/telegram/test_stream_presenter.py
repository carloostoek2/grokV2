"""Tests de presenters de la capa telegram (item 5, R2/R3/R5 + O3/O4/M1/A4).

Verifica la traducción de un stream de eventos a UN status message por flujo:

* ``present_single_image`` — header, retries ``(intento N/M)``, ítem fallido
  terminal, cancel de job suprime media (A4), y ítem refinable → refine 2-stage.
* ``present_video`` — ``ItemFailed`` con ``terminal=False`` finaliza igual (O3).
* ``present_batch`` — golden variables (header ``0/N``, items, resumen), ítem
  fallido que no corta el batch, retries, ``BatchCancelled``, terminales
  early (JobsFull/EmptyList/BatchRejected), y multipose (O4: álbum + resumen de
  poses desde ``combos``).

Copy byte a byte contra los strings de grok; prompts/ids anonimizados (R8).
0 red, 0 ``unittest.mock``: los fakes del conftest implementan los Protocols.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from conftest import (
    CHAT_ID,
    USER_ID,
    DUMMY_PROMPT,
    FakeComfyuiProvider,
    FakeTelegramGateway,
    flat_callback_data,
    make_result,
)
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
from grokbot.application.job_manager import JobManager
from grokbot.application.refine_flow import RefineDecision, ResolveRefineUseCase
from grokbot.domain.generation import GenerationRequest, MediaType
from grokbot.domain.user_config import ComfyUIConfig, UserConfig
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.formatters import JOBS_FULL_MSG
from grokbot.telegram.sender import ResultSender
from grokbot.telegram.stream_presenter import (
    present_batch,
    present_single_image,
    present_video,
)

NAME = "Grok Imagine (xAI • v2)"
MODEL = {"name": NAME}
URL = "https://example.invalid/img/out.png"
VIDEO_URL = "https://example.invalid/video/clip.mp4"
PROMPT = DUMMY_PROMPT
POSES = ("de pie", "sentada", "perfil", "mirando", "corriendo")

# Copy de presenters (no corregir typos originales de grok).
_VAR_HEADER = "🎲 <b>Variables</b>: {verb} {i}/{n} imágenes con {name}..."
_EMPTY_LIST_VAR = (
    "La lista de <b>Poses</b> está vacía.\n"
    "Usa <b>/listas</b> para añadir opciones antes de usar /variables."
)


async def _stream(*events: object):
    for ev in events:
        yield ev


def _url_item(
    *,
    index: int | None = None,
    total: int | None = None,
    prompt: str = PROMPT,
    elapsed: int = 12,
) -> ItemResult:
    result = make_result(
        provider="xai",
        model_id="fake-image-model",
        remote_url=URL,
        meta={"urls": [URL], "elapsed_sec": elapsed},
    )
    return ItemResult(result=result, prompt=prompt, index=index, total=total)


def _video_item(*, prompt: str = PROMPT) -> ItemResult:
    result = make_result(
        provider="xai",
        model_id="fake-video",
        media_type=MediaType.VIDEO,
        remote_url=VIDEO_URL,
        meta={"urls": [VIDEO_URL], "elapsed_sec": 30},
    )
    return ItemResult(result=result, prompt=prompt)


def _sender(gateway, downloader, refs_repo) -> ResultSender:
    return ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)


def _ui(gateway) -> ChatUI:
    return ChatUI(gateway, CHAT_ID)


def _comfy_local_item(tmp_path) -> ItemResult:
    p = tmp_path / "base.png"
    p.write_bytes(b"base")
    request = GenerationRequest(
        provider="comfyui",
        model_id="krea2",
        media_type=MediaType.IMAGE,
        prompt=PROMPT,
        params={"model": "krea2", "lora": "none", "refine": "1"},
    )
    result = make_result(
        provider="comfyui",
        model_id="krea2",
        media_type=MediaType.IMAGE,
        file_path=str(p),
        meta={
            "file_paths": [str(p)],
            "comfyui_remotes": ["/workspace/base.png"],
            "elapsed_sec": 20,
        },
    )
    return ItemResult(result=result, prompt=PROMPT, request=request)


def _local_album(tmp_path, *, n: int = 5) -> ItemResult:
    paths, remotes = [], []
    for i in range(n):
        p = tmp_path / f"pose_{i}.png"
        p.write_bytes(b"pose")
        paths.append(str(p))
        remotes.append(f"/workspace/pose_{i}.png")
    result = make_result(
        provider="comfyui",
        model_id="krea2",
        media_type=MediaType.IMAGE,
        file_path=paths[0],
        meta={"file_paths": paths, "comfyui_remotes": remotes, "elapsed_sec": 45},
    )
    return ItemResult(
        result=result,
        prompt=PROMPT,
        combos=POSES,
        request=GenerationRequest(
            provider="comfyui",
            model_id="krea2",
            media_type=MediaType.IMAGE,
            prompt=PROMPT,
            params={"model": "krea2", "lora": "multipose_batch"},
        ),
    )


class _AutoYesRefineUseCase(ResolveRefineUseCase):
    """Refine use case que resuelve SIEMPRE ``yes`` al registrarse un pending."""

    def register(self, *, user_id, message_id=None, job_id=None) -> str:
        token = super().register(user_id=user_id, message_id=message_id, job_id=job_id)

        async def _decide() -> None:
            await asyncio.sleep(0)
            self.decide(token, user_id, "yes")

        asyncio.create_task(_decide())
        return token


class _CancelDuringRefineProvider(FakeComfyuiProvider):
    """Refine que setea el cancel del job justo cuando corre el refine (M1).

    Reproduce el "cancel del job durante ``refine_uc.refine``" (después de que el
    usuario confirmó Refinar): el hook del JobManager no aborta el provider en
    vuelo, así que el refine SÍ completa; la supresión de la refinada depende del
    ``cancel_event`` que el presenter pasa a ``run_refine_flow``.
    """

    def __init__(self, *, on_refine, **kwargs) -> None:
        super().__init__(**kwargs)
        self._on_refine = on_refine

    async def refine(self, request, remote_paths):
        self._on_refine()
        return await super().refine(request, remote_paths)


def _refine_cfg() -> UserConfig:
    return replace(
        UserConfig.defaults(),
        comfyui=ComfyUIConfig(model="krea2", lora="none", refine="1"),
    )


# --------------------------------------------------------------------------- #
# present_single_image (R2)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_single_golden_retry_then_result_deletes_status(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        RetryScheduled(attempt=1, max_attempts=3, provider="xai"),
        _url_item(),
    )

    await present_single_image(ui, events, label="Generando imagen...", sender=sender)

    # status inicial creado.
    sent_status = gateway.calls_by_method("send_message")
    assert sent_status[0]["text"] == "Generando imagen..."
    # retry pintado.
    edits = gateway.calls_by_method("edit_message_text")
    assert any(c["text"] == "Generando imagen... (intento 1/3)" for c in edits)
    # resultado enviado y status borrado (delete_status default True).
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1
    assert photos[0]["caption"] == "<b>Prompt:</b> 12s"
    deleted = [c["message_id"] for c in gateway.calls_by_method("delete_message")]
    assert sent_status[0]["sent"].message_id in deleted


@pytest.mark.asyncio
async def test_single_item_failed_edits_status_and_returns(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(ItemFailed(reason="El proveedor rechazó el prompt", prompt=PROMPT, terminal=True))

    await present_single_image(ui, events, label="Generando imagen...", sender=sender)

    assert gateway.calls_by_method("send_photo") == []
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "El proveedor rechazó el prompt"
    assert edits[-1]["reply_markup"] is None


@pytest.mark.asyncio
async def test_single_cancel_suppresses_media(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    jm = JobManager()
    job = jm.start(USER_ID, "edit")
    assert job is not None
    jm.cancel(USER_ID, job.job_id)

    await present_single_image(
        ui, _stream(_url_item()), label="Generando imagen...",
        sender=sender, user_id=USER_ID, job=job, job_manager=jm,
    )

    # A4: la cancelación del job suprime el media y edita el status.
    assert gateway.calls_by_method("send_photo") == []
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "⏹ Edición cancelada."
    assert edits[-1]["reply_markup"] is None
    # el status inicial llevaba el botón de cancelar del job real.
    sent_status = gateway.calls_by_method("send_message")
    assert flat_callback_data(sent_status[0]["reply_markup"]) == [f"cancel_job:{job.job_id}"]


@pytest.mark.asyncio
async def test_single_refinable_runs_refine_flow_and_yes_sends_refined(
    tmp_path, gateway, downloader, refs_repo
):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    refined_p = tmp_path / "refined.png"
    refined_p.write_bytes(b"ref")
    provider = FakeComfyuiProvider(
        refine_outcomes=[
            make_result(
                provider="comfyui",
                model_id="krea2",
                media_type=MediaType.IMAGE,
                file_path=str(refined_p),
                meta={
                    "file_paths": [str(refined_p)],
                    "comfyui_remotes": ["/workspace/refined.png"],
                    "elapsed_sec": 25,
                },
            )
        ]
    )
    refine_uc = _AutoYesRefineUseCase(provider=provider, timeout=10.0)

    await present_single_image(
        ui, _stream(_comfy_local_item(tmp_path)), label="Generando imagen...",
        sender=sender, user_id=USER_ID, cfg=_refine_cfg(), refine_uc=refine_uc,
    )

    assert provider.refine_calls[0][1] == ["/workspace/base.png"]
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 2  # base + refinada
    assert photos[0]["photo"] == b"base"
    assert photos[1]["photo"] == b"ref"
    # la base llevaba el keyboard de confirm de refine.
    assert len(flat_callback_data(photos[0]["reply_markup"])) == 2
    assert flat_callback_data(photos[0]["reply_markup"])[0].startswith("refine:")
    # base borrada tras entregar la refinada.
    deleted = [c["message_id"] for c in gateway.calls_by_method("delete_message")]
    assert photos[0]["sent"].message_id in deleted


# --------------------------------------------------------------------------- #
# present_video (R3 + O3)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_video_golden_status_and_send(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)

    await present_video(
        ui, _stream(_video_item()),
        model_id="grok-imagine-video", prompt="un perro corriendo", sender=sender,
    )

    sent_status = gateway.calls_by_method("send_message")
    assert sent_status[0]["text"] == (
        "Generando video con <b>grok-imagine-video</b>...\n\n"
        "<i>un perro corriendo</i>"
    )
    videos = gateway.calls_by_method("send_video")
    assert len(videos) == 1
    deleted = [c["message_id"] for c in gateway.calls_by_method("delete_message")]
    assert sent_status[0]["sent"].message_id in deleted


@pytest.mark.asyncio
async def test_video_item_failed_terminal_false_finalizes(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)

    await present_video(
        ui, _stream(ItemFailed(reason="Se agotaron los intentos", prompt=PROMPT, terminal=False)),
        model_id="grok-imagine-video", prompt=PROMPT, sender=sender,
    )

    assert gateway.calls_by_method("send_video") == []
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "Se agotaron los intentos"
    assert edits[-1]["reply_markup"] is None


# --------------------------------------------------------------------------- #
# present_batch (R5): variables golden
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_batch_variables_golden(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        BatchStarted(style="variables", total=2, job_id="job1"),
        ItemStarted(index=1, total=2),
        _url_item(index=1, total=2),
        ItemStarted(index=2, total=2),
        _url_item(index=2, total=2),
        BatchSummary(completed=2, failed=0, total=2),
    )

    await present_batch(
        ui, events, verb="Generando", count=2, model=MODEL, sender=sender,
    )

    texts = [c["text"] for c in gateway.calls_by_method("send_message")]
    assert texts[0] == _VAR_HEADER.format(verb="Generando", i=0, n=2, name=NAME)
    # header con botón de cancelar del job del batch.
    assert flat_callback_data(
        gateway.calls_by_method("send_message")[0]["reply_markup"]
    ) == ["cancel_job:job1"]

    edits = [c["text"] for c in gateway.calls_by_method("edit_message_text")]
    assert edits[0] == _VAR_HEADER.format(verb="Generando", i=1, n=2, name=NAME)
    assert edits[1] == _VAR_HEADER.format(verb="Generando", i=2, n=2, name=NAME)
    assert edits[-1] == "✅ Listo: 2/2 imágenes generadas."
    # cada ítem se envió con delete_status=False (no se borra el status).
    assert len(gateway.calls_by_method("send_photo")) == 2
    assert gateway.calls_by_method("delete_message") == []
    # caption de modelo (modelo/tiempo + prompt truncado).
    caption = gateway.calls_by_method("send_photo")[0]["caption"]
    assert caption.startswith("<b>Modelo:</b> Grok Imagine (xAI • v2)\n<b>Tiempo:</b> 12s")
    assert caption.endswith("<b>Prompt:</b> retrato de una persona de pie")


@pytest.mark.asyncio
async def test_batch_item_failed_continues_with_notify(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        BatchStarted(style="variables", total=2, job_id="job1"),
        ItemStarted(index=1, total=2),
        ItemFailed(reason="El proveedor falló", prompt=PROMPT, index=1, total=2),
        ItemStarted(index=2, total=2),
        _url_item(index=2, total=2),
        BatchSummary(completed=1, failed=1, total=2),
    )

    await present_batch(
        ui, events, verb="Generando", count=2, model=MODEL, sender=sender,
    )

    # notify aparte del ítem fallido con el prompt intentado.
    notify = [c for c in gateway.calls_by_method("send_message") if "falló" in c["text"]]
    assert len(notify) == 1
    assert notify[0]["text"] == (
        f"Generación 1/2 falló con el siguiente prompt:\n\n{PROMPT}"
    )
    # el batch siguió y el resumen refleja el ítem fallido.
    edits = [c["text"] for c in gateway.calls_by_method("edit_message_text")]
    assert edits[-1] == "✅ Listo: 1/2 imágenes generadas (1 error)."
    assert len(gateway.calls_by_method("send_photo")) == 1


@pytest.mark.asyncio
async def test_batch_retry_scheduled_marks_intento(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        BatchStarted(style="variables", total=1, job_id="job1"),
        ItemStarted(index=1, total=1),
        RetryScheduled(attempt=1, max_attempts=2, provider="xai", index=1, total=1),
        _url_item(index=1, total=1),
        BatchSummary(completed=1, failed=0, total=1),
    )

    await present_batch(
        ui, events, verb="Generando", count=1, model=MODEL, sender=sender,
    )

    edits = [c["text"] for c in gateway.calls_by_method("edit_message_text")]
    retry = [t for t in edits if "(intento 1/2)" in t]
    assert len(retry) == 1
    assert retry[0] == _VAR_HEADER.format(verb="Generando", i=1, n=1, name=NAME) + " (intento 1/2)"


@pytest.mark.asyncio
async def test_batch_cancelled_reflected(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        BatchStarted(style="variables", total=2, job_id="job1"),
        ItemStarted(index=1, total=2),
        BatchCancelled(completed=1, failed=0, total=2),
    )

    await present_batch(
        ui, events, verb="Generando", count=2, model=MODEL, sender=sender,
    )

    # M1: el presenter solo refleja el BatchCancelled (el app ya suprimió items).
    edits = [c["text"] for c in gateway.calls_by_method("edit_message_text")]
    assert edits[-1] == "⏹ Cancelado. Completadas 1/2 imágenes."
    assert edits[-1].endswith("imágenes.")  # copy exacta sin fuga de ids
    assert gateway.calls_by_method("send_photo") == []


@pytest.mark.asyncio
async def test_batch_refine_cancel_during_refine_suppresses_refined(
    tmp_path, gateway, downloader, refs_repo
):
    """M1: un cancel del job DURANTE el refine de un batch (post-yes) no entrega la refinada.

    El refine llega a completarse (el cancel no aborta el provider en vuelo),
    pero el ``cancel_event`` del job que ahora pasa ``present_batch`` hace que
    ``run_refine_flow`` descarte la refinada y resuelva ``cancelled``; el batch
    sigue y el use case emite ``BatchCancelled``.
    """
    jm = JobManager()
    job = jm.start(USER_ID, "variables")
    assert job is not None
    refined_p = tmp_path / "refined.png"
    refined_p.write_bytes(b"ref")
    provider = _CancelDuringRefineProvider(
        on_refine=lambda: jm.cancel(USER_ID, job.job_id),
        refine_outcomes=[
            make_result(
                provider="comfyui",
                model_id="krea2",
                media_type=MediaType.IMAGE,
                file_path=str(refined_p),
                meta={
                    "file_paths": [str(refined_p)],
                    "comfyui_remotes": ["/workspace/refined.png"],
                    "elapsed_sec": 25,
                },
            )
        ],
    )
    refine_uc = _AutoYesRefineUseCase(provider=provider, timeout=10.0)
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        BatchStarted(style="variables", total=1, job_id=job.job_id),
        ItemStarted(index=1, total=1),
        _comfy_local_item(tmp_path),
        BatchCancelled(completed=1, failed=0, total=1),
    )

    await present_batch(
        ui, events, verb="generando", count=1, model=MODEL, sender=sender,
        refine_uc=refine_uc, cfg=_refine_cfg(), user_id=USER_ID,
        job_manager=jm,
    )

    # la refinada NO se entregó: solo la base se envió como foto.
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1
    assert photos[0]["photo"] == b"base"
    # el refine sí corrió (el cancel fue post-yes, durante el refine en vuelo).
    assert len(provider.refine_calls) == 1
    # el status refleja el BatchCancelled que emite el use case al reanudar.
    edits = [c["text"] for c in gateway.calls_by_method("edit_message_text")]
    assert edits[-1] == "⏹ Cancelado. Completadas 1/1 imágenes."


# --------------------------------------------------------------------------- #
# present_batch: terminales early (sin status todavía)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_batch_jobs_full_sends_message(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    await present_batch(
        ui, _stream(JobsFull(active=3, max_active=3)),
        verb="Generando", count=2, model=MODEL, sender=sender,
    )
    texts = [c["text"] for c in gateway.calls_by_method("send_message")]
    assert texts[-1] == JOBS_FULL_MSG
    assert gateway.calls_by_method("edit_message_text") == []


@pytest.mark.asyncio
async def test_batch_empty_list_early_sends_message(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    await present_batch(
        ui, _stream(EmptyList(name="poses")),
        verb="Generando", count=2, model=MODEL, sender=sender,
    )
    texts = [c["text"] for c in gateway.calls_by_method("send_message")]
    assert texts[-1] == _EMPTY_LIST_VAR


@pytest.mark.asyncio
async def test_batch_empty_list_mid_edits_status(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        BatchStarted(style="variables", total=2, job_id="job1"),
        EmptyList(name="poses"),
    )
    await present_batch(
        ui, events, verb="Generando", count=2, model=MODEL, sender=sender,
    )
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == _EMPTY_LIST_VAR
    assert edits[-1]["reply_markup"] is None


@pytest.mark.asyncio
async def test_batch_rejected_early_sends_reason(gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    reason = "El modelo seleccionado no genera video de variables."
    await present_batch(
        ui, _stream(BatchRejected(reason=reason)),
        verb="Generando", count=2, model=MODEL, sender=sender,
    )
    texts = [c["text"] for c in gateway.calls_by_method("send_message")]
    assert texts[-1] == reason


# --------------------------------------------------------------------------- #
# present_batch: multipose (O4)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_batch_multipose_album_then_poses_summary(tmp_path, gateway, downloader, refs_repo):
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        BatchStarted(style="multipose", total=5, provider="comfyui", model_id="krea2", job_id="mpjob"),
        _local_album(tmp_path),
        BatchSummary(completed=5, failed=0, total=5, combos=POSES),
    )

    await present_batch(
        ui, events, verb="Generando", count=5, model=MODEL, sender=sender,
        style="multipose",
    )

    # header multipose sin índice.
    texts = [c["text"] for c in gateway.calls_by_method("send_message")]
    assert texts[0] == f"🎲 <b>Multi-pose ×5</b>: generando 5 poses con {NAME}..."
    # álbum local enviado.
    groups = gateway.calls_by_method("send_media_group")
    assert len(groups) == 1
    assert len(groups[0]["media"]) == 5
    # resumen de poses en mensaje aparte tras borrar el status.
    assert texts[-1] == (
        "<b>🎲 Multi-pose ×5</b> — poses usadas:\n"
        "  1. de pie\n  2. sentada\n  3. perfil\n  4. mirando\n  5. corriendo"
    )
    status_id = gateway.calls_by_method("send_message")[0]["sent"].message_id
    assert status_id in [c["message_id"] for c in gateway.calls_by_method("delete_message")]


@pytest.mark.asyncio
async def test_batch_multipose_style_comes_from_event_not_handler_param(
    gateway, downloader, refs_repo
):
    """C1: un /variables que deriva a multipose pinta el header multipose.

    El handler pasa ``style="variables"`` (default) pero el ``BatchStarted`` trae
    ``style="multipose"``: el evento manda, no el parámetro.
    """
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        BatchStarted(style="multipose", total=5, job_id="mpjob"),
        BatchCancelled(completed=0, failed=0, total=1),
    )

    # Sin style (el default "variables" del handler del flujo random).
    await present_batch(
        ui, events, verb="generando", count=5, model=MODEL, sender=sender
    )

    texts = [c["text"] for c in gateway.calls_by_method("send_message")]
    assert texts[0] == f"🎲 <b>Multi-pose ×5</b>: generando 5 poses con {NAME}..."
    assert flat_callback_data(
        gateway.calls_by_method("send_message")[0]["reply_markup"]
    ) == ["cancel_job:mpjob"]
    edits = [c["text"] for c in gateway.calls_by_method("edit_message_text")]
    assert edits[-1] == "⏹ Cancelado. Completadas 0/1 imágenes."


@pytest.mark.asyncio
async def test_batch_multipose_item_failed_is_terminal(gateway, downloader, refs_repo):
    """C2: un fallo del ítem en multipose (single-shot) NO deja el status colgado.

    Se edita el header al error user-safe y el flujo termina; sin notify aparte
    ni resumen.
    """
    ui = _ui(gateway)
    sender = _sender(gateway, downloader, refs_repo)
    events = _stream(
        BatchStarted(style="multipose", total=5, job_id="mpjob"),
        ItemFailed(reason="Se agotaron los intentos", prompt=PROMPT),
    )

    await present_batch(
        ui, events, verb="generando", count=5, model=MODEL, sender=sender
    )

    texts = [c["text"] for c in gateway.calls_by_method("send_message")]
    assert len(texts) == 1, "solo el header multipose"
    assert texts[0].startswith("🎲 <b>Multi-pose ×5</b>")
    edit_calls = gateway.calls_by_method("edit_message_text")
    assert len(edit_calls) == 1
    assert edit_calls[-1]["text"] == "Se agotaron los intentos"
    assert edit_calls[-1]["reply_markup"] is None
    # sin notify aparte de ítem fallido ni media.
    assert not any("falló" in t for t in texts)
    assert gateway.calls_by_method("send_photo") == []
