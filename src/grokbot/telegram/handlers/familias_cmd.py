"""Families sub-panel of ``/listas`` — coherent value pools per list.

A family narrows each referenced list to the values that make sense together so
the batch never combines incoherent options (a mirror selfie does not walk down
the street). This module owns the admin UI: list/view/activate/delete families
and create/replace them by pasting JSON.

Shared panel plumbing (guards, panel redraw, text helpers) lives in
``listas_cmd``; the dependency is one-way (this module imports from it, never the
reverse) and both are registered from ``handlers.register_all``.
"""

from __future__ import annotations

import json
from functools import partial

from aiogram import Dispatcher, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from grokbot.domain.families import Family, family_to_record
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.fsm_states import VarStates
from grokbot.telegram.handlers._common import answer_callback
from grokbot.telegram.handlers.listas_cmd import (
    LIST_LABELS,
    _esc,
    _input_text,
    _reject_non_admin_callback,
    _reject_non_admin_message,
    _reject_non_private_callback,
    _reject_non_private_message,
    _reject_stale_callback,
    _session_outdated,
    _set_panel,
    _show_new_panel,
    _slugify_package_name,
    _truncate,
)
from grokbot.telegram.keyboards import (
    families_keyboard,
    family_view_keyboard,
    variables_cancel_keyboard,
)


def _families_text(families: dict[str, Family], active: str | None) -> str:
    lines = ["<b>👪 Familias</b>\n"]
    if active:
        lines.append(f"Activa: <b>{_esc(active)}</b>\n")
    else:
        lines.append("Activa: <i>ninguna</i>\n")
    if not families:
        lines.append("<i>No hay familias creadas.</i>\n")
    for family_id, family in families.items():
        mark = " ✅" if family_id == active else ""
        lines.append(
            f"• <b>{_esc(family.label)}</b> (<code>{_esc(family_id)}</code>){mark}"
        )
    lines.append(
        "\n<i>Una familia limita los valores permitidos por campo para que las "
        "combinaciones sean coherentes (p. ej. un selfie no camina por la calle).</i>"
    )
    return "\n".join(lines)


def _family_view_text(family: Family, active: str | None) -> str:
    lines = [f"<b>👪 {_esc(family.label)}</b> (<code>{_esc(family.id)}</code>)", ""]
    if family.description:
        lines.append(_esc(family.description))
        lines.append("")
    lines.append("✅ Activa" if family.id == active else "Inactiva")
    if family.pools:
        lines.append("\n<b>Valores permitidos:</b>")
        for field, values in family.pools.items():
            label = _esc(LIST_LABELS.get(field, field))
            shown = ", ".join(_esc(_truncate(value, 40)) for value in values[:6])
            extra = f" … (+{len(values) - 6})" if len(values) > 6 else ""
            lines.append(f"• <b>{label}</b> ({len(values)}): {shown}{extra}")
    else:
        lines.append("\n<i>Sin restricciones: usa todas las listas.</i>")
    if family.avoid:
        lines.append("\n<b>Vetados:</b>")
        for field, values in family.avoid.items():
            label = _esc(LIST_LABELS.get(field, field))
            shown = ", ".join(_esc(value) for value in values[:6])
            extra = f" … (+{len(values) - 6})" if len(values) > 6 else ""
            lines.append(f"• <b>{label}</b>: {shown}{extra}")
    return "\n".join(lines)


async def _show_families(target: types.Message, state: FSMContext, deps: BotDeps) -> None:
    families = deps.manage_lists.list_families()
    active = deps.manage_lists.active_family_name()
    await _set_panel(state, target)
    ui = ChatUI.for_message(deps.gateway, target)
    await ui.edit_text(
        target.message_id,
        _families_text(families, active),
        reply_markup=families_keyboard({fid: f.label for fid, f in families.items()}),
    )


async def _show_family(
    target: types.Message,
    state: FSMContext,
    deps: BotDeps,
    family_id: str,
) -> None:
    family = deps.manage_lists.get_family(family_id)
    if family is None:
        await _show_families(target, state, deps)
        return
    active = deps.manage_lists.active_family_name()
    await _set_panel(state, target)
    ui = ChatUI.for_message(deps.gateway, target)
    await ui.edit_text(
        target.message_id,
        _family_view_text(family, active),
        reply_markup=family_view_keyboard(family_id, active),
    )


def _family_id_from_callback(callback: types.CallbackQuery) -> str | None:
    parts = callback.data.split(":", 3)
    if len(parts) != 4 or not parts[3]:
        return None
    return parts[3]


async def handle_fam_list(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    await _show_families(callback.message, state, deps)
    await answer_callback(deps.gateway, callback)


async def handle_fam_view(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    family_id = _family_id_from_callback(callback)
    if family_id is None or deps.manage_lists.get_family(family_id) is None:
        await answer_callback(deps.gateway, callback, "Familia no encontrada.", show_alert=True)
        return
    await _show_family(callback.message, state, deps, family_id)
    await answer_callback(deps.gateway, callback)


async def handle_fam_activate(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    family_id = _family_id_from_callback(callback)
    family = deps.manage_lists.get_family(family_id) if family_id else None
    if family is None:
        await answer_callback(deps.gateway, callback, "Familia no encontrada.", show_alert=True)
        return
    ok, err = deps.manage_lists.activate_family(family.id)
    if not ok:
        await answer_callback(
            deps.gateway, callback,
            err or "No se pudo activar la familia.",
            show_alert=True,
        )
        return
    await _show_family(callback.message, state, deps, family.id)
    await answer_callback(deps.gateway, callback, f"✅ Familia activada: {family.label}")


async def handle_fam_deactivate(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    deps.manage_lists.activate_family(None)
    await _show_families(callback.message, state, deps)
    await answer_callback(deps.gateway, callback, "Familia desactivada.")


async def handle_fam_del(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    family_id = _family_id_from_callback(callback)
    if family_id is None:
        await answer_callback(deps.gateway, callback, "Acción no válida.", show_alert=True)
        return
    if deps.manage_lists.active_family_name() == family_id:
        await answer_callback(
            deps.gateway, callback,
            "La familia activa no se puede eliminar. Desactívala primero.",
            show_alert=True,
        )
        return
    if not deps.manage_lists.delete_family(family_id):
        await answer_callback(deps.gateway, callback, "No se pudo eliminar la familia.", show_alert=True)
        return
    await _show_families(callback.message, state, deps)
    await answer_callback(deps.gateway, callback, f"Eliminada: {family_id}")


async def handle_fam_new(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    ui = ChatUI.for_message(deps.gateway, callback.message)
    await ui.edit_text(
        callback.message.message_id,
        "➕ <b>Nueva familia</b>\n\nEnvía el <b>identificador</b> de la familia "
        "(ej. <i>mirror</i>). Se usa en <b>/variables N mirror</b>.",
        reply_markup=variables_cancel_keyboard(),
    )
    await state.set_state(VarStates.fam_name)
    await state.update_data(
        fam_id=None,
        vars_list=None,
        vars_message_id=callback.message.message_id,
        vars_chat_id=callback.message.chat.id,
    )
    await answer_callback(deps.gateway, callback)


async def handle_fam_edit(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_non_admin_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(VarStates.menu,)):
        return
    family_id = _family_id_from_callback(callback)
    family = deps.manage_lists.get_family(family_id) if family_id else None
    if family is None:
        await answer_callback(deps.gateway, callback, "Familia no encontrada.", show_alert=True)
        return
    current = json.dumps(family_to_record(family), ensure_ascii=False)
    ui = ChatUI.for_message(deps.gateway, callback.message)
    await ui.edit_text(
        callback.message.message_id,
        f"✏️ <b>Reemplazar {_esc(family.label)}</b>\n\n"
        f"Actual: <code>{_esc(_truncate(current, 600))}</code>\n\n"
        "Envía el JSON nuevo:",
        reply_markup=variables_cancel_keyboard(),
    )
    await state.set_state(VarStates.fam_json)
    await state.update_data(
        fam_id=family.id,
        vars_list=None,
        vars_message_id=callback.message.message_id,
        vars_chat_id=callback.message.chat.id,
    )
    await answer_callback(deps.gateway, callback)


async def handle_fam_name_text(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_message(message, deps):
        return
    if await _reject_non_admin_message(message, deps):
        return
    # Keep callback_data under Telegram's 64-byte cap (var:fam:edit:<id>).
    slug = _slugify_package_name(_input_text(message))[:40]
    if not slug:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text("El identificador no es válido. Usa letras, números o espacios.")
        return
    if deps.manage_lists.get_family(slug) is not None:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text(
            "Ya existe una familia con ese identificador. "
            "Usa «Reemplazar JSON» para editarla."
        )
        return  # stay in fam_name: never overwrite an existing family
    data = await state.get_data()
    old_chat_id = data.get("vars_chat_id")
    old_message_id = data.get("vars_message_id")
    await state.set_state(VarStates.fam_json)
    await state.update_data(fam_id=slug)
    ui = ChatUI.for_message(deps.gateway, message)
    prompt_msg = await ui.send_text(
        "Ahora envía el <b>JSON</b> de la familia:\n\n"
        '<code>{ "label": "Mirror / Selfie", "description": "...", '
        '"pools": { "poses": ["..."], "camera": ["..."] }, '
        '"avoid": { "actions": ["..."] } }</code>\n\n'
        "Los campos y valores deben existir en las listas activas. "
        "También acepta <code>preferred</code> en lugar de <code>pools</code>.",
        reply_markup=variables_cancel_keyboard(),
    )
    await state.update_data(
        vars_message_id=prompt_msg.message_id,
        vars_chat_id=prompt_msg.chat_id,
    )
    if old_chat_id and old_message_id:
        try:
            await ChatUI(deps.gateway, old_chat_id).delete(old_message_id)
        except Exception:
            pass  # best-effort; the stale guard neutralizes old buttons.


async def handle_fam_json_text(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_message(message, deps):
        return
    if await _reject_non_admin_message(message, deps):
        return
    data = await state.get_data()
    family_id = data.get("fam_id")
    if not isinstance(family_id, str) or not family_id:
        await state.clear()
        await _session_outdated(message, deps)
        return
    try:
        payload = json.loads(_input_text(message))
    except json.JSONDecodeError:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text("JSON inválido. Revisa el formato e inténtalo de nuevo.")
        return  # stay in fam_json: retry with a valid JSON
    if not isinstance(payload, dict):
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text("El JSON debe ser un objeto.")
        return
    created = deps.manage_lists.get_family(family_id) is None
    ok, err = deps.manage_lists.save_family(family_id, payload)
    if not ok:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text(f"No se pudo guardar la familia: {_esc(str(err))}")
        return
    # Creating activates the new family; replacing leaves the active one as-is.
    if created:
        deps.manage_lists.activate_family(family_id)
    family = deps.manage_lists.get_family(family_id)
    if family is None:
        await state.clear()
        await _session_outdated(message, deps)
        return
    active = deps.manage_lists.active_family_name()
    await _show_new_panel(
        message, state, deps,
        _family_view_text(family, active),
        family_view_keyboard(family_id, active),
    )
    ui = ChatUI.for_message(deps.gateway, message)
    suffix = "guardada y activada" if created else "actualizada"
    await ui.send_text(f"✅ Familia <b>{_esc(family.label)}</b> {suffix}.")


def register_familias(dp: Dispatcher, deps: BotDeps) -> None:
    """Register the family text entries and callbacks (after ``register_listas``)."""
    # Text or photo+caption feed the same FSM entry.
    _input = F.text | (F.photo & F.caption)
    dp.message.register(partial(handle_fam_name_text, deps=deps), StateFilter(VarStates.fam_name), _input)
    dp.message.register(partial(handle_fam_json_text, deps=deps), StateFilter(VarStates.fam_json), _input)
    dp.callback_query.register(partial(handle_fam_list, deps=deps), lambda c: c.data == "var:fam:list")
    dp.callback_query.register(partial(handle_fam_view, deps=deps), lambda c: c.data and c.data.startswith("var:fam:view:"))
    dp.callback_query.register(partial(handle_fam_activate, deps=deps), lambda c: c.data and c.data.startswith("var:fam:on:"))
    dp.callback_query.register(partial(handle_fam_deactivate, deps=deps), lambda c: c.data == "var:fam:off")
    dp.callback_query.register(partial(handle_fam_del, deps=deps), lambda c: c.data and c.data.startswith("var:fam:del:"))
    dp.callback_query.register(partial(handle_fam_edit, deps=deps), lambda c: c.data and c.data.startswith("var:fam:edit:"))
    dp.callback_query.register(partial(handle_fam_new, deps=deps), lambda c: c.data == "var:fam:new")
