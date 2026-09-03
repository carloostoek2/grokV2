"""Flujos de generación de imagen (handlers, item 5) — texto, foto, reply, regen.

Rutea los updates aiogram de generación a los use cases con copy exacto de grok:

* texto → faceswap D8; validar prompt; grok/grok_video → confirm efímero
  ``PendingPrompts`` + keyboard ``confirm:yes/no`` (D7/A6); resto → single image.
* foto+caption → var/variables ya los capturó variables_cmd (Command antes); aquí
  degrade integrate ``/s`` y long-prompt; grok_video → imagen-a-video
  (video.run_video_generation, sin job); faceswap D8; resto → edit con job "edit".
* foto sin caption → hint de editar/video (grok 2895-2928).
* reply texto→foto → edit sin job (KieTaskRef o download por gateway); video si
  grok_video (grok 2934-3061).
* callbacks ``confirm:yes/no`` (1274-1271) y ``regen`` (1305-1418, job "regen").

Nunca ``message.answer`` directo: TODO outbound por :class:`ChatUI` y callbacks por
``deps.gateway.answer_callback``. Media I/O (file_ids) por ``deps.gateway``.
"""

from __future__ import annotations

from functools import partial

from aiogram import Dispatcher, types

from grokbot.domain.generation import KieTaskRef
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.formatters import (
    JOBS_FULL_MSG,
    escape,
    model_display,
    validate_prompt,
    video_start_message,
)
from grokbot.telegram.handlers._common import (
    D8_ALBUM_MSG,
    D8_CMD_MSG,
    D8_FACESWAP_MSG,
    D8_INTEGRATE_MSG,
    D8_LONG_PROMPT_MSG,
    SOURCE_MEDIA_UNAVAILABLE_MSG,
    TELEGRAM_CAPTION_COLLECT_THRESHOLD,
    answer_callback,
    cfg_override_from_regen,
    effective_image_provider,
    fetch_source_bytes,
    is_album,
    is_photo_caption,
    is_photo_no_caption,
    is_plain_prompt,
    is_reply_edit,
    largest_photo,
    make_sender,
    parse_integrate_caption,
    resolve_reply_kie_ref,
)
from grokbot.telegram.handlers.video import is_video_cfg, run_video_generation
from grokbot.telegram.keyboards import cancel_job_keyboard, confirmation_keyboard
from grokbot.telegram.stream_presenter import present_single_image

# Hints de foto sin caption (grok 2916-2928).
_HINT_VIDEO = (
    "Para animar una imagen (imagen a video), enviala con un <b>caption</b> describiendo el movimiento.\n\n"
    "Ejemplo: envia tu foto con el texto <i>\"haz que el agua caiga y aleja la camara lentamente\"</i>"
)
_HINT_EDIT = (
    "Para editar una imagen, enviala con un <b>caption</b> describiendo los cambios que quieres.\n\n"
    "Ejemplo: envia tu foto con el texto <i>\"cambia el fondo a una playa al atardecer\"</i>"
)
_NO_PENDING = "Ya no hay nada pendiente. Envia una imagen o prompt nuevo."
_EDIT_CANCEL_TEXT = "⏹ Edición cancelada."
_REGEN_CANCEL_TEXT = "⏹ Regeneración cancelada."


def _chat_ui(deps: BotDeps, message: types.Message) -> ChatUI:
    return ChatUI.for_message(deps.gateway, message)


# --------------------------------------------------------------------------- #
# Texto
# --------------------------------------------------------------------------- #
async def handle_text(message: types.Message, deps: BotDeps) -> None:
    cfg = deps.sessions.get_config(message.from_user.id)
    ui = _chat_ui(deps, message)
    if cfg.model == "faceswap":
        await ui.send_text(D8_FACESWAP_MSG)
        return
    prompt = message.text.strip()
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await ui.send_text(prompt_err)
        return
    if cfg.model in ("grok", "grok_video"):
        deps.pending.set(message.from_user.id, prompt)
        media_word = "video" if cfg.model == "grok_video" else "imagen"
        await ui.send_text(
            f"¿Confirmas generar este {media_word}?\n\n<i>{escape(prompt)}</i>",
            reply_markup=confirmation_keyboard(),
        )
        return
    await _run_single_image(deps, message, cfg, prompt, uid=message.from_user.id, prefix="Prompt")


# --------------------------------------------------------------------------- #
# Foto + caption / sin caption / álbum
# --------------------------------------------------------------------------- #
async def handle_photo_caption(message: types.Message, deps: BotDeps) -> None:
    cfg = deps.sessions.get_config(message.from_user.id)
    ui = _chat_ui(deps, message)
    if cfg.model == "faceswap":
        await ui.send_text(D8_FACESWAP_MSG)
        return
    integrate_mode, prompt = parse_integrate_caption(message.caption)
    if integrate_mode:
        await ui.send_text(D8_INTEGRATE_MSG)
        return
    if len(prompt) > TELEGRAM_CAPTION_COLLECT_THRESHOLD:
        await ui.send_text(D8_LONG_PROMPT_MSG)
        return
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await ui.send_text(prompt_err)
        return
    file_id = largest_photo(message)
    if is_video_cfg(cfg):
        image_data = await fetch_source_bytes(deps.gateway, file_id)
        if image_data is None:
            await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
            return
        await run_video_generation(
            deps, message, uid=message.from_user.id, cfg=cfg,
            prompt=prompt, source_image=image_data, prefix="Edit",
        )
        return
    await _process_single_photo_edit(deps, message, prompt, file_id)


async def handle_photo_no_caption(message: types.Message, deps: BotDeps) -> None:
    cfg = deps.sessions.get_config(message.from_user.id)
    ui = _chat_ui(deps, message)
    if cfg.model == "faceswap":
        await ui.send_text(D8_FACESWAP_MSG)
        return
    if is_video_cfg(cfg):
        await ui.send_text(_HINT_VIDEO)
        return
    await ui.send_text(_HINT_EDIT)


async def handle_album(message: types.Message, deps: BotDeps) -> None:
    """Álbum/media group entrante → degradación D8 (no silencio cuando trae caption)."""
    if not message.caption:
        return
    ui = _chat_ui(deps, message)
    await ui.send_text(D8_ALBUM_MSG)


# --------------------------------------------------------------------------- #
# Reply texto→foto (edit sin job; video si grok_video)
# --------------------------------------------------------------------------- #
async def handle_reply_edit(message: types.Message, deps: BotDeps) -> None:
    reply = message.reply_to_message
    cfg = deps.sessions.get_config(message.from_user.id)
    ui = _chat_ui(deps, message)
    if cfg.model == "faceswap":
        await ui.send_text(D8_FACESWAP_MSG)
        return
    if reply is None or not reply.photo:
        return
    prompt = message.text.strip()
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await ui.send_text(prompt_err)
        return

    source = None
    source_image = None
    source_file_id = None
    if effective_image_provider(cfg) == "kie":
        source = resolve_reply_kie_ref(deps, reply)
    if source is None:
        file_id = largest_photo(reply)
        if file_id:
            source_image = await fetch_source_bytes(deps.gateway, file_id)
            if source_image is None:
                await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
                return
            source_file_id = file_id

    if is_video_cfg(cfg):
        await run_video_generation(
            deps, message, uid=message.from_user.id, cfg=cfg,
            prompt=prompt, source_image=source_image, source=source, prefix="Edit",
        )
        return
    await _run_single_image(
        deps, message, cfg, prompt, uid=message.from_user.id, prefix="Edit",
        source_image=source_image, source=source, source_file_id=source_file_id,
    )


# --------------------------------------------------------------------------- #
# Confirm (D7/A6) y regen
# --------------------------------------------------------------------------- #
async def handle_confirm_yes(callback: types.CallbackQuery, deps: BotDeps) -> None:
    if callback.message is None:
        await answer_callback(deps.gateway, callback, "Acción inválida.", show_alert=True)
        return
    ui = _chat_ui(deps, callback.message)
    uid = callback.from_user.id
    message_id = callback.message.message_id
    prompt = deps.pending.pop(uid)
    if not prompt:
        await ui.edit_text(message_id, _NO_PENDING, reply_markup=None)
        await answer_callback(deps.gateway, callback)
        return
    cfg = deps.sessions.get_config(uid)
    model = model_display(cfg)
    if cfg.model == "grok_video":
        raw = cfg.video.model
        await ui.edit_text(message_id, video_start_message(raw, prompt), reply_markup=None)
        await answer_callback(deps.gateway, callback)
        await run_video_generation(
            deps, callback.message, uid=uid, cfg=cfg, prompt=prompt,
            prefix="Prompt", status_id=message_id,
        )
        return
    await ui.edit_text(
        message_id,
        f"Generando imagen con {model['name']}...\n\n<i>{escape(prompt)}</i>",
        reply_markup=None,
    )
    await answer_callback(deps.gateway, callback)
    await _run_single_image(
        deps, callback.message, cfg, prompt, uid=uid,
        prefix="Prompt", status_id=message_id,
    )


async def handle_confirm_no(callback: types.CallbackQuery, deps: BotDeps) -> None:
    if callback.message is None:
        await answer_callback(deps.gateway, callback, "Acción inválida.", show_alert=True)
        return
    deps.pending.clear(callback.from_user.id)
    ui = _chat_ui(deps, callback.message)
    await ui.edit_text(callback.message.message_id, "Generacion cancelada.", reply_markup=None)
    await answer_callback(deps.gateway, callback)


async def handle_regenerate(callback: types.CallbackQuery, deps: BotDeps) -> None:
    """Botón Regenerar bajo una imagen enviada (regen context persistido)."""
    gateway = deps.gateway
    if callback.message is None or not callback.message.photo:
        await answer_callback(gateway, callback, "Mensaje no valido.", show_alert=True)
        return
    ref = deps.refs.get(callback.message.chat.id, callback.message.message_id)
    regen = ref.get("regen") if ref else None
    if not regen:
        await answer_callback(gateway, callback, "No se puede regenerar (contexto expirado).", show_alert=True)
        return
    prompt = str(regen.get("prompt", "")).strip()
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await answer_callback(gateway, callback, prompt_err, show_alert=True)
        return

    uid = callback.from_user.id
    cfg = cfg_override_from_regen(deps.sessions.get_config(uid), regen)
    model = model_display(cfg)
    mode = regen.get("mode", "text")
    await answer_callback(gateway, callback, "Regenerando...")

    ui = _chat_ui(deps, callback.message)
    job = deps.job_manager.start(uid, "regen")
    if job is None:
        await answer_callback(gateway, callback, JOBS_FULL_MSG, show_alert=True)
        return
    label = f"Regenerando imagen con {model['name']}..."
    status_id = (await ui.send_text(label, reply_markup=cancel_job_keyboard(job.job_id))).message_id
    try:
        source_image = None
        source = None
        source_file_id = None
        kie_ref = regen.get("kie_source_ref")
        if kie_ref:
            source = KieTaskRef(
                task_id=str(kie_ref["task_id"]), index=int(kie_ref.get("index", 0))
            )
        elif mode == "edit":
            file_id = regen.get("source_file_id")
            if not file_id:
                await ui.edit_text(
                    status_id,
                    "No se pudo recuperar la imagen original para regenerar.",
                    reply_markup=None,
                )
                return
            source_image = await fetch_source_bytes(deps.gateway, file_id)
            if source_image is None:
                await ui.edit_text(
                    status_id, SOURCE_MEDIA_UNAVAILABLE_MSG, reply_markup=None
                )
                return
            source_file_id = file_id
        await _run_single_image(
            deps, callback.message, cfg, prompt, uid=uid,
            prefix="Edit" if mode == "edit" else "Prompt",
            source_image=source_image, source=source, source_file_id=source_file_id,
            label=label, status_id=status_id,
            cancel_text=_REGEN_CANCEL_TEXT, job=job,
        )
    finally:
        deps.job_manager.finish(uid, job.job_id)


# --------------------------------------------------------------------------- #
# Internal: single image (text/edit/regen) + photo edit con job
# --------------------------------------------------------------------------- #
async def _run_single_image(
    deps: BotDeps,
    message: types.Message,
    cfg,
    prompt: str,
    *,
    uid: int,
    prefix: str,
    source_image: bytes | None = None,
    source: object | None = None,
    source_file_id: str | None = None,
    label: str | None = None,
    status_id: int | None = None,
    delete_status: bool = True,
    cancel_text: str = _EDIT_CANCEL_TEXT,
    job=None,
) -> None:
    ui = _chat_ui(deps, message)
    model = model_display(cfg)
    label = label or f"Generando imagen con {model['name']}..."
    events = deps.generate_image.run(
        user_id=uid,
        prompt=prompt,
        source_image=source_image,
        source=source,
        source_file_id=source_file_id,
        cfg_override=cfg,
    )
    await present_single_image(
        ui,
        events,
        label=label,
        sender=make_sender(deps),
        prefix=prefix,
        status_id=status_id,
        delete_status=delete_status,
        cancel_text=cancel_text,
        user_id=uid,
        cfg=cfg,
        refine_uc=deps.refine_uc,
        job=job,
        job_manager=deps.job_manager if job is not None else None,
    )


async def _process_single_photo_edit(
    deps: BotDeps, message: types.Message, prompt: str, file_id: str | None
) -> None:
    """Foto + caption de edición → job "edit" con source descargado (parity 1702-1812)."""
    uid = message.from_user.id
    ui = _chat_ui(deps, message)
    cfg = deps.sessions.get_config(uid)
    model = model_display(cfg)
    if file_id:
        source_image = await fetch_source_bytes(deps.gateway, file_id)
        if source_image is None:
            await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
            return
    else:
        source_image = None

    job = deps.job_manager.start(uid, "edit")
    if job is None:
        await ui.send_text(JOBS_FULL_MSG)
        return
    label = f"Editando imagen con {model['name']}..."
    try:
        await _run_single_image(
            deps, message, cfg, prompt, uid=uid, prefix="Edit",
            source_image=source_image, source_file_id=file_id,
            label=label, cancel_text=_EDIT_CANCEL_TEXT, job=job,
        )
    finally:
        deps.job_manager.finish(uid, job.job_id)


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
async def _cmd_unavailable(message: types.Message, deps: BotDeps) -> None:
    ui = _chat_ui(deps, message)
    await ui.send_text(D8_CMD_MSG)


def register_generation(dp: Dispatcher, deps: BotDeps) -> None:
    # Comandos residuales D8 (flujos de grok sin use case).
    for command in ("cambiar_source", "cambiar_referencia", "estado"):
        from aiogram.filters import Command

        dp.message.register(partial(_cmd_unavailable, deps=deps), Command(command))
    # Texto plano de generación (no comando, no reply).
    dp.message.register(partial(handle_text, deps=deps), is_plain_prompt)
    # Foto: caption / sin caption / álbum.
    dp.message.register(partial(handle_photo_caption, deps=deps), is_photo_caption)
    dp.message.register(partial(handle_photo_no_caption, deps=deps), is_photo_no_caption)
    dp.message.register(partial(handle_album, deps=deps), is_album)
    # Reply texto → foto (edit).
    dp.message.register(partial(handle_reply_edit, deps=deps), is_reply_edit)
    # Callbacks de confirmación y regen.
    dp.callback_query.register(
        partial(handle_confirm_yes, deps=deps),
        lambda c: c.data == "confirm:yes",
    )
    dp.callback_query.register(
        partial(handle_confirm_no, deps=deps),
        lambda c: c.data == "confirm:no",
    )
    dp.callback_query.register(
        partial(handle_regenerate, deps=deps),
        lambda c: c.data == "regen",
    )


__all__ = ["register_generation", "handle_text", "handle_reply_edit"]
