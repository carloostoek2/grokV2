"""Seam de outbound de la capa telegram (D1, item 5).

La capa de presentación NUNCA habla con Telegram directo: cada operación de
envío/edición/borrado/media sale por :class:`TelegramGateway` (Protocol) que
implementan el adaptador real ``adapters/aiogram_gateway.AiogramGateway`` y los
fakes de test. :class:`ChatUI` (chat_ui.py) encadena el ``chat_id`` a cada
operación.

También se declara :class:`MediaDownloader`: el fetch de media remota (URL →
bytes) se inyecta para que los handlers no abran sockets (SPEC §5.2) y la suite
sea offline (0 red).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aiogram.types import InlineKeyboardMarkup


@dataclass(frozen=True)
class SentMessage:
    """Un mensaje enviado: ids reales devueltos por Telegram."""

    message_id: int
    chat_id: int


@dataclass(frozen=True)
class OutboundMedia:
    """Media cruda a enviar en un álbum (bytes + filename + caption).

    ``kind`` distingue photo/video para elegir el InputMedia en el álbum; el
    sender lo setea según el media que normalizó.
    """

    media: bytes
    filename: str
    caption: str | None = None
    parse_mode: str = "HTML"
    kind: str = "photo"  # "photo" | "video"


class TelegramGateway(Protocol):
    """Contrato de outbound hacia la Bot API de Telegram (sin chat_id fijo)."""

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> SentMessage: ...

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
    ) -> SentMessage: ...

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
    ) -> SentMessage: ...

    async def send_media_group(
        self,
        chat_id: int,
        media: list[OutboundMedia],
        *,
        reply_to_message_id: int | None = None,
    ) -> list[SentMessage]: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool: ...

    async def edit_message_caption(
        self,
        chat_id: int,
        message_id: int,
        caption: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool: ...

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> bool: ...

    async def delete_message(self, chat_id: int, message_id: int) -> None: ...

    async def answer_callback(
        self,
        callback_id: str,
        text: str | None = None,
        *,
        show_alert: bool = False,
    ) -> None: ...

    async def get_file_bytes(self, file_id: str) -> bytes: ...


class MediaDownloader(Protocol):
    """Descarga de media remota (URL → bytes) con allowlist de hosts (R7/D5)."""

    async def download(self, url: str, *, allowlist: str | None = None) -> bytes: ...
