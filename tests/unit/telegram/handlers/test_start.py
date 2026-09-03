"""Tests de /start (item 5, handlers): texto de bienvenida por modelo activo.

Transcribe el copy de grok bot.py 1066-1101; TODO outbound por el gateway fake
(0 red / 0 ``unittest.mock``). Fixtures anonimizados (user/chat 111111111).
"""

from __future__ import annotations

from aiogram import Bot

from conftest import CHAT_ID, USER_ID, make_deps, make_dispatcher, message_update, text_message
from grokbot.telegram.formatters import model_display

_UID = USER_ID
_BOT = Bot("42:TEST")


async def _start(deps):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/start", user_id=_UID, chat_id=CHAT_ID)))
    return deps.gateway.calls_by_method("send_message")


def _text(send: list) -> str:
    assert send, "se esperaba un send_message"
    return send[-1]["text"]


async def test_start_default_grok():
    deps = make_deps()
    text = _text(await _start(deps))
    assert "Modelo actual: <b>Grok Imagine (Kie.ai • Alta calidad)</b>" in text
    assert "Envame un prompt y te genero la imagen" in text


async def test_start_grok_video_prompt():
    deps = make_deps()
    deps.update_config.set_model(_UID, "grok_video")
    text = _text(await _start(deps))
    assert "Envame un prompt y te genero un <b>video</b>." in text
    assert "Modelo actual: <b>Grok Imagine Video" in text


async def test_start_faceswap_notice():
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    text = _text(await _start(deps))
    assert "Modelo actual: <b>Face Swap</b>" in text
    assert "El modo Face Swap no está disponible en esta versión." in text


async def test_start_comfyui_generic():
    deps = make_deps()
    deps.update_config.set_model(_UID, "comfyui")
    cfg = deps.sessions.get_config(_UID)
    name = model_display(cfg)["name"]  # "ComfyUI (krea2 • lora none)"
    text = _text(await _start(deps))
    assert f"Modelo actual: <b>{name}</b>" in text
    # texto genérico de imagen (sin líneas de video)
    assert "Envame un prompt y te genero la imagen" in text


async def test_start_seedream_model_line():
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    text = _text(await _start(deps))
    assert "Modelo actual: <b>Seedream 5.0</b>" in text
