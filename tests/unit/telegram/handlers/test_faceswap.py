"""Tests de los flujos Face Swap reales (R4 Item 2, Task 3) — /cambiar_source,
guías de texto/reply, foto single/álbum, confirm dedicado, progreso + cancel.

0 red / 0 ``unittest.mock``: fakes del conftest (FakeTelegramGateway,
FakeSourceFacesRepo, FakeImageProvider). El rate-limit real (10s) entre ítems
del batch se neutraliza en los tests que lo necesitan con un ``asyncio.sleep``
corto vía ``monkeypatch`` (fixture de pytest, permitido por el pool). Los
asserts fijan copy byte-parity de grok (typos/acentos originales) y terminales
user-safe (A8: nunca file_ids/URLs). Fixtures anonimizados.
"""

from __future__ import annotations

import asyncio
import dataclasses

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    FakeImageProvider,
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
from grokbot.providers.base import ProviderUnavailableError

_UID = USER_ID
_OTHER = 222222222  # segundo user anonimizado (R8/C4)
_CHAT = CHAT_ID
_GROUP = 333333333
_BOT = Bot("42:TEST")
_SOURCE_BYTES = b"src-bytes"


async def _msg(deps, message):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(message))
    return deps


async def _cb(deps, callback):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, callback_update(callback))
    return deps


async def _wait_until(pred, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.005)


def _set_faceswap(deps, *, source: bool = False):
    deps.update_config.set_model(_UID, "faceswap")
    if source:
        deps.source_faces.save_source(_UID, _SOURCE_BYTES)
    return deps


async def _feed_album(deps, n: int, *, group: str = "faceswap-album",
                      file_prefix: str = "FAKE:faceswap_a"):
    """Alimentar un media group de ``n`` fotos faceswap (un solo dispatcher)."""
    dp, deps = make_dispatcher(deps)
    for i in range(1, n + 1):
        msg = make_photo_message(
            message_id=100 + i,
            file_id=f"{file_prefix}{i}",
            media_group_id=group,
        )
        await dp.feed_update(_BOT, message_update(msg))
    return deps


# --------------------------------------------------------------------------- #
# /cambiar_source
# --------------------------------------------------------------------------- #
async def test_cambiar_source_other_model_copy():
    deps = make_deps()  # cfg default grok
    deps = await _msg(deps, text_message("/cambiar_source"))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == (
        "Este comando solo esta disponible en modo <b>Face Swap</b>.\n"
        "Usa /config para cambiar al modo Face Swap."
    )
    cfg = deps.sessions.get_config(_UID)
    assert cfg.state == "IDLE", "no se cambia a AWAITING_SOURCE fuera de faceswap"


async def test_cambiar_source_faceswap_prompt_and_awaiting():
    deps = _set_faceswap(make_deps())
    deps = await _msg(deps, text_message("/cambiar_source"))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == "Envia tu foto source (la cara que quieres usar para el swap)."
    assert deps.sessions.get_config(_UID).state == "AWAITING_SOURCE"


# --------------------------------------------------------------------------- #
# Guías de texto / reply
# --------------------------------------------------------------------------- #
async def test_text_guidance_without_source():
    deps = _set_faceswap(make_deps())
    deps = await _msg(deps, text_message("hola"))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == (
        "Primero configura tu cara fuente con /cambiar_source.\n"
        "Luego enviame fotos para intercambiar las caras."
    )


async def test_text_guidance_with_source():
    deps = _set_faceswap(make_deps(), source=True)
    deps = await _msg(deps, text_message("hola"))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == (
        "Envia una <b>foto</b> para hacer el face swap.\n"
        "Usa /cambiar_source si quieres cambiar la cara fuente."
    )


async def test_reply_text_to_photo_faceswap_not_used():
    deps = _set_faceswap(make_deps())
    reply_photo = make_photo_message(message_id=9000, file_id="FAKE:reply_target")
    deps = await _msg(
        deps,
        text_message("algo", message_id=6, reply_to_message=reply_photo),
    )
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == (
        "En modo Face Swap no se usa reply con texto.\n"
        "Simplemente envia la foto directamente para hacer el swap."
    )


async def test_reply_text_to_text_faceswap_silent():
    """Reply de texto a un TEXTO (no foto) en faceswap → silencio (parity 2957-2965)."""
    deps = _set_faceswap(make_deps())
    reply_text = text_message("otro", message_id=9100)
    deps = await _msg(
        deps,
        text_message("algo", message_id=6, reply_to_message=reply_text),
    )
    assert deps.gateway.calls_by_method("send_message") == []


# --------------------------------------------------------------------------- #
# Foto single (source-save / confirm)
# --------------------------------------------------------------------------- #
async def test_photo_single_without_source_guides():
    deps = _set_faceswap(make_deps())
    deps = await _msg(deps, make_photo_message(file_id="FAKE:single", message_id=5))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == "Primero configura tu cara fuente con /cambiar_source."
    assert deps.faceswap_pending.get(_UID) is None


async def test_photo_single_in_awaiting_source_saves_source():
    deps = _set_faceswap(make_deps())
    deps.source_faces.begin_awaiting_source(_UID)
    deps = await _msg(deps, make_photo_message(file_id="FAKE:source_photo", message_id=7))
    gets = deps.gateway.calls_by_method("get_file_bytes")
    assert gets and gets[-1]["file_id"] == "FAKE:source_photo"
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == "Source actualizado. Ahora envia tus fotos para hacer face swap."
    cfg = deps.sessions.get_config(_UID)
    assert cfg.source_path is not None and cfg.state == "IDLE"
    assert deps.source_faces.source_available(_UID) is True


async def test_photo_single_with_source_confirms():
    deps = _set_faceswap(make_deps(), source=True)
    deps = await _msg(
        deps,
        make_photo_message(file_id="FAKE:single", caption="un caption que se ignora", message_id=5),
    )
    confirm = deps.gateway.calls_by_method("send_message")[-1]
    assert confirm["text"] == "¿Confirmas hacer face swap con esta imagen?"
    assert flat_callback_data(confirm["reply_markup"]) == [
        "faceswap:confirm:yes", "faceswap:confirm:no",
    ]
    stored = deps.faceswap_pending.get(_UID)
    assert stored == ["FAKE:single"]


async def test_photo_single_source_path_stale_clears_and_missing():
    """A4: source_path configurado pero sin archivo → clear + SOURCE_MISSING_SINGLE."""
    deps = make_deps()
    cfg = deps.sessions.get_config(_UID)
    deps.sessions.save_config(
        _UID, dataclasses.replace(cfg, model="faceswap", source_path="/stale/source.jpg")
    )
    deps = await _msg(deps, make_photo_message(file_id="FAKE:single", message_id=8))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == "Source no encontrado. Usa /cambiar_source para configurar de nuevo."
    assert deps.sessions.get_config(_UID).source_path is None, "A4 limpia el path stale"


# --------------------------------------------------------------------------- #
# Álbum
# --------------------------------------------------------------------------- #
async def test_album_with_source_confirms_n():
    deps = _set_faceswap(make_deps(), source=True)
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3)
    await _wait_until(lambda: deps.faceswap_pending.get(_UID) is not None)
    confirm = deps.gateway.calls_by_method("send_message")[-1]
    assert confirm["text"] == "¿Confirmas hacer face swap con estas 3 imágenes?"
    assert flat_callback_data(confirm["reply_markup"]) == [
        "faceswap:confirm:yes", "faceswap:confirm:no",
    ]
    assert deps.faceswap_pending.get(_UID) == ["FAKE:faceswap_a1", "FAKE:faceswap_a2", "FAKE:faceswap_a3"]


async def test_album_in_awaiting_source_saves_last_photo_once():
    deps = _set_faceswap(make_deps())
    deps.source_faces.begin_awaiting_source(_UID)
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, file_prefix="FAKE:faceswap_src")
    await _wait_until(lambda: any(
        s["text"] == "Source actualizado. Ahora envia tus fotos para hacer face swap."
        for s in deps.gateway.calls_by_method("send_message")
    ))
    saved = [
        s["text"]
        for s in deps.gateway.calls_by_method("send_message")
        if s["text"] == "Source actualizado. Ahora envia tus fotos para hacer face swap."
    ]
    assert len(saved) == 1, "A2: una sola respuesta de source-save (final-wins)"
    gets = deps.gateway.calls_by_method("get_file_bytes")
    assert gets[-1]["file_id"] == "FAKE:faceswap_src3", "la última foto del álbum es el source"
    cfg = deps.sessions.get_config(_UID)
    assert cfg.source_path is not None and cfg.state == "IDLE"


async def test_album_without_source_guides_once():
    deps = _set_faceswap(make_deps())
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 2)
    await _wait_until(lambda: any(
        s["text"] == "Primero configura tu cara fuente con /cambiar_source."
        for s in deps.gateway.calls_by_method("send_message")
    ))
    guided = [
        s["text"]
        for s in deps.gateway.calls_by_method("send_message")
        if s["text"] == "Primero configura tu cara fuente con /cambiar_source."
    ]
    assert len(guided) == 1, "A2: sin source se guía una sola vez desde el drain"


# --------------------------------------------------------------------------- #
# Confirm dedicado (faceswap:confirm:*)
# --------------------------------------------------------------------------- #
async def test_confirm_yes_single_success():
    provider = FakeImageProvider(name="replicate")
    deps = _set_faceswap(make_deps(registry=make_registry(replicate=provider)), source=True)
    deps = await _msg(deps, make_photo_message(file_id="FAKE:single", message_id=5))
    confirm = deps.gateway.calls_by_method("send_message")[-1]
    mid = confirm["sent"].message_id

    deps = await _cb(deps, callback_query("faceswap:confirm:yes", message_id=mid))
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[0]["text"].startswith("Face swap\n")
    assert edits[0]["text"].endswith("Imagen 1/1 en Replicate...")
    assert flat_callback_data(edits[0]["reply_markup"])[0].startswith("cancel_job:")
    assert edits[-1]["text"].endswith("Procesada 1 imagen.")
    photos = deps.gateway.calls_by_method("send_photo")
    assert len(photos) == 1 and photos[0]["filename"] == "faceswap.jpg"
    assert len(provider.swap_face_calls) == 1
    assert provider.swap_face_calls[0][0] == _SOURCE_BYTES, "swap_image es la cara fuente"
    assert provider.swap_face_calls[0][1] == b"fake-file-bytes", "input_image es la foto target"
    assert deps.faceswap_pending.get(_UID) is None
    assert deps.job_manager.active_jobs(_UID) == (), "el job se cierra en finally"


async def test_confirm_yes_batch_sends_media_group(monkeypatch):
    real_sleep = asyncio.sleep

    async def _short_sleep(delay):
        if delay and delay >= 1.0:  # neutraliza el rate-limit real (10s) del batch
            await real_sleep(0)
        else:
            await real_sleep(delay)

    monkeypatch.setattr(asyncio, "sleep", _short_sleep)
    provider = FakeImageProvider(name="replicate")
    deps = _set_faceswap(make_deps(registry=make_registry(replicate=provider)), source=True)
    deps.faceswap_pending.set(
        _UID, ["FAKE:b1", "FAKE:b2", "FAKE:b3"], chat_id=_CHAT, message_id=888
    )
    deps = await _cb(deps, callback_query("faceswap:confirm:yes", message_id=888))
    groups = deps.gateway.calls_by_method("send_media_group")
    assert len(groups) == 1 and len(groups[0]["media"]) == 3
    edits = [c["text"] for c in deps.gateway.calls_by_method("edit_message_text")]
    assert edits[-1].endswith("Procesadas 3/3 imagenes.")
    assert any("Imagen 1/3 en Replicate..." in t for t in edits)
    assert any("Imagen 2/3 en Replicate..." in t for t in edits)
    assert len(provider.swap_face_calls) == 3
    assert deps.faceswap_pending.get(_UID) is None
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_confirm_yes_single_source_fetch_failure_degrades():
    """M2: file_id expirado en el swap → status user-safe y job cerrado."""
    deps = _set_faceswap(make_deps(), source=True)
    deps.faceswap_pending.set(_UID, ["EXPIRED:broken"], chat_id=_CHAT, message_id=889)
    deps = await _cb(deps, callback_query("faceswap:confirm:yes", message_id=889))
    edits = deps.gateway.calls_by_method("edit_message_text")
    last = edits[-1]
    assert last["text"] == (
        "No se pudo recuperar la imagen. Puede que el archivo haya expirado. "
        "Envíala de nuevo."
    )
    assert "EXPIRED" not in last["text"], "A8: no filtra file_ids"
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_confirm_no_cancels_confirmation():
    deps = _set_faceswap(make_deps(), source=True)
    deps = await _msg(deps, make_photo_message(file_id="FAKE:single", message_id=5))
    confirm = deps.gateway.calls_by_method("send_message")[-1]
    mid = confirm["sent"].message_id

    deps = await _cb(deps, callback_query("faceswap:confirm:no", message_id=mid))
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "Generacion cancelada."
    assert deps.faceswap_pending.get(_UID) is None
    assert deps.gateway.calls_by_method("send_photo") == []


async def test_confirm_yes_stale_model_edits_no_pending():
    """A1: confirm faceswap stale (model ya no es faceswap) → _NO_PENDING sin swap."""
    provider = FakeImageProvider(name="replicate")
    deps = make_deps(registry=make_registry(replicate=provider))
    deps.update_config.set_model(_UID, "seedream")  # ya no faceswap
    deps.faceswap_pending.set(_UID, ["FAKE:stale"], chat_id=_CHAT, message_id=890)
    deps = await _cb(deps, callback_query("faceswap:confirm:yes", message_id=890))
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "Ya no hay nada pendiente. Envia una imagen o prompt nuevo."
    assert edits[-1]["reply_markup"] is None
    assert provider.swap_face_calls == []
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_confirm_yes_source_missing_clears_and_edits():
    """A4: sin archivo source en disco al confirmar → clear + _SOURCE_MISSING_SINGLE."""
    provider = FakeImageProvider(name="replicate")
    deps = make_deps(registry=make_registry(replicate=provider))
    cfg = deps.sessions.get_config(_UID)
    deps.sessions.save_config(
        _UID, dataclasses.replace(cfg, model="faceswap", source_path="/stale/source.jpg")
    )
    deps.faceswap_pending.set(_UID, ["FAKE:miss"], chat_id=_CHAT, message_id=891)
    deps = await _cb(deps, callback_query("faceswap:confirm:yes", message_id=891))
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "Source no encontrado. Usa /cambiar_source para configurar de nuevo."
    assert deps.sessions.get_config(_UID).source_path is None
    assert provider.swap_face_calls == []
    assert deps.job_manager.active_jobs(_UID) == ()


class _GatedFaceswapProvider(FakeImageProvider):
    """Provider que pausa en CADA swap_face hasta que el test lo suelta.

    ``started``/``releases`` son un evento por llamada. Permite el cancel-mid
    determinístico del batch: item 1 completa, item 2 queda en la compuerta, el
    test cancela y recién ahí suelta la compuerta.
    """

    def __init__(self) -> None:
        super().__init__(name="replicate")
        self.started: list[asyncio.Event] = []
        self.releases: list[asyncio.Event] = []

    async def swap_face(self, *, swap_image: bytes, input_image: bytes):
        self.swap_face_calls.append((swap_image, input_image))
        started = asyncio.Event()
        release = asyncio.Event()
        self.started.append(started)
        self.releases.append(release)
        started.set()
        await release.wait()
        return make_result(provider="replicate", model_id="fake-faceswap")


async def test_confirm_yes_batch_cancel_mid_way(monkeypatch):
    real_sleep = asyncio.sleep

    async def _short_sleep(delay):
        if delay and delay >= 1.0:
            await real_sleep(0)
        else:
            await real_sleep(delay)

    monkeypatch.setattr(asyncio, "sleep", _short_sleep)
    provider = _GatedFaceswapProvider()
    deps = _set_faceswap(make_deps(registry=make_registry(replicate=provider)), source=True)
    deps.faceswap_pending.set(
        _UID, ["FAKE:c1", "FAKE:c2", "FAKE:c3"], chat_id=_CHAT, message_id=892
    )
    dp, deps = make_dispatcher(deps)
    cb = callback_query("faceswap:confirm:yes", message_id=892)
    task = asyncio.create_task(dp.feed_update(_BOT, callback_update(cb)))

    # Item 1 queda en su compuerta: soltarlo para que complete.
    await _wait_until(lambda: len(provider.started) >= 1)
    provider.releases[0].set()
    # Item 2 arranca y se bloquea en su compuerta → cancelar ahí.
    await _wait_until(lambda: len(provider.started) >= 2)
    job_id = None
    for edit in deps.gateway.calls_by_method("edit_message_text"):
        data = flat_callback_data(edit["reply_markup"])
        if data and data[0].startswith("cancel_job:"):
            job_id = data[0].split(":", 1)[1]
    assert job_id, "el status inicial lleva cancel_job:<id>"
    await dp.feed_update(
        _BOT, callback_update(callback_query(f"cancel_job:{job_id}", message_id=892))
    )
    provider.releases[1].set()
    await task

    edits = [c["text"] for c in deps.gateway.calls_by_method("edit_message_text")]
    assert any("⏹ Cancelado. Completadas 1/3 imagenes." in t for t in edits)
    assert len(deps.gateway.calls_by_method("send_photo")) == 1, "solo el item 1 completo"
    assert deps.job_manager.active_jobs(_UID) == ()
    assert deps.faceswap_pending.get(_UID) is None


# --------------------------------------------------------------------------- #
# C4: ownership del confirm dedicado en grupos
# --------------------------------------------------------------------------- #
async def test_group_other_user_cannot_confirm_faceswap():
    deps = _set_faceswap(make_deps(), source=True)
    msg = make_photo_message(
        user_id=_UID, chat_id=_GROUP, chat_type="group",
        message_id=10, file_id="FAKE:group_photo",
    )
    deps = await _msg(deps, msg)
    confirm = deps.gateway.calls_by_method("send_message")[-1]
    mid = confirm["sent"].message_id

    deps = await _cb(
        deps,
        callback_query(
            "faceswap:confirm:yes", user_id=_OTHER, chat_id=_GROUP,
            chat_type="group", message_id=mid,
        ),
    )
    ans = deps.gateway.calls_by_method("answer_callback")[-1]
    assert ans["text"] == "Esta confirmación pertenece a otro usuario."
    assert ans["show_alert"] is True
    assert deps.faceswap_pending.get(_UID) is not None, "el pendiente de A sigue intacto"
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.gateway.calls_by_method("edit_message_text") == [], "no se toca el mensaje ajeno"
    assert deps.job_manager.active_jobs(_UID) == ()


# --------------------------------------------------------------------------- #
# Fix round test-guardian GAP-A/GAP-B: rama `failures` del terminal y
# source-missing batch de álbum (0-mock; copy byte-exacto; A8 sin file_ids).
# --------------------------------------------------------------------------- #
async def test_confirm_yes_batch_partial_failure_terminal(monkeypatch):
    """Un ítem del batch falla (ProviderUnavailableError) y el resto completa.

    Terminal byte-exacto "Completadas 2/3 imagenes.\nFallos: imagen 2: ..." sin
    filtrar el detalle interno (A8: type name, no el mensaje crudo del provider).
    """
    real_sleep = asyncio.sleep

    async def _short_sleep(delay):
        if delay and delay >= 1.0:  # neutraliza el rate-limit real (10s) del batch
            await real_sleep(0)
        else:
            await real_sleep(delay)

    monkeypatch.setattr(asyncio, "sleep", _short_sleep)
    provider = FakeImageProvider(
        name="replicate",
        outcomes=[
            make_result(provider="replicate", model_id="fake-faceswap"),
            ProviderUnavailableError(
                "http 503 upstream", user_message="El proveedor no está disponible temporalmente."
            ),
        ],
    )
    deps = _set_faceswap(make_deps(registry=make_registry(replicate=provider)), source=True)
    deps.faceswap_pending.set(
        _UID, ["FAKE:f1", "FAKE:f2", "FAKE:f3"], chat_id=_CHAT, message_id=893
    )
    deps = await _cb(deps, callback_query("faceswap:confirm:yes", message_id=893))

    last = deps.gateway.calls_by_method("edit_message_text")[-1]["text"]
    assert last == (
        "[███████░░░] 2/3 (66%)\n"
        "Completadas 2/3 imagenes.\n"
        "Fallos: imagen 2: El proveedor no está disponible temporalmente."
    )
    assert "FAKE" not in last and "/sources" not in last, "A8: sin file_ids/paths"
    assert "http 503 upstream" not in last, "A8: no filtra el detalle técnico"
    groups = deps.gateway.calls_by_method("send_media_group")
    assert len(groups) == 1 and len(groups[0]["media"]) == 2, "las 2 fotos OK salen en álbum"
    assert deps.faceswap_pending.get(_UID) is None
    assert deps.job_manager.active_jobs(_UID) == (), "el job se cierra en finally"


async def test_confirm_yes_single_failure_terminal():
    """Fallo total single → terminal "No se pudo procesar ninguna..." + job cerrado."""
    provider = FakeImageProvider(
        name="replicate",
        outcomes=[
            ProviderUnavailableError(
                "http 503 caido", user_message="El proveedor no está disponible temporalmente."
            )
        ],
    )
    deps = _set_faceswap(make_deps(registry=make_registry(replicate=provider)), source=True)
    deps.faceswap_pending.set(_UID, ["FAKE:f_solo"], chat_id=_CHAT, message_id=894)
    deps = await _cb(deps, callback_query("faceswap:confirm:yes", message_id=894))

    last = deps.gateway.calls_by_method("edit_message_text")[-1]["text"]
    assert last == (
        "[░░░░░░░░░░] 0/1 (0%)\n"
        "No se pudo procesar ninguna de las 1 imagenes.\n"
        "Fallos: imagen 1: El proveedor no está disponible temporalmente."
    )
    assert "FAKE" not in last and "http 503 caido" not in last, "A8: sin file_ids ni detalle técnico"
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.gateway.calls_by_method("send_media_group") == []
    assert deps.faceswap_pending.get(_UID) is None
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_album_source_missing_batch_clears_and_responds_once():
    """GAP-B: álbum con source stale (path sin archivo) → _SOURCE_MISSING_BATCH.

    Copy batch exacto, clear del source_path y una sola respuesta (A2).
    """
    deps = make_deps()
    cfg = deps.sessions.get_config(_UID)
    deps.sessions.save_config(
        _UID, dataclasses.replace(cfg, model="faceswap", source_path="/stale/source.jpg")
    )
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 2, file_prefix="FAKE:fs_miss")
    await _wait_until(lambda: any(
        s["text"] == "Source no encontrado. Usa /cambiar_source."
        for s in deps.gateway.calls_by_method("send_message")
    ))
    missing = [
        s["text"]
        for s in deps.gateway.calls_by_method("send_message")
        if s["text"] == "Source no encontrado. Usa /cambiar_source."
    ]
    assert len(missing) == 1, "A2: una sola respuesta desde el drain"
    assert deps.sessions.get_config(_UID).source_path is None, "A4: clear del path stale"
    assert deps.faceswap_pending.get(_UID) is None
