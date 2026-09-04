"""Tests de handlers de generación de imagen (item 5): texto/foto/reply/regen.

Flujos NUCLEO con copy exacto de grok; 0 red / 0 ``unittest.mock`` (fakes del
conftest). Los asserts fijan strings transcritos byte a byte ("Generacion
cancelada." etc.). Fixtures anonimizados.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from conftest import (
    CHAT_ID,
    USER_ID,
    FakeImageProvider,
    FakeMediaDownloader,
    FakeTelegramGateway,
    callback_query,
    callback_update,
    flat_callback_data,
    make_deps,
    make_dispatcher,
    make_photo_message,
    make_registry,
    make_result,
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


# --------------------------------------------------------------------------- #
# Álbumes / media groups → colección + edición secuencial (Task 3)
# --------------------------------------------------------------------------- #
_ALBUM_LABEL = "Grok Imagine (Kie.ai • Alta calidad)"


async def _feed_album(deps, n: int, *, group: str = "album-1", caption_on: int | None = 1,
                      file_prefix: str = "FAKE:album"):
    """Alimentar un media group de ``n`` fotos (una sola dispatcher)."""
    dp, deps = make_dispatcher(deps)
    for i in range(1, n + 1):
        msg = make_photo_message(
            caption=("un cambio" if caption_on == i else None),
            message_id=100 + i,
            file_id=f"{file_prefix}{i}",
            media_group_id=group,
        )
        await dp.feed_update(_BOT, message_update(msg))
    return deps


async def test_album_grok_edits_sequentially():
    deps = make_deps()
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3)
    await _wait_until(lambda: any(
        c["text"] == "Completadas 3/3 imágenes."
        for c in deps.gateway.calls_by_method("edit_message_text")
    ))
    sends = deps.gateway.calls_by_method("send_message")
    status = sends[0]
    assert status["text"] == f"Editando 0/3 imágenes con {_ALBUM_LABEL}..."
    data = flat_callback_data(status["reply_markup"])
    assert data and data[0].startswith("cancel_job:"), "status de álbum lleva cancel_job:<id>"
    edits = [c["text"] for c in deps.gateway.calls_by_method("edit_message_text")]
    assert f"Editando 1/3 imágenes con {_ALBUM_LABEL}..." in edits
    assert f"Editando 2/3 imágenes con {_ALBUM_LABEL}..." in edits
    assert f"Editando 3/3 imágenes con {_ALBUM_LABEL}..." in edits
    assert edits[-1] == "Completadas 3/3 imágenes."
    assert len(deps.gateway.calls_by_method("send_photo")) == 3
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_album_no_caption_shows_hint():
    deps = make_deps()
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, caption_on=None)
    await _wait_until(lambda: any(
        s["text"].startswith("Para editar una imagen, enviala con un")
        for s in deps.gateway.calls_by_method("send_message")
    ))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"].startswith("Para editar una imagen, enviala con un")
    assert deps.gateway.calls_by_method("send_photo") == []


async def test_album_too_many_photos_errors():
    deps = make_deps()
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 11, group="album-big")
    await _wait_until(lambda: any(
        s["text"] == "El album tiene 11 fotos; el maximo es 10."
        for s in deps.gateway.calls_by_method("send_message")
    ))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == "El album tiene 11 fotos; el maximo es 10."
    assert deps.gateway.calls_by_method("send_photo") == []


async def test_album_non_grok_model_silent():
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, caption_on=1)
    # Sin tarea de drain (handle_album no agenda nada para no-grok): asserts directos.
    assert deps.gateway.calls_by_method("send_message") == []
    assert deps.gateway.calls_by_method("send_photo") == []


async def test_album_faceswap_degrades():
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 2, caption_on=1)
    # Cada foto faceswap degrada en handle_album (sin drain): asserts directos.
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert "El modo Face Swap no está disponible en esta versión." in last["text"]


async def test_album_integrate_caption_still_d8():
    deps = make_deps()
    deps.album.delay = 0.05
    dp, deps = make_dispatcher(deps)
    msg = make_photo_message(
        caption="/s referencia extra", message_id=201, media_group_id="album-int"
    )
    await dp.feed_update(_BOT, message_update(msg))
    msg2 = make_photo_message(message_id=202, media_group_id="album-int")
    await dp.feed_update(_BOT, message_update(msg2))
    await _wait_until(lambda: any(
        "La edición con referencia (/s) no está disponible en esta versión." in s["text"]
        for s in deps.gateway.calls_by_method("send_message")
    ))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert "La edición con referencia (/s) no está disponible en esta versión." in last["text"]


async def test_album_long_caption_defers_to_text():
    deps = make_deps()
    deps.album.delay = 0.05
    dp, deps = make_dispatcher(deps)
    for i in (301, 302, 303):
        cap = "x" * 1100 if i == 301 else None
        msg = make_photo_message(
            caption=cap, message_id=i,
            file_id=f"FAKE:albumlong{i}", media_group_id="album-long",
        )
        await dp.feed_update(_BOT, message_update(msg))
    await _wait_until(lambda: deps.long_prompt.is_awaiting(_UID))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"].startswith("El caption es demasiado largo para procesarlo directamente.")
    assert "He guardado tus 3 fotos del álbum." in last["text"]
    assert deps.long_prompt.is_awaiting(_UID) is True
    assert deps.gateway.calls_by_method("send_photo") == []


class _StagedAlbumProvider(FakeImageProvider):
    """Fake kie provider que pausa en CADA generate hasta que el test lo suelta.

    Expone ``started`` (un evento por llamada, seteado al entrar a generate) y
    ``releases`` (un evento por llamada, para abrir la compuerta). Permite el
    cancel-mid deterministico: item 1 completa, item 2 queda bloqueado en la
    compuerta, el test cancela y recién ahí suelta la compuerta.
    """

    def __init__(self) -> None:
        super().__init__(name="kie")
        self.started: list[asyncio.Event] = []
        self.releases: list[asyncio.Event] = []

    async def generate(self, request, *, source_image=None):
        started = asyncio.Event()
        release = asyncio.Event()
        self.started.append(started)
        self.releases.append(release)
        started.set()
        await release.wait()
        return make_result(provider="kie", model_id=request.model_id)


async def _wait_event(event: asyncio.Event, timeout: float = 2.0) -> None:
    await asyncio.wait_for(event.wait(), timeout=timeout)


async def _wait_until(pred, timeout: float = 2.0) -> None:
    """Polling corto hasta que ``pred()`` sea verdadero (evita race del drain)."""
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.005)


async def test_album_cancel_mid_way():
    provider = _StagedAlbumProvider()
    deps = make_deps(registry=make_registry(kie=provider))
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, file_prefix="FAKE:alb_cancel")
    # El ítem 1 queda bloqueado en su compuerta: soltarlo para que complete.
    await _wait_until(lambda: len(provider.started) >= 1)
    provider.releases[0].set()
    # El ítem 2 arranca y se bloquea en su compuerta → cancelar ahí.
    await _wait_until(lambda: len(provider.started) >= 2)
    status = deps.gateway.calls_by_method("send_message")[0]
    job_id = flat_callback_data(status["reply_markup"])[0].split(":", 1)[1]
    deps = await _cb(
        deps, callback_query(f"cancel_job:{job_id}", message_id=status["sent"].message_id)
    )
    provider.releases[1].set()
    await _wait_until(lambda: any(
        c["text"] == "⏹ Cancelado. Completadas 1/3 imágenes."
        for c in deps.gateway.calls_by_method("edit_message_text")
    ))
    edits = [c["text"] for c in deps.gateway.calls_by_method("edit_message_text")]
    assert "⏹ Cancelado. Completadas 1/3 imágenes." in edits
    assert len(deps.gateway.calls_by_method("send_photo")) == 1, "solo el ítem 1 completo"
    assert deps.job_manager.active_jobs(_UID) == ()


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


# --------------------------------------------------------------------------- #
# Fix round: contención del drain + gaps de test-guardian
# --------------------------------------------------------------------------- #
class _AlbumSendFailsGateway(FakeTelegramGateway):
    """Gateway que falla al enviar la foto del ítem (TelegramBadRequest)."""

    async def send_photo(self, chat_id, photo, *, filename="generated.png", caption=None,
                         parse_mode="HTML", reply_markup=None, reply_to_message_id=None):
        self._record(
            "send_photo", chat_id=chat_id, photo=photo, filename=filename, caption=caption,
            parse_mode=parse_mode, reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )
        raise TelegramBadRequest("chat not found")


async def _feed_album_long_caption(deps, *, prefix: str = "FAKE:albumlong", group: str = "album-long"):
    """Álbum de 3 fotos donde la primera trae caption > 1020 (→ long-prompt)."""
    dp, deps = make_dispatcher(deps)
    for i in (301, 302, 303):
        cap = "x" * 1100 if i == 301 else None
        msg = make_photo_message(
            caption=cap, message_id=i, file_id=f"{prefix}{i}", media_group_id=group
        )
        await dp.feed_update(_BOT, message_update(msg))
    return deps


async def test_album_whitespace_caption_shows_hint():
    """Fix round: caption de solo espacios NO es prompt → hint de edición."""
    deps = make_deps()
    deps.album.delay = 0.05
    dp, deps = make_dispatcher(deps)
    for i in (401, 402, 403):
        cap = "   " if i == 401 else None
        msg = make_photo_message(
            caption=cap, message_id=i, file_id=f"FAKE:alb_ws{i}", media_group_id="album-ws"
        )
        await dp.feed_update(_BOT, message_update(msg))
    await _wait_until(lambda: any(
        s["text"].startswith("Para editar una imagen, enviala con un")
        for s in deps.gateway.calls_by_method("send_message")
    ))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"].startswith("Para editar una imagen, enviala con un")
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_album_unexpected_send_error_degrades_status():
    """Fix round: excepción inesperada en el drain edita el status user-safe."""
    gateway = _AlbumSendFailsGateway()
    deps = make_deps(gateway=gateway)
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, file_prefix="FAKE:alb_err", caption_on=1)
    await _wait_until(lambda: any(
        c["text"] == "Ocurrió un error inesperado procesando las imágenes. Inténtalo de nuevo."
        for c in deps.gateway.calls_by_method("edit_message_text")
    ))
    assert deps.job_manager.active_jobs(_UID) == (), "el job se cierra en finally"
    assert len(deps.gateway.calls_by_method("send_photo")) == 1, "el primer ítem intentó enviar"


async def test_album_long_caption_text_completes_multi_edit():
    """Fix round (GAP-1): texto pendiente completa la edición del álbum (multi-foto)."""
    deps = make_deps()
    deps.album.delay = 0.05
    deps = await _feed_album_long_caption(deps, prefix="FAKE:alb_lp_multi")
    await _wait_until(lambda: deps.long_prompt.is_awaiting(_UID))
    assert deps.long_prompt.is_awaiting(_UID) is True
    deps = await _msg(deps, text_message("un prompt que edita el album", message_id=500))
    assert deps.gateway.calls_by_method("send_photo"), "edita las 3 fotos"
    edits = [c["text"] for c in deps.gateway.calls_by_method("edit_message_text")]
    assert edits[-1] == "Completadas 3/3 imágenes."
    assert deps.long_prompt.is_awaiting(_UID) is False, "consumo único (A3)"
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_long_caption_video_defers_to_text_then_sends_video():
    """Fix round (GAP-2): long-prompt con modelo video → texto completa i2v."""
    deps = make_deps()
    deps.update_config.set_model(_UID, "grok_video")
    deps = await _msg(
        deps, make_photo_message(caption="x" * 1100, file_id="FAKE:video_src", message_id=5)
    )
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert "para animar la imagen" in last["text"]
    assert deps.long_prompt.is_awaiting(_UID) is True
    assert deps.gateway.calls_by_method("send_video") == [], "todavía no genera"
    deps = await _msg(deps, text_message("mueve la camara lentamente", message_id=6))
    assert deps.gateway.calls_by_method("send_video"), "el texto completa la i2v"
    assert deps.long_prompt.is_awaiting(_UID) is False


# --------------------------------------------------------------------------- #
# Fix round 2 (review 571e1bc9): opens 1, 2, 4, 5
# --------------------------------------------------------------------------- #
class _AlbumStatusSendFailsGateway(FakeTelegramGateway):
    """Gateway que falla al publicar el status inicial del álbum (send_message)."""

    async def send_message(self, chat_id, text, *, parse_mode="HTML", reply_markup=None, reply_to_message_id=None):
        self._record(
            "send_message", chat_id=chat_id, text=text, parse_mode=parse_mode,
            reply_markup=reply_markup, reply_to_message_id=reply_to_message_id,
        )
        raise TelegramBadRequest("chat not found")


async def test_long_caption_invalid_text_keeps_collection():
    """Open 4: texto inválido tras caption largo NO pierde la colección (A3)."""
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    deps = await _msg(
        deps, make_photo_message(caption="x" * 1100, file_id="FAKE:keep_cap", message_id=5)
    )
    assert deps.long_prompt.is_awaiting(_UID) is True
    deps = await _msg(deps, text_message("ab", message_id=6))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == "El prompt es muy corto. Dame algo mas descriptivo."
    assert deps.long_prompt.is_awaiting(_UID) is True, "texto inválido no consume la colección"
    # El reintento con un texto válido completa la edición.
    deps = await _msg(deps, text_message("un prompt válido ahora", message_id=7))
    assert deps.gateway.calls_by_method("send_photo"), "el texto válido completa la edición"
    assert deps.long_prompt.is_awaiting(_UID) is False


async def test_album_delivery_failure_does_not_claim_completed():
    """Open 1: send_image None (entrega fallida) NO cuenta como completada.

    La descarga de la URL del ítem falla: send_image edita el status con el error
    user-safe y devuelve None; el álbum NO debe afirmar "Completadas N/N".
    """
    downloader = FakeMediaDownloader()
    downloader.error = Exception("transporte caido")
    deps = make_deps(downloader=downloader)
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, file_prefix="FAKE:alb_deliv", caption_on=1)
    await _wait_until(lambda: any(
        c["text"] == "No se pudo descargar el archivo. Intenta de nuevo más tarde."
        for c in deps.gateway.calls_by_method("edit_message_text")
    ))
    edits = [c["text"] for c in deps.gateway.calls_by_method("edit_message_text")]
    assert not any(t.startswith("Completadas ") for t in edits)
    assert deps.gateway.calls_by_method("send_photo") == [], "el ítem 1 nunca llegó a enviarse"
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_album_status_send_failure_closes_job():
    """Open 2: si el status inicial lanza, el job album_edit se cierra igual.

    El drain arranca el job y el status lanza; todo el tramo (start → status →
    except → finally:finish) corre en un único burst síncrono sin suspensión que
    un poll de active_jobs pueda observar, así que esperamos el efecto lateral
    observable: post-fix hay DOS send_message (el status que falla + el fallback
    best-effort del error genérico) y el job queda cerrado. Pre-fix la excepción
    del status escapa del try (job huérfano + task muerto) y jamás llega el
    segundo send_message → la espera se cuelga y el test falla.
    """
    gateway = _AlbumStatusSendFailsGateway()
    deps = make_deps(gateway=gateway)
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, file_prefix="FAKE:alb_status", caption_on=1)
    await _wait_until(lambda: len(deps.gateway.calls_by_method("send_message")) >= 2)
    assert deps.job_manager.active_jobs(_UID) == (), "el finally cierra el job en cualquier salida"


async def test_album_item_failed_breaks_partial():
    """Open 5: ItemFailed (error terminal del provider) corta el álbum en parcial."""
    from grokbot.providers.base import ProviderInputError

    err = ProviderInputError("source inválida", user_message="La imagen de origen no es válida.")
    provider = FakeImageProvider(name="kie", outcomes=[err])
    deps = make_deps(registry=make_registry(kie=provider))
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, file_prefix="FAKE:alb_fail", caption_on=1)
    await _wait_until(lambda: any(
        c["text"] == "0/3 completadas; error en imagen 1: La imagen de origen no es válida."
        for c in deps.gateway.calls_by_method("edit_message_text")
    ))
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_album_retry_scheduled_paints_intento_and_completes(monkeypatch):
    """Open 5: RetryScheduled pinta el intento sobre el label y el loop completa."""
    import grokbot.providers.base as prov_base
    from grokbot.providers.base import ProviderRateLimitError

    monkeypatch.setattr(prov_base, "POLL_RETRY_BACKOFF_SEC", (0.0, 0.0, 0.0))
    err = ProviderRateLimitError("rate", user_message="Demasiadas peticiones.")
    provider = FakeImageProvider(name="kie", outcomes=[err])
    deps = make_deps(registry=make_registry(kie=provider))
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 1, caption_on=1, group="album-retry")
    await _wait_until(lambda: len(deps.gateway.calls_by_method("send_photo")) == 1)
    edits = [c["text"] for c in deps.gateway.calls_by_method("edit_message_text")]
    assert any("(intento 2/6)" in t for t in edits)
    assert edits[-1] == "Completadas 1/1 imágenes."
    assert deps.job_manager.active_jobs(_UID) == ()
