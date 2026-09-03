"""Tests de /variables y /var (item 5, handlers/variables_cmd.py).

Parsing de count/prompt, estrategias (RandomComboStrategy vs FixedPromptStrategy
con ``PromptTemplate.render_inline``), JobsFull y EmptyList. 0 red / 0
``unittest.mock``.
"""

from __future__ import annotations

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    FakeVariablesRepo,
    JobManager,
    make_deps,
    make_dispatcher,
    make_photo_message,
    message_update,
    text_message,
)
from grokbot.domain.variables import VARIABLES_MAX
from grokbot.telegram.formatters import JOBS_FULL_MSG
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


async def test_var_fixed_prompt_text():
    deps = make_deps()
    _use_seedream(deps)
    deps = await _msg(deps, text_message("/var de pie, frontal, elegante"))
    texts = _texts(deps)
    assert any(t.startswith("🎲 <b>Var</b>: generando 0/1 imágenes con Seedream 5.0...") for t in texts)
    assert deps.gateway.calls_by_method("send_photo")


async def test_var_short_prompt_validation():
    deps = make_deps()
    _use_seedream(deps)
    deps = await _msg(deps, text_message("/var ab"))
    assert any("El prompt es muy corto." in t for t in _texts(deps))


async def test_var_jobs_full():
    deps = make_deps(job_manager=JobManager(max_active=0))
    _use_seedream(deps)
    deps = await _msg(deps, text_message("/var de pie"))
    assert JOBS_FULL_MSG in _texts(deps)


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
