"""Smoke e2e offline de routing telegram (item 5, Task 4, D9).

`Dispatcher(storage=MemoryStorage())` + `Bot("42:TEST")` + `register_all` con
deps fake → `dp.feed_update` de updates reales de aiogram. Sin red/token;
los asserts fijan strings transcritos de grok. Fixtures anonimizados.
"""

from __future__ import annotations

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    callback_query,
    callback_update,
    make_deps,
    make_dispatcher,
    message_update,
    text_message,
)

_BOT = Bot("42:TEST")


def _send_texts(deps) -> list[str]:
    return [c["text"] for c in deps.gateway.calls_by_method("send_message")]


async def test_routing_start_welcome():
    deps = make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/start")))
    texts = _send_texts(deps)
    assert any("Modelo actual:" in t for t in texts)
    assert any("Envame un prompt y te genero la imagen" in t for t in texts)


async def test_routing_config_models_screen():
    deps = make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/config")))
    sent = deps.gateway.calls_by_method("send_message")[-1]
    assert sent["text"] == "Selecciona el modelo:"


async def test_routing_confirm_no_cancels():
    deps = make_deps()
    deps.pending.set(USER_ID, "un prompt de ejemplo")
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, callback_update(callback_query("confirm:no", message_id=3)))
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "Generacion cancelada."
    assert deps.pending.get(USER_ID) is None


async def test_routing_seedream_text_direct():
    deps = make_deps()
    deps.update_config.set_model(USER_ID, "seedream")
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("un pato de goma azul")))
    sends = deps.gateway.calls_by_method("send_message")
    assert sends[-1]["text"].startswith("Generando imagen con Seedream 5.0...")
    assert deps.gateway.calls_by_method("send_photo"), "esperaba el resultado en photo"
