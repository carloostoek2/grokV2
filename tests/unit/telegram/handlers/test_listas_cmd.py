"""Tests del panel `/listas` (item 5, handlers/listas_cmd.py).

Autorización (variables_admin_ids / allowed_telegram_ids), navegación,
CRUD add/edit/del + plantilla (entradas de texto FSM), sección de paquetes
(ver/activar/eliminar) y la degradación D8 de "crear paquete pegando JSON".
0 red / 0 ``unittest.mock``. Fixtures anonimizados.
"""

from __future__ import annotations

from aiogram import Bot

from conftest import (
    CHAT_ID,
    USER_ID,
    FakeVariablesRepo,
    callback_query,
    callback_update,
    flat_callback_data,
    make_deps,
    make_dispatcher,
    message_update,
    text_message,
)
from grokbot.telegram.fsm_states import VarStates

_UID = USER_ID
_CHAT = CHAT_ID
_BOT = Bot("42:TEST")


def _default_variables():
    return FakeVariablesRepo(
        lists={
            "poses": ["de pie", "sentada"],
            "angles": ["frontal", "perfil"],
            "actions": ["mirando a cámara"],
        }
    )


async def _open_listas(deps=None):
    deps = deps if deps is not None else make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/listas")))
    return dp, deps


def _panel_id(deps) -> int:
    return deps.gateway.calls_by_method("send_message")[-1]["sent"].message_id


def _last_answer(deps) -> dict:
    return deps.gateway.calls_by_method("answer_callback")[-1]


def _last_edit_text(deps) -> dict:
    return deps.gateway.calls_by_method("edit_message_text")[-1]


async def _cb(dp, data: str, mid: int) -> None:
    await dp.feed_update(_BOT, callback_update(callback_query(data, message_id=mid)))


# --------------------------------------------------------------------------- #
# Autorización y guards
# --------------------------------------------------------------------------- #
async def test_listas_menu_shows_lists_and_buttons():
    dp, deps = await _open_listas()
    sent = deps.gateway.calls_by_method("send_message")[-1]
    assert sent["text"].startswith("<b>🎛 Listas de variables</b>")
    data = flat_callback_data(sent["reply_markup"])
    assert "var:open:poses" in data
    assert "var:open:angles" in data
    assert "var:open:actions" in data


async def test_listas_denied_for_non_admin_message():
    deps = make_deps(variables_admin_ids={222})
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/listas")))
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert texts[-1] == "No tienes permiso para administrar las variables."


async def test_listas_denied_for_non_admin_callback():
    deps = make_deps(variables_admin_ids={222})
    dp, deps = make_dispatcher(deps)
    await _cb(dp, "var:open:poses", mid=10)
    ans = _last_answer(deps)
    assert ans["text"] == "No tienes permiso para administrar las variables."
    assert ans["show_alert"] is True


async def test_listas_allowed_for_explicit_admin():
    deps = make_deps(variables_admin_ids={_UID})
    dp, deps = await _open_listas(deps)
    sent = deps.gateway.calls_by_method("send_message")[-1]
    assert sent["text"].startswith("<b>🎛 Listas de variables</b>")


async def test_listas_denied_in_group():
    deps = make_deps()
    dp, deps = make_dispatcher(deps)
    await dp.feed_update(_BOT, message_update(text_message("/listas", chat_type="group")))
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert texts[-1] == (
        "La administración de variables solo está disponible en chats privados."
    )


# --------------------------------------------------------------------------- #
# Navegación open / close / cancel
# --------------------------------------------------------------------------- #
async def test_open_list_shows_items():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:open:poses", mid)
    edit = _last_edit_text(deps)
    assert edit["text"].startswith("<b>📌 Poses</b> — 2 opcione")
    assert "1. de pie" in edit["text"]
    assert "2. sentada" in edit["text"]
    data = flat_callback_data(edit["reply_markup"])
    assert "var:add:poses" in data


async def test_close_clears_panel():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:close", mid)
    edit = _last_edit_text(deps)
    assert edit["text"] == "Panel de variables cerrado."
    assert edit["reply_markup"] is None


async def test_stale_callback_wrong_message_id():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:open:poses", mid + 3)
    ans = _last_answer(deps)
    assert ans["text"] == "Esta pantalla ya no está activa. Usa /listas para empezar de nuevo."
    assert ans["show_alert"] is True


# --------------------------------------------------------------------------- #
# Añadir (entrada de texto FSM)
# --------------------------------------------------------------------------- #
async def test_add_item_updates_list():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:add:poses", mid)
    assert _last_edit_text(deps)["text"].startswith("➕ <b>Añadir a Poses</b>")
    await dp.feed_update(_BOT, message_update(text_message("sentada elegante", message_id=40)))
    assert "sentada elegante" in deps.variables.get_list("poses")
    # El panel se refrescó con un mensaje nuevo (lista + teclado).
    sent = deps.gateway.calls_by_method("send_message")[-1]
    assert sent["text"].startswith("<b>📌 Poses</b> — 3 opcione")
    assert "3. sentada elegante" in sent["text"]


async def test_add_duplicate_item_error():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:add:poses", mid)
    await dp.feed_update(_BOT, message_update(text_message("de pie", message_id=41)))
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert texts[-1] == "Ese elemento ya existe en la lista."


async def test_add_short_item_error():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:add:poses", mid)
    await dp.feed_update(_BOT, message_update(text_message("a", message_id=42)))
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert "mínimo" in texts[-1].lower() or "corto" in texts[-1].lower()


async def test_cancel_returns_to_list():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:add:poses", mid)
    await _cb(dp, "var:cancel", mid)
    edit = _last_edit_text(deps)
    assert edit["text"].startswith("<b>📌 Poses</b>")


# --------------------------------------------------------------------------- #
# Editar / Eliminar (picker + entradas FSM)
# --------------------------------------------------------------------------- #
async def test_edit_item_updates_list():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:edit:poses", mid)
    picker = _last_edit_text(deps)
    assert picker["text"].startswith("<b>Editar Poses</b>")
    await _cb(dp, "var:item:edit:poses:0", mid)
    assert "Actual: de pie" in _last_edit_text(deps)["text"]
    await dp.feed_update(_BOT, message_update(text_message("de pie elegante", message_id=50)))
    assert deps.variables.get_list("poses") == ["de pie elegante", "sentada"]


async def test_edit_item_already_exists_error():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:edit:poses", mid)
    await _cb(dp, "var:item:edit:poses:1", mid)  # "sentada"
    await dp.feed_update(_BOT, message_update(text_message("de pie", message_id=51)))
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert texts[-1] == "No se pudo editar (¿elemento eliminado o duplicado?)."


async def test_delete_item_updates_list():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:del:poses", mid)
    picker = _last_edit_text(deps)
    assert picker["text"].startswith("<b>Eliminar Poses</b>")
    await _cb(dp, "var:item:del:poses:1", mid)
    assert _last_answer(deps)["text"] == "Eliminado: sentada"
    assert deps.variables.get_list("poses") == ["de pie"]
    assert _last_edit_text(deps)["text"].startswith("<b>📌 Poses</b> — 1 opcion")


# --------------------------------------------------------------------------- #
# Plantilla (entrada FSM)
# --------------------------------------------------------------------------- #
async def test_template_update_refreshes_menu():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:tmpl", mid)
    assert _last_edit_text(deps)["text"].startswith("✏️ <b>Plantilla del prompt</b>")
    new_template = "{action} — {pose} — {angle}"
    await dp.feed_update(_BOT, message_update(text_message(new_template, message_id=60)))
    assert deps.variables.get_template() == new_template
    sent = deps.gateway.calls_by_method("send_message")[-1]
    assert "<code>{action} — {pose} — {angle}</code>" in sent["text"]


async def test_template_too_short_error():
    dp, deps = await _open_listas()
    mid = _panel_id(deps)
    await _cb(dp, "var:tmpl", mid)
    await dp.feed_update(_BOT, message_update(text_message("ab", message_id=61)))
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert texts[-1] == "La plantilla es demasiado corta."


# --------------------------------------------------------------------------- #
# Paquetes (ver / activar / eliminar / D8 crear)
# --------------------------------------------------------------------------- #
def _packed_variables():
    return FakeVariablesRepo(
        lists={
            "poses": ["de pie", "sentada"],
            "angles": ["frontal"],
            "actions": ["sonriendo"],
        },
        packages={
            "fiesta": {
                "lists": {
                    "poses": ["de pie", "saltando"],
                    "angles": ["frontal"],
                    "actions": ["celebrar"],
                },
                "template": "{pose} en la {action}",
            },
            "segundo": {
                "lists": {"poses": ["corriendo"]},
                "template": "{pose}",
            },
        },
    )


async def test_packages_screen_lists_packages():
    dp, deps = await _open_listas(make_deps(variables=_packed_variables()))
    mid = _panel_id(deps)
    await _cb(dp, "var:packs", mid)
    edit = _last_edit_text(deps)
    assert edit["text"].startswith("<b>📦 Paquetes de variables</b>")
    assert "Activo: <i>personalizado (editado a mano)</i>" in edit["text"]
    # Los paquetes se listan como botones (teclado), no en el texto.
    data = flat_callback_data(edit["reply_markup"])
    assert "var:pack:view:fiesta" in data
    assert "var:pack:view:segundo" in data
    assert "var:pack:new" in data


async def test_package_view_and_activate():
    deps = make_deps(variables=_packed_variables())
    dp, deps = await _open_listas(deps)
    mid = _panel_id(deps)
    await _cb(dp, "var:packs", mid)
    await _cb(dp, "var:pack:view:fiesta", mid)
    edit = _last_edit_text(deps)
    assert edit["text"].startswith("<b>📦 fiesta</b>")
    await _cb(dp, "var:pack:activate:fiesta", mid)
    assert _last_answer(deps)["text"] == "✅ Paquete activado: fiesta"
    assert deps.variables.active_package_name() == "fiesta"
    # El payload activo reemplazó las listas activas.
    assert deps.variables.get_list("poses") == ["de pie", "saltando"]


async def test_package_delete_active_refused():
    deps = make_deps(variables=_packed_variables())
    dp, deps = await _open_listas(deps)
    mid = _panel_id(deps)
    await _cb(dp, "var:packs", mid)
    await _cb(dp, "var:pack:activate:fiesta", mid)
    await _cb(dp, "var:pack:del:fiesta", mid)
    ans = _last_answer(deps)
    assert ans["text"] == "El paquete activo no se puede eliminar."
    assert ans["show_alert"] is True


async def test_package_delete_inactive_succeeds():
    deps = make_deps(variables=_packed_variables())
    dp, deps = await _open_listas(deps)
    mid = _panel_id(deps)
    await _cb(dp, "var:packs", mid)
    await _cb(dp, "var:pack:del:segundo", mid)
    assert _last_answer(deps)["text"] == "Eliminado: segundo"
    assert "segundo" not in deps.variables.list_packages()


# --------------------------------------------------------------------------- #
# Crear paquete pegando JSON (FSM pack_name → pack_json)
# --------------------------------------------------------------------------- #
def _state_key():
    from aiogram.fsm.storage.base import StorageKey

    return StorageKey(bot_id=_BOT.id, chat_id=_CHAT, user_id=_UID)


async def _fsm_state(dp) -> str | None:
    return await dp.storage.get_state(_state_key())


async def _enter_pack_new(deps=None):
    """/listas → paquetes → ➕ Crear paquete (queda en state pack_name)."""
    deps = deps if deps is not None else make_deps(variables=_packed_variables())
    dp, deps = await _open_listas(deps)
    mid = _panel_id(deps)
    await _cb(dp, "var:packs", mid)
    await _cb(dp, "var:pack:new", mid)
    return dp, deps


async def _submit_name(dp, name: str, *, message_id: int = 40) -> None:
    await dp.feed_update(_BOT, message_update(text_message(name, message_id=message_id)))


async def test_pack_new_edits_panel_to_name_prompt():
    dp, deps = await _enter_pack_new()
    edit = _last_edit_text(deps)
    assert edit["text"] == (
        "➕ <b>Crear paquete</b>\n\nEnvía el <b>nombre</b> del paquete (ej. <i>2b_outfits</i>):"
    )
    data = flat_callback_data(edit["reply_markup"])
    assert data == ["var:cancel"]
    assert await _fsm_state(dp) == VarStates.pack_name.state


async def test_pack_name_invalid_stays_in_pack_name():
    dp, deps = await _enter_pack_new()
    await _submit_name(dp, "!!!")
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert texts[-1] == "El nombre no es válido. Usa letras, números o espacios."
    assert await _fsm_state(dp) == VarStates.pack_name.state
    # Un segundo texto válido (mismo estado) avanza a pack_json.
    await _submit_name(dp, "2b_outfits", message_id=41)
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert texts[-1].startswith("Ahora envía el <b>JSON</b> del paquete:")
    assert await _fsm_state(dp) == VarStates.pack_json.state


async def test_pack_json_valid_lists_creates_and_activates():
    deps = make_deps(variables=_packed_variables())
    dp, deps = await _enter_pack_new(deps)
    await _submit_name(dp, "2b outfits")
    payload = (
        '{ "lists": { "poses": ["de pie", "saltando"], "actions": ["celebrar"] }, '
        '"template": "{pose} en la {action}" }'
    )
    await dp.feed_update(_BOT, message_update(text_message(payload, message_id=50)))
    assert deps.variables.active_package_name() == "2b_outfits"
    assert "2b_outfits" in deps.variables.list_packages()
    # El payload activo reemplazó las listas activas y refrescó el menú + ✅.
    assert deps.variables.get_list("poses") == ["de pie", "saltando"]
    sends = deps.gateway.calls_by_method("send_message")
    assert sends[-1]["text"] == "✅ Paquete <b>2b_outfits</b> creado y activado."
    assert sends[-2]["text"].startswith("<b>🎛 Listas de variables</b>")
    assert await _fsm_state(dp) == VarStates.menu.state


async def test_pack_json_valid_fields_creates():
    deps = make_deps(variables=_packed_variables())
    dp, deps = await _enter_pack_new(deps)
    await _submit_name(dp, "Mi Paquete")
    payload = (
        '{ "fields": { "poses": ["de pie"], "actions": ["sonriendo"] }, '
        '"template": "{pose} {action}" }'
    )
    await dp.feed_update(_BOT, message_update(text_message(payload, message_id=51)))
    assert deps.variables.active_package_name() == "mi_paquete"
    assert "mi_paquete" in deps.variables.list_packages()
    sends = deps.gateway.calls_by_method("send_message")
    assert sends[-1]["text"] == "✅ Paquete <b>mi_paquete</b> creado y activado."


async def test_pack_json_invalid_json_stays():
    deps = make_deps(variables=_packed_variables())
    dp, deps = await _enter_pack_new(deps)
    await _submit_name(dp, "2b_outfits")
    await dp.feed_update(_BOT, message_update(text_message("{no es json", message_id=60)))
    texts = [c["text"] for c in deps.gateway.calls_by_method("send_message")]
    assert texts[-1] == "JSON inválido. Revisa el formato e inténtalo de nuevo."
    assert await _fsm_state(dp) == VarStates.pack_json.state
    # Un JSON válido posterior (mismo estado) completa la creación.
    ok_json = '{"lists": {"poses": ["de pie"]}, "template": "{pose}"}'
    await dp.feed_update(_BOT, message_update(text_message(ok_json, message_id=61)))
    assert deps.variables.active_package_name() == "2b_outfits"


async def test_pack_cancel_from_name_returns_menu():
    dp, deps = await _enter_pack_new()
    mid = _panel_id(deps)
    await _cb(dp, "var:cancel", mid)
    edit = _last_edit_text(deps)
    assert edit["text"].startswith("<b>🎛 Listas de variables</b>")
    assert await _fsm_state(dp) == VarStates.menu.state


async def test_pack_cancel_from_json_returns_menu():
    dp, deps = await _enter_pack_new()
    await _submit_name(dp, "2b_outfits")
    # El prompt JSON es el último mensaje enviado y lleva el botón var:cancel.
    json_prompt = deps.gateway.calls_by_method("send_message")[-1]
    json_prompt_id = json_prompt["sent"].message_id
    data = flat_callback_data(json_prompt["reply_markup"])
    assert data == ["var:cancel"]
    await _cb(dp, "var:cancel", json_prompt_id)
    edit = _last_edit_text(deps)
    assert edit["text"].startswith("<b>🎛 Listas de variables</b>")
    assert await _fsm_state(dp) == VarStates.menu.state
