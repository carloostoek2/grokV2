"""Tests de `/estado` (R4 Item 1, Task 4): tarjeta de configuración byte-parity.

El comando sale del bucle D8 y responde la tarjeta de grok ``cmd_estado`` (sin
lista de jobs activos — decisión de owner). Copy puro en ``formatters.estado_card``;
los asserts fijan el texto completo transcrito (typos originales incluidos).
0 red / 0 ``unittest.mock`` (fakes del conftest). Fixtures anonimizados.
"""

from __future__ import annotations

import dataclasses

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    make_deps,
    make_dispatcher,
    message_update,
    text_message,
)

_UID = USER_ID
_CHAT = CHAT_ID
_BOT = Bot("42:TEST")


async def _estado_text(deps) -> str:
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/estado")))
    sends = deps.gateway.calls_by_method("send_message")
    assert sends, "esperaba la tarjeta de /estado"
    return sends[-1]["text"]


async def test_estado_grok_card():
    deps = make_deps()
    text = await _estado_text(deps)
    assert text == (
        "Estado\n\n"
        "Modelo: Grok Imagine (Kie.ai • Alta calidad)\n\n"
        "API / Backend: Kie.ai • Alta calidad\n\n"
        "Referencia integrate (/s): No configurada\n\n"
        "Listo para generar/editar imagenes."
    )


async def test_estado_grok_with_integrate_ref():
    deps = make_deps()
    cfg = deps.sessions.get_config(_UID)
    deps.sessions.save_config(
        _UID, dataclasses.replace(cfg, integrate_ref_path="/home/user/ref.png")
    )
    text = await _estado_text(deps)
    assert "Referencia integrate (/s): Configurada" in text
    assert "No configurada" not in text


async def test_estado_grok_video_card():
    deps = make_deps()
    deps.update_config.set_model(_UID, "grok_video")
    text = await _estado_text(deps)
    assert "Modelo: Grok Imagine Video (Kie.ai)" in text
    assert "API / Backend: Kie.ai" in text
    assert "Video: Base, 5s → 6s (Kie.ai), 16:9, 720p" in text
    assert "Listo para generar videos (texto o imagen a video)." in text
    assert "Usa /config (o /video) para configurar modelo, duración, aspecto y resolución." in text


async def test_estado_seedream_card():
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    text = await _estado_text(deps)
    assert text == (
        "Estado\n\n"
        "Modelo: Seedream 5.0\n\n"
        "Listo para generar/editar imagenes."
    )


async def test_estado_faceswap_card():
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    text = await _estado_text(deps)
    assert "Source: No configurado" in text
    assert "Estado: IDLE" in text


async def test_estado_faceswap_card_awaiting_source():
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    deps.source_faces.begin_awaiting_source(_UID)
    text = await _estado_text(deps)
    assert "Source: No configurado" in text
    assert "Estado: AWAITING_SOURCE" in text


async def test_estado_faceswap_card_source_configured():
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    deps.source_faces.save_source(_UID, b"src-bytes")
    text = await _estado_text(deps)
    assert "Source: Configurado" in text
    assert "Estado: IDLE" in text


async def test_estado_does_not_leak_ids():
    deps = make_deps()
    text = await _estado_text(deps)
    assert "FAKE:" not in text
    assert "file_id" not in text
    assert "/home/" not in text
    assert str(_UID) not in text


async def test_estado_grok_video_replicate_images_card():
    """Open 7: grok_video con imagen vía Replicate → tarjeta byte-parity.

    Cuando el lado imagen de Grok Imagine va por Replicate, el backend efectivo
    de video es xAI (Replicate no tiene API de video) y la tarjeta muestra el
    paréntesis "(Imágenes: Replicate; video vía xAI)" y la duración sin clamp
    de Kie ("5s", no "5s → 6s (Kie.ai)").
    """
    deps = make_deps()
    cfg = deps.sessions.get_config(_UID)
    deps.sessions.save_config(
        _UID,
        dataclasses.replace(cfg, model="grok_video", grok_imagine_provider="replicate"),
    )
    text = await _estado_text(deps)
    assert text == (
        "Estado\n\n"
        "Modelo: Grok Imagine Video (xAI; imágenes: Replicate)\n\n"
        "API / Backend: xAI (oficial)\n\n"
        "(Imágenes: Replicate; video vía xAI)\n\n"
        "Video: Base, 5s, 16:9, 720p\n\n"
        "Listo para generar videos (texto o imagen a video).\n"
        "Usa /config (o /video) para configurar modelo, duración, aspecto y resolución."
    )


async def test_estado_is_plain_text_parse_mode_none():
    deps = make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/estado")))
    send = deps.gateway.calls_by_method("send_message")[-1]
    assert send["parse_mode"] is None
    # A6: sin tags HTML interpretables (texto plano byte-a-byte).
    assert "<" not in send["text"]


async def test_d8_commands_empty_and_referencia_real():
    """D8_COMMANDS == (); /cambiar_referencia es real (R4 Item 3).

    /cambiar_source en default grok responde el copy other-model (no degrada);
    /cambiar_referencia en default grok responde el prompt real y activa el flag
    efímero awaiting-ref (no degrada con D8).
    """
    from grokbot.telegram.handlers._common import D8_COMMANDS

    assert D8_COMMANDS == ()
    assert "estado" not in D8_COMMANDS
    assert "cambiar_source" not in D8_COMMANDS
    assert "cambiar_referencia" not in D8_COMMANDS

    deps = make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/cambiar_source")))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == (
        "Este comando solo esta disponible en modo <b>Face Swap</b>.\n"
        "Usa /config para cambiar al modo Face Swap."
    )
    assert deps.sessions.get_config(_UID).state == "IDLE", "no se configura fuera de faceswap"

    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/cambiar_referencia")))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == (
        "Envia la foto que sera tu <b>referencia fija</b>.\n\n"
        "Luego, en un album (o foto) con caption que empiece por <b>/s</b>, "
        "cada imagen se editara junto con esta referencia.\n"
        "Requiere proveedor <b>xAI (oficial)</b> en /config."
    )
    assert _UID in deps.integrate_ref_pending


async def test_cambiar_referencia_other_model():
    """/cambiar_referencia en modelo no-grok → copy other-model y pending vacío."""
    deps = make_deps()
    deps.update_config.set_model(_UID, "seedream")
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/cambiar_referencia")))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == (
        "Este comando solo esta disponible en modo <b>Grok Imagine</b>.\n"
        "Usa /config para cambiar al modo Grok Imagine."
    )
    assert _UID not in deps.integrate_ref_pending


async def test_cambiar_source_faceswap_prompt_and_state():
    """/cambiar_source en modo faceswap → prompt real + estado AWAITING_SOURCE."""
    deps = make_deps()
    deps.update_config.set_model(_UID, "faceswap")
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/cambiar_source")))
    last = deps.gateway.calls_by_method("send_message")[-1]
    assert last["text"] == "Envia tu foto source (la cara que quieres usar para el swap)."
    assert deps.sessions.get_config(_UID).state == "AWAITING_SOURCE"
