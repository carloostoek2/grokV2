"""Panel admin `/listas` (handlers, item 5) — CRUD de listas de variables.

Parity grok ``variables_flow.py``: menú principal → pantalla de lista
(añadir/editar/eliminar con picker) → entradas de texto FSM (add/edit/template);
sección de paquetes (ver/activar/eliminar). El I/O de datos se hace por
:class:`ManageListsUseCase` + el repo ``variables`` del :class:`BotDeps`
(``get_lists``/``is_valid_list_name``/paquetes); TODO outbound por :class:`ChatUI`
y ``answer_callback``.

Autorización del panel (parity variables_flow 145-156): lista explícita
``variables_admin_ids`` → si no, allowlist ``allowed_telegram_ids`` → si no,
cualquier usuario es admin.

DEGRADACIÓN D8 por layering (SPEC §5.2: handlers sin parseo de JSON): el botón
"➕ Crear paquete" (en grok pega el JSON del paquete) responde un mensaje
disponible-en-esta-versión en vez de leer ``json.loads``. El resto del flujo de
paquetes (ver/activar/eliminar) sí opera sobre payloads ya persistidos.
"""

from __future__ import annotations

import html
from functools import partial

from aiogram import Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext

from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.fsm_states import VarStates
from grokbot.telegram.handlers._common import answer_callback
from grokbot.telegram.keyboards import (
    packages_keyboard,
    package_view_keyboard,
    variables_cancel_keyboard,
    variables_list_keyboard,
    variables_menu_keyboard,
    variables_picker_keyboard,
)

# Etiquetas humanas de las listas constantes (parity variables_flow 49-53).
LIST_LABELS = {
    "poses": "Poses",
    "angles": "Ángulos",
    "actions": "Acciones",
}

# Ítems visibles en la pantalla de lista (límite de texto de Telegram).
LIST_DISPLAY_MAX = 30
# Ítems renderizados en el picker (límite de 100 botones inline, con back).
PICKER_MAX_ITEMS = 90

_PACK_NEW_D8 = (
    "La creación de paquetes pegando JSON no está disponible en esta versión. "
    "Edita las listas activas desde el menú de /listas."
)


# --------------------------------------------------------------------------- #
# Helpers de texto (puros)
# --------------------------------------------------------------------------- #
def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _truncate(text: str, limit: int) -> str:
    """Trunca a ``limit`` reemplazando saltos por espacios (variables_flow 159-163)."""
    text = str(text).replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _pluralized(count: int) -> str:
    return "s" if count != 1 else "n"


def _menu_text(lists: dict[str, list[str]], template: str) -> str:
    lines = [
        "<b>🎛 Listas de variables</b>\n",
        "Se usan en <b>/variables N</b> para editar imágenes combinando "
        "una opción aleatoria de cada lista.\n",
    ]
    for name, items in lists.items():
        count = len(items)
        label = LIST_LABELS.get(name, name)
        lines.append(f"• <b>{label}</b>: {count} opcione{_pluralized(count)}")
    lines.append(f"\n<b>Plantilla:</b> <code>{_esc(_truncate(template, 80))}</code>")
    lines.append(
        "\n<i>Placeholders: {pose}, {angle}, {action}.</i> "
        "Toque una lista para gestionarla."
    )
    return "\n".join(lines)


def _list_text(name: str, items: list[str]) -> str:
    label = LIST_LABELS.get(name, name)
    count = len(items)
    lines = [f"<b>📌 {label}</b> — {count} opcione{_pluralized(count)}\n"]
    if not items:
        lines.append("<i>La lista está vacía.</i>")
    shown = items[:LIST_DISPLAY_MAX]
    for i, item in enumerate(shown, 1):
        lines.append(f"{i}. {_esc(_truncate(item, 80))}")
    hidden = len(items) - len(shown)
    if hidden > 0:
        lines.append(f"\n<i>… y {hidden} más (usa Editar/Eliminar para verlas).</i>")
    return "\n".join(lines)


def _packages_text(packages: list[str], active: str | None) -> str:
    lines = ["<b>📦 Paquetes de variables</b>\n"]
    if active:
        lines.append(f"Activo: <b>{_esc(active)}</b>\n")
    else:
        lines.append("Activo: <i>personalizado (editado a mano)</i>\n")
    if not packages:
        lines.append("<i>No hay paquetes guardados todavía.</i>\n")
    lines.append("Cada paquete es un conjunto de listas + plantilla. Créalo pegando su JSON.")
    return "\n".join(lines)


def _package_view_text(payload: dict) -> str:
    slug = str(payload.get("name") or payload.get("slug") or "?")
    lists = payload.get("lists") or {}
    template = payload.get("template") or ""
    lines = [f"<b>📦 {_esc(slug)}</b>", ""]
    for field, items in lists.items():
        label = LIST_LABELS.get(field, field)
        count = len(items)
        lines.append(
            f"• <b>{label}</b> (<code>{_esc(str(field))}</code>): "
            f"{count} opcione{_pluralized(count)}"
        )
    lines.append(f"\n<b>Plantilla:</b> <code>{_esc(_truncate(template, 200))}</code>")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #
def _chat_is_private(chat: types.Chat) -> bool:
    from grokbot.telegram.middlewares import chat_is_private

    return chat_is_private(chat)


def _is_admin(deps: BotDeps, user_id: int) -> bool:
    admins = deps.variables_admin_ids
    if admins is not None:
        return user_id in admins
    allowed = deps.allowed_telegram_ids
    if allowed is not None:
        return user_id in allowed
    return True


async def _reject_non_private_message(message: types.Message, deps: BotDeps) -> bool:
    if _chat_is_private(message.chat):
        return False
    ui = ChatUI.for_message(deps.gateway, message)
    await ui.send_text("La administración de variables solo está disponible en chats privados.")
    return True


async def _reject_non_admin_message(message: types.Message, deps: BotDeps) -> bool:
    if _is_admin(deps, message.from_user.id):
        return False
    ui = ChatUI.for_message(deps.gateway, message)
    await ui.send_text("No tienes permiso para administrar las variables.")
    return True


async def _reject_non_private_callback(callback: types.CallbackQuery, deps: BotDeps) -> bool:
    if callback.message is not None and _chat_is_private(callback.message.chat):
        return False
    await answer_callback(
        deps.gateway, callback,
        "La administración de variables solo está disponible en chats privados.",
        show_alert=True,
    )
    return True


async def _reject_non_admin_callback(callback: types.CallbackQuery, deps: BotDeps) -> bool:
    if _is_admin(deps, callback.from_user.id):
        return False
    await answer_callback(
        deps.gateway, callback,
        "No tienes permiso para administrar las variables.",
        show_alert=True,
    )
    return True


async def _reject_stale_callback(
    callback: types.CallbackQuery,
    state: FSMContext,
    deps: BotDeps,
    *,
    allowed_states: tuple,
) -> bool:
    """Rechaza callbacks de una pantalla vieja (parity variables_flow 114-137)."""
    data = await state.get_data()
    stored_id = data.get("vars_message_id")
    if stored_id is not None and callback.message is not None and callback.message.message_id != stored_id:
        await answer_callback(
            deps.gateway, callback,
            "Esta pantalla ya no está activa. Usa /listas para empezar de nuevo.",
            show_alert=True,
        )
        return True
    current = await state.get_state()
    allowed = {st.state for st in allowed_states}
    if current not in allowed:
        await answer_callback(
            deps.gateway, callback,
            "Esta pantalla ya no está activa. Usa /listas para empezar de nuevo.",
            show_alert=True,
        )
        return True
    return False


# --------------------------------------------------------------------------- #
# Showers (editan el mensaje del panel y refrescan vars_message_id)
# --------------------------------------------------------------------------- #
async def _set_panel(state: FSMContext, message: types.Message) -> None:
    await state.set_state(VarStates.menu)
    await state.update_data(
        vars_message_id=message.message_id,
        vars_chat_id=message.chat.id,
    )


async def _show_menu(target: types.Message, state: FSMContext, deps: BotDeps) -> None:
    lists = deps.variables.get_lists()
    template = deps.variables.get_template()
    await _set_panel(state, target)
    ui = ChatUI.for_message(deps.gateway, target)
    await ui.edit_text(
        target.message_id,
        _menu_text(lists, template),
        reply_markup=variables_menu_keyboard(lists, LIST_LABELS),
    )


async def _show_list(target: types.Message, state: FSMContext, deps: BotDeps, name: str) -> None:
    items = deps.manage_lists.get_list(name) or []
    await _set_panel(state, target)
    ui = ChatUI.for_message(deps.gateway, target)
    await ui.edit_text(
        target.message_id,
        _list_text(name, items),
        reply_markup=variables_list_keyboard(name),
    )


async def _show_packages(target: types.Message, state: FSMContext, deps: BotDeps) -> None:
    packages = deps.variables.list_packages()
    active = deps.variables.active_package_name()
    await _set_panel(state, target)
    ui = ChatUI.for_message(deps.gateway, target)
    await ui.edit_text(
        target.message_id,
        _packages_text(packages, active),
        reply_markup=packages_keyboard(packages),
    )


async def _show_package_view(target: types.Message, state: FSMContext, deps: BotDeps, slug: str) -> None:
    payload = deps.variables.load_package(slug)
    active = deps.variables.active_package_name()
    if payload is None:
        ui = ChatUI.for_message(deps.gateway, target)
        await ui.edit_text(target.message_id, "<b>📦 Paquete no encontrado.</b>", reply_markup=None)
        return
    payload.setdefault("name", slug)
    text = _package_view_text(payload)
    if active == slug:
        text += "\n<i>✅ Este paquete está activo.</i>"
    await _set_panel(state, target)
    ui = ChatUI.for_message(deps.gateway, target)
    await ui.edit_text(target.message_id, text, reply_markup=package_view_keyboard(slug, active))


async def _show_new_panel(message: types.Message, state: FSMContext, deps: BotDeps, text: str, keyboard) -> None:
    """Tras una entrada de texto: mensaje nuevo + borrado best-effort del anterior."""
    data = await state.get_data()
    old_chat_id = data.get("vars_chat_id")
    old_message_id = data.get("vars_message_id")
    ui = ChatUI.for_message(deps.gateway, message)
    new_msg = await ui.send_text(text, reply_markup=keyboard)
    await state.set_state(VarStates.menu)
    await state.update_data(
        vars_message_id=new_msg.message_id,
        vars_chat_id=new_msg.chat_id,
    )
    if old_chat_id and old_message_id:
        try:
            await ChatUI(deps.gateway, old_chat_id).delete(old_message_id)
        except Exception:
            pass  # best-effort; el stale guard vuelve inertes los botones viejos.


# --------------------------------------------------------------------------- #
# /listas
# --------------------------------------------------------------------------- #
async def cmd_listas(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_message(message, deps):
        return
    if await _reject_non_admin_message(message, deps):
        return
    await state.clear()
    lists = deps.variables.get_lists()
    template = deps.variables.get_template()
    await state.set_state(VarStates.menu)
    ui = ChatUI.for_message(deps.gateway, message)
    sent = await ui.send_text(
        _menu_text(lists, template),
        reply_markup=variables_menu_keyboard(lists, LIST_LABELS),
    )
    await state.update_data(vars_message_id=sent.message_id, vars_chat_id=sent.chat_id)


# --------------------------------------------------------------------------- #
# Navegación: open / back / close / cancel
# --------------------------------------------------------------------------- #
async def handle_var_open(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    name = callback.data.split(":", 2)[2]
    if not deps.variables.is_valid_list_name(name):
        await answer_callback(deps.gateway, callback, "Lista no válida.", show_alert=True)
        return
    await _show_list(callback.message, state, deps, name)
    await answer_callback(deps.gateway, callback)


async def handle_var_back(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    await _show_menu(callback.message, state, deps)
    await answer_callback(deps.gateway, callback)


async def handle_var_close(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    await state.clear()
    ui = ChatUI.for_message(deps.gateway, callback.message)
    await ui.edit_text(callback.message.message_id, "Panel de variables cerrado.", reply_markup=None)
    await answer_callback(deps.gateway, callback)


async def handle_var_cancel(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    """Cancela una entrada add/edit/template en curso y vuelve a la pantalla previa."""
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    current = await state.get_state()
    allowed = {
        VarStates.add_item.state,
        VarStates.edit_text.state,
        VarStates.template.state,
    }
    if current not in allowed:
        await answer_callback(deps.gateway, callback)
        return
    data = await state.get_data()
    name = data.get("vars_list")
    if isinstance(name, str) and deps.variables.is_valid_list_name(name):
        await _show_list(callback.message, state, deps, name)
    else:
        await _show_menu(callback.message, state, deps)
    await answer_callback(deps.gateway, callback)


# --------------------------------------------------------------------------- #
# Añadir / Editar / Eliminar (callbacks)
# --------------------------------------------------------------------------- #
async def _begin_add(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps, name: str) -> None:
    label = LIST_LABELS.get(name, name)
    await state.set_state(VarStates.add_item)
    await state.update_data(
        vars_list=name,
        vars_message_id=callback.message.message_id,
        vars_chat_id=callback.message.chat.id,
    )
    ui = ChatUI.for_message(deps.gateway, callback.message)
    await ui.edit_text(
        callback.message.message_id,
        f"➕ <b>Añadir a {label}</b>\n\nEnvía el nuevo elemento de la lista. "
        f"Un elemento por mensaje.",
        reply_markup=variables_cancel_keyboard(),
    )
    await answer_callback(deps.gateway, callback)


async def handle_var_add(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    name = callback.data.split(":", 2)[2]
    if not deps.variables.is_valid_list_name(name):
        await answer_callback(deps.gateway, callback, "Lista no válida.", show_alert=True)
        return
    await _begin_add(callback, state, deps, name)


async def _begin_picker(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps, name: str, action: str) -> None:
    items = deps.manage_lists.get_list(name) or []
    label = LIST_LABELS.get(name, name)
    if not items:
        await answer_callback(
            deps.gateway, callback, "La lista está vacía. Añade elementos primero.", show_alert=True,
        )
        return
    verb = "Editar" if action == "edit" else "Eliminar"
    await _set_panel(state, callback.message)
    ui = ChatUI.for_message(deps.gateway, callback.message)
    await ui.edit_text(
        callback.message.message_id,
        f"<b>{verb} {label}</b>\n\nToca el elemento:",
        reply_markup=variables_picker_keyboard(name, items, action),
    )
    await answer_callback(deps.gateway, callback)


async def handle_var_edit_list(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    name = callback.data.split(":", 2)[2]
    if not deps.variables.is_valid_list_name(name):
        await answer_callback(deps.gateway, callback, "Lista no válida.", show_alert=True)
        return
    await _begin_picker(callback, state, deps, name, "edit")


async def handle_var_del_list(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    name = callback.data.split(":", 2)[2]
    if not deps.variables.is_valid_list_name(name):
        await answer_callback(deps.gateway, callback, "Lista no válida.", show_alert=True)
        return
    await _begin_picker(callback, state, deps, name, "del")


async def _parse_item_parts(callback: types.CallbackQuery, deps: BotDeps, action: str) -> tuple[str, int] | None:
    parts = callback.data.split(":")
    if len(parts) != 5 or parts[2] != action:
        await answer_callback(deps.gateway, callback, "Acción no válida.", show_alert=True)
        return None
    _, _, _, name, index_str = parts
    if not deps.variables.is_valid_list_name(name):
        await answer_callback(deps.gateway, callback, "Lista no válida.", show_alert=True)
        return None
    try:
        index = int(index_str)
    except ValueError:
        await answer_callback(deps.gateway, callback, "Elemento no válido.", show_alert=True)
        return None
    return name, index


async def handle_var_item_edit(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    parsed = await _parse_item_parts(callback, deps, "edit")
    if parsed is None:
        return
    name, index = parsed
    items = deps.manage_lists.get_list(name) or []
    if not 0 <= index < len(items):
        await answer_callback(deps.gateway, callback, "El elemento ya no existe.", show_alert=True)
        return
    label = LIST_LABELS.get(name, name)
    await state.set_state(VarStates.edit_text)
    await state.update_data(
        vars_list=name,
        vars_index=index,
        vars_message_id=callback.message.message_id,
        vars_chat_id=callback.message.chat.id,
    )
    ui = ChatUI.for_message(deps.gateway, callback.message)
    await ui.edit_text(
        callback.message.message_id,
        f"✏️ <b>Editar {label}</b> — elemento {index + 1}\n\n"
        f"Actual: {_esc(_truncate(items[index], 120))}\n\n"
        f"Envía el nuevo texto:",
        reply_markup=variables_cancel_keyboard(),
    )
    await answer_callback(deps.gateway, callback)


async def handle_var_item_del(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    parsed = await _parse_item_parts(callback, deps, "del")
    if parsed is None:
        return
    name, index = parsed
    items = deps.manage_lists.get_list(name) or []
    if not 0 <= index < len(items):
        await answer_callback(deps.gateway, callback, "El elemento ya no existe.", show_alert=True)
        return
    removed = items[index]
    deps.manage_lists.delete_item(name, index)
    await _show_list(callback.message, state, deps, name)
    await answer_callback(deps.gateway, callback, f"Eliminado: {_truncate(removed, 40)}")


async def handle_var_tmpl(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    template = deps.variables.get_template()
    await state.set_state(VarStates.template)
    await state.update_data(
        vars_message_id=callback.message.message_id,
        vars_chat_id=callback.message.chat.id,
    )
    ui = ChatUI.for_message(deps.gateway, callback.message)
    await ui.edit_text(
        callback.message.message_id,
        "✏️ <b>Plantilla del prompt</b>\n\n"
        "Usa los placeholders <code>{pose}</code>, "
        "<code>{angle}</code> y <code>{action}</code> para insertar las "
        "opciones aleatorias.\n\n"
        f"Actual: <code>{_esc(_truncate(template, 200))}</code>\n\n"
        "Envía la nueva plantilla:",
        reply_markup=variables_cancel_keyboard(),
    )
    await answer_callback(deps.gateway, callback)


# --------------------------------------------------------------------------- #
# Entradas de texto FSM (add / edit / template)
# --------------------------------------------------------------------------- #
async def _session_outdated(message: types.Message, deps: BotDeps) -> None:
    ui = ChatUI.for_message(deps.gateway, message)
    await ui.send_text("Sesión desactualizada. Usa /listas de nuevo.")


async def handle_add_text(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_message(message, deps):
        return
    if await _reject_non_admin_message(message, deps):
        return
    data = await state.get_data()
    name = data.get("vars_list")
    if not isinstance(name, str) or not deps.variables.is_valid_list_name(name):
        await state.clear()
        await _session_outdated(message, deps)
        return
    text = message.text.strip()
    err = deps.manage_lists.validate_item(text)
    if err:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text(err)
        return
    if not deps.manage_lists.add_item(name, text):
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text("Ese elemento ya existe en la lista.")
        return
    items = deps.manage_lists.get_list(name) or []
    await _show_new_panel(
        message, state, deps,
        _list_text(name, items),
        variables_list_keyboard(name),
    )


async def handle_edit_text(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_message(message, deps):
        return
    if await _reject_non_admin_message(message, deps):
        return
    data = await state.get_data()
    name = data.get("vars_list")
    index = data.get("vars_index")
    if not isinstance(name, str) or not deps.variables.is_valid_list_name(name) or not isinstance(index, int):
        await state.clear()
        await _session_outdated(message, deps)
        return
    text = message.text.strip()
    err = deps.manage_lists.validate_item(text)
    if err:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text(err)
        return
    if not deps.manage_lists.update_item(name, index, text):
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text("No se pudo editar (¿elemento eliminado o duplicado?).")
        return
    items = deps.manage_lists.get_list(name) or []
    await _show_new_panel(
        message, state, deps,
        _list_text(name, items),
        variables_list_keyboard(name),
    )


async def handle_template_text(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_message(message, deps):
        return
    if await _reject_non_admin_message(message, deps):
        return
    text = message.text.strip()
    if len(text) < 3:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text("La plantilla es demasiado corta.")
        return
    if not deps.manage_lists.set_template(text):
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text("No se pudo guardar la plantilla.")
        return
    lists = deps.variables.get_lists()
    template = deps.variables.get_template()
    await _show_new_panel(
        message, state, deps,
        _menu_text(lists, template),
        variables_menu_keyboard(lists, LIST_LABELS),
    )


# --------------------------------------------------------------------------- #
# Paquetes (ver / activar / eliminar / D8 crear)
# --------------------------------------------------------------------------- #
async def handle_var_packs(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    await _show_packages(callback.message, state, deps)
    await answer_callback(deps.gateway, callback)


async def handle_pack_view(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await answer_callback(deps.gateway, callback, "Acción no válida.", show_alert=True)
        return
    slug = parts[3]
    if not deps.variables.package_exists(slug):
        await answer_callback(deps.gateway, callback, "Paquete no encontrado.", show_alert=True)
        return
    await _show_package_view(callback.message, state, deps, slug)
    await answer_callback(deps.gateway, callback)


async def handle_pack_activate(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await answer_callback(deps.gateway, callback, "Acción no válida.", show_alert=True)
        return
    slug = parts[3]
    if not deps.variables.activate_package(slug):
        await answer_callback(deps.gateway, callback, "No se pudo activar el paquete.", show_alert=True)
        return
    await _show_package_view(callback.message, state, deps, slug)
    await answer_callback(deps.gateway, callback, f"✅ Paquete activado: {slug}")


async def handle_pack_del(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await answer_callback(deps.gateway, callback, "Acción no válida.", show_alert=True)
        return
    slug = parts[3]
    if deps.variables.active_package_name() == slug:
        await answer_callback(deps.gateway, callback, "El paquete activo no se puede eliminar.", show_alert=True)
        return
    if not deps.variables.delete_package(slug):
        await answer_callback(deps.gateway, callback, "No se pudo eliminar el paquete.", show_alert=True)
        return
    await _show_packages(callback.message, state, deps)
    await answer_callback(deps.gateway, callback, f"Eliminado: {slug}")


async def handle_pack_new(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    """Degradación D8: crear paquete pegando JSON no se implementa (layering)."""
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    await answer_callback(deps.gateway, callback, _PACK_NEW_D8, show_alert=True)


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
def register_listas(dp: Dispatcher, deps: BotDeps) -> None:
    dp.message.register(partial(cmd_listas, deps=deps), Command("listas"))
    dp.message.register(partial(handle_add_text, deps=deps), StateFilter(VarStates.add_item), F.text)
    dp.message.register(partial(handle_edit_text, deps=deps), StateFilter(VarStates.edit_text), F.text)
    dp.message.register(partial(handle_template_text, deps=deps), StateFilter(VarStates.template), F.text)
    dp.callback_query.register(partial(handle_var_open, deps=deps), lambda c: c.data and c.data.startswith("var:open:"))
    dp.callback_query.register(partial(handle_var_add, deps=deps), lambda c: c.data and c.data.startswith("var:add:"))
    dp.callback_query.register(partial(handle_var_edit_list, deps=deps), lambda c: c.data and c.data.startswith("var:edit:"))
    dp.callback_query.register(partial(handle_var_del_list, deps=deps), lambda c: c.data and c.data.startswith("var:del:"))
    dp.callback_query.register(partial(handle_var_item_edit, deps=deps), lambda c: c.data and c.data.startswith("var:item:edit:"))
    dp.callback_query.register(partial(handle_var_item_del, deps=deps), lambda c: c.data and c.data.startswith("var:item:del:"))
    dp.callback_query.register(partial(handle_var_tmpl, deps=deps), lambda c: c.data == "var:tmpl")
    dp.callback_query.register(partial(handle_var_cancel, deps=deps), lambda c: c.data == "var:cancel")
    dp.callback_query.register(partial(handle_var_back, deps=deps), lambda c: c.data in ("var:back", "var:pack:back"))
    dp.callback_query.register(partial(handle_var_close, deps=deps), lambda c: c.data == "var:close")
    dp.callback_query.register(partial(handle_var_packs, deps=deps), lambda c: c.data == "var:packs")
    dp.callback_query.register(partial(handle_pack_view, deps=deps), lambda c: c.data and c.data.startswith("var:pack:view:"))
    dp.callback_query.register(partial(handle_pack_activate, deps=deps), lambda c: c.data and c.data.startswith("var:pack:activate:"))
    dp.callback_query.register(partial(handle_pack_del, deps=deps), lambda c: c.data and c.data.startswith("var:pack:del:"))
    dp.callback_query.register(partial(handle_pack_new, deps=deps), lambda c: c.data == "var:pack:new")


__all__ = [
    "register_listas",
    "cmd_listas",
    "_menu_text",
    "_list_text",
    "_packages_text",
    "_package_view_text",
]
