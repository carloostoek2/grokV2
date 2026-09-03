"""Adaptadores del seam de outbound de la capa telegram (item 5).

Contiene el adaptador real ``aiogram_gateway.AiogramGateway(Bot)`` que
implementa :class:`grokbot.telegram.ports.TelegramGateway`. Los tests usan un
``FakeTelegramGateway`` (conftest) — este adaptador no se ejercita en la suite
(requiere red/token).
"""

from __future__ import annotations
