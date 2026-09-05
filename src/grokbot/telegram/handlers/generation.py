"""Flujos de generación de imagen (handlers, item 5) — texto, foto, reply, regen.

Rutea los updates aiogram de generación a los use cases con copy exacto de grok:

* texto → faceswap delega en faceswap.send_faceswap_text (guía con/sin source);
  validar prompt; grok/grok_video → confirm efímero ``PendingPrompts`` + keyboard
  ``confirm:yes/no`` (D7/A6); resto → single image.
* foto+caption → var/variables ya los capturó variables_cmd (Command antes); el
  flag awaiting-ref (integrate_ref_pending) guarda la referencia; aquí corre el
  integrate ``/s`` real (single/álbum/long-prompt) y long-prompt; grok_video →
  imagen-a-video (video.run_video_generation, sin job; A5: la ref se ignora);
  faceswap delega en faceswap.handle_faceswap_photo (source-save o confirm single);
  resto → edit con job "edit".
* foto sin caption → awaiting-ref guarda la referencia; faceswap delega en
  faceswap.handle_faceswap_photo; resto → hint de editar/video (grok 2895-2928).
* álbum → awaiting-ref agenda integrate_ref.drain_integrate_ref_album (final-wins,
  A1); faceswap agenda faceswap.drain_faceswap_album; grok → _drain_grok_album.
* reply texto→foto → faceswap responde reply-not-used solo si el reply es foto;
  resto → edit sin job (KieTaskRef o download por gateway); video si
  grok_video (grok 2934-3061).
* callbacks ``confirm:yes/no`` (1274-1271) y ``regen`` (1305-1418, job "regen";
  el regen integrate re-descarga source y recarga la ref por uid — A10).

Nunca ``message.answer`` directo: TODO outbound por :class:`ChatUI` y callbacks por
``deps.gateway.answer_callback``. Media I/O (file_ids) por ``deps.gateway``.
"""

from __future__ import annotations

import asyncio
from functools import partial

from aiogram import Dispatcher, types

from grokbot.application.events import ItemFailed, ItemResult, RetryScheduled
from grokbot.application.integrate_refs import IntegrateReferenceError
from grokbot.domain.generation import KieTaskRef
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.formatters import (
    escape,
    estado_card,
    model_display,
    prov_label,
    retry_status_text,
    validate_prompt,
    video_start_message,
)
from grokbot.telegram.handlers._common import (
    INTEGRATE_MAX_ALBUM,
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
from grokbot.telegram.handlers import faceswap as faceswap_handlers
from grokbot.telegram.handlers import integrate_ref as integrate_ref_handlers
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
# R8: el botón Regenerar de una imagen ajena (ref con owner_uid de otro user).
_REGEN_NOT_OWNER = "Esta regeneración pertenece a otro usuario."
# Recordatorio cuando hay un long-prompt pendiente y llega otra foto sin caption.
_LONG_PROMPT_REMINDER = (
    "Tienes una edición pendiente. Envíame el prompt como <b>mensaje de texto</b> "
    "(no hace falta responder a ningún mensaje)."
)
# Contención user-safe del drain de álbum (parity grok 1957-1962): una excepción
# inesperada (p. ej. TelegramBadRequest) edita el status con un error genérico,
# sin exponer el detalle (R6). El job se cierra en el ``finally``.
_ALBUM_UNEXPECTED_ERROR = (
    "Ocurrió un error inesperado procesando las imágenes. Inténtalo de nuevo."
)


def _chat_ui(deps: BotDeps, message: types.Message) -> ChatUI:
    return ChatUI.for_message(deps.gateway, message)


def _long_prompt_reply_text(*, is_video: bool, n_photos: int) -> str:
    """Reply de colección long-prompt (grok ``_long_prompt_collection_reply`` bot.py 544-563)."""
    if is_video:
        action = "animar la imagen"
    elif n_photos > 1:
        action = "editar las imágenes"
    else:
        action = "editar la imagen"
    album_note = f"\n\nHe guardado tus {n_photos} fotos del álbum." if n_photos > 1 else ""
    return (
        "El caption es demasiado largo para procesarlo directamente.\n\n"
        f"Envíame el prompt como <b>mensaje de texto</b> para {action}.{album_note}"
    )


async def _complete_long_prompt_collection(deps: BotDeps, message: types.Message) -> None:
    """Completa la edición pendiente de un long-prompt (grok bot.py 1966-2012).

    Consume el store con ``pop`` tras validar el prompt (A3): un texto inválido no
    descarta la colección (el user reintenta); un prompt válido consume la entrada
    y despacha single/álbum/video según los file_ids guardados.
    """
    uid = message.from_user.id
    ui = _chat_ui(deps, message)
    prompt = message.text.strip()
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await ui.send_text(prompt_err)
        return
    entry = deps.long_prompt.pop(uid)
    if entry is None or not entry.get("file_ids"):
        await ui.send_text("No hay fotos guardadas para editar. Vuelve a enviar la imagen con caption largo.")
        return
    file_ids = entry["file_ids"]
    is_video = entry.get("is_video", False)
    integrate_mode = entry.get("integrate_mode", False)
    cfg = deps.sessions.get_config(uid)
    if is_video and len(file_ids) == 1:
        image_data = await fetch_source_bytes(deps.gateway, file_ids[0])
        if image_data is None:
            await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
            return
        await run_video_generation(
            deps, message, uid=uid, cfg=cfg, prompt=prompt,
            source_image=image_data, prefix="Edit",
        )
        return
    if len(file_ids) == 1:
        await _process_single_photo_edit(
            deps, message, prompt, file_ids[0], integrate_mode=integrate_mode
        )
        return
    # Multi-file (álbum grok) — _process_album_edit (A4: álbum /s largo consume).
    await _process_album_edit(
        deps, message, prompt, file_ids, cfg, integrate_mode=integrate_mode
    )


# --------------------------------------------------------------------------- #
# Texto
# --------------------------------------------------------------------------- #
async def handle_text(message: types.Message, deps: BotDeps) -> None:
    # Long-prompt collection pendiente → el texto completa la edición (grok 1531-1533).
    if deps.long_prompt.is_awaiting(message.from_user.id):
        await _complete_long_prompt_collection(deps, message)
        return
    cfg = deps.sessions.get_config(message.from_user.id)
    if cfg.model == "faceswap":
        await faceswap_handlers.send_faceswap_text(deps, message)
        return
    ui = _chat_ui(deps, message)
    prompt = message.text.strip()
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await ui.send_text(prompt_err)
        return
    if cfg.model in ("grok", "grok_video"):
        media_word = "video" if cfg.model == "grok_video" else "imagen"
        sent = await ui.send_text(
            f"¿Confirmas generar este {media_word}?\n\n<i>{escape(prompt)}</i>",
            reply_markup=confirmation_keyboard(),
        )
        # C4: el pendiente queda atado a este mensaje concreto (chat/message_id)
        # y a su dueño; en grupos otro usuario no puede consumirlo.
        deps.pending.set(
            message.from_user.id, prompt,
            chat_id=message.chat.id, message_id=sent.message_id,
        )
        return
    await _run_single_image(deps, message, cfg, prompt, uid=message.from_user.id, prefix="Prompt")


# --------------------------------------------------------------------------- #
# Foto + caption / sin caption / álbum
# --------------------------------------------------------------------------- #
async def handle_photo_caption(message: types.Message, deps: BotDeps) -> None:
    uid = message.from_user.id
    # A2: flag awaiting-ref chequeado ANTES de faceswap/otras rutas (parity grok 2853-2855).
    if uid in deps.integrate_ref_pending:
        await integrate_ref_handlers.save_reference_from_photo(deps, message)
        return
    cfg = deps.sessions.get_config(uid)
    if cfg.model == "faceswap":
        await faceswap_handlers.handle_faceswap_photo(deps, message)
        return
    ui = _chat_ui(deps, message)
    integrate_mode, prompt = parse_integrate_caption(message.caption)
    if len(prompt) > TELEGRAM_CAPTION_COLLECT_THRESHOLD:
        file_id = largest_photo(message)
        # grok _set_long_prompt_collection clears pending_prompt (bot.py 525).
        deps.pending.clear(uid)
        deps.long_prompt.set(
            uid,
            file_ids=[file_id] if file_id else [],
            integrate_mode=integrate_mode,
            is_video=is_video_cfg(cfg),
        )
        await ui.send_text(_long_prompt_reply_text(is_video=is_video_cfg(cfg), n_photos=1))
        return
    # A3/hardening single: preflight integrate ANTES de validar el prompt (el copy
    # "requiere xAI"/"no hay referencia" no queda enmascarado por un prompt corto).
    # grok_video ignora la ref y NO valida prereqs (A5): el branch video corre luego.
    # validate_for_edit es barato (provider + exists, SIN leer los bytes): la única
    # lectura completa ocurre en _process_single_photo_edit → load_for_edit.
    if integrate_mode and not is_video_cfg(cfg):
        try:
            deps.integrate_refs.validate_for_edit(uid, cfg)
        except IntegrateReferenceError as exc:
            await ui.send_text(exc.user_message)
            return
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await ui.send_text(prompt_err)
        return
    deps.long_prompt.clear(uid)  # parity grok 2881
    file_id = largest_photo(message)
    if is_video_cfg(cfg):
        image_data = await fetch_source_bytes(deps.gateway, file_id)
        if image_data is None:
            await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
            return
        await run_video_generation(
            deps, message, uid=uid, cfg=cfg,
            prompt=prompt, source_image=image_data, prefix="Edit",
        )
        return
    await _process_single_photo_edit(
        deps, message, prompt, file_id, integrate_mode=integrate_mode
    )


async def handle_photo_no_caption(message: types.Message, deps: BotDeps) -> None:
    uid = message.from_user.id
    # A2: awaiting-ref → guardar la referencia (parity grok 2899-2901).
    if uid in deps.integrate_ref_pending:
        await integrate_ref_handlers.save_reference_from_photo(deps, message)
        return
    cfg = deps.sessions.get_config(uid)
    if cfg.model == "faceswap":
        await faceswap_handlers.handle_faceswap_photo(deps, message)
        return
    ui = _chat_ui(deps, message)
    # Long-prompt pendiente: recordar el prompt por texto (grok 2907-2913).
    if deps.long_prompt.is_awaiting(uid):
        await ui.send_text(_LONG_PROMPT_REMINDER)
        return
    if is_video_cfg(cfg):
        await ui.send_text(_HINT_VIDEO)
        return
    await ui.send_text(_HINT_EDIT)


async def handle_album(message: types.Message, deps: BotDeps) -> None:
    """Media group → colección efímera y edición/drain secuencial (grok/faceswap)."""
    uid = message.from_user.id
    key = (message.chat.id, message.media_group_id)
    # A2/A1: álbum en awaiting-ref → drain que guarda la ÚLTIMA foto (final-wins,
    # una sola respuesta; parity grok 3084-3086 delega por update).
    if uid in deps.integrate_ref_pending:
        if deps.album.add(key, message):
            asyncio.create_task(integrate_ref_handlers.drain_integrate_ref_album(deps, key))
        return
    cfg = deps.sessions.get_config(uid)
    if cfg.model == "faceswap":
        # El drain decide por cfg.state: source-save (AWAITING_SOURCE, final-wins)
        # o confirm N (parity grok 3088-3113; A2: respuestas una sola vez).
        if deps.album.add(key, message):
            asyncio.create_task(faceswap_handlers.drain_faceswap_album(deps, key, message))
        return
    if cfg.model != "grok":
        return  # paridad grok 3088-3089: seedream/comfyui/grok_video en silencio
    if deps.album.add(key, message):
        asyncio.create_task(_drain_grok_album(deps, key))


def _album_prompt(messages: list) -> str | None:
    """Prompt del álbum: primer caption no vacío tras strip (sorted por message_id).

    Un caption de SOLO espacios no es prompt (grok ``caption.strip()``): cae al
    hint de edición, no a "prompt muy corto".
    """
    for m in sorted(messages, key=lambda x: x.message_id):
        if m.caption and m.caption.strip():
            return m.caption
    return None


async def _drain_grok_album(deps: BotDeps, key: tuple[int, str]) -> None:
    """Espera el delay de colección y procesa el álbum (parity grok 3067-3180)."""
    await asyncio.sleep(deps.album.delay)
    messages = deps.album.pop(key)
    if not messages:
        return
    messages = sorted(messages, key=lambda m: m.message_id)
    first = messages[0]
    ui = _chat_ui(deps, first)
    n = len(messages)
    if n > INTEGRATE_MAX_ALBUM:
        await ui.send_text(f"El album tiene {n} fotos; el maximo es {INTEGRATE_MAX_ALBUM}.")
        return
    raw_caption = _album_prompt(messages)
    if not raw_caption:
        await ui.send_text(_HINT_EDIT)  # copy grok 3139-3143
        return
    integrate_mode, prompt = parse_integrate_caption(raw_caption)
    if len(prompt) > TELEGRAM_CAPTION_COLLECT_THRESHOLD:
        file_ids = [largest_photo(m) for m in messages if m.photo]
        file_ids = [f for f in file_ids if f]
        uid = first.from_user.id
        deps.pending.clear(uid)
        deps.long_prompt.set(
            uid, file_ids=file_ids, integrate_mode=integrate_mode, is_video=False
        )
        await ui.send_text(_long_prompt_reply_text(is_video=False, n_photos=len(file_ids)))
        return
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await ui.send_text(prompt_err)
        return
    cfg = deps.sessions.get_config(first.from_user.id)
    deps.long_prompt.clear(first.from_user.id)
    file_ids = [largest_photo(m) for m in messages if m.photo]
    file_ids = [f for f in file_ids if f]
    await _process_album_edit(
        deps, first, prompt, file_ids, cfg, integrate_mode=integrate_mode
    )


async def _process_album_edit(
    deps: BotDeps,
    anchor_message,
    prompt: str,
    file_ids: list[str],
    cfg,
    *,
    integrate_mode: bool = False,
) -> None:
    """Edición secuencial del álbum con UN job ``album_edit`` (parity 1815-1963).

    NO reutiliza ``present_single_image``: necesita continuar por foto editando un
    UNICO status con cancel cooperativo y terminales byte-parity. Álbum solo llega
    con ``cfg.model == "grok"`` (sin refine). A2 (Item 2): la rama no-integrate
    mantiene los labels SIN sufijo ``({backend})``. La rama integrate (R4 Item 3)
    transcribe el copy de grok CON sufijo ``({backend})`` (A6: en la práctica
    ``backend == "xAI"`` por el prereq del use case).
    """
    uid = anchor_message.from_user.id
    ui = _chat_ui(deps, anchor_message)
    # Preflight integrate (A3) ANTES de abrir job: provider xai + ref presente.
    reference_image = None
    if integrate_mode:
        try:
            reference_image = deps.integrate_refs.load_for_edit(uid, cfg)
        except IntegrateReferenceError as exc:
            await ui.send_text(exc.user_message)
            return
    model = model_display(cfg)
    backend = prov_label(effective_image_provider(cfg))
    n = len(file_ids)
    job = deps.job_manager.start(uid, "album_edit")
    if job is None:  # defensivo; R9 no tiene tope de concurrencia
        return
    completed = 0
    status_id = None
    initial_label = (
        f"Integrando referencia 0/{n} imágenes ({backend})..."
        if integrate_mode
        else f"Editando 0/{n} imágenes con {model['name']}..."
    )
    try:
        # El status inicial va DENTRO del try/finally: si este send_text lanza, el
        # job se cierra igual (nada de jobs huérfanos ni task del drain muerto).
        status = await ui.send_text(
            initial_label,
            reply_markup=cancel_job_keyboard(job.job_id),
        )
        status_id = status.message_id
        for i, file_id in enumerate(file_ids, 1):
            if deps.job_manager.is_cancelled(job):
                await ui.edit_text(
                    status_id, f"⏹ Cancelado. Completadas {completed}/{n} imágenes.", reply_markup=None
                )
                return
            label = (
                f"Integrando referencia {i}/{n} imágenes ({backend})..."
                if integrate_mode
                else f"Editando {i}/{n} imágenes con {model['name']}..."
            )
            await ui.edit_text(status_id, label, reply_markup=cancel_job_keyboard(job.job_id))
            image_data = await fetch_source_bytes(deps.gateway, file_id)
            if image_data is None:
                await ui.edit_text(status_id, SOURCE_MEDIA_UNAVAILABLE_MSG, reply_markup=None)
                return
            if deps.job_manager.is_cancelled(job):
                await ui.edit_text(
                    status_id, f"⏹ Cancelado. Completadas {completed}/{n} imágenes.", reply_markup=None
                )
                return
            events = deps.generate_image.run(
                user_id=uid,
                prompt=prompt,
                source_image=image_data,
                source_file_id=file_id,
                cfg_override=cfg,
                reference_image=reference_image,
            )
            async for ev in events:
                if isinstance(ev, RetryScheduled):
                    await ui.edit_text(
                        status_id,
                        retry_status_text(label, ev.attempt, ev.max_attempts),
                        reply_markup=cancel_job_keyboard(job.job_id),
                    )
                    continue
                if isinstance(ev, ItemFailed):
                    await ui.edit_text(
                        status_id,
                        f"{completed}/{n} completadas; error en imagen {i}: {ev.reason}",
                        reply_markup=None,
                    )
                    return
                if isinstance(ev, ItemResult):
                    if deps.job_manager.is_cancelled(job):
                        await ui.edit_text(
                            status_id,
                            f"⏹ Cancelado. Completadas {completed}/{n} imágenes.",
                            reply_markup=None,
                        )
                        return
                    sent = await make_sender(deps).send_image(
                        ui, ev, "Edit", status_id=status_id,
                        delete_status=False, owner_uid=uid,
                    )
                    if sent is None:
                        # send_image ya editó el status con el error user-safe (R6/M2)
                        # y devolvió None: NO contar como completada ni pisar el error
                        # con un falso "Completadas N/N"; el álbum termina en parcial.
                        return
                    completed += 1
            # El stream single termina tras su ItemResult/ItemFailed.
        await ui.edit_text(status_id, f"Completadas {n}/{n} imágenes.", reply_markup=None)
    except Exception:  # noqa: BLE001 — contención del task (parity grok 1957-1962)
        if status_id is not None:
            await ui.edit_text(status_id, _ALBUM_UNEXPECTED_ERROR, reply_markup=None)
        else:
            # El status inicial nunca llegó a publicarse: feedback genérico
            # best-effort (si también falla, el finally igual cierra el job).
            try:
                await ui.send_text(_ALBUM_UNEXPECTED_ERROR)
            except Exception:  # noqa: BLE001 — sin canal, no hay nada más que hacer
                pass
    finally:
        deps.job_manager.finish(uid, job.job_id)


# --------------------------------------------------------------------------- #
# Reply texto→foto (edit sin job; video si grok_video)
# --------------------------------------------------------------------------- #
async def handle_reply_edit(message: types.Message, deps: BotDeps) -> None:
    # Long-prompt collection pendiente → el texto completa la edición (grok 2953-2955).
    if deps.long_prompt.is_awaiting(message.from_user.id):
        await _complete_long_prompt_collection(deps, message)
        return
    reply = message.reply_to_message
    cfg = deps.sessions.get_config(message.from_user.id)
    if cfg.model == "faceswap":
        # Si el reply NO es una foto → silencio (parity grok 2957-2965).
        if reply is not None and reply.photo:
            await faceswap_handlers.send_faceswap_reply(deps, message)
        return
    ui = _chat_ui(deps, message)
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
    chat_id = callback.message.chat.id
    if not deps.pending.owns(chat_id, message_id, uid):
        if deps.pending.owner_of(chat_id, message_id) is not None:
            # C4: el mensaje pertenece a otro user (grupos) → no consumir ni editar.
            await answer_callback(
                deps.gateway, callback,
                "Esta confirmación pertenece a otro usuario.", show_alert=True,
            )
            return
        await ui.edit_text(message_id, _NO_PENDING, reply_markup=None)
        await answer_callback(deps.gateway, callback)
        return
    prompt = deps.pending.pop(uid)
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
    ui = _chat_ui(deps, callback.message)
    uid = callback.from_user.id
    message_id = callback.message.message_id
    chat_id = callback.message.chat.id
    if not deps.pending.owns(chat_id, message_id, uid):
        if deps.pending.owner_of(chat_id, message_id) is not None:
            # C4: el mensaje pertenece a otro user (grupos) → no cancelar lo ajeno.
            await answer_callback(
                deps.gateway, callback,
                "Esta confirmación pertenece a otro usuario.", show_alert=True,
            )
            return
        await answer_callback(deps.gateway, callback)
        return
    deps.pending.clear(uid)
    await ui.edit_text(message_id, "Generacion cancelada.", reply_markup=None)
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

    uid = callback.from_user.id
    # R8: el botón Regenerar queda scoped al dueño de la generación (C4, misma
    # mecánica que la confirmación). Un ref sin owner_uid (legacy) no bloquea.
    owner_uid = ref.get("owner_uid")
    if owner_uid is not None and int(owner_uid) != uid:
        await answer_callback(gateway, callback, _REGEN_NOT_OWNER, show_alert=True)
        return

    prompt = str(regen.get("prompt", "")).strip()
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await answer_callback(gateway, callback, prompt_err, show_alert=True)
        return

    cfg = cfg_override_from_regen(deps.sessions.get_config(uid), regen)
    model = model_display(cfg)
    mode = regen.get("mode", "text")
    await answer_callback(gateway, callback, "Regenerando...")

    ui = _chat_ui(deps, callback.message)
    # R9: sin tope de procesos en curso (bot privado); el job se registra para
    # Cancelar / refine.
    job = deps.job_manager.start(uid, "regen")
    label = f"Regenerando imagen con {model['name']}..."
    status_id = (await ui.send_text(label, reply_markup=cancel_job_keyboard(job.job_id))).message_id
    try:
        source_image = None
        source = None
        source_file_id = None
        reference_image = None
        integrate_mode = bool(regen.get("integrate_mode"))
        kie_ref = regen.get("kie_source_ref")
        if kie_ref:
            source = KieTaskRef(
                task_id=str(kie_ref["task_id"]), index=int(kie_ref.get("index", 0))
            )
        elif integrate_mode:
            # Regen integrate (parity grok 1343-1356): re-descarga el source ORIGINAL
            # y recarga la referencia por el uid del CONTEXTO (regen["user_id"], nunca
            # del ref opaco — R8/A10). grok usa regen["user_id"] (bot.py:1353); con un
            # ref legacy SIN owner_uid esto evita cargar la ref del clickeador. Ref
            # borrada → degrada el status user-safe y cierra el job.
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
            try:
                reference_image = deps.integrate_refs.load_for_edit(
                    int(regen.get("user_id") or uid), cfg
                )
            except IntegrateReferenceError as exc:
                await ui.edit_text(status_id, exc.user_message, reply_markup=None)
                return
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
            reference_image=reference_image,
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
    reference_image: bytes | None = None,
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
        reference_image=reference_image,
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
    deps: BotDeps,
    message: types.Message,
    prompt: str,
    file_id: str | None,
    *,
    integrate_mode: bool = False,
) -> None:
    """Foto + caption de edición → job "edit" con source descargado (parity 1702-1812).

    Con ``integrate_mode`` preflight A3 ANTES de descargar el source y de abrir el
    job: provider efectivo xai + referencia presente; los errores user-safe salen
    por ``IntegrateReferenceError.user_message`` (copy byte-parity de grok). El
    label del status integra es "Editando imagen (referencia+foto)..." (grok 1733-1737).
    """
    uid = message.from_user.id
    ui = _chat_ui(deps, message)
    cfg = deps.sessions.get_config(uid)
    model = model_display(cfg)
    reference_image = None
    if integrate_mode:
        try:
            reference_image = deps.integrate_refs.load_for_edit(uid, cfg)
        except IntegrateReferenceError as exc:
            await ui.send_text(exc.user_message)
            return
    if file_id:
        source_image = await fetch_source_bytes(deps.gateway, file_id)
        if source_image is None:
            await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
            return
    else:
        source_image = None

    job = deps.job_manager.start(uid, "edit")
    label = (
        "Editando imagen (referencia+foto)..."
        if integrate_mode
        else f"Editando imagen con {model['name']}..."
    )
    try:
        await _run_single_image(
            deps, message, cfg, prompt, uid=uid, prefix="Edit",
            source_image=source_image, source_file_id=file_id,
            reference_image=reference_image,
            label=label, cancel_text=_EDIT_CANCEL_TEXT, job=job,
        )
    finally:
        deps.job_manager.finish(uid, job.job_id)


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
async def handle_estado(message: types.Message, deps: BotDeps) -> None:
    """``/estado`` → tarjeta de configuración (grok cmd_estado, sin jobs activos)."""
    cfg = deps.sessions.get_config(message.from_user.id)
    ui = _chat_ui(deps, message)
    await ui.send_text(
        estado_card(cfg, integrate_ref=cfg.integrate_ref_path is not None),
        parse_mode=None,
    )


def register_generation(dp: Dispatcher, deps: BotDeps) -> None:
    from aiogram.filters import Command

    # /estado responde la tarjeta de configuración (R4 Item 1); /cambiar_referencia
    # es real y lo registra integrate_ref.register_integrate_ref (R4 Item 3).
    dp.message.register(partial(handle_estado, deps=deps), Command("estado"))
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
