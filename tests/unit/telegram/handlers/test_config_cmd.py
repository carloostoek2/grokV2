"""Tests del FSM de `/config` (item 5, handlers/config_cmd.py).

Flujo modelo → proveedor → variante/opciones con guards de privacidad y stale
callback (config_message_id + estado). Copy transcrito de grok config_flow;
0 red / 0 ``unittest.mock``. Fixtures anonimizados.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from conftest import (
    CHAT_ID,
    USER_ID,
    callback_query,
    callback_update,
    flat_callback_data,
    make_chat,
    make_deps,
    make_dispatcher,
    make_user,
    message_update,
    text_message,
)

_UID = USER_ID
_CHAT = CHAT_ID
_BOT = Bot("42:TEST")
# Id del propio bot: en Telegram real el mensaje del panel (bot-sent) lleva
# from_user = el bot, NO el usuario que configura (regresión del smoke live).
_PANEL_BOT_ID = 999999999

_STALE = "Esta pantalla ya no está activa. Usa /config para empezar de nuevo."


async def _open_config(deps=None):
    deps = deps if deps is not None else make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/config")))
    return dp, deps


def _bot_panel_message(message_id: int) -> Message:
    """Mensaje del PANEL tal como llega en vivo: enviado por el bot (from_user =
    el bot) en el chat privado del dueño (chat.id = el dueño)."""
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=make_chat(_CHAT, type="private"),
        from_user=make_user(_PANEL_BOT_ID, is_bot=True),
        text="base",
    )


def _bot_panel_callback(
    data: str, message_id: int, *, user_id: int = _UID
) -> CallbackQuery:
    """Tap del DUEÑO sobre un botón del panel del bot (callback.message = bot)."""
    return CallbackQuery(
        id="cb-botpanel",
        from_user=make_user(user_id),
        chat_instance="chat-instance",
        message=_bot_panel_message(message_id),
        data=data,
    )


def _panel_id(deps) -> int:
    return deps.gateway.calls_by_method("send_message")[-1]["sent"].message_id


def _last_answer(deps) -> dict:
    return deps.gateway.calls_by_method("answer_callback")[-1]


def _last_edit_text(deps) -> dict:
    return deps.gateway.calls_by_method("edit_message_text")[-1]


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #
async def test_config_denied_in_group():
    deps = make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(
        _BOT, message_update(text_message("/config", chat_type="group"))
    )
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert texts[-1] == "La configuración solo está disponible en chats privados."


async def test_callback_denied_in_group():
    deps = make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/config")))
    mid = _panel_id(deps)
    cb = callback_query("cfg:model:grok", message_id=mid, chat_type="group")
    await dp.feed_update(_BOT, callback_update(cb))
    ans = _last_answer(deps)
    assert ans["text"] == "La configuración solo está disponible en chats privados."
    assert ans["show_alert"] is True


# --------------------------------------------------------------------------- #
# Modelo → proveedor → variante
# --------------------------------------------------------------------------- #
async def test_config_shows_model_keyboard():
    dp, deps = await _open_config()
    sent = deps.gateway.calls_by_method("send_message")[-1]
    assert sent["text"] == "Selecciona el modelo:"
    data = flat_callback_data(sent["reply_markup"])
    assert "cfg:model:grok" in data
    assert "cfg:model:seedream" in data
    assert "cfg:close" in data


async def test_select_grok_shows_provider_screen():
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:model:grok", message_id=mid)))
    edit = _last_edit_text(deps)
    assert edit["text"].startswith("Configuración de <b>Grok Imagine</b> — proveedor.")
    data = flat_callback_data(edit["reply_markup"])
    assert {"cfg:provider:xai", "cfg:provider:replicate", "cfg:provider:kie"} <= set(data)
    assert _last_answer(deps)["text"] == "Modelo: Grok Imagine"


async def test_select_seedream_simple_screen():
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:model:seedream", message_id=mid)))
    edit = _last_edit_text(deps)
    assert "Modelo cambiado a <b>Seedream 5.0</b>." in edit["text"]
    assert _last_answer(deps)["text"] == "Modelo: Seedream 5.0"


async def test_select_comfyui_screen():
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:model:comfyui", message_id=mid)))
    edit = _last_edit_text(deps)
    assert edit["text"].startswith("Configuración de ComfyUI (GPU propia):")
    assert _last_answer(deps)["text"] == "Modelo: ComfyUI (GPU propia)"


async def test_provider_change_xai_updates_variant_screen():
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:model:grok", message_id=mid)))
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:provider:xai", message_id=mid)))
    edit = _last_edit_text(deps)
    assert edit["text"].startswith("Configuración de <b>Grok Imagine</b> — nivel de calidad.")
    assert "Actual: <b>xAI • Alta calidad</b>" in edit["text"]
    assert _last_answer(deps)["text"] == "Proveedor: xAI"


async def test_provider_noop_then_variant_noop():
    """Default grok/kie: elegir el proveedor activo no cambia nada y pasa a variante."""
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:model:grok", message_id=mid)))
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:provider:kie", message_id=mid)))
    assert _last_answer(deps)["text"] == "Ya está activo ese proveedor."
    # Pantalla de variante: la variante activa (quality) como no-op.
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:variant:quality", message_id=mid)))
    assert _last_answer(deps)["text"] == "Ya está activa esa configuración."


async def test_variant_change_updates_screen():
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:model:grok", message_id=mid)))
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:provider:xai", message_id=mid)))
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:variant:standard", message_id=mid)))
    edit = _last_edit_text(deps)
    assert "✅ Configuración de Grok Imagine actualizada y guardada." in edit["text"]
    assert "Actual: <b>xAI • Estándar</b>" in edit["text"]
    assert _last_answer(deps)["text"] == "Grok Imagine: xAI • Estándar"


async def test_same_seedream_model_says_already_using():
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    dp, deps = await _open_config(deps)
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:model:seedream", message_id=mid)))
    assert _last_answer(deps)["text"] == "Ya estás usando ese modelo."


# --------------------------------------------------------------------------- #
# Regresión (smoke live 2026-09-04): el panel es mensaje del BOT
#
# En Telegram real callback.message.from_user es el BOT, no el dueño. Los
# showers de /config NO deben leer la config del dueño desde target.from_user
# (devuelve la config default del bot: Grok Imagine / Kie / Alta calidad). Todo
# cambio de un tap debe guardarse y reflejarse en la config del dueño (uid).
# --------------------------------------------------------------------------- #
async def test_panel_bot_cambio_seedream_guarda_y_muestra_config_del_dueño():
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(_bot_panel_callback("cfg:model:seedream", mid)))
    edit = _last_edit_text(deps)
    assert "Modelo cambiado a <b>Seedream 5.0</b>." in edit["text"]
    assert "Grok Imagine" not in edit["text"], "la pantalla no debe mostrar el modelo default del bot"
    assert deps.sessions.get_config(_UID).model == "seedream", "el cambio se guarda en la config del dueño"
    assert _last_answer(deps)["text"] == "Modelo: Seedream 5.0"


async def test_panel_bot_provider_xai_refleja_config_del_dueño():
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(_bot_panel_callback("cfg:model:grok", mid)))
    await dp.feed_update(_BOT, callback_update(_bot_panel_callback("cfg:provider:xai", mid)))
    edit = _last_edit_text(deps)
    assert "Actual: <b>xAI • Alta calidad</b>" in edit["text"]
    assert "Actual: <b>Kie" not in edit["text"], "no debe quedar el proveedor default del bot"
    assert deps.sessions.get_config(_UID).grok_imagine_provider == "xai"
    assert _last_answer(deps)["text"] == "Proveedor: xAI"


# --------------------------------------------------------------------------- #
# Video (config granular)
# --------------------------------------------------------------------------- #
async def _open_video(deps=None):
    deps = deps if deps is not None else make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/video")))
    return dp, deps


async def test_video_aspect_noop_keeps_joined_value():
    """cfg:video:aspect:16:9 → el value reconstruido es '16:9' (no-op)."""
    dp, deps = await _open_video()
    mid = _panel_id(deps)
    screen = deps.gateway.calls_by_method("send_message")[-1]["text"]
    assert "Configuración de <b>Grok Imagine Video</b>" in screen
    await dp.feed_update(
        _BOT, callback_update(callback_query("cfg:video:aspect:16:9", message_id=mid))
    )
    ans = _last_answer(deps)
    assert ans["text"] == "Ya está activa esa relación de aspecto."


async def test_video_aspect_change_9_16():
    dp, deps = await _open_video()
    mid = _panel_id(deps)
    await dp.feed_update(
        _BOT, callback_update(callback_query("cfg:video:aspect:9:16", message_id=mid))
    )
    edit = _last_edit_text(deps)
    assert "✅ Configuración de video actualizada y guardada." in edit["text"]
    assert "9:16" in edit["text"]
    assert "9:16" in _last_answer(deps)["text"]


async def test_video_mode_denied_for_xai():
    """Modo (spicy/normal) solo disponible con Kie.ai."""
    deps = make_deps()
    deps.update_config.set_grok_imagine_provider(_UID, "xai")
    dp, deps = await _open_video(deps)
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:video:mode:spicy", message_id=mid)))
    ans = _last_answer(deps)
    assert ans["text"] == "Modo de video solo disponible con Kie.ai."
    assert ans["show_alert"] is True


# --------------------------------------------------------------------------- #
# Stale / desync
# --------------------------------------------------------------------------- #
async def test_stale_callback_wrong_message_id():
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(
        _BOT, callback_update(callback_query("cfg:model:grok", message_id=mid + 7))
    )
    ans = _last_answer(deps)
    assert ans["text"] == _STALE
    assert ans["show_alert"] is True


async def test_stale_callback_wrong_state():
    """cfg:variant:* exige estado configure; desde select_provider queda inerte."""
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:model:grok", message_id=mid)))
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:variant:standard", message_id=mid)))
    assert _last_answer(deps)["text"] == _STALE


async def test_close_clears_panel():
    dp, deps = await _open_config()
    mid = _panel_id(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("cfg:close", message_id=mid)))
    edit = _last_edit_text(deps)
    assert edit["text"] == "Configuración cerrada."
    assert edit["reply_markup"] is None
