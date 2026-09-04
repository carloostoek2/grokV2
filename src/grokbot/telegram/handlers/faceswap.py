"""Flujos Face Swap (handlers, R4 Item 2) — source, single, álbum, confirm.

Los 5 puntos de entrada dejan de degradar D8 (parity grok bot.py 1424-1437,
1535-1547, 2857-2860, 2903-2905, 2957-2965, 3088-3113, 3183-3710):

* ``/cambiar_source`` → modo ``AWAITING_SOURCE`` y guardado de la foto source.
* texto en faceswap → guía (con/sin source).
* reply texto→foto en faceswap → ``_REPLY_NOT_USED``.
* foto single (caption o no) → confirm single; álbum → drain (source-save o
  confirm N); source ausente → copy user-safe (A4).
* confirm dedicado ``faceswap:confirm:*`` (store :class:`FaceswapPending`, sin
  colisión con ``confirm:yes/no``) → job ``"faceswap"`` con progreso + cancel
  cooperativo y terminales byte-parity de grok.

Reglas de capa (SPEC §5.2): TODO outbound por :class:`ChatUI` y callbacks por
``deps.gateway.answer_callback``; media I/O (file_ids/descargas) por los seams
``deps.gateway``/``deps.downloader`` y los use cases inyectados (nunca bytes en
handlers vía ``SourceFacesUseCase.save_source``). 0 ``print`` de progreso (A7).
Los copys se transcriben byte a byte de grok (typos incluidos); los errores
nunca exponen file_ids/URLs/paths (R6/R8/A8).
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from aiogram import Dispatcher, types
from aiogram.filters import Command

from grokbot.domain.user_config import AWAITING_SOURCE
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.formatters import (
    faceswap_progress_message,
    format_faceswap_batch_status,
)
from grokbot.telegram.handlers._common import (
    SOURCE_MEDIA_UNAVAILABLE_MSG,
    answer_callback,
    fetch_source_bytes,
    largest_photo,
)
from grokbot.telegram.keyboards import (
    cancel_job_keyboard,
    faceswap_confirmation_keyboard,
)
from grokbot.telegram.ports import OutboundMedia

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Copy (byte-parity grok; typos/acentos originales)
# --------------------------------------------------------------------------- #
_CAMBIAR_SOURCE_OTHER_MODEL = (
    "Este comando solo esta disponible en modo <b>Face Swap</b>.\n"
    "Usa /config para cambiar al modo Face Swap."
)
_CAMBIAR_SOURCE_PROMPT = "Envia tu foto source (la cara que quieres usar para el swap)."
_TEXT_WITH_SOURCE = (
    "Envia una <b>foto</b> para hacer el face swap.\n"
    "Usa /cambiar_source si quieres cambiar la cara fuente."
)
_TEXT_NO_SOURCE = (
    "Primero configura tu cara fuente con /cambiar_source.\n"
    "Luego enviame fotos para intercambiar las caras."
)
_REPLY_NOT_USED = (
    "En modo Face Swap no se usa reply con texto.\n"
    "Simplemente envia la foto directamente para hacer el swap."
)
_PHOTO_NO_SOURCE = "Primero configura tu cara fuente con /cambiar_source."
_SOURCE_MISSING_SINGLE = "Source no encontrado. Usa /cambiar_source para configurar de nuevo."
_SOURCE_MISSING_BATCH = "Source no encontrado. Usa /cambiar_source."
_SOURCE_SAVED = "Source actualizado. Ahora envia tus fotos para hacer face swap."
_CONFIRM_SINGLE = "¿Confirmas hacer face swap con esta imagen?"
_CONFIRM_N = lambda n: f"¿Confirmas hacer face swap con estas {n} imágenes?"
_NO_PENDING = "Ya no hay nada pendiente. Envia una imagen o prompt nuevo."
_NOT_OWNER = "Esta confirmación pertenece a otro usuario."
# Parity REPLICATE_RATE_LIMIT_SEC (grok) entre ítems del batch.
_FACESWAP_RATE_LIMIT_SEC = 10
_MEDIA_GROUP_MAX = 10
# Contención user-safe de un error inesperado del batch (parity grok 3629-3645).
_FACESWAP_UNEXPECTED_ERROR = (
    "Ocurrió un error inesperado procesando las imágenes. Inténtalo de nuevo."
)


def _chat_ui(deps: BotDeps, message: types.Message) -> ChatUI:
    return ChatUI.for_message(deps.gateway, message)


async def _edit_quietly(ui: ChatUI, message_id: int, text: str, *, reply_markup=None) -> None:
    """Edit best-effort del status (el mensaje puede haber desaparecido)."""
    try:
        await ui.edit_text(message_id, text, reply_markup=reply_markup)
    except Exception as exc:  # noqa: BLE001 — sin canal no hay nada más que hacer
        _logger.debug("edit_quietly ignorado (%s)", type(exc).__name__)


# --------------------------------------------------------------------------- #
# /cambiar_source y guías
# --------------------------------------------------------------------------- #
async def cmd_cambiar_source(message: types.Message, deps: BotDeps) -> None:
    """``/cambiar_source`` — solo en modo faceswap; pasa a ``AWAITING_SOURCE``."""
    uid = message.from_user.id
    cfg = deps.sessions.get_config(uid)
    ui = _chat_ui(deps, message)
    if cfg.model != "faceswap":
        await ui.send_text(_CAMBIAR_SOURCE_OTHER_MODEL)
        return
    deps.source_faces.begin_awaiting_source(uid)
    await ui.send_text(_CAMBIAR_SOURCE_PROMPT)


async def send_faceswap_text(deps: BotDeps, message: types.Message) -> None:
    """Texto plano en faceswap → guía (con/sin source)."""
    uid = message.from_user.id
    cfg = deps.sessions.get_config(uid)
    ui = _chat_ui(deps, message)
    text = _TEXT_WITH_SOURCE if cfg.source_path else _TEXT_NO_SOURCE
    await ui.send_text(text)


async def send_faceswap_reply(deps: BotDeps, message: types.Message) -> None:
    """Reply texto→foto en faceswap: el reply no se usa (parity grok 2960-2965)."""
    ui = _chat_ui(deps, message)
    await ui.send_text(_REPLY_NOT_USED)


# --------------------------------------------------------------------------- #
# Source-save
# --------------------------------------------------------------------------- #
async def _save_source_bytes(deps: BotDeps, uid: int, data: bytes, ui: ChatUI) -> None:
    deps.source_faces.save_source(uid, data)
    await ui.send_text(_SOURCE_SAVED)


async def save_source_from_photo(deps: BotDeps, message: types.Message) -> None:
    """Foto en ``AWAITING_SOURCE`` → guardar la cara fuente (parity 3678-3692)."""
    uid = message.from_user.id
    ui = _chat_ui(deps, message)
    file_id = largest_photo(message)
    data = await fetch_source_bytes(deps.gateway, file_id)
    if data is None:
        await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
        return
    await _save_source_bytes(deps, uid, data, ui)


# --------------------------------------------------------------------------- #
# Foto single / álbum
# --------------------------------------------------------------------------- #
async def handle_faceswap_photo(deps: BotDeps, message: types.Message) -> None:
    """Foto single en faceswap (caption o no): source-save o confirm single."""
    uid = message.from_user.id
    cfg = deps.sessions.get_config(uid)
    if cfg.state == AWAITING_SOURCE:
        await save_source_from_photo(deps, message)
        return
    if not deps.source_faces.source_available(uid):
        ui = _chat_ui(deps, message)
        if not cfg.source_path:
            await ui.send_text(_PHOTO_NO_SOURCE)
        else:
            deps.source_faces.clear_source(uid)
            await ui.send_text(_SOURCE_MISSING_SINGLE)
        return
    file_id = largest_photo(message)
    if not file_id:
        return
    await _request_faceswap_confirmation(deps, message, [file_id])


async def drain_faceswap_album(deps: BotDeps, key: tuple[int, str], message: types.Message) -> None:
    """Espera el delay de colección y drena un álbum faceswap.

    Decide por ``cfg.state`` (parity grok 3095-3113): en ``AWAITING_SOURCE``
    guarda la ÚLTIMA foto como source (A2: final-wins, una sola respuesta); si no,
    valida source disponible y pide confirmación de las N fotos. Sin source →
    guía/source-missing UNA sola vez desde el drain.
    """
    await asyncio.sleep(deps.album.delay)
    messages = deps.album.pop(key)
    if not messages:
        return
    messages = sorted(messages, key=lambda m: m.message_id)
    first = messages[0]
    uid = first.from_user.id
    cfg = deps.sessions.get_config(uid)
    if cfg.state == AWAITING_SOURCE:
        await _drain_faceswap_source_album(deps, first, messages)
        return
    file_ids = [largest_photo(m) for m in messages if m.photo]
    file_ids = [f for f in file_ids if f]
    if not file_ids:
        return
    if not deps.source_faces.source_available(uid):
        ui = _chat_ui(deps, first)
        if not cfg.source_path:
            await ui.send_text(_PHOTO_NO_SOURCE)
        else:
            deps.source_faces.clear_source(uid)
            await ui.send_text(_SOURCE_MISSING_BATCH)
        return
    await _request_faceswap_confirmation(deps, first, file_ids)


async def _drain_faceswap_source_album(deps: BotDeps, anchor: types.Message, messages: list) -> None:
    """Source-save final-wins de un álbum (A2): la última foto, una sola respuesta."""
    ui = _chat_ui(deps, anchor)
    last = messages[-1]
    file_id = largest_photo(last)
    data = await fetch_source_bytes(deps.gateway, file_id)
    if data is None:
        await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
        return
    await _save_source_bytes(deps, anchor.from_user.id, data, ui)


async def _request_faceswap_confirmation(deps: BotDeps, message: types.Message, file_ids: list[str]) -> None:
    """Pide confirmación con el store/keyboard dedicados (parity grok 3208-3217)."""
    uid = message.from_user.id
    n = len(file_ids)
    text = _CONFIRM_SINGLE if n == 1 else _CONFIRM_N(n)
    deps.pending.clear(uid)  # parity grok 3210
    ui = _chat_ui(deps, message)
    sent = await ui.send_text(text, reply_markup=faceswap_confirmation_keyboard())
    deps.faceswap_pending.set(
        uid, list(file_ids), chat_id=message.chat.id, message_id=sent.message_id
    )


# --------------------------------------------------------------------------- #
# Confirm dedicado (faceswap:confirm:yes/no)
# --------------------------------------------------------------------------- #
async def handle_faceswap_confirm_yes(callback: types.CallbackQuery, deps: BotDeps) -> None:
    """Ejecuta el swap (single o batch) con progreso + cancel; job en ``finally``."""
    if callback.message is None:
        await answer_callback(deps.gateway, callback, "Acción inválida.", show_alert=True)
        return
    ui = _chat_ui(deps, callback.message)
    uid = callback.from_user.id
    message_id = callback.message.message_id
    chat_id = callback.message.chat.id
    if not deps.faceswap_pending.owns(chat_id, message_id, uid):
        if deps.faceswap_pending.owner_of(chat_id, message_id) is not None:
            # C4: el mensaje pertenece a otro user (grupos) → no consumir ni editar.
            await answer_callback(deps.gateway, callback, _NOT_OWNER, show_alert=True)
            return
        await _edit_quietly(ui, message_id, _NO_PENDING)
        await answer_callback(deps.gateway, callback)
        return
    file_ids = deps.faceswap_pending.pop(uid)
    cfg = deps.sessions.get_config(uid)
    if cfg.model != "faceswap" or not file_ids:
        # A1: confirm stale (el user cambió de modelo) → no swap.
        await _edit_quietly(ui, message_id, _NO_PENDING)
        await answer_callback(deps.gateway, callback)
        return
    if not deps.source_faces.source_available(uid):
        # A4: source configurado en la config pero sin archivo → clear + missing.
        # Parity grok: el copy distingue single/batch según len(file_ids).
        deps.source_faces.clear_source(uid)
        missing = _SOURCE_MISSING_BATCH if len(file_ids) > 1 else _SOURCE_MISSING_SINGLE
        await _edit_quietly(ui, message_id, missing)
        await answer_callback(deps.gateway, callback)
        return
    total = len(file_ids)
    job = deps.job_manager.start(uid, "faceswap")  # JobManager.start nunca devuelve None
    try:
        await ui.edit_text(
            message_id,
            faceswap_progress_message(0, total, current=1 if total else None),
            reply_markup=cancel_job_keyboard(job.job_id),
        )
        await answer_callback(deps.gateway, callback)
        if total == 1:
            await _run_single(deps, uid, file_ids[0], ui=ui, status_id=message_id, job=job)
        else:
            await _run_batch(deps, uid, file_ids, ui=ui, status_id=message_id, job=job)
    except Exception as exc:  # noqa: BLE001 — contención del task (parity grok 3629-3645)
        # Un bug interno no debe quedar 100% silencioso: se loguea el tipo y se
        # degrada user-safe (A8/C3). Llega al log, no al @dp.errors.
        _logger.error("confirm faceswap falló (%s)", type(exc).__name__)
        await _edit_quietly(ui, message_id, _FACESWAP_UNEXPECTED_ERROR)
    finally:
        deps.job_manager.finish(uid, job.job_id)


async def handle_faceswap_confirm_no(callback: types.CallbackQuery, deps: BotDeps) -> None:
    """Cancela la confirmación (faceswap:confirm:no) → 'Generacion cancelada.'."""
    if callback.message is None:
        await answer_callback(deps.gateway, callback, "Acción inválida.", show_alert=True)
        return
    ui = _chat_ui(deps, callback.message)
    uid = callback.from_user.id
    message_id = callback.message.message_id
    chat_id = callback.message.chat.id
    if not deps.faceswap_pending.owns(chat_id, message_id, uid):
        if deps.faceswap_pending.owner_of(chat_id, message_id) is not None:
            # C4: el mensaje pertenece a otro user (grupos) → no cancelar lo ajeno.
            await answer_callback(deps.gateway, callback, _NOT_OWNER, show_alert=True)
            return
        await answer_callback(deps.gateway, callback)
        return
    deps.faceswap_pending.clear(uid)
    await _edit_quietly(ui, message_id, "Generacion cancelada.")
    await answer_callback(deps.gateway, callback)


# --------------------------------------------------------------------------- #
# Ejecución single / batch
# --------------------------------------------------------------------------- #
def _faceswap_failure_detail(i: int, exc: Exception) -> str:
    """Detalle de fallo user-safe (A8): ``user_message`` o ``type(exc).__name__``."""
    detail = getattr(exc, "user_message", None) or type(exc).__name__
    return f"imagen {i}: {detail}"


async def _run_single(deps: BotDeps, uid: int, file_id: str, *, ui: ChatUI, status_id: int, job) -> None:
    """Swap de una foto: descarga target → swap → send_photo → terminal."""
    target = await fetch_source_bytes(deps.gateway, file_id)
    if target is None:
        await _edit_quietly(ui, status_id, SOURCE_MEDIA_UNAVAILABLE_MSG)
        return
    if deps.job_manager.is_cancelled(job):
        await _edit_quietly(ui, status_id, format_faceswap_batch_status(0, 1, [], cancelled=True))
        return
    try:
        result = await deps.swap_face.swap(uid, input_image=target)
        if deps.job_manager.is_cancelled(job):
            # Cancelado durante la espera del API → terminal cancelado sin media.
            await _edit_quietly(ui, status_id, format_faceswap_batch_status(0, 1, [], cancelled=True))
            return
        data = await deps.downloader.download(result.remote_url, allowlist=None)
        await ui.send_photo(data, filename="faceswap.jpg")
        await _edit_quietly(ui, status_id, format_faceswap_batch_status(1, 1, []))
    except Exception as exc:  # noqa: BLE001 — detalle user-safe A8
        await _edit_quietly(
            ui, status_id, format_faceswap_batch_status(0, 1, [_faceswap_failure_detail(1, exc)])
        )


async def _run_batch(deps: BotDeps, uid: int, file_ids: list[str], *, ui: ChatUI, status_id: int, job) -> None:
    """Swap de un álbum: loop con progreso, rate-limit y cancel en 3 puntos."""
    count = len(file_ids)
    results: list[bytes] = []
    failures: list[str] = []
    cancelled = False
    try:
        for i, file_id in enumerate(file_ids, 1):
            if deps.job_manager.is_cancelled(job):
                cancelled = True
                break
            await _edit_quietly(
                ui,
                status_id,
                faceswap_progress_message(i - 1, count, current=i),
                reply_markup=cancel_job_keyboard(job.job_id),
            )
            try:
                target = await fetch_source_bytes(deps.gateway, file_id)
                if target is None:
                    failures.append(f"imagen {i}: {SOURCE_MEDIA_UNAVAILABLE_MSG}")
                    continue
                if deps.job_manager.is_cancelled(job):
                    cancelled = True
                    break
                result = await deps.swap_face.swap(uid, input_image=target)
                if deps.job_manager.is_cancelled(job):
                    # Se cae el resultado si cancelaron durante la espera del API.
                    cancelled = True
                    break
                data = await deps.downloader.download(result.remote_url, allowlist=None)
                results.append(data)
                await _edit_quietly(
                    ui,
                    status_id,
                    faceswap_progress_message(i, count),
                    reply_markup=cancel_job_keyboard(job.job_id),
                )
            except Exception as exc:  # noqa: BLE001 — detalle user-safe A8
                failures.append(_faceswap_failure_detail(i, exc))
            finally:
                if not cancelled and i < count and not deps.job_manager.is_cancelled(job):
                    await asyncio.sleep(_FACESWAP_RATE_LIMIT_SEC)
                elif deps.job_manager.is_cancelled(job):
                    cancelled = True
    except Exception as exc:  # noqa: BLE001 — contención del task (parity grok 3629-3645)
        _logger.error("faceswap batch falló (%s)", type(exc).__name__)
        # Rescue: se envían las imágenes ya swapeadas y el fallo va al terminal
        # con conteos (parity grok bot.py 3629-3645), nunca al genérico.
        if results:
            delivered, send_failures = await _send_faceswap_results(ui, results)
            failures.extend(send_failures)
        else:
            delivered = 0
        failures.append(f"procesamiento: {type(exc).__name__}")
    else:
        if results:
            delivered, send_failures = await _send_faceswap_results(ui, results)
            failures.extend(send_failures)
        else:
            delivered = 0
    await _edit_quietly(
        ui,
        status_id,
        format_faceswap_batch_status(delivered, count, failures, cancelled=cancelled),
    )


def _faceswap_send_failure(exc: Exception) -> str:
    """Detalle user-safe de un fallo de envío (A8/C3): user_message o type name."""
    detail = getattr(exc, "user_message", None) or type(exc).__name__
    return f"envio a Telegram: {detail}"


async def _send_faceswap_results(ui: ChatUI, results: list[bytes]) -> tuple[int, list[str]]:
    """Envía resultados → (entregadas, fallos de envío).

    1 → ``send_photo``; N → media groups de a 10; si un media group falla,
    reintenta el chunk foto a foto (parity grok bot.py 3604-3632). Devuelve
    cuántas imágenes se ENTREGARON (una foto cuyo envío falló NO cuenta) y la
    lista de fallos de envío (user-safe, sin bytes/file_ids/paths). Nunca
    lanza: todo error de envío, incluida una foto individual del fallback, se
    traduce a un fallo del terminal.
    """
    if not results:
        return 0, []
    failures: list[str] = []
    delivered = 0
    if len(results) == 1:
        try:
            await ui.send_photo(results[0], filename="faceswap.jpg")
            delivered = 1
        except Exception as exc:  # noqa: BLE001 — detalle user-safe A8
            _logger.error("faceswap send_photo falló (%s)", type(exc).__name__)
            failures.append(_faceswap_send_failure(exc))
        return delivered, failures
    for offset in range(0, len(results), _MEDIA_GROUP_MAX):
        chunk = results[offset : offset + _MEDIA_GROUP_MAX]
        media = [
            OutboundMedia(media=data, filename=f"faceswap_{offset + i + 1}.jpg")
            for i, data in enumerate(chunk)
        ]
        try:
            await ui.send_media_group(media)
            delivered += len(chunk)
        except Exception as exc:  # noqa: BLE001 — detalle user-safe A8
            _logger.error("faceswap media group falló (%s), fallback foto a foto", type(exc).__name__)
            failures.append(_faceswap_send_failure(exc))
            for i, data in enumerate(chunk):
                try:
                    await ui.send_photo(data, filename=f"faceswap_{offset + i + 1}.jpg")
                    delivered += 1
                except Exception as photo_exc:  # noqa: BLE001 — detalle user-safe A8
                    _logger.error("faceswap fallback send falló (%s)", type(photo_exc).__name__)
                    failures.append(_faceswap_send_failure(photo_exc))
    return delivered, failures


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
def register_faceswap(dp: Dispatcher, deps: BotDeps) -> None:
    """Registra /cambiar_source y los callbacks dedicados de confirmación.

    Las fotos/textos/replys/álbumes ya rutean por los filtros de generation.py
    que delegan acá; este módulo solo añade el comando y los callbacks
    ``faceswap:confirm:*``.
    """
    dp.message.register(partial(cmd_cambiar_source, deps=deps), Command("cambiar_source"))
    dp.callback_query.register(
        partial(handle_faceswap_confirm_yes, deps=deps),
        lambda c: c.data == "faceswap:confirm:yes",
    )
    dp.callback_query.register(
        partial(handle_faceswap_confirm_no, deps=deps),
        lambda c: c.data == "faceswap:confirm:no",
    )


__all__ = [
    "register_faceswap",
    "cmd_cambiar_source",
    "send_faceswap_text",
    "send_faceswap_reply",
    "save_source_from_photo",
    "handle_faceswap_photo",
    "drain_faceswap_album",
    "handle_faceswap_confirm_yes",
    "handle_faceswap_confirm_no",
]
