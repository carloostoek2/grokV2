"""Registro de handlers de la capa telegram (item 5).

``register_all(dp, deps)`` cablea los módulos de ``handlers/*`` a un
:class:`Dispatcher` de aiogram: cada handler recibe el :class:`BotDeps` como
kwarg de registro (inyección por constructor, sin DI global ni ``settings``).

El orden de registro importa (paridad grok): los ``Command`` de /config, /listas,
/variables y /var se registran ANTES que los flujos genéricos de texto/foto para
que los comandos ganen; los callbacks de confirm/refine/regen/cancel se registran
después de los de config/listas.
"""

from __future__ import annotations

from aiogram import Dispatcher

from grokbot.telegram.deps import BotDeps
from grokbot.telegram.handlers import (
    config_cmd,
    generation,
    jobs,
    listas_cmd,
    start,
    variables_cmd,
    video,
)

__all__ = ["register_all"]


def register_all(dp: Dispatcher, deps: BotDeps) -> None:
    """Registrar todos los handlers con sus filtros y el deps inyectado."""
    # Orden: comandos de panel/estado primero (no dejan que el texto los capture).
    start.register_start(dp, deps)
    config_cmd.register_config(dp, deps)
    listas_cmd.register_listas(dp, deps)
    variables_cmd.register_variables(dp, deps)
    # Flujos de generación (texto/foto/reply/video) después de los comandos.
    video.register_video(dp, deps)
    generation.register_generation(dp, deps)
    # Callbacks transaccionales (confirm/refine/regen/cancel).
    jobs.register_jobs(dp, deps)
