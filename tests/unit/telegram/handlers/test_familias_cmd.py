"""Familias: /variables N familia + panel /listas → Familias."""

from __future__ import annotations

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    FakeImageProvider,
    FakeVariablesRepo,
    callback_query,
    callback_update,
    flat_callback_data,
    make_deps,
    make_dispatcher,
    make_registry,
    message_update,
    text_message,
)
from grokbot.domain.families import Family
from grokbot.telegram.handlers._common import parse_variables_request

_UID = USER_ID
_CHAT = CHAT_ID
_BOT = Bot("42:TEST")


def _lists() -> dict[str, list[str]]:
    return {
        "poses": ["de pie", "sentada"],
        "angles": ["frontal", "perfil"],
        "actions": ["mirando a cámara", "sonriendo"],
    }


def _family() -> Family:
    return Family(
        id="mirror",
        label="Mirror / Selfie",
        description="Autofotos frente al espejo.",
        pools={
            "poses": ("de pie",),
            "angles": ("frontal",),
            "actions": ("mirando a cámara",),
        },
    )


def _repo(*, active: str | None = None, with_family: bool = True) -> FakeVariablesRepo:
    return FakeVariablesRepo(
        lists=_lists(),
        families={"mirror": _family()} if with_family else {},
        active_family=active,
    )


async def _msg(deps, message):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(message))
    return deps


def _texts(deps) -> list[str]:
    return [c["text"] for c in deps.gateway.calls_by_method("send_message")]


def _last_edit(deps) -> dict:
    return deps.gateway.calls_by_method("edit_message_text")[-1]


def _last_answer(deps) -> dict:
    return deps.gateway.calls_by_method("answer_callback")[-1]


async def _open_listas(deps):
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/listas")))
    return dp, deps


def _panel_id(deps) -> int:
    return deps.gateway.calls_by_method("send_message")[-1]["sent"].message_id


async def _cb(dp, data: str, mid: int) -> None:
    await dp.feed_update(_BOT, callback_update(callback_query(data, message_id=mid)))


# --------------------------------------------------------------------------- #
# Parsing y /variables N familia
# --------------------------------------------------------------------------- #
def test_parse_variables_request():
    assert parse_variables_request("/variables") == (1, None)
    assert parse_variables_request("/variables 4") == (4, None)
    assert parse_variables_request("/variables 4 mirror") == (4, "mirror")
    assert parse_variables_request("/variables mirror") == (1, "mirror")
    assert parse_variables_request("/variables 99 mirror") == (10, "mirror")
    assert parse_variables_request("/variables 0") is None
    assert parse_variables_request("/variables a b") is None


async def test_variables_with_family_uses_its_pool():
    provider = FakeImageProvider(name="kie")
    deps = make_deps(variables=_repo(), registry=make_registry(kie=provider))

    deps = await _msg(deps, text_message("/variables 1 mirror"))

    assert provider.calls, "esperaba una generación"
    assert provider.calls[0][0].prompt == "de pie, frontal, mirando a cámara"


async def test_variables_without_token_uses_active_family():
    provider = FakeImageProvider(name="kie")
    deps = make_deps(variables=_repo(active="mirror"), registry=make_registry(kie=provider))

    deps = await _msg(deps, text_message("/variables 1"))

    assert provider.calls[0][0].prompt == "de pie, frontal, mirando a cámara"


async def test_variables_unknown_family_is_user_safe():
    deps = make_deps(variables=_repo())

    deps = await _msg(deps, text_message("/variables 1 nope"))

    assert any("No existe la familia 'nope'." in text for text in _texts(deps))
    assert any("Disponibles: mirror." in text for text in _texts(deps))


async def test_variables_single_unknown_token_is_a_family_error():
    deps = make_deps(variables=_repo())

    deps = await _msg(deps, text_message("/variables abc"))

    assert any("No existe la familia 'abc'." in text for text in _texts(deps))


async def test_family_lookup_is_forgiving_with_case_and_spaces():
    provider = FakeImageProvider(name="kie")
    deps = make_deps(variables=_repo(), registry=make_registry(kie=provider))

    deps = await _msg(deps, text_message("/variables 1 Mirror"))

    assert provider.calls[0][0].prompt == "de pie, frontal, mirando a cámara"


async def test_unknown_family_hint_escapes_ids():
    repo = FakeVariablesRepo(lists=_lists(), families={"<b>x</b>": _family()})
    deps = make_deps(variables=repo)

    deps = await _msg(deps, text_message("/variables 1 nope"))

    assert any("&lt;b&gt;x&lt;/b&gt;" in text for text in _texts(deps))


def test_family_view_escapes_field_names():
    from grokbot.telegram.handlers.familias_cmd import _family_view_text

    family = Family(id="f", label="F", pools={"<b>poses</b>": ("de pie",)})
    assert "&lt;b&gt;poses&lt;/b&gt;" in _family_view_text(family, None)


async def test_active_family_missing_is_not_silently_unrestricted():
    repo = _repo(active="ghost")
    deps = make_deps(variables=repo)

    deps = await _msg(deps, text_message("/variables 1"))

    assert any("La familia activa 'ghost' ya no existe." in text for text in _texts(deps))


# --------------------------------------------------------------------------- #
# Panel /listas → Familias
# --------------------------------------------------------------------------- #
def test_menu_mentions_families():
    from grokbot.telegram.handlers.listas_cmd import _menu_text

    text = _menu_text(_lists(), "{pose}", {"mirror": _family()}, "mirror")
    assert "👪 <b>Familias:</b> 1 · activa: <b>mirror</b>" in text


async def test_fam_list_and_view():
    dp, deps = await _open_listas(make_deps(variables=_repo(active="mirror")))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:list", mid)
    edit = _last_edit(deps)
    assert "<b>👪 Familias</b>" in edit["text"]
    data = flat_callback_data(edit["reply_markup"])
    assert "var:fam:view:mirror" in data
    assert "var:fam:new" in data

    await _cb(dp, "var:fam:view:mirror", mid)
    edit = _last_edit(deps)
    assert "Mirror / Selfie" in edit["text"]
    assert "✅ Activa" in edit["text"]
    data = flat_callback_data(edit["reply_markup"])
    assert "var:fam:off" in data
    assert "var:fam:edit:mirror" in data
    assert "var:fam:del:mirror" in data


async def test_fam_activate_and_deactivate():
    dp, deps = await _open_listas(make_deps(variables=_repo()))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:view:mirror", mid)
    await _cb(dp, "var:fam:on:mirror", mid)
    assert deps.variables.active_family_name() == "mirror"
    assert "✅ Activa" in _last_edit(deps)["text"]
    assert _last_answer(deps)["text"] == "✅ Familia activada: Mirror / Selfie"

    await _cb(dp, "var:fam:off", mid)
    assert deps.variables.active_family_name() is None
    assert "Activa: <i>ninguna</i>" in _last_edit(deps)["text"]


async def test_delete_active_family_is_refused():
    dp, deps = await _open_listas(make_deps(variables=_repo(active="mirror")))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:del:mirror", mid)

    assert deps.variables.get_family("mirror") is not None
    assert _last_answer(deps)["show_alert"] is True
    assert "La familia activa no se puede eliminar" in _last_answer(deps)["text"]


async def test_delete_inactive_family():
    dp, deps = await _open_listas(make_deps(variables=_repo()))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:del:mirror", mid)

    assert deps.variables.get_family("mirror") is None
    assert _last_answer(deps)["text"] == "Eliminada: mirror"


async def test_edit_family_does_not_change_the_active_one():
    dp, deps = await _open_listas(make_deps(variables=_repo()))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:view:mirror", mid)
    await _cb(dp, "var:fam:edit:mirror", mid)
    payload = '{"label": "Mirror v2", "pools": {"poses": ["sentada"]}}'
    await dp.feed_update(_BOT, message_update(text_message(payload)))

    family = deps.variables.get_family("mirror")
    assert family is not None and family.label == "Mirror v2"
    assert deps.variables.active_family_name() is None
    assert any("actualizada" in text for text in _texts(deps))


async def test_cancel_after_family_edit_does_not_hijack_list_flows():
    dp, deps = await _open_listas(make_deps(variables=_repo()))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:view:mirror", mid)
    await _cb(dp, "var:fam:edit:mirror", mid)
    await dp.feed_update(
        _BOT,
        message_update(text_message('{"label": "M2", "pools": {"poses": ["de pie"]}}')),
    )
    # The family view is the first of the two messages sent by the edit flow.
    panel_mid = deps.gateway.calls_by_method("send_message")[-2]["sent"].message_id

    await _cb(dp, "var:open:poses", panel_mid)
    await _cb(dp, "var:add:poses", panel_mid)
    await _cb(dp, "var:cancel", panel_mid)

    assert "📌 Poses" in _last_edit(deps)["text"]


async def test_new_family_does_not_overwrite_an_existing_one():
    dp, deps = await _open_listas(make_deps(variables=_repo()))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:new", mid)
    await dp.feed_update(_BOT, message_update(text_message("mirror")))

    assert any("Ya existe una familia con ese identificador" in text for text in _texts(deps))
    # Still mirror's original pools: the "new" flow did not advance to fam_json.
    assert deps.variables.get_family("mirror").pools["poses"] == ("de pie",)


async def test_long_family_id_is_truncated_for_callback_data():
    dp, deps = await _open_listas(make_deps(variables=_repo(with_family=False)))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:new", mid)
    await dp.feed_update(_BOT, message_update(text_message("a" * 80)))
    await dp.feed_update(
        _BOT,
        message_update(text_message('{"label": "L", "pools": {"poses": ["de pie"]}}')),
    )

    assert deps.variables.get_families()
    assert all(len(family_id) <= 40 for family_id in deps.variables.get_families())


async def test_activating_a_stale_family_fails_closed():
    family = Family(id="mirror", label="Mirror", pools={"poses": ("de pie",)})
    repo = FakeVariablesRepo(lists={"bodies": ["x"]}, families={"mirror": family})
    dp, deps = await _open_listas(make_deps(variables=repo))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:view:mirror", mid)
    await _cb(dp, "var:fam:on:mirror", mid)

    assert deps.variables.active_family_name() is None
    answer = _last_answer(deps)
    assert answer["show_alert"] is True
    assert "no encaja con las listas activas" in answer["text"]


async def test_create_family_from_json_activates_it():
    dp, deps = await _open_listas(make_deps(variables=_repo(with_family=False)))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:new", mid)
    assert "Nueva familia" in _last_edit(deps)["text"]

    await dp.feed_update(_BOT, message_update(text_message("mirror")))
    assert any("JSON" in text for text in _texts(deps))

    payload = '{"label": "Mirror", "pools": {"poses": ["de pie"]}}'
    await dp.feed_update(_BOT, message_update(text_message(payload)))

    family = deps.variables.get_family("mirror")
    assert family is not None and family.label == "Mirror"
    assert deps.variables.active_family_name() == "mirror"
    assert any("guardada y activada" in text for text in _texts(deps))


async def test_create_family_retries_on_invalid_json():
    dp, deps = await _open_listas(make_deps(variables=_repo(with_family=False)))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:new", mid)
    await dp.feed_update(_BOT, message_update(text_message("mirror")))
    await dp.feed_update(_BOT, message_update(text_message("{not json")))

    assert any("JSON inválido" in text for text in _texts(deps))
    assert deps.variables.get_family("mirror") is None

    await dp.feed_update(
        _BOT,
        message_update(text_message('{"label": "Mirror", "pools": {"poses": ["de pie"]}}')),
    )
    assert deps.variables.get_family("mirror") is not None


async def test_create_family_rejects_unknown_value_without_leaving_state():
    dp, deps = await _open_listas(make_deps(variables=_repo(with_family=False)))
    mid = _panel_id(deps)

    await _cb(dp, "var:fam:new", mid)
    await dp.feed_update(_BOT, message_update(text_message("mirror")))
    await dp.feed_update(
        _BOT,
        message_update(text_message('{"label": "Mirror", "pools": {"poses": ["volando"]}}')),
    )

    assert any("no existe en la lista 'poses'" in text for text in _texts(deps))
    assert deps.variables.get_family("mirror") is None

    # Still in fam_json: a corrected payload succeeds.
    await dp.feed_update(
        _BOT,
        message_update(text_message('{"label": "Mirror", "pools": {"poses": ["de pie"]}}')),
    )
    assert deps.variables.get_family("mirror") is not None
