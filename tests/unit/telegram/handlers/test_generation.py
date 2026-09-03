"""Tests de handlers de generación de imagen (item 5): texto/foto/reply/regen.

Flujos NUCLEO con copy exacto de grok; 0 red / 0 ``unittest.mock`` (fakes del
conftest). Los asserts fijan strings transcritos byte a byte ("Generacion
cancelada." etc.). Fixtures anonimizados.
"""

from __future__ import annotations

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    callback_query,
    callback_update,
    flat_callback_data,
    make_deps,
    make_dispatcher,
    make_photo_message,
    message_update,
    text_message,
)

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


async def test_text_grok_confirm_and_yes():
    deps = make_deps()
    deps = await _msg(deps, text_message("retrato de pie"))
    sends = deps.gateway.calls_by_method("send_message")
    last = sends[-1]
    assert last["text"].startswith("¿Confirmas generar este imagen?")
    assert "<i>retrato de pie</i>" in last["text"]
    assert flat_callback_data(last["reply_markup"]) == ["confirm:yes", "confirm:no"]
    mid = last["sent"].message_id

    deps = await _cb(deps, callback_query("confirm:yes", message_id=mid))
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"].startswith("Generando imagen con Grok Imagine (Kie.ai • Alta calidad)...")
    assert deps.gateway.calls_by_method("send_photo"), "esperaba el resultado en photo"
    # el prompt pendiente ya fue consumido
    assert deps.pending.get(_UID) is None


async def test_text_grok_confirm_no():
    deps = make_deps()
    deps.pending.set(_UID, "retrato de pie")
    deps = await _cb(deps, callback_query("confirm:no", message_id=3))
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "Generacion cancelada."
    assert deps.pending.get(_UID) is None


async def test_text_seedream_direct():
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    deps = await _msg(deps, text_message("un gato rojo"))
    sends = deps.gateway.calls_by_method("send_message")
    assert sends[-1]["text"].startswith("Generando imagen con Seedream 5.0...")
    assert deps.gateway.calls_by_method("send_photo"), "esperaba el resultado en photo"


async def test_photo_caption_edit_job_downloads_source():
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    deps = await _msg(
        deps,
        make_photo_message(caption="ponle un sombrero", file_id="FAKE:photo_edit", message_id=5),
    )
    methods = [c["method"] for c in deps.gateway.calls]
    assert "get_file_bytes" in methods
    gets = deps.gateway.calls_by_method("get_file_bytes")
    assert gets[-1]["file_id"] == "FAKE:photo_edit"
    sends = deps.gateway.calls_by_method("send_message")
    status = sends[-1]
    assert status["text"].startswith("Editando imagen con Seedream 5.0...")
    data = flat_callback_data(status["reply_markup"])
    assert data and data[0].startswith("cancel_job:"), "job edit lleva cancel_job:<id>"
    assert deps.gateway.calls_by_method("send_photo")


async def test_reply_to_kie_photo_uses_ref_without_download():
    deps = make_deps()
    # cfg default grok/kie: reply-edit no confirma y usa KieTaskRef del refs.
    ref_photo = make_photo_message(message_id=9000)
    deps.refs.save(_CHAT, 9000, kie_task_id="task-kie-1", kie_index=2, provider="kie", kind="image")
    reply_text = text_message(
        "hazla sonreir", message_id=6, reply_to_message=ref_photo
    )
    deps = await _msg(deps, reply_text)
    methods = [c["method"] for c in deps.gateway.calls]
    assert "get_file_bytes" not in methods, "reply a imagen kie del bot no re-descarga"
    assert deps.gateway.calls_by_method("send_photo"), "esperaba el resultado en photo"


async def test_regen_valid_context():
    deps = make_deps()
    ref_photo = make_photo_message(message_id=7000)
    deps.refs.save(
        _CHAT, 7000,
        provider="replicate", kind="image", prompt="retrato de pie",
        regen={"model_key": "seedream", "mode": "text", "prompt": "retrato de pie"},
    )
    deps = await _cb(deps, callback_query("regen", message_id=7000, message=ref_photo))
    sends = deps.gateway.calls_by_method("send_message")
    assert any(s["text"].startswith("Regenerando imagen con Seedream 5.0...") for s in sends)
    assert deps.gateway.calls_by_method("send_photo")
    # el job regen se cerró
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_regen_without_context_expired():
    deps = make_deps()
    ref_photo = make_photo_message(message_id=7001)
    deps = await _cb(deps, callback_query("regen", message_id=7001, message=ref_photo))
    ans = deps.gateway.calls_by_method("answer_callback")[-1]
    assert ans["text"] == "No se puede regenerar (contexto expirado)."
    assert ans["show_alert"] is True


async def test_regen_on_non_photo_message_invalid():
    deps = make_deps()
    deps = await _cb(deps, callback_query("regen", message_id=4))
    ans = deps.gateway.calls_by_method("answer_callback")[-1]
    assert ans["text"] == "Mensaje no valido."
    assert ans["show_alert"] is True


async def test_faceswap_text_degrades():
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    deps = await _msg(deps, text_message("hola"))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == (
        "El modo Face Swap no está disponible en esta versión. Usa /config para cambiar de modelo."
    )


async def test_photo_integrate_degrades():
    deps = make_deps()
    deps = await _msg(deps, make_photo_message(caption="/s referencia extra", message_id=5))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert "La edición con referencia (/s) no está disponible en esta versión." in last["text"]


async def test_photo_long_caption_degrades():
    deps = make_deps()
    deps = await _msg(deps, make_photo_message(caption="x" * 1100, message_id=5))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert "El caption es demasiado largo." in last["text"]


async def test_album_with_caption_degrades():
    deps = make_deps()
    photo = make_photo_message(caption="un cambio", message_id=5, media_group_id="album-1")
    deps = await _msg(deps, photo)
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert "Los álbumes todavía no están disponibles en esta versión." in last["text"]


async def test_cancel_suppresses_media():
    """A4: un job cancelado antes de enviar el item suprime el media."""
    from grokbot.telegram.handlers.generation import _run_single_image

    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    cfg = deps.sessions.get_config(_UID)
    job = deps.job_manager.start(_UID, "edit")
    deps.job_manager.cancel(_UID, job.job_id)
    message = text_message("x", message_id=9)
    await _run_single_image(
        deps, message, cfg, "un prompt", uid=_UID, prefix="Edit", job=job
    )
    assert not deps.gateway.calls_by_method("send_photo"), "item cancelado no se envía"
    deps.job_manager.finish(_UID, job.job_id)
