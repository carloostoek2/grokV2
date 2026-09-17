"""FSM states centralizados de la capa telegram (item 5, R10).

Espejo de config_flow.py:16-19 y variables_flow.py:63-69 de grok. El estado
efímero de confirmación de prompts (``PendingPrompts``) NO usa FSM de aiogram
(paridad ``pending_prompt`` de grok; A6) — vive en los handlers.
"""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


class ConfigStates(StatesGroup):
    """Estados del FSM de /config (modelo → proveedor → variante/opciones)."""

    select_model = State()
    select_provider = State()
    configure = State()


class VarStates(StatesGroup):
    """Estados del FSM del panel admin /listas."""

    menu = State()
    add_item = State()
    edit_text = State()
    template = State()
    pack_name = State()
    pack_json = State()


# Entradas de texto del panel /listas (add/edit/template/paquete). Mientras el
# usuario está en uno de estos estados, generation NO debe encolar ComfyUI /
# edits: el update lo consume listas_cmd (texto o caption de foto).
VAR_TEXT_ENTRY_STATES: frozenset[str] = frozenset(
    {
        VarStates.add_item.state,
        VarStates.edit_text.state,
        VarStates.template.state,
        VarStates.pack_name.state,
        VarStates.pack_json.state,
    }
)


async def in_var_text_entry(state: FSMContext) -> bool:
    """True cuando el FSM de /listas espera texto (o caption) del admin."""
    current = await state.get_state()
    return current in VAR_TEXT_ENTRY_STATES
