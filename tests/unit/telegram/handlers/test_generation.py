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
from grokbot.telegram.handlers._common import SOURCE_MEDIA_UNAVAILABLE_MSG

_UID = USER_ID
_OTHER = 222222222  # segundo user anonimizado (R8: owner ajeno al ref)
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


_OTHER = 222222222
_GROUP = 333333333


async def test_group_other_user_cannot_confirm_others_prompt():
    """C4: en un grupo, otro usuario no consume ni ve la confirmación ajena."""
    deps = make_deps()
    # A ofrece un prompt en el grupo.
    deps = await _msg(
        deps,
        text_message("retrato de pie", user_id=_UID, chat_id=_GROUP, chat_type="group", message_id=10),
    )
    confirm = deps.gateway.calls_by_method("send_message")[-1]
    mid = confirm["sent"].message_id
    assert deps.pending.get(_UID) is not None

    # B clickea Confirmar sobre el mensaje de A.
    deps = await _cb(
        deps,
        callback_query("confirm:yes", user_id=_OTHER, chat_id=_GROUP, chat_type="group", message_id=mid),
    )
    ans = deps.gateway.calls_by_method("answer_callback")[-1]
    assert ans["text"] == "Esta confirmación pertenece a otro usuario."
    assert ans["show_alert"] is True
    # el pendiente de A sigue intacto y no se generó nada para B.
    assert deps.pending.get(_UID) is not None
    assert deps.gateway.calls_by_method("send_photo") == []
    # el mensaje de confirmación de A no se tocó.
    assert deps.gateway.calls_by_method("edit_message_text") == []


async def test_group_other_user_cannot_cancel_others_prompt():
    """C4: en un grupo, otro usuario no cancela la confirmación ajena."""
    deps = make_deps()
    deps = await _msg(
        deps,
        text_message("retrato de pie", user_id=_UID, chat_id=_GROUP, chat_type="group", message_id=11),
    )
    confirm = deps.gateway.calls_by_method("send_message")[-1]
    mid = confirm["sent"].message_id

    deps = await _cb(
        deps,
        callback_query("confirm:no", user_id=_OTHER, chat_id=_GROUP, chat_type="group", message_id=mid),
    )
    ans = deps.gateway.calls_by_method("answer_callback")[-1]
    assert ans["text"] == "Esta confirmación pertenece a otro usuario."
    assert deps.pending.get(_UID) is not None, "A sigue pendiente"
    assert not any(
        c["text"] == "Generacion cancelada."
        for c in deps.gateway.calls_by_method("edit_message_text")
    )


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


async def test_photo_caption_edit_source_fetch_failure_degrades():
    """M2: file_id expirado en foto+caption de edición → mensaje user-safe, sin job."""
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    deps = await _msg(
        deps,
        make_photo_message(caption="ponle un sombrero", file_id="EXPIRED:photo_edit", message_id=5),
    )
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == SOURCE_MEDIA_UNAVAILABLE_MSG
    assert "EXPIRED" not in last["text"], "no filtra el file_id al usuario"
    assert deps.gateway.calls_by_method("send_photo") == [], "sin edición no se genera"
    assert deps.job_manager.active_jobs(_UID) == (), "no se abre job sin fuente"


async def test_reply_edit_source_fetch_failure_degrades():
    """M2: reply a foto del bot con file_id roto (no-kie) → degrada user-safe."""
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    reply_photo = make_photo_message(message_id=9000, file_id="EXPIRED:reply_src")
    deps = await _msg(
        deps,
        text_message("hazla sonreir", message_id=6, reply_to_message=reply_photo),
    )
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == SOURCE_MEDIA_UNAVAILABLE_MSG
    assert deps.gateway.calls_by_method("send_photo") == []


async def test_regen_edit_source_fetch_failure_degrades():
    """M2: regen en modo edit con file_id expirado → status degradado y job cerrado."""
    deps = make_deps()
    ref_photo = make_photo_message(message_id=7002)
    deps.refs.save(
        _CHAT, 7002,
        provider="replicate", kind="image", prompt="retrato de pie",
        regen={
            "model_key": "seedream", "mode": "edit",
            "prompt": "retrato de pie", "source_file_id": "EXPIRED:regen_src",
        },
        owner_uid=_UID,
    )
    deps = await _cb(deps, callback_query("regen", message_id=7002, message=ref_photo))
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == SOURCE_MEDIA_UNAVAILABLE_MSG
    assert edits[-1]["reply_markup"] is None
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.job_manager.active_jobs(_UID) == (), "el job regen se cierra en finally"


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
        owner_uid=_UID,
    )
    deps = await _cb(deps, callback_query("regen", message_id=7000, message=ref_photo))
    sends = deps.gateway.calls_by_method("send_message")
    assert any(s["text"].startswith("Regenerando imagen con Seedream 5.0...") for s in sends)
    assert deps.gateway.calls_by_method("send_photo")
    # el job regen se cerró
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_regen_other_owner_denied_no_job():
    """R8: un click de OTRO user sobre una imagen con owner_uid ajeno no regenera.

    Misma mecánica que C4 (confirmación): alerta, sin tocar el mensaje del dueño
    ni arrancar el job de regeneración (que reprocesaría el source ajeno).
    """
    deps = make_deps()
    ref_photo = make_photo_message(message_id=7010)
    deps.refs.save(
        _CHAT, 7010,
        provider="replicate", kind="image", prompt="retrato de pie",
        regen={"model_key": "seedream", "mode": "edit",
               "prompt": "retrato de pie", "source_file_id": "FAKE:owner_src"},
        owner_uid=_OTHER,
    )
    deps = await _cb(deps, callback_query("regen", message_id=7010, message=ref_photo))
    ans = deps.gateway.calls_by_method("answer_callback")[-1]
    assert ans["text"] == "Esta regeneración pertenece a otro usuario."
    assert ans["show_alert"] is True
    # sin status de regeneración, sin photo, sin job, sin editar el mensaje ajeno.
    assert not any(s["text"].startswith("Regenerando imagen") for s in deps.gateway.calls_by_method("send_message"))
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.gateway.calls_by_method("edit_message_text") == []
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_regen_owner_mismatch_ref_without_owner_uid_allows_legacy():
    """R8: ref legacy sin owner_uid no bloquea (bot privado de un solo dueño)."""
    deps = make_deps()
    ref_photo = make_photo_message(message_id=7011)
    deps.refs.save(
        _CHAT, 7011,
        provider="replicate", kind="image", prompt="retrato de pie",
        regen={"model_key": "seedream", "mode": "text", "prompt": "retrato de pie"},
    )
    deps = await _cb(deps, callback_query("regen", message_id=7011, message=ref_photo))
    sends = deps.gateway.calls_by_method("send_message")
    assert any(s["text"].startswith("Regenerando imagen con Seedream 5.0...") for s in sends)
    assert deps.gateway.calls_by_method("send_photo")
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


async def test_long_caption_starts_collection_reply():
    deps = make_deps()  # cfg default grok
    deps = await _msg(deps, make_photo_message(caption="x" * 1100, message_id=5))
    sends = deps.gateway.calls_by_method("send_message")
    assert sends[-1]["text"].startswith(
        "El caption es demasiado largo para procesarlo directamente."
    )
    assert "para editar la imagen" in sends[-1]["text"]
    assert deps.gateway.calls_by_method("send_photo") == [], "no edita todavía"
    assert deps.long_prompt.is_awaiting(_UID) is True


async def test_long_caption_then_text_completes_edit():
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    deps = await _msg(
        deps,
        make_photo_message(caption="x" * 1100, file_id="FAKE:long_cap", message_id=5),
    )
    assert deps.long_prompt.is_awaiting(_UID) is True
    deps = await _msg(deps, text_message("un prompt corto", message_id=6))
    gets = deps.gateway.calls_by_method("get_file_bytes")
    assert gets and gets[-1]["file_id"] == "FAKE:long_cap"
    assert deps.gateway.calls_by_method("send_photo"), "el texto completo la edición"
    assert deps.long_prompt.is_awaiting(_UID) is False


async def test_short_caption_does_not_collect():
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    deps = await _msg(deps, make_photo_message(caption="c" * 100, message_id=5))
    sends = deps.gateway.calls_by_method("send_message")
    assert not any("El caption es demasiado largo" in s["text"] for s in sends)
    assert deps.gateway.calls_by_method("send_photo")
    assert deps.long_prompt.is_awaiting(_UID) is False


async def test_integrate_still_beats_long():
    deps = make_deps()
    deps = await _msg(
        deps, make_photo_message(caption="/s " + "x" * 1100, message_id=5)
    )
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert "La edición con referencia (/s) no está disponible en esta versión." in last["text"]
    assert deps.long_prompt.is_awaiting(_UID) is False


async def test_photo_no_caption_while_awaiting_reminds():
    deps = make_deps()
    deps.long_prompt.set(_UID, file_ids=["FAKE:photo1"], integrate_mode=False, is_video=False)
    deps = await _msg(deps, make_photo_message(message_id=7))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"].startswith("Tienes una edición pendiente.")
    assert deps.long_prompt.is_awaiting(_UID) is True, "el recordatorio no consume la colección"


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
