"""Fachada ``ChatUI`` por chat (item 5).

Encadena el ``chat_id`` a cada operación del :class:`TelegramGateway` inyectado,
de modo que los handlers/presenters nunca pasan el chat a mano y no dependen de
``message.answer`` (TODO outbound por gateway). Sin lógica de copy: solo
delegación tipada. ``for_message`` deriva el chat desde un objeto aiogram con
``.chat.id``.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from grokbot.telegram.ports import OutboundMedia, SentMessage, TelegramGateway


class ChatUI:
    """Operaciones de mensajería para un chat fijo (gateway + chat_id)."""

    __slots__ = ("_gateway", "_chat_id")

    def __init__(self, gateway: TelegramGateway, chat_id: int) -> None:
        self._gateway = gateway
        self._chat_id = chat_id

    @classmethod
    def for_message(cls, gateway: TelegramGateway, message) -> "ChatUI":
        """Construir la UI desde un objeto aiogram Message/CallbackQuery."""
        return cls(gateway, message.chat.id)

    @property
    def chat_id(self) -> int:
        return self._chat_id

    # -- texto -----------------------------------------------------------

    async def send_text(
        self,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessage:
        return await self._gateway.send_message(
            self._chat_id,
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )

    async def edit_text(
        self,
        message_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        return await self._gateway.edit_message_text(
            self._chat_id,
            message_id,
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    async def edit_caption(
        self,
        message_id: int,
        caption: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        return await self._gateway.edit_message_caption(
            self._chat_id,
            message_id,
            caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    async def edit_reply_markup(
        self,
        message_id: int,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> bool:
        return await self._gateway.edit_message_reply_markup(
            self._chat_id, message_id, reply_markup
        )

    async def delete(self, message_id: int) -> None:
        await self._gateway.delete_message(self._chat_id, message_id)

    # -- media -----------------------------------------------------------

    async def send_photo(
        self,
        photo: bytes,
        *,
        filename: str = "generated.png",
        caption: str | None = None,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessage:
        return await self._gateway.send_photo(
            self._chat_id,
            photo,
            filename=filename,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )

    async def send_video(
        self,
        video: bytes,
        *,
        filename: str = "generated.mp4",
        caption: str | None = None,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessage:
        return await self._gateway.send_video(
            self._chat_id,
            video,
            filename=filename,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )

    async def send_media_group(
        self,
        media: list[OutboundMedia],
        *,
        reply_to_message_id: int | None = None,
    ) -> list[SentMessage]:
        return await self._gateway.send_media_group(
            self._chat_id, media, reply_to_message_id=reply_to_message_id
        )
