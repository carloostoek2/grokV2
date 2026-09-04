"""Middlewares de gate de la capa telegram (item 5).

Reimplementan el gate de allowlist de grok (bot.py:954-977). R9: el bot es de
un solo owner en SOLO chat privado (decisión de producto) → el gate de chat
privado es GLOBAL y se adjunta en ``main.assemble_dispatcher`` a ``dp.message``
y ``dp.callback_query``; el gate de allowlist (identidad) sigue siendo
configurable. Al denegar NO llaman al handler y emiten el mensaje por el seam
(0 ``message.answer``).

* :class:`AllowlistMiddleware` — ``allowed_ids=None`` deja pasar a todos
  (default abierto de grok). Deny por texto en messages y ``show_alert`` en
  callbacks.
* :class:`PrivateChatOnlyMiddleware` — deny cuando el chat no es privado
  (group/supergroup/channel); gate global del bot. /config y /listas conservan
  su chequeo inline (copy específico) como defensa en profundidad.

El gate de admin del panel /listas vive en el handler (chequea deps), como en
variables_flow.py:145-156.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, types

from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.ports import TelegramGateway

_NON_PRIVATE_CHAT_TYPES = frozenset({"group", "supergroup", "channel"})

_DENY_TEXT = "No tienes permiso para usar este bot."
_DENY_PRIVATE_TEXT = "Este bot solo funciona en chats privados."


def chat_is_private(chat: types.Chat) -> bool:
    """True cuando el chat es privado (tolera el enum ``ChatType`` de aiogram)."""
    chat_type = getattr(chat, "type", None)
    if chat_type is None:
        return True
    if hasattr(chat_type, "value"):
        chat_type = chat_type.value
    return str(chat_type) not in _NON_PRIVATE_CHAT_TYPES


def _event_chat(event) -> types.Chat | None:
    """Chat del evento: ``Message.chat`` o ``CallbackQuery.message.chat``."""
    if isinstance(event, types.CallbackQuery):
        message = getattr(event, "message", None)
        return getattr(message, "chat", None)
    return getattr(event, "chat", None)


class AllowlistMiddleware(BaseMiddleware):
    """Bloquea messages/callbacks cuando ``allowed_ids`` está configurado."""

    def __init__(self, gateway: TelegramGateway, allowed_ids: set[int] | None) -> None:
        self._gateway = gateway
        self._allowed_ids = allowed_ids

    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        user_id = getattr(from_user, "id", None) if from_user is not None else None
        if self._allowed_ids is not None and (
            user_id is None or user_id not in self._allowed_ids
        ):
            await self._deny(event)
            return None
        return await handler(event, data)

    async def _deny(self, event: types.TelegramObject) -> None:
        if isinstance(event, types.CallbackQuery):
            await self._gateway.answer_callback(
                event.id, _DENY_TEXT, show_alert=True
            )
            return
        chat = _event_chat(event)
        if chat is not None:
            ui = ChatUI(self._gateway, chat.id)
            await ui.send_text(_DENY_TEXT)


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Deny global cuando el chat del evento no es privado (R9: bot single-owner)."""

    def __init__(self, gateway: TelegramGateway) -> None:
        self._gateway = gateway

    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = _event_chat(event)
        if chat is None or not chat_is_private(chat):
            if isinstance(event, types.CallbackQuery):
                await self._gateway.answer_callback(
                    event.id, _DENY_PRIVATE_TEXT, show_alert=True
                )
            else:
                ui = ChatUI(self._gateway, chat.id)
                await ui.send_text(_DENY_PRIVATE_TEXT)
            return None
        return await handler(event, data)
