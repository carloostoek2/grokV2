"""Adaptador real del seam ``TelegramGateway`` sobre aiogram (item 5).

``AiogramGateway(Bot)`` convierte los tipos neutros del port (bytes, captions,
markup) a la Bot API. Errores conocidos de edición/borrado se traducen:

* editar un mensaje sin cambios ("message is not modified") → ``False`` sin
  relanzar (R10; los handlers no tratan eso como error).
* borrar un mensaje inexistente ("message to delete not found") → no-op
  (best-effort, R10).

Este adaptador NO se ejercita en la suite unit/e2e (requiere red/token); los
tests usan ``FakeTelegramGateway``.
"""

from __future__ import annotations

from io import BytesIO

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
)

from grokbot.telegram.ports import OutboundMedia, SentMessage

_NOT_MODIFIED_FRAGMENT = "message is not modified"
_NOT_FOUND_FRAGMENT = "message to delete not found"


class AiogramGateway(Bot):
    """Implementación real de :class:`grokbot.telegram.ports.TelegramGateway`."""

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _file(data: bytes, filename: str) -> BufferedInputFile:
        return BufferedInputFile(data, filename=filename)

    @staticmethod
    def _sent(message) -> SentMessage:
        return SentMessage(message_id=message.message_id, chat_id=message.chat.id)

    @staticmethod
    def _is_not_modified(exc: TelegramBadRequest) -> bool:
        return _NOT_MODIFIED_FRAGMENT in str(exc).lower()

    @staticmethod
    def _is_not_found(exc: TelegramBadRequest) -> bool:
        return _NOT_FOUND_FRAGMENT in str(exc).lower()

    # -- texto -----------------------------------------------------------

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessage:
        msg = await super().send_message(
            chat_id,
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )
        return self._sent(msg)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            await super().edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True
        except TelegramBadRequest as exc:
            if self._is_not_modified(exc):
                return False
            raise

    async def edit_message_caption(
        self,
        chat_id: int,
        message_id: int,
        caption: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            await super().edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True
        except TelegramBadRequest as exc:
            if self._is_not_modified(exc):
                return False
            raise

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> bool:
        try:
            await super().edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            return True
        except TelegramBadRequest as exc:
            if self._is_not_modified(exc):
                return False
            raise

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            await super().delete_message(chat_id, message_id)
        except TelegramBadRequest as exc:
            if self._is_not_found(exc):
                return
            raise

    async def answer_callback(
        self,
        callback_id: str,
        text: str | None = None,
        *,
        show_alert: bool = False,
    ) -> None:
        await super().answer_callback_query(
            callback_id, text=text, show_alert=show_alert
        )

    # -- media -----------------------------------------------------------

    async def send_photo(
        self,
        chat_id: int,
        photo: bytes,
        *,
        filename: str = "generated.png",
        caption: str | None = None,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessage:
        msg = await super().send_photo(
            chat_id,
            self._file(photo, filename),
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )
        return self._sent(msg)

    async def send_video(
        self,
        chat_id: int,
        video: bytes,
        *,
        filename: str = "generated.mp4",
        caption: str | None = None,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessage:
        msg = await super().send_video(
            chat_id,
            self._file(video, filename),
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )
        return self._sent(msg)

    async def send_media_group(
        self,
        chat_id: int,
        media: list[OutboundMedia],
        *,
        reply_to_message_id: int | None = None,
    ) -> list[SentMessage]:
        items = []
        for i, item in enumerate(media):
            # Telegram solo admite caption en el primer elemento del álbum.
            caption = item.caption if i == 0 else None
            file = self._file(item.media, item.filename)
            if item.kind == "video":
                items.append(
                    InputMediaVideo(
                        media=file,
                        caption=caption,
                        parse_mode=item.parse_mode,
                    )
                )
            else:
                items.append(
                    InputMediaPhoto(
                        media=file,
                        caption=caption,
                        parse_mode=item.parse_mode,
                    )
                )
        sent = await super().send_media_group(
            chat_id,
            items,
            reply_to_message_id=reply_to_message_id,
        )
        return [self._sent(msg) for msg in sent]

    async def get_file_bytes(self, file_id: str) -> bytes:
        """Descargar un ``file_id`` de Telegram a bytes (adaptador)."""
        file = await super().get_file(file_id)
        buf = BytesIO()
        await super().download_file(file.file_path, destination=buf)
        return buf.getvalue()
