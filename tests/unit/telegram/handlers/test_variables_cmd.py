"""Tests de /variables y /var (item 5, handlers/variables_cmd.py).

Parsing de count/prompt, estrategias (RandomComboStrategy vs FixedPromptStrategy
con ``PromptTemplate.render_inline``) y EmptyList. R9: sin tope de concurrencia.
0 red / 0 ``unittest.mock``.
"""

from __future__ import annotations

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    FakeVariablesRepo,
    make_deps,
    make_dispatcher,
    make_photo_message,
    message_update,
    text_message,
)
from grokbot.domain.variables import VARIABLES_MAX
from grokbot.telegram.handlers._common import (
    SOURCE_MEDIA_UNAVAILABLE_MSG,
    parse_var_count_and_text,
    parse_var_prompt,
    parse_variables_count,
)

_UID = USER_ID
_CHAT = CHAT_ID
_BOT = Bot("42:TEST")


async def _msg(deps, message):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(message))
    return deps


def _texts(deps) -> list[str]:
    return [c["text"] for c in deps.gateway.calls_by_method("send_message")]


def _use_seedream(deps) -> None:
    deps.update_config.set_model(_UID, "seedream")


# --------------------------------------------------------------------------- #
# Parsing puro (parity bot.py 2019-2045 / 2532-2580)
# --------------------------------------------------------------------------- #
def test_parse_variables_count():
    assert parse_variables_count("/variables") == 1
    assert parse_variables_count("/variables 5") == 5
    assert parse_variables_count("/variables 99") == VARIABLES_MAX
    assert parse_variables_count("/variables abc") is None


def test_parse_var_count_and_text():
    assert parse_var_count_and_text("/var de pie") == (1, "de pie")
    assert parse_var_count_and_text("/var 3 de pie") == (3, "de pie")
    assert parse_var_count_and_text("/var") == (1, None)
    assert parse_var_prompt("/var una foto cualquiera") == "una foto cualquiera"


# --------------------------------------------------------------------------- #
# Routing y estrategias
# --------------------------------------------------------------------------- #
async def test_variables_bare_text_generates_random():
    deps = make_deps()
    _use_seedream(deps)
    deps = await _msg(deps, text_message("/variables"))
    texts = _texts(deps)
    assert any(t.startswith("🎲 <b>Variables</b>: generando 0/1 imágenes con Seedream 5.0...") for t in texts)
    assert deps.gateway.calls_by_method("send_photo")


async def test_variables_non_numeric_shows_help():
    deps = make_deps()
    _use_seedream(deps)
    deps = await _msg(deps, text_message("/variables abc"))
    assert any("Para usar <b>/variables</b>:" in t for t in _texts(deps))


async def test_variables_photo_caption_edit():
    deps = make_deps()
    _use_seedream(deps)
    deps = await _msg(
        deps,
        make_photo_message(caption="/variables 1", file_id="FAKE:var_src", message_id=5),
    )
    methods = [c["method"] for c in deps.gateway.calls]
    assert "get_file_bytes" in methods
    texts = _texts(deps)
    assert any(t.startswith("🎲 <b>Variables</b>: editando 0/1 imágenes con Seedream 5.0...") for t in texts)


async def test_variables_photo_source_fetch_failure_degrades():
    """M2: /variables con foto de file_id expirado → degrada user-safe, sin batch."""
    deps = make_deps()
    _use_seedream(deps)
    deps = await _msg(
        deps,
        make_photo_message(caption="/variables 1", file_id="EXPIRED:var_src", message_id=5),
    )
    texts = _texts(deps)
    assert SOURCE_MEDIA_UNAVAILABLE_MSG in texts
    assert not deps.gateway.calls_by_method("send_photo")
    assert deps.job_manager.active_jobs(_UID) == (), "sin fuente no arranca el batch"


async def test_variables_reply_to_photo_edit():
    deps = make_deps()
    _use_seedream(deps)
    reply_photo = make_photo_message(message_id=9000)
    deps = await _msg(
        deps,
        text_message("/variables 2", message_id=6, reply_to_message=reply_photo),
    )
    gets = deps.gateway.calls_by_method("get_file_bytes")
    assert gets, "reply a foto descarga la fuente"
    texts = _texts(deps)
    assert any(t.startswith("🎲 <b>Variables</b>: editando 0/2 imágenes con Seedream 5.0...") for t in texts)


async def test_variables_batch_photos_reply_to_command():
    """Las fotos de un batch responden al mensaje del comando que las invocó."""
    deps = make_deps()
    _use_seedream(deps)
    deps = await _msg(deps, text_message("/variables 2"))
    photos = deps.gateway.calls_by_method("send_photo")
    assert photos, "esperaba las fotos del batch"
    assert all(p["reply_to_message_id"] == 1 for p in photos)


async def test_var_fixed_prompt_text():
    deps = make_deps()
    _use_seedream(deps)
    deps = await _msg(deps, text_message("/var de pie, frontal, elegante"))
    texts = _texts(deps)
    assert any(t.startswith("🎲 <b>Var</b>: generando 0/1 imágenes con Seedream 5.0...") for t in texts)
    photos = deps.gateway.calls_by_method("send_photo")
    assert photos
    # la foto del /var responde al mensaje del comando (message_id=1).
    assert photos[-1]["reply_to_message_id"] == 1


async def test_var_short_prompt_validation():
    deps = make_deps()
    _use_seedream(deps)
    deps = await _msg(deps, text_message("/var ab"))
    assert any("El prompt es muy corto." in t for t in _texts(deps))


async def test_var_batch_starts_with_active_jobs_of_same_user():
    """R9: sin tope de concurrencia — /var arranca aunque ya haya jobs en curso."""
    deps = make_deps()
    _use_seedream(deps)
    prev = {
        deps.job_manager.start(_UID, "edit").job_id,
        deps.job_manager.start(_UID, "regen").job_id,
    }
    deps = await _msg(deps, text_message("/var de pie"))
    texts = _texts(deps)
    assert any(t.startswith("🎲 <b>Var</b>: generando 0/1 imágenes con Seedream 5.0...") for t in texts)
    assert deps.gateway.calls_by_method("send_photo")
    # El batch no canceló ni pisó los jobs previos del user (siguen activos).
    assert prev <= {j.job_id for j in deps.job_manager.active_jobs(_UID)}
    # El job del batch termina solo vía el finally del use case (finish en
    # generator close); lo verifica la suite a nivel use case, no aquí.


async def test_variables_empty_list_message():
    deps = make_deps(
        variables=FakeVariablesRepo(
            lists={
                "poses": ["de pie", "sentada"],
                "angles": ["frontal", "perfil"],
                "actions": [],
            }
        )
    )
    _use_seedream(deps)
    deps = await _msg(deps, text_message("/variables"))
    texts = _texts(deps)
    assert any("La lista de <b>Acciones</b> está vacía." in t for t in texts)
    assert not deps.gateway.calls_by_method("send_photo")
