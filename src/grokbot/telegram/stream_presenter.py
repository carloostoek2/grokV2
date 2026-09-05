"""Presenters de la capa telegram: traducen un stream de eventos a UN status msg.

Consumen el ``AsyncIterator`` que devuelven los use cases de ``application``
(generate_image / generate_video / run_variable_batch) y orquestan el status de
cada flujo con paridad de copy de grok:

* :func:`present_single_image` — flujos single (text/reply/edit/regen): crea o
  reusa el status, pinta retries ``(intento N/M)`` re-aplicando el keyboard
  (Telegram lo suelta si ``edit_text`` omite ``reply_markup``), consulta cancel
  del job en cada evento (A4: suprime media si el job se canceló) y delega el
  envío + refine al :class:`ResultSender` / :func:`refine_chat.run_refine_flow`.
* :func:`present_video` — single-attempt sin job; cualquier ``ItemFailed`` (aun
  ``terminal=False``) finaliza (O3). El status queda fijo (sin estados de poll
  del provider) y el terminal lo reemplaza.
* :func:`present_batch` — variables/var/multipose: header ``0/N``, cada
  ``ItemStarted`` pinta ``i/N`` (re-markup), retries idem con intento, items se
  envían con ``delete_status=False``, ítem fallido → notify aparte y el batch
  sigue, y el resumen/cancel reemplaza el status. El estilo efectivo se toma del
  ``BatchStarted`` (C1: un handler ``/variables`` que deriva a multipose pinta el
  header multipose, no "Variables"); en multipose un ``ItemFailed`` es terminal
  del batch (C2: el status se edita al error, sin notify aparte ni header
  colgado). Multipose resume el resumen de poses desde ``combos`` (O4), nunca
  desde ``total``. El ``EmptyList`` del precheck multipose transporta su propio
  ``style`` (C15: se emite antes del ``BatchStarted``, así el target del copy es
  "el modo Multi-pose", no "/variables").

Nunca se loguean IDs/prompts/payloads/file_ids/URLs de contenido (R6/R8). El
``try/finally`` de jobs lo hace el propio use case; acá solo se refleja el evento.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from grokbot.application.events import (
    BatchCancelled,
    BatchRejected,
    BatchStarted,
    BatchSummary,
    EmptyList,
    ItemFailed,
    ItemResult,
    ItemStarted,
    RetryScheduled,
)
from grokbot.domain.generation import MediaType
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.formatters import (
    LIST_LABELS,
    format_failed_item_message,
    format_multipose_summary,
    format_variables_batch_summary,
    retry_status_text,
    video_start_message,
)
from grokbot.telegram.keyboards import cancel_job_keyboard
from grokbot.telegram.refine_chat import run_refine_flow
from grokbot.telegram.sender import ResultSender

if TYPE_CHECKING:
    from grokbot.application.job_manager import JobManager
    from grokbot.application.refine_flow import ResolveRefineUseCase
    from grokbot.domain.job import Job
    from grokbot.domain.user_config import UserConfig

# Copy de lista vacía (grok 2168-2172 multipose / 2304-2308 variables).
_EMPTY_LIST_MSG = "La lista de <b>{label}</b> está vacía.\nUsa <b>/listas</b> para añadir opciones antes de usar {target}."
_EMPTY_DRAW_MSG = "No se pudo construir el prompt: alguna lista está vacía. Usa /listas."


def _batch_title(style: str, total: int) -> str:
    if style == "multipose":
        return f"Multi-pose ×{total}"
    if style == "var":
        return "Var"
    return "Variables"


def _batch_status_label(style: str, verb: str, total: int, index: int, model_label: str) -> str:
    """Label del status batch (header ``0/N`` o item ``i/N``), copy grok."""
    title = _batch_title(style, total)
    if style == "multipose":
        return (
            f"🎲 <b>{title}</b>: generando {total} poses con {model_label}..."
        )
    return f"🎲 <b>{title}</b>: {verb} {index}/{total} imágenes con {model_label}..."


def _batch_item_prefix(style: str, total: int, index: int | None = None) -> str:
    """Prefijo de caption por ítem (multipose usa el título, no índice)."""
    title = _batch_title(style, total)
    if style == "multipose":
        return title
    i = index if index is not None else 1
    return f"{title} {i}/{total}"


def _empty_list_text(name: str, style: str) -> str:
    if not name:
        return _EMPTY_DRAW_MSG
    label = LIST_LABELS.get(name, name)
    target = "el modo Multi-pose" if style == "multipose" else "/variables"
    return _EMPTY_LIST_MSG.format(label=label, target=target)


# --------------------------------------------------------------------------- #
# Single image
# --------------------------------------------------------------------------- #
async def present_single_image(
    ui: ChatUI,
    events: AsyncIterator,
    *,
    label: str,
    sender: ResultSender,
    prefix: str = "Prompt",
    caption_model: dict | None = None,
    caption_prompt: bool = False,
    status_id: int | None = None,
    delete_status: bool = True,
    cancel_text: str = "⏹ Edición cancelada.",
    user_id: int | None = None,
    cfg: "UserConfig | None" = None,
    refine_uc: "ResolveRefineUseCase | None" = None,
    job: "Job | None" = None,
    job_manager: "JobManager | None" = None,
    reply_to: int | None = None,
) -> None:
    """Presenta un stream single de imagen (text/reply/edit/regen).

    Crea el status ``label`` cuando no se reutiliza uno del confirm. Si el ítem
    es refinable (``refine_uc.offer``) corre el refine 2-stage; el generator queda
    suspendido durante la decisión y se reanuda al volver (R1). ``reply_to``
    (message_id del invocador) se propaga al envío de la imagen/refinada.
    """
    if status_id is None:
        sent = await ui.send_text(label, reply_markup=_job_markup(job, job_manager))
        status_id = sent.message_id

    async for ev in events:
        if isinstance(ev, RetryScheduled):
            await ui.edit_text(
                status_id,
                retry_status_text(label, ev.attempt, ev.max_attempts),
                reply_markup=_job_markup(job, job_manager),
            )
            continue
        if isinstance(ev, ItemFailed):
            await ui.edit_text(status_id, ev.reason, reply_markup=None)
            return
        if isinstance(ev, ItemResult):
            if job is not None and job_manager is not None and job_manager.is_cancelled(job):
                await ui.edit_text(status_id, cancel_text, reply_markup=None)
                return
            if (
                refine_uc is not None
                and cfg is not None
                and user_id is not None
                and refine_uc.offer(cfg.comfyui, ev.result)
            ):
                await run_refine_flow(
                    ui,
                    item=ev,
                    sender=sender,
                    refine_uc=refine_uc,
                    prefix=prefix,
                    status_id=status_id,
                    delete_status=delete_status,
                    caption_model=caption_model,
                    caption_prompt=caption_prompt,
                    user_id=user_id,
                    owner_uid=user_id,
                    job_id=job.job_id if job is not None and job_manager is not None else None,
                    cancel_event=(
                        job_manager.cancel_event(job)
                        if job is not None and job_manager is not None
                        else None
                    ),
                    reply_to=reply_to,
                )
                return
            await sender.send_image(
                ui, ev, prefix,
                status_id=status_id,
                delete_status=delete_status,
                caption_model=caption_model,
                caption_prompt=caption_prompt,
                owner_uid=user_id,
                reply_to=reply_to,
            )
            return


# --------------------------------------------------------------------------- #
# Video (single attempt, sin job)
# --------------------------------------------------------------------------- #
async def present_video(
    ui: ChatUI,
    events: AsyncIterator,
    *,
    model_id: str,
    prompt: str,
    sender: ResultSender,
    prefix: str = "Prompt",
    caption_model: dict | None = None,
    status_id: int | None = None,
    delete_status: bool = True,
    user_id: int | None = None,
    reply_to: int | None = None,
) -> None:
    """Presenta un stream single de video (grok_video / ComfyUI video).

    El status arranca con el mensaje de video (o reusa el del confirm). Un
    ``ItemFailed`` con ``terminal=False`` igual finaliza (O3: sin reintentos de
    presentación para video). ``user_id`` (R8) se persiste como owner del ref.
    ``reply_to`` (message_id del invocador) se propaga al envío del video.
    """
    if status_id is None:
        sent = await ui.send_text(video_start_message(model_id, prompt))
        status_id = sent.message_id

    async for ev in events:
        if isinstance(ev, ItemFailed):
            await ui.edit_text(status_id, ev.reason, reply_markup=None)
            return
        if isinstance(ev, ItemResult):
            if ev.result.media_type == MediaType.VIDEO:
                await sender.send_video(
                    ui, ev, prefix,
                    status_id=status_id,
                    delete_status=delete_status,
                    caption_model=caption_model,
                    owner_uid=user_id,
                    reply_to=reply_to,
                )
            else:
                await sender.send_image(
                    ui, ev, prefix,
                    status_id=status_id,
                    delete_status=delete_status,
                    caption_model=caption_model,
                    owner_uid=user_id,
                    reply_to=reply_to,
                )
            return


# --------------------------------------------------------------------------- #
# Batch (variables / var / multipose)
# --------------------------------------------------------------------------- #
def _batch_job(
    job_manager: "JobManager | None",
    user_id: int | None,
    job_id: str | None,
) -> "Job | None":
    """Localiza el Job activo del batch (M1: cancel_event para refine).

    El use case arranca el job dentro de su generator y lo termina en un
    ``finally`` al agotarse el stream; mientras el presenter lo consume (incluso
    durante el refine, donde el generator queda suspendido en un ``yield``) el
    job sigue activo en el ``JobManager``.
    """
    if job_manager is None or user_id is None or job_id is None:
        return None
    for job in job_manager.active_jobs(user_id):
        if job.job_id == job_id:
            return job
    return None


async def present_batch(
    ui: ChatUI,
    events: AsyncIterator,
    *,
    verb: str,
    count: int,
    model: dict,
    sender: ResultSender,
    style: str = "variables",
    fail_label: str = "Generación",
    refine_uc: "ResolveRefineUseCase | None" = None,
    cfg: "UserConfig | None" = None,
    user_id: int | None = None,
    job_manager: "JobManager | None" = None,
    reply_to: int | None = None,
) -> None:
    """Presenta un batch de variables/var/multipose sobre UN status message.

    El status se crea con el header ``0/N`` al llegar ``BatchStarted`` (que trae
    el ``job_id``) y cada ``ItemStarted`` lo edita a ``i/N`` re-aplicando el
    teclado de cancelar. Los ítems se envían con ``delete_status=False``; un ítem
    fallido se notifica aparte y el batch continúa. El terminal (summary/cancel/
    reject/empty_list) reemplaza o cierra el status.

    ``job_manager`` (inyectado por los handlers) se usa SOLO para consultar el
    ``cancel_event`` del job del batch y pasarlo al refine (M1): un cancel durante
    el refine post-yes suprime la refinada en vuelo, igual que en single-image.
    """
    status_id: int | None = None
    job_id: str | None = None
    item_index: int | None = None
    item_total: int | None = None
    total = count

    def _markup() -> object:
        return cancel_job_keyboard(job_id) if job_id else None

    async for ev in events:
        # --- arranque ------------------------------------------------------
        if isinstance(ev, BatchStarted):
            total = ev.total or count
            job_id = ev.job_id
            if ev.style:
                # C1: el estilo lo manda el evento (un /variables puede derivar a
                # multipose); el param del handler es solo el default inicial.
                style = ev.style
            sent = await ui.send_text(
                _batch_status_label(style, verb, total, 0, model["name"]),
                reply_markup=_markup(),
            )
            status_id = sent.message_id
            continue

        # --- early terminal (no hay status todavía) ------------------------
        if status_id is None:
            if isinstance(ev, BatchRejected):
                await ui.send_text(ev.reason)
                return
            if isinstance(ev, EmptyList):
                # C15: el precheck multipose llega ANTES del BatchStarted y trae su
                # propio style; el param del handler aún es el default (p. ej.
                # "variables") así que el evento manda.
                await ui.send_text(_empty_list_text(ev.name, ev.style or style))
                return
            if isinstance(ev, ItemFailed):
                await ui.send_text(ev.reason)
                return

        # --- progreso ------------------------------------------------------
        if isinstance(ev, ItemStarted):
            item_index = ev.index
            item_total = ev.total or total
            await ui.edit_text(
                status_id,
                _batch_status_label(style, verb, item_total or total, item_index, model["name"]),
                reply_markup=_markup(),
            )
            continue

        if isinstance(ev, RetryScheduled):
            i = ev.index if ev.index is not None else (item_index or 0)
            t = ev.total or item_total or total
            base = _batch_status_label(style, verb, t, i, model["name"])
            await ui.edit_text(
                status_id,
                retry_status_text(base, ev.attempt, ev.max_attempts),
                reply_markup=_markup(),
            )
            continue

        if isinstance(ev, ItemResult):
            index = ev.index or item_index or 1
            t = ev.total or item_total or total
            prefix = _batch_item_prefix(style, t, index)
            if (
                refine_uc is not None
                and cfg is not None
                and user_id is not None
                and refine_uc.offer(cfg.comfyui, ev.result)
            ):
                job = _batch_job(job_manager, user_id, job_id)
                await run_refine_flow(
                    ui,
                    item=ev,
                    sender=sender,
                    refine_uc=refine_uc,
                    prefix=prefix,
                    status_id=status_id,
                    delete_status=False,
                    caption_model=model,
                    caption_prompt=True,
                    user_id=user_id,
                    owner_uid=user_id,
                    job_id=job_id,
                    cancel_event=(
                        job_manager.cancel_event(job)
                        if job_manager is not None and job is not None
                        else None
                    ),
                    reply_to=reply_to,
                )
            else:
                await sender.send_image(
                    ui, ev, prefix,
                    status_id=status_id,
                    delete_status=False,
                    caption_model=model,
                    caption_prompt=True,
                    owner_uid=user_id,
                    reply_to=reply_to,
                )
            continue

        if isinstance(ev, ItemFailed):
            if style == "multipose" and status_id is not None:
                # Multipose es single-shot (1 round-trip, N poses): el fallo del
                # ítem ES el terminal del batch (C2). Se edita el status al error
                # y se vuelve; sin notify aparte ni header colgado (grok edita el
                # header a "Error: ...").
                await ui.edit_text(status_id, ev.reason, reply_markup=None)
                return
            # ítem fallido → notify aparte y el batch sigue (grok 2119-2135).
            i = ev.index or item_index or 0
            t = ev.total or item_total or total
            await ui.send_text(
                format_failed_item_message(fail_label, i, t, ev.prompt)
            )
            continue

        # --- terminales ----------------------------------------------------
        if isinstance(ev, BatchSummary):
            if ev.combos is not None:
                # Multipose (O4): resumen de poses usadas en mensaje aparte.
                if status_id is not None:
                    await ui.delete(status_id)
                await ui.send_text(format_multipose_summary(ev.combos))
            else:
                await ui.edit_text(
                    status_id,
                    format_variables_batch_summary(ev.completed, ev.failed, ev.total),
                    reply_markup=None,
                )
            return

        if isinstance(ev, BatchCancelled):
            await ui.edit_text(
                status_id,
                f"⏹ Cancelado. Completadas {ev.completed}/{ev.total} imágenes.",
                reply_markup=None,
            )
            return

        if isinstance(ev, EmptyList):
            text = _empty_list_text(ev.name, ev.style or style)
            if status_id is not None:
                await ui.edit_text(status_id, text, reply_markup=None)
            else:
                await ui.send_text(text)
            return

        if isinstance(ev, BatchRejected):
            if status_id is not None:
                await ui.edit_text(status_id, ev.reason, reply_markup=None)
            else:
                await ui.send_text(ev.reason)
            return


def _job_markup(job: "Job | None", job_manager: "JobManager | None") -> object:
    """Keyboard de cancelar solo cuando el flujo tiene un job real (R11/A3)."""
    if job is not None and job_manager is not None:
        return cancel_job_keyboard(job.job_id)
    return None


__all__ = [
    "present_single_image",
    "present_video",
    "present_batch",
]
