"""FSM states centralizados de la capa telegram (item 5, R10).

Espejo de config_flow.py:16-19 y variables_flow.py:63-69 de grok. El estado
efímero de confirmación de prompts (``PendingPrompts``) NO usa FSM de aiogram
(paridad ``pending_prompt`` de grok; A6) — vive en los handlers.
"""

from __future__ import annotations

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
