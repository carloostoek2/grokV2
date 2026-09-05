"""Tests de los flujos de referencia fija /s (R4 Item 3, Task 3) — comandos,
guardado de la referencia (foto single caption/sin caption/álbum) y edición
integrate real (single/álbum/long-prompt/regen) contra el seam edit_with_reference.

0 red / 0 ``unittest.mock``: fakes del conftest. Los asserts fijan copy
byte-parity de grok (typos/acentos originales) y terminales user-safe (R6/R8:
nunca file_ids/URLs/paths). Fixtures anonimizados.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    FakeImageProvider,
    callback_query,
    callback_update,
    make_deps,
    make_dispatcher,
    make_photo_message,
    make_registry,
    message_update,
    text_message,
)

_UID = USER_ID
_OTHER = 222222222  # segundo user anonimizado (R8/C4/A10)
_CHAT = CHAT_ID
_BOT = Bot("42:TEST")

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
_REQUIRES_XAI = "requiere el proveedor <b>xAI (oficial)</b>"
_NO_REFERENCE = (
    "No hay imagen de referencia configurada. "
    "Usa /cambiar_referencia para establecerla."
)


def _deps_with_xai(prov: FakeImageProvider | None = None):
    """BotDeps con cfg grok/xAI, ref sembrada y un provider xai inyectado."""
    prov = prov or FakeImageProvider(name="xai")
    deps = make_deps(registry=make_registry(xai=prov))
    deps.update_config.set_grok_imagine_provider(_UID, "xai")
    deps.integrate_refs.set_reference(_UID, b"ref")
    return deps, prov


async def _msg(deps, message):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(message))
    return deps


async def _wait_until(pred, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.005)


async def _feed_album(deps, n: int, *, group: str = "ref-album", caption_on: int | None = None,
                      caption: str | None = None, file_prefix: str = "FAKE:ref_a"):
    """Alimentar un media group de ``n`` fotos (un solo dispatcher)."""
    dp, deps = make_dispatcher(deps)
    for i in range(1, n + 1):
        msg = make_photo_message(
            caption=(caption if caption_on == i else None),
            message_id=100 + i,
            file_id=f"{file_prefix}{i}",
            media_group_id=group,
        )
        await dp.feed_update(_BOT, message_update(msg))
    return deps


# --------------------------------------------------------------------------- #
# /cambiar_referencia
# --------------------------------------------------------------------------- #
async def test_cambiar_referencia_grok_prompt_and_pending():
    deps = make_deps()  # cfg default grok
    deps = await _msg(deps, text_message("/cambiar_referencia"))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == _CAMBIAR_REF_PROMPT
    assert _UID in deps.integrate_ref_pending


async def test_cambiar_referencia_seedream_other_model():
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    deps = await _msg(deps, text_message("/cambiar_referencia"))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == _CAMBIAR_REF_OTHER_MODEL
    assert _UID not in deps.integrate_ref_pending


# --------------------------------------------------------------------------- #
# Guardado de la referencia (foto caption / sin caption / álbum)
# --------------------------------------------------------------------------- #
async def test_photo_caption_awaiting_saves_reference():
    deps = make_deps()
    deps.integrate_ref_pending.add(_UID)
    deps = await _msg(
        deps,
        make_photo_message(caption="cualquier caption", file_id="FAKE:ref1", message_id=5),
    )
    gets = deps.gateway.calls_by_method("get_file_bytes")
    assert gets and gets[-1]["file_id"] == "FAKE:ref1"
    assert deps.sessions.get_config(_UID).integrate_ref_path == f"/integrate_refs/{_UID}.jpg"
    assert deps.integrate_refs.reference_available(_UID) is True
    assert deps.integrate_ref_pending == set()
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == _REF_SAVED
    assert deps.gateway.calls_by_method("send_photo") == []


async def test_photo_no_caption_awaiting_saves_reference():
    deps = make_deps()
    deps.integrate_ref_pending.add(_UID)
    deps = await _msg(
        deps,
        make_photo_message(file_id="FAKE:ref2", message_id=6),
    )
    assert deps.sessions.get_config(_UID).integrate_ref_path == f"/integrate_refs/{_UID}.jpg"
    assert deps.integrate_ref_pending == set()
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == _REF_SAVED


async def test_album_awaiting_saves_last_photo_once():
    deps = make_deps()
    deps.integrate_ref_pending.add(_UID)
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, group="ref-album-1", file_prefix="FAKE:refalb")
    await _wait_until(lambda: deps.integrate_ref_pending == set())
    saved = [c for c in deps.gateway.calls_by_method("send_message") if c["text"] == _REF_SAVED]
    assert len(saved) == 1, "final-wins: una sola respuesta"
    # La referencia guardada es la ÚLTIMA foto del álbum (A1).
    gets = deps.gateway.calls_by_method("get_file_bytes")
    assert gets and gets[-1]["file_id"] == "FAKE:refalb3"
    assert deps.sessions.get_config(_UID).integrate_ref_path == f"/integrate_refs/{_UID}.jpg"


async def test_photo_awaiting_media_unavailable_keeps_pending():
    deps = make_deps()
    deps.integrate_ref_pending.add(_UID)
    # file_id no resoluble (no FAKE:* y ausente de file_bytes) → M2 → None.
    deps = await _msg(deps, make_photo_message(file_id="unknown_id_xyz", message_id=7))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert "No se pudo recuperar la imagen." in last["text"]
    assert _UID in deps.integrate_ref_pending, "el flag se conserva para reintentar"
    assert deps.sessions.get_config(_UID).integrate_ref_path is None


# --------------------------------------------------------------------------- #
# Single /s — prereqs y happy path integrate
# --------------------------------------------------------------------------- #
async def test_single_s_requires_xai():
    deps = make_deps()  # cfg default grok/kie
    deps = await _msg(deps, make_photo_message(caption="/s x", message_id=8))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert _REQUIRES_XAI in last["text"]
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_single_s_no_reference():
    deps = make_deps()
    deps.update_config.set_grok_imagine_provider(_UID, "xai")
    deps = await _msg(deps, make_photo_message(caption="/s hazla sonreir", message_id=9))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == _NO_REFERENCE
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_single_s_happy_path_integrate():
    deps, prov = _deps_with_xai()
    deps = await _msg(
        deps,
        make_photo_message(caption="/s hazla sonreir", file_id="FAKE:single_edit", message_id=10),
    )
    sends = deps.gateway.calls_by_method("send_message")
    assert any(s["text"] == "Editando imagen (referencia+foto)..." for s in sends)
    assert len(prov.edit_with_reference_calls) == 1
    request, source, reference = prov.edit_with_reference_calls[0]
    assert source == b"fake-file-bytes"
    assert reference == b"ref"
    assert deps.gateway.calls_by_method("send_photo")
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_long_prompt_single_s_completes_with_ref():
    deps, prov = _deps_with_xai()
    deps = await _msg(
        deps, make_photo_message(caption="/s " + "x" * 1100, file_id="FAKE:long_cap", message_id=11)
    )
    assert deps.long_prompt.is_awaiting(_UID) is True
    assert deps.long_prompt.get(_UID)["integrate_mode"] is True
    deps = await _msg(deps, text_message("hazla sonreir", message_id=12))
    assert len(prov.edit_with_reference_calls) == 1
    assert deps.gateway.calls_by_method("send_photo")
    assert deps.long_prompt.is_awaiting(_UID) is False


async def test_grok_video_s_caption_ignores_reference_and_animates():
    """A5: /s con grok_video anima ignorando la ref; NO valida prereqs (quirk parity).

    El branch video corre antes del single-edit: ni "requiere xAI" ni
    "No hay imagen de referencia" deben aparecer, y la foto se anima (send_video)
    sin abrir job de imagen (parity grok: la ref se ignora en i2v).
    """
    deps = make_deps()
    deps.update_config.set_model(_UID, "grok_video")
    deps = await _msg(
        deps, make_photo_message(caption="/s mueve la camara lento", file_id="FAKE:vid_src", message_id=14)
    )
    sends = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert any(t.startswith("Animando imagen con <b>grok-imagine-video</b>") for t in sends)
    assert not any("requiere el proveedor <b>xAI (oficial)</b>" in t for t in sends)
    assert not any("No hay imagen de referencia configurada" in t for t in sends)
    assert deps.gateway.calls_by_method("send_video"), "i2v anima ignorando la referencia"
    assert deps.job_manager.active_jobs(_UID) == (), "el flujo video no abre job de imagen"


async def test_awaiting_ref_beats_faceswap_routing():
    """A2: flag pending chequeado ANTES de faceswap en foto+caption.

    Un user que activó /cambiar_referencia (grok) y cambia a faceswap antes de
    mandar la foto debe seguir guardando la referencia, no entrar al flujo
    faceswap (parity grok 2853-2855: el chequeo de awaiting precede al routing).
    """
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    deps.integrate_ref_pending.add(_UID)
    deps = await _msg(
        deps, make_photo_message(caption="mi cara", file_id="FAKE:ref_fs", message_id=15)
    )
    assert deps.sessions.get_config(_UID).integrate_ref_path == f"/integrate_refs/{_UID}.jpg"
    assert deps.integrate_ref_pending == set()
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == _REF_SAVED
    assert deps.sessions.get_config(_UID).state == "IDLE", "no entró al flujo faceswap"


async def test_photo_no_caption_awaiting_beats_faceswap_routing():
    """A2: pending chequeado ANTES de faceswap en foto SIN caption."""
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    deps.integrate_ref_pending.add(_UID)
    deps = await _msg(deps, make_photo_message(file_id="FAKE:ref_nc", message_id=16))
    assert deps.sessions.get_config(_UID).integrate_ref_path == f"/integrate_refs/{_UID}.jpg"
    assert deps.integrate_ref_pending == set()
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == _REF_SAVED
    assert deps.sessions.get_config(_UID).state == "IDLE", "no entró al flujo faceswap"
    assert deps.gateway.calls_by_method("send_photo") == []


async def test_album_awaiting_beats_faceswap_routing():
    """A2/A1: álbum con pending + cfg faceswap → guarda la ref (final-wins), no faceswap."""
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    deps.integrate_ref_pending.add(_UID)
    deps.album.delay = 0.05
    deps = await _feed_album(deps, 3, group="ref-album-fs", file_prefix="FAKE:refalb_fs")
    await _wait_until(lambda: deps.integrate_ref_pending == set())
    saved = [c for c in deps.gateway.calls_by_method("send_message") if c["text"] == _REF_SAVED]
    assert len(saved) == 1, "final-wins: una sola respuesta"
    gets = deps.gateway.calls_by_method("get_file_bytes")
    assert gets and gets[-1]["file_id"] == "FAKE:refalb_fs3"
    assert deps.sessions.get_config(_UID).state == "IDLE", "no entró al flujo faceswap"


async def test_long_prompt_single_s_non_xai_degrades_on_completion():
    """A4: /s largo no-xai completa → copy "requiere xAI"; consume la colección.

    La rama try/except de _process_single_photo_edit solo es alcanzable al
    COMPLETAR un long-prompt single /s; un /s largo no-xai no debe completar en
    silencio como edición de 1 imagen (sin send_photo).
    """
    deps = make_deps()  # cfg default grok/kie
    deps = await _msg(
        deps, make_photo_message(caption="/s " + "x" * 1100, file_id="FAKE:lp_nx", message_id=20)
    )
    assert deps.long_prompt.is_awaiting(_UID) is True
    assert deps.long_prompt.get(_UID)["integrate_mode"] is True
    deps = await _msg(deps, text_message("hazla sonreir", message_id=21))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert _REQUIRES_XAI in last["text"]
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.long_prompt.is_awaiting(_UID) is False, "A4: pop consume la colección"
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_long_prompt_single_s_ref_missing_degrades_on_completion():
    """A4/A8: ref ausente al completar un /s largo xai → copy sin-referencia."""
    deps = make_deps()
    deps.update_config.set_grok_imagine_provider(_UID, "xai")  # xai pero sin ref
    deps = await _msg(
        deps, make_photo_message(caption="/s " + "x" * 1100, file_id="FAKE:lp_noref", message_id=22)
    )
    assert deps.long_prompt.is_awaiting(_UID) is True
    deps = await _msg(deps, text_message("hazla sonreir", message_id=23))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == _NO_REFERENCE
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.long_prompt.is_awaiting(_UID) is False, "A4: pop consume la colección"
    assert deps.job_manager.active_jobs(_UID) == ()


# --------------------------------------------------------------------------- #
# Álbum integrate / álbum /s largo
# --------------------------------------------------------------------------- #
async def test_album_s_integrate_sequential():
    deps, prov = _deps_with_xai()
    deps.album.delay = 0.05
    deps = await _feed_album(
        deps, 3, group="alb-int-happy",
        caption_on=1, caption="/s hazla sonreir", file_prefix="FAKE:albedit",
    )
    await _wait_until(lambda: any(
        c["text"] == "Completadas 3/3 imágenes."
        for c in deps.gateway.calls_by_method("edit_message_text")
    ))
    sends = deps.gateway.calls_by_method("send_message")
    status = sends[0]
    assert status["text"] == "Integrando referencia 0/3 imágenes (xAI)..."
    edits = [c["text"] for c in deps.gateway.calls_by_method("edit_message_text")]
    assert "Integrando referencia 1/3 imágenes (xAI)..." in edits
    assert "Integrando referencia 2/3 imágenes (xAI)..." in edits
    assert "Integrando referencia 3/3 imágenes (xAI)..." in edits
    assert edits[-1] == "Completadas 3/3 imágenes."
    assert len(deps.gateway.calls_by_method("send_photo")) == 3
    assert len(prov.edit_with_reference_calls) == 3
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_long_prompt_album_s_completes_with_ref():
    deps, prov = _deps_with_xai()
    deps.album.delay = 0.05
    deps = await _feed_album(
        deps, 3, group="alb-long-int",
        caption_on=1, caption="/s " + "x" * 1100, file_prefix="FAKE:alblong",
    )
    await _wait_until(lambda: deps.long_prompt.is_awaiting(_UID))
    entry = deps.long_prompt.get(_UID)
    assert entry["integrate_mode"] is True
    assert len(entry["file_ids"]) == 3
    deps = await _msg(deps, text_message("hazla sonreir", message_id=13))
    await _wait_until(lambda: any(
        c["text"] == "Completadas 3/3 imágenes."
        for c in deps.gateway.calls_by_method("edit_message_text")
    ))
    assert len(prov.edit_with_reference_calls) == 3
    assert deps.gateway.calls_by_method("send_photo")
    assert deps.job_manager.active_jobs(_UID) == ()


# --------------------------------------------------------------------------- #
# Regen integrate
# --------------------------------------------------------------------------- #
async def _feed_regen_callback(deps, *, message_id: int, regen: dict) -> None:
    deps.refs.save(_CHAT, message_id, regen=regen, owner_uid=_UID)
    dp, deps = make_dispatcher(deps)
    msg = make_photo_message(message_id=message_id)
    cb = callback_query("regen", message_id=message_id, message=msg)
    await dp.feed_update(_BOT, callback_update(cb))
    return deps


def _regen_payload() -> dict:
    return {
        "model_key": "grok",
        "imagine_provider": "xai",
        "imagine_variant": "quality",
        "mode": "edit",
        "prompt": "hazla sonreir",
        "source_file_id": "FAKE:orig_src",
        "integrate_mode": True,
    }


async def test_regen_integrate_reloads_reference():
    deps, prov = _deps_with_xai()
    deps = await _feed_regen_callback(deps, message_id=777, regen=_regen_payload())
    gets = deps.gateway.calls_by_method("get_file_bytes")
    assert gets and gets[-1]["file_id"] == "FAKE:orig_src", "re-descarga el source original"
    assert len(prov.edit_with_reference_calls) == 1
    _request, source, reference = prov.edit_with_reference_calls[0]
    assert source == b"fake-file-bytes"
    assert reference == b"ref"
    assert deps.gateway.calls_by_method("send_photo")
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_regen_integrate_missing_reference_degrades():
    deps = make_deps()
    deps.update_config.set_grok_imagine_provider(_UID, "xai")  # cfg xai, SIN ref
    deps = await _feed_regen_callback(deps, message_id=778, regen=_regen_payload())
    edits = deps.gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == _NO_REFERENCE
    assert edits[-1]["reply_markup"] is None
    assert deps.gateway.calls_by_method("send_photo") == []
    assert deps.job_manager.active_jobs(_UID) == ()


async def test_regen_integrate_legacy_without_owner_uid_uses_ctx_user_id():
    """A10/R8 legacy: regen SIN owner_uid carga la ref por regen["user_id"].

    grok usa regen["user_id"] (bot.py:1353); un ref legacy sin owner_uid NO debe
    cargar la referencia del clickeador (fuga cross-user): la ref vive en _OTHER
    (el uid del ctx), el clickeador _UID no tiene ref configurada.
    """
    prov = FakeImageProvider(name="xai")
    deps = make_deps(registry=make_registry(xai=prov))
    deps.update_config.set_grok_imagine_provider(_OTHER, "xai")
    deps.integrate_refs.set_reference(_OTHER, b"other-ref")
    regen = _regen_payload()
    regen["user_id"] = _OTHER
    deps.refs.save(_CHAT, 888, regen=regen)  # SIN owner_uid (legacy)
    dp, deps = make_dispatcher(deps)
    msg = make_photo_message(user_id=_UID, message_id=888)
    cb = callback_query("regen", user_id=_UID, message_id=888, message=msg)
    await dp.feed_update(_BOT, callback_update(cb))
    assert len(prov.edit_with_reference_calls) == 1
    _request, _source, reference = prov.edit_with_reference_calls[0]
    assert reference == b"other-ref"
    assert deps.gateway.calls_by_method("send_photo")
    assert deps.job_manager.active_jobs(_UID) == ()
