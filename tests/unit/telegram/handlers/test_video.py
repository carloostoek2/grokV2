"""Tests de orquestación de video (item 5, handlers/video.py).

Cubre text→video (confirm grok_video), foto+caption→video (imagen a video) y la
finalización O3 ante un ``ItemFailed`` (aun ``terminal=False``). 0 red / 0
``unittest.mock``.
"""

from __future__ import annotations

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    FakeVideoProvider,
    callback_query,
    callback_update,
    flat_callback_data,
    make_deps,
    make_dispatcher,
    make_photo_message,
    make_registry,
    message_update,
    text_message,
)
from grokbot.providers.base import ProviderError
from grokbot.telegram.handlers._common import SOURCE_MEDIA_UNAVAILABLE_MSG

_UID = USER_ID
_CHAT = CHAT_ID
_BOT = Bot("42:TEST")


async def _msg(deps, message):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(message))
    return deps


async def _cb(deps, callback):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, callback_update(callback))
    return deps


def _send_texts(deps) -> list[str]:
    return [c["text"] for c in deps.gateway.calls_by_method("send_message")]


async def test_video_text_confirm_yes_sends_video():
    deps = make_deps()
    deps.update_config.set_model(_UID, "grok_video")
    deps = await _msg(deps, text_message("un pato volando sobre un lago"))
    confirm = deps.gateway.calls_by_method("send_message")[-1]
    assert confirm["text"].startswith("¿Confirmas generar este video?")
    assert flat_callback_data(confirm["reply_markup"]) == ["confirm:yes", "confirm:no"]
    mid = confirm["sent"].message_id
    deps = await _cb(deps, callback_query("confirm:yes", message_id=mid))
    assert deps.gateway.calls_by_method("send_video"), "esperaba el video enviado"


async def test_video_photo_caption_i2v():
    deps = make_deps()
    deps.update_config.set_model(_UID, "grok_video")
    deps = await _msg(
        deps, make_photo_message(caption="haz que el agua caiga", file_id="FAKE:src_video")
    )
    texts = _send_texts(deps)
    assert any(t.startswith("Animando imagen con <b>grok-imagine-video</b>") for t in texts)
    assert deps.gateway.calls_by_method("send_video")


async def test_video_photo_caption_source_fetch_failure_degrades():
    """M2: foto+caption i2v con file_id expirado → degrada user-safe, sin video."""
    deps = make_deps()
    deps.update_config.set_model(_UID, "grok_video")
    deps = await _msg(
        deps,
        make_photo_message(caption="haz que el agua caiga", file_id="EXPIRED:src_video"),
    )
    texts = _send_texts(deps)
    assert SOURCE_MEDIA_UNAVAILABLE_MSG in texts
    assert "EXPIRED" not in texts[-1], "no filtra el file_id al usuario"
    assert deps.gateway.calls_by_method("send_video") == []
    assert deps.job_manager.active_jobs(_UID) == (), "el flujo i2v no abre job"


async def test_video_itemfailed_terminal_false_finalizes():
    """O3: ItemFailed aun ``terminal=False`` finaliza el flujo (sin reintento)."""
    registry = make_registry(
        kie=FakeVideoProvider(
            outcomes=[ProviderError("fallo video", user_message="Fallo el video.")]  # base retryable=True
        )
    )
    deps = make_deps(registry=registry)
    deps.update_config.set_model(_UID, "grok_video")
    deps = await _msg(deps, text_message("un dron sobrevolando una ciudad"))
    confirm = deps.gateway.calls_by_method("send_message")[-1]
    mid = confirm["sent"].message_id
    deps = await _cb(deps, callback_query("confirm:yes", message_id=mid))
    assert not deps.gateway.calls_by_method("send_video"), "video fallido no se envía"
    out = [c["text"] for c in deps.gateway.calls_by_method("edit_message_text")]
    out += _send_texts(deps)
    assert any("Fallo el video." in t for t in out)
