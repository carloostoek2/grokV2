"""Handler de /start (item 5).

Texto de bienvenida por modelo activo (paridad grok bot.py 1066-1101). La rama
faceswap anuncia el flujo real (R4 Item 2): /cambiar_source, fotos y álbumes,
con la línea "Source ya configurado" cuando hay cara fuente.

Todo outbound sale por :class:`ChatUI` (0 ``message.answer`` directo). El deps
se inyecta por ``functools.partial`` en el registro (aiogram 3 no acepta kwargs
adicionales en ``register``).
"""

from __future__ import annotations

from functools import partial

from aiogram import Dispatcher, types
from aiogram.filters import Command

from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.formatters import model_display

_GENERIC_LINES = [
    "Envame un prompt y te genero la imagen (o video si eliges Grok Imagine Video).\n",
    "Ejemplo: <i>a cat wearing a wizard hat in a neon-lit cyberpunk alley</i>\n",
    "Tambien puedes enviar una <b>foto con caption</b> para editarla o animarla:\n",
    "la IA tomara tu imagen y aplicara los cambios que describas en el caption.\n",
]

_VIDEO_LINES = [
    "Envame un prompt y te genero un <b>video</b>.\n",
    "Ejemplo: <i>un gato descansando en un rayo de sol, moviendo la cola suavemente</i>\n",
    "Tambien puedes enviar una <b>foto con caption</b> para animarla (imagen a video):\n",
    "la IA tomara tu imagen y generara un video segun el caption.\n",
]

# Parity grok bot.py 1078-1086 (typos originales incluidos).
_FACESWAP_LINES = [
    "Modo <b>Face Swap</b> activo.\n",
    "Usa /cambiar_source para configurar la cara fuente.\n",
    "Luego envia fotos para intercambiar las caras.\n",
    "Tambien puedes enviar albumes de fotos.\n",
]


async def cmd_start(message: types.Message, deps: BotDeps) -> None:
    cfg = deps.sessions.get_config(message.from_user.id)
    model = model_display(cfg)
    if cfg.model == "grok_video":
        lines = list(_VIDEO_LINES)
    elif cfg.model == "faceswap":
        lines = list(_FACESWAP_LINES)
        if cfg.source_path:
            lines.insert(2, "Source ya configurado. Envia tus fotos.\n")
    else:
        lines = list(_GENERIC_LINES)
    lines.append(f"Modelo actual: <b>{model['name']}</b>\n")
    lines.append("Usa /config para cambiar de modelo o ajustar opciones.")

    ui = ChatUI.for_message(deps.gateway, message)
    await ui.send_text("".join(lines))


def register_start(dp: Dispatcher, deps: BotDeps) -> None:
    dp.message.register(partial(cmd_start, deps=deps), Command("start"))
