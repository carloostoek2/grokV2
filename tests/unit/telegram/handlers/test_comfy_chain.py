"""Chain buttons under a ComfyUI result: set the flow, wait for the next prompt."""

from __future__ import annotations

from conftest import (
    USER_ID,
    callback_query,
    callback_update,
    make_deps,
    make_dispatcher,
    make_photo_message,
    make_registry,
    message_update,
    text_message,
)
from grokbot.telegram.handlers.generation import (
    _CHAIN_NOT_APPLICABLE,
)


def _bot():
    from aiogram import Bot
    return Bot("42:TEST")


async def _msg(deps, message):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_bot(), message_update(message))
    return deps


async def _cb(deps, callback):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_bot(), callback_update(callback))
    return deps


async def test_edit_sets_sibling_and_next_text_uses_last_image(tmp_path):
    registry = make_registry()
    deps = make_deps(registry=registry)
    img = tmp_path / "last.png"
    img.write_bytes(b"png-chain")
    deps.comfy_chain.remember(
        USER_ID, path=str(img), flow_id="grok_style", prompt="un gato",
    )
    photo = make_photo_message(message_id=9)
    deps = await _cb(deps, callback_query("pipe:edit", message=photo, message_id=9))

    cfg = deps.sessions.get_config(USER_ID)
    assert cfg.model == "comfyui"
    assert cfg.comfyui.model == "grok_edit"
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert any("Editar con Grok Style Edit" in t for t in texts)
    assert deps.comfy_chain.is_armed(USER_ID)

    deps = await _msg(deps, text_message("night street"))
    calls = registry.provider("comfyui").calls
    assert calls, "expected a Comfy generation"
    request, source = calls[-1]
    assert request.params["model"] == "grok_edit"
    assert source == b"png-chain"
    assert not deps.comfy_chain.is_armed(USER_ID)


async def test_retake_does_not_send_the_previous_image(tmp_path):
    registry = make_registry()
    deps = make_deps(registry=registry)
    img = tmp_path / "last.png"
    img.write_bytes(b"png-chain")
    deps.comfy_chain.remember(
        USER_ID, path=str(img), flow_id="flux1_dev_t2i", prompt="a portrait",
    )
    photo = make_photo_message(message_id=4)
    deps = await _cb(deps, callback_query("pipe:retake", message=photo, message_id=4))
    assert deps.sessions.get_config(USER_ID).comfyui.model == "flux1_dev_t2i"

    deps = await _msg(deps, text_message("otra toma del retrato"))
    request, source = registry.provider("comfyui").calls[-1]
    assert request.params["model"] == "flux1_dev_t2i"
    assert source is None


async def test_edit_hidden_when_flow_has_no_sibling(tmp_path):
    registry = make_registry()
    deps = make_deps(registry=registry)
    img = tmp_path / "last.png"
    img.write_bytes(b"png-chain")
    deps.update_config.set_model(USER_ID, "comfyui")
    deps.update_config.set_comfyui(USER_ID, model="flux1_dev_t2i")
    deps.comfy_chain.remember(
        USER_ID, path=str(img), flow_id="flux1_dev_t2i", prompt="a portrait",
    )
    photo = make_photo_message(message_id=5)
    deps = await _cb(deps, callback_query("pipe:edit", message=photo, message_id=5))
    answers = deps.gateway.calls_by_method("answer_callback")
    assert answers and _CHAIN_NOT_APPLICABLE in (answers[-1].get("text") or "")
    assert deps.sessions.get_config(USER_ID).comfyui.model == "flux1_dev_t2i"
    assert not deps.comfy_chain.is_armed(USER_ID)
    assert registry.provider("comfyui").calls == []
