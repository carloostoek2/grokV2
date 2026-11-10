"""Cargador de LoRAs extra del flujo ComfyUI (`/config` → 🎨 LoRAs).

Apila LoRAs del usuario **encima** del flujo: los horneados en el template no se
tocan. El catálogo sale del box por el caso de uso (`ListComfyLorasUseCase`), no
por el provider directo. La pantalla abre desde `config_comfyui_keyboard` y usa
el mismo FSM de `/config`; la búsqueda consume un turno de texto en
`ConfigStates.comfyui_lora_search`.

Extraído de `config_cmd.py` (techo blando de 800 líneas): comparte los guards y
la pantalla ComfyUI de ese módulo sin ciclo de imports.
"""

from __future__ import annotations

import html
from functools import partial

from aiogram import Dispatcher, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from grokbot.domain.user_config import (
    COMFYUI_FLOW_LABELS,
    MAX_COMFYUI_LORAS_PER_FLOW,
)
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.fsm_states import ConfigStates
from grokbot.telegram.handlers._common import answer_callback
from grokbot.telegram.handlers.config_cmd import (
    _reject_non_private_callback,
    _reject_non_private_message,
    _reject_stale_callback,
    _render_comfyui_panel,
    _show_configure_screen,
)
from grokbot.telegram.keyboards import (
    COMFYUI_LORA_PAGE_SIZE,
    config_comfyui_lora_catalog_keyboard,
    config_comfyui_lora_keyboard,
    config_comfyui_lora_search_keyboard,
)


# --------------------------------------------------------------------------- #
# ComfyUI — LoRAs extra (stack por flujo)
# --------------------------------------------------------------------------- #
_LORA_STALE_LIST = "Lista desactualizada. Abre 🎨 LoRAs de nuevo."
_LORA_NO_CATALOG = (
    "No se pudo leer el catálogo de LoRAs del box. Revisa la conexión e intenta de nuevo."
)
_LORA_FULL = (
    f"Este flujo ya tiene {MAX_COMFYUI_LORAS_PER_FLOW} LoRAs extra. "
    "Quita uno para agregar otro."
)
_LORA_ALREADY = "Ese LoRA ya está en el stack."
_LORA_ADDED = "LoRA agregado. Se aplicará a las próximas imágenes."
_LORA_REMOVED = "LoRA extra quitado."
_LORA_CLEARED = "Stack vaciado: el flujo queda como de fábrica."
_LORA_SEARCH_PROMPT = (
    "Envía un fragmento del nombre del LoRA. "
    "Escribe /cancelar para volver a la pantalla de ComfyUI."
)
_LORA_LOAD_NOTE = (
    "Agregar o quitar un LoRA obliga a ComfyUI a recargar pesos: "
    "la siguiente imagen puede tardar más."
)


def _lora_screen_text(cfg, *, updated: bool = False, note: str | None = None) -> str:
    cc = cfg.comfyui
    entries = cc.loras_for(cc.model)
    flow_name = COMFYUI_FLOW_LABELS.get(cc.model, cc.model)
    header = "LoRA agregado ✅\n" if updated else ""
    lines = [
        f"{header}LoRAs extra de <b>{flow_name}</b>.",
        f"<b>Stack:</b> {len(entries)}/{MAX_COMFYUI_LORAS_PER_FLOW}",
        "",
    ]
    if entries:
        for index, entry in enumerate(entries, 1):
            lines.append(
                f"{index}. <code>{html.escape(entry.name)}</code> · "
                f"{entry.strength:.1f}"
            )
    else:
        lines.append("Sin LoRAs extra: el flujo usa solo los que trae de fábrica.")
    lines.extend(
        [
            "",
            "Se apilan al final de la cadena de modelo. "
            "Los LoRAs del flujo no se tocan.",
            "<i>Toca el nombre para ver el archivo completo; −/+ ajusta la "
            "intensidad (0.0–2.0); 🗑 quita solo ese LoRA extra.</i>",
            f"<i>{_LORA_LOAD_NOTE}</i>",
        ]
    )
    if note:
        lines.extend(["", f"<i>{note}</i>"])
    return "\n".join(lines)


def _lora_catalog_text(cfg, names, *, page: int = 0) -> str:
    cc = cfg.comfyui
    flow_name = COMFYUI_FLOW_LABELS.get(cc.model, cc.model)
    if not names:
        return (
            "No hay LoRAs en <code>models/loras</code> del box.\n\n"
            "Descarga el archivo y colócalo en esa carpeta; luego reabre esta "
            "pantalla."
        )
    total_pages = max(
        1, (len(names) + COMFYUI_LORA_PAGE_SIZE - 1) // COMFYUI_LORA_PAGE_SIZE
    )
    page = min(max(0, page), total_pages - 1)
    start = page * COMFYUI_LORA_PAGE_SIZE + 1
    end = min(start + COMFYUI_LORA_PAGE_SIZE - 1, len(names))
    return (
        f"LoRAs disponibles para <b>{flow_name}</b>.\n\n"
        f"Mostrando {start}–{end} de {len(names)}.\n"
        "✅ = ya está en el stack.\n\n"
        "<i>Solo se agrega: los LoRAs horneados del flujo no se modifican.</i>"
    )


def _lora_search_results_text(cfg, query: str, names) -> str:
    flow_name = COMFYUI_FLOW_LABELS.get(cfg.comfyui.model, cfg.comfyui.model)
    safe_query = html.escape(query)
    if not names:
        return (
            f"Sin coincidencias con «{safe_query}».\n\n"
            "Prueba con otro fragmento o vuelve al catálogo."
        )
    extra = ""
    if len(names) > COMFYUI_LORA_PAGE_SIZE:
        extra = (
            f"\nMuestro los primeros {COMFYUI_LORA_PAGE_SIZE}; "
            "afina la búsqueda para ver otros."
        )
    return (
        f"Coincidencias con «{safe_query}» en <b>{flow_name}</b>: "
        f"{len(names)}.\n"
        f"{extra}\n"
        "<i>Toca uno para agregarlo; los horneados del flujo no se tocan.</i>"
    )


async def _refresh_lora_catalog(deps: BotDeps) -> bool:
    """Consultar el box (cache del provider) y guardar el mapa short-id."""
    if deps.comfy_loras is None:
        return False
    result = await deps.comfy_loras.execute()
    if not result.ok:
        return False
    deps.lora_catalog.remember(result.names)
    return True


async def _show_lora_screen(
    target: types.Message,
    state: FSMContext,
    deps: BotDeps,
    *,
    uid: int,
    updated: bool = False,
    note: str | None = None,
) -> None:
    await state.set_state(ConfigStates.configure)
    await state.update_data(config_model="comfyui", config_message_id=target.message_id)
    ui = ChatUI.for_message(deps.gateway, target)
    cfg = deps.sessions.get_config(uid)
    await ui.edit_text(
        target.message_id,
        _lora_screen_text(cfg, updated=updated, note=note),
        reply_markup=config_comfyui_lora_keyboard(cfg.comfyui),
    )


async def _show_lora_catalog(
    target: types.Message,
    state: FSMContext,
    deps: BotDeps,
    *,
    uid: int,
    page: int = 0,
) -> None:
    await state.set_state(ConfigStates.configure)
    await state.update_data(config_model="comfyui", config_message_id=target.message_id)
    ui = ChatUI.for_message(deps.gateway, target)
    cfg = deps.sessions.get_config(uid)
    names = deps.lora_catalog.names
    selected = tuple(
        entry.name for entry in cfg.comfyui.loras_for(cfg.comfyui.model)
    )
    await ui.edit_text(
        target.message_id,
        _lora_catalog_text(cfg, names, page=page),
        reply_markup=config_comfyui_lora_catalog_keyboard(
            names, page=page, selected=selected
        ),
    )


def _index_arg(parts: list[str], position: int) -> int:
    try:
        return int(parts[position])
    except (IndexError, ValueError):
        return -1


async def handle_cfg_lora(
    callback: types.CallbackQuery, state: FSMContext, deps: BotDeps
) -> None:
    """Pantalla de LoRAs extra: abrir, catálogo, agregar, ajustar y quitar."""
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_stale_callback(
        callback,
        state,
        deps,
        allowed_states=(
            ConfigStates.configure,
            ConfigStates.comfyui_lora_search,
        ),
        required_config_models=("comfyui",),
    ):
        return
    parts = (callback.data or "").split(":")
    action = parts[2] if len(parts) > 2 else ""
    gateway = deps.gateway
    uid = callback.from_user.id
    cfg = deps.sessions.get_config(uid)
    flow_id = cfg.comfyui.model

    if action == "n":  # contador de página (sin acción)
        await answer_callback(gateway, callback)
        return
    if action == "open":
        ok = await _refresh_lora_catalog(deps)
        await _show_lora_screen(
            callback.message,
            state,
            deps,
            uid=uid,
            note=None if ok else _LORA_NO_CATALOG,
        )
        await answer_callback(gateway, callback)
        return
    if action in ("add", "p"):
        if not await _refresh_lora_catalog(deps):
            await answer_callback(gateway, callback, _LORA_NO_CATALOG, show_alert=True)
            return
        page = _index_arg(parts, 3) if action == "p" else 0
        await _show_lora_catalog(
            callback.message, state, deps, uid=uid, page=max(0, page)
        )
        await answer_callback(gateway, callback)
        return
    if action == "a":
        short_id = parts[3] if len(parts) > 3 else ""
        name = deps.lora_catalog.resolve(short_id)
        if name is None and await _refresh_lora_catalog(deps):
            name = deps.lora_catalog.resolve(short_id)
        if name is None:
            await answer_callback(gateway, callback, _LORA_STALE_LIST, show_alert=True)
            return
        result = deps.update_config.add_comfyui_lora(uid, flow_id, name)
        if not result.ok:
            await _show_lora_screen(
                callback.message, state, deps, uid=uid, note=_LORA_FULL
            )
            await answer_callback(gateway, callback, _LORA_FULL, show_alert=True)
            return
        await _show_lora_screen(
            callback.message,
            state,
            deps,
            uid=uid,
            updated=result.changed,
        )
        await answer_callback(
            gateway, callback, _LORA_ADDED if result.changed else _LORA_ALREADY
        )
        return
    if action in ("d", "u"):
        index = _index_arg(parts, 3)
        delta = -0.1 if action == "d" else 0.1
        result = deps.update_config.shift_comfyui_lora_strength(
            uid, flow_id, index, delta
        )
        if not result.ok:
            await answer_callback(gateway, callback, _LORA_STALE_LIST, show_alert=True)
            return
        new_cfg = deps.sessions.get_config(uid)
        entries = new_cfg.comfyui.loras_for(flow_id)
        strength = entries[index].strength if 0 <= index < len(entries) else 0.0
        await _show_lora_screen(callback.message, state, deps, uid=uid)
        await answer_callback(gateway, callback, f"Intensidad: {strength:.1f}")
        return
    if action == "x":
        index = _index_arg(parts, 3)
        result = deps.update_config.remove_comfyui_lora(uid, flow_id, index)
        if not result.ok:
            await answer_callback(gateway, callback, _LORA_STALE_LIST, show_alert=True)
            return
        await _show_lora_screen(callback.message, state, deps, uid=uid)
        await answer_callback(gateway, callback, _LORA_REMOVED)
        return
    if action == "s":
        index = _index_arg(parts, 3)
        entries = cfg.comfyui.loras_for(flow_id)
        if 0 <= index < len(entries):
            await answer_callback(
                gateway, callback, entries[index].name, show_alert=True
            )
            return
        await answer_callback(gateway, callback, _LORA_STALE_LIST, show_alert=True)
        return
    if action == "clear":
        deps.update_config.clear_comfyui_loras(uid, flow_id)
        await _show_lora_screen(callback.message, state, deps, uid=uid)
        await answer_callback(gateway, callback, _LORA_CLEARED)
        return
    if action == "back":
        await _show_configure_screen(
            callback.message, state, deps, "comfyui", uid=uid
        )
        await answer_callback(gateway, callback)
        return
    if action == "search":
        # El catálogo se consulta al entrar para que la lista de resultados
        # salga ya resuelta; si el box falla, se avisa al pintar.
        await _refresh_lora_catalog(deps)
        await state.set_state(ConfigStates.comfyui_lora_search)
        await state.update_data(
            config_model="comfyui", config_message_id=callback.message.message_id
        )
        ui = ChatUI.for_message(deps.gateway, callback.message)
        await ui.edit_text(
            callback.message.message_id,
            _LORA_SEARCH_PROMPT,
            reply_markup=config_comfyui_lora_search_keyboard(),
        )
        await answer_callback(gateway, callback)
        return
    await answer_callback(gateway, callback, "Opción inválida.", show_alert=True)


async def handle_cfg_lora_search_text(
    message: types.Message, state: FSMContext, deps: BotDeps
) -> None:
    """Filtro por texto del catálogo; un turno y vuelve a la pantalla ComfyUI."""
    if await _reject_non_private_message(message, deps):
        return
    if await state.get_state() != ConfigStates.comfyui_lora_search.state:
        return
    data = await state.get_data()
    panel_id = data.get("config_message_id")
    uid = message.from_user.id
    ui = ChatUI.for_message(deps.gateway, message)
    query = (message.text or "").strip()
    await state.set_state(ConfigStates.configure)

    if query.lower() in ("/cancelar", "cancelar", "/cancel"):
        if panel_id is None:
            sent = await ui.send_text("ComfyUI")
            panel_id = sent.message_id
            await state.update_data(config_message_id=panel_id)
        await _render_comfyui_panel(ui, panel_id, deps, uid)
        return

    catalog_ok = await _refresh_lora_catalog(deps)
    needle = query.casefold()
    names = tuple(
        name for name in deps.lora_catalog.names if needle in name.casefold()
    )
    cfg = deps.sessions.get_config(uid)
    selected = tuple(
        entry.name for entry in cfg.comfyui.loras_for(cfg.comfyui.model)
    )
    text = _lora_search_results_text(cfg, query, names)
    if not catalog_ok:
        text += "\n\n<i>No se pudo leer el catálogo del box.</i>"
    markup = config_comfyui_lora_catalog_keyboard(
        names[:COMFYUI_LORA_PAGE_SIZE], selected=selected
    )
    if panel_id is None:
        sent = await ui.send_text(text, reply_markup=markup)
        await state.update_data(config_message_id=sent.message_id)
        return
    await ui.edit_text(panel_id, text, reply_markup=markup)


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
def register_config_lora(dp: Dispatcher, deps: BotDeps) -> None:
    """Registrar la búsqueda por texto y los callbacks ``cfg:lora:*``.

    Se llama desde ``register_all`` justo después de ``config_cmd`` y antes de
    los flujos de generación, para que el turno de búsqueda no lo capture el
    handler genérico de texto.
    """
    dp.message.register(
        partial(handle_cfg_lora_search_text, deps=deps),
        StateFilter(ConfigStates.comfyui_lora_search),
    )
    dp.callback_query.register(
        partial(handle_cfg_lora, deps=deps),
        lambda c: c.data and c.data.startswith("cfg:lora:"),
    )


__all__ = [
    "handle_cfg_lora",
    "handle_cfg_lora_search_text",
    "register_config_lora",
]
