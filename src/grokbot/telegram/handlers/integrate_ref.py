"""Flujo de referencia fija para edición /s (R4 Item 3) — parity grok 1443-1461 / 3695-3713.

``/cambiar_referencia`` (solo modo ``grok``) agrega el uid al flag efímero
``deps.integrate_ref_pending`` (A2: en memoria, NUNCA en ``cfg.state``/sessions) y
pide la foto que será la referencia. La foto siguiente (single caption, single sin
caption o álbum) se persiste vía ``IntegrateRefsUseCase.set_reference`` en
``data/integrate_refs/{uid}.jpg`` + ``integrate_ref_path`` en ``sessions.json``.
Álbum en awaiting-ref: final-wins + una sola respuesta (A1), sin locks.

Reglas de capa (SPEC §5.2): TODO outbound por :class:`ChatUI`; media I/O por el
seam ``deps.gateway`` y el use case inyectado (nunca bytes en handlers vía repos).
Los copys se transcriben byte a byte de grok (typos incluidos); nunca exponen
file_ids/URLs/paths (R6/R8). Este módulo NO importa ``generation``.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from aiogram import Dispatcher, types
from aiogram.filters import Command

from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.handlers._common import (
    SOURCE_MEDIA_UNAVAILABLE_MSG,
    fetch_source_bytes,
    largest_photo,
)

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Copy (byte-parity grok; typos/acentos originales)
# --------------------------------------------------------------------------- #
_CAMBIAR_REF_OTHER_MODEL = (
    "Este comando solo esta disponible en modo <b>Grok Imagine</b>.\n"
    "Usa /config para cambiar al modo Grok Imagine."
)
_CAMBIAR_REF_PROMPT = (
    "Envia la foto que sera tu <b>referencia fija</b>.\n\n"
    "Luego, en un album (o foto) con caption que empiece por <b>/s</b>, "
    "cada imagen se editara junto con esta referencia.\n"
    "Requiere proveedor <b>xAI (oficial)</b> en /config."
)
_REF_SAVED = (
    "Referencia actualizada. En un album o foto con caption que empiece por "
    "<b>/s</b>, cada imagen se editara junto con esta referencia."
)
# Contención user-safe del drain en asyncio.create_task (parity con los drains de
# grokV2): un fallo inesperado NUNCA expone el detalle (R6) y limpia el flag.
_UNEXPECTED_ERROR = (
    "Ocurrió un error inesperado guardando la referencia. Inténtalo de nuevo."
)


def _chat_ui(deps: BotDeps, message: types.Message) -> ChatUI:
    return ChatUI.for_message(deps.gateway, message)


# --------------------------------------------------------------------------- #
# Comando /cambiar_referencia
# --------------------------------------------------------------------------- #
async def cmd_cambiar_referencia(message: types.Message, deps: BotDeps) -> None:
    """``/cambiar_referencia`` — solo en modo grok; activa el flag awaiting-ref."""
    uid = message.from_user.id
    cfg = deps.sessions.get_config(uid)
    ui = _chat_ui(deps, message)
    if cfg.model != "grok":
        await ui.send_text(_CAMBIAR_REF_OTHER_MODEL)
        return
    deps.integrate_ref_pending.add(uid)
    await ui.send_text(_CAMBIAR_REF_PROMPT)


# --------------------------------------------------------------------------- #
# Guardado de la referencia (single caption / sin caption / álbum)
# --------------------------------------------------------------------------- #
async def _save_reference(deps: BotDeps, ui: ChatUI, message: types.Message) -> None:
    """Persiste la foto como referencia y limpia el flag (parity grok 3695-3713).

    Si el media no se puede recuperar (file_id expirado/roto) degrada user-safe
    y NO limpia el flag: el user puede reintentar enviando otra foto (misma
    semántica que el source-save de faceswap).
    """
    uid = message.from_user.id
    file_id = largest_photo(message)
    data = await fetch_source_bytes(deps.gateway, file_id)
    if data is None:
        await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
        return
    deps.integrate_refs.set_reference(uid, data)
    deps.integrate_ref_pending.discard(uid)
    await ui.send_text(_REF_SAVED)


async def save_reference_from_photo(deps: BotDeps, message: types.Message) -> None:
    """Foto single (caption o sin caption) con flag pending → guardar referencia."""
    ui = _chat_ui(deps, message)
    await _save_reference(deps, ui, message)


async def drain_integrate_ref_album(deps: BotDeps, key: tuple[int, str]) -> None:
    """Drena un álbum en awaiting-ref: guarda la ÚLTIMA foto, una sola respuesta (A1).

    Contención del task (C3): un fallo inesperado en ``set_reference``/send limpia
    el flag pending (no queda colgado) y degrada con feedback best-effort
    user-safe; el detalle solo va al log por ``type(exc).__name__``.
    """
    await asyncio.sleep(deps.album.delay)
    messages = deps.album.pop(key)
    if not messages:
        return
    messages = sorted(messages, key=lambda m: m.message_id)
    last = messages[-1]
    ui = _chat_ui(deps, last)
    try:
        await _save_reference(deps, ui, last)
    except Exception as exc:  # noqa: BLE001 — contención del task (parity drains grokV2)
        uid = last.from_user.id
        deps.integrate_ref_pending.discard(uid)
        _logger.error("drain integrate ref album falló (%s)", type(exc).__name__)
        try:
            await ui.send_text(_UNEXPECTED_ERROR)
        except Exception:  # noqa: BLE001 — sin canal no hay nada más que hacer
            pass


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
def register_integrate_ref(dp: Dispatcher, deps: BotDeps) -> None:
    """Registra /cambiar_referencia (Command real; gate de modelo en el handler).

    Las fotos/textos/álbumes en awaiting-ref rutean por los filtros de
    generation.py que delegan acá; este módulo solo añade el comando.
    """
    dp.message.register(partial(cmd_cambiar_referencia, deps=deps), Command("cambiar_referencia"))


__all__ = [
    "cmd_cambiar_referencia",
    "save_reference_from_photo",
    "drain_integrate_ref_album",
    "register_integrate_ref",
]
