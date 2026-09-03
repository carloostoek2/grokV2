"""Capa de presentación de Telegram (item 5).

Traduce 1:1 el vocabulario de eventos tipados de :mod:`grokbot.application`
(item 4) a mensajes, teclados y media de Telegram con paridad de copy/UX de
``grok/bot.py`` + ``config_flow.py`` + ``variables_flow.py`` (referencia
semántica read-only).

Reglas duras (SPEC §5.1/§5.2):

* Nada de aiohttp/replicate/subprocess/JSON directo desde handlers.
* Todo outbound de media (enviar/editar/borrar/descargar) sale por el seam
  ``TelegramGateway``/``MediaDownloader`` inyectados (ports.py) — 0 red en
  tests, 0 ``unittest.mock``.
* Este paquete NO arma DI global ni ``main.py`` (item 6); expone
  ``handlers.register_all(dp, deps)`` para que el ensamblado lo reutilice.
* Los handlers reciben updates de aiogram y traducen a use cases; nunca
  llaman ``message.answer/edit_text`` directo (TODO outbound por ChatUI).
"""

from __future__ import annotations

# Intencionalmente sin re-exports perezosos (M2 item 4): importar la capa no
# debe tocar settings/env. Los submódulos se importan de forma explícita.
