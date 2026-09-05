"""ResultSender — envía el media de un :class:`ItemResult` y persiste refs (R4).

Reimplementa el fan-out de grok ``process_image_result`` (bot.py:4973-5063),
``_send_comfyui_image/album/video`` (4087-4310) y ``process_video_result``
(5373-5422): URL única → photo, multi-URL → N photos separadas con variante
``{i+1}/{N}``, ComfyUI local 1 → photo y N → álbum (máx 10), video local/remoto
→ ``send_video``. R10: un video remoto que supera el tope único
(``media.MAX_MEDIA_BYTES``) o que la Bot API rechaza se degrada ofreciendo la
URL firmada como camino de recuperación (chat privado del dueño único; paridad
grok bot.py 5402-5407).

Reglas de la capa (SPEC §5.2 / PLAN D1):

* El I/O de media (descargar URL, leer file_paths locales, enviar/editar/borrar)
  vive acá, nunca en handlers. ``MediaDownloader`` y ``TelegramGateway`` se
  inyectan (0 red en tests, 0 ``unittest.mock``).
* ``generation_refs`` se guarda POST-envío con el ``message_id`` real (álbum →
  ``sent[0]``; multi-URL → índice por foto). ``regen_context`` viaja opaco.
* Errores user-safe (R6): un fallo de descarga/lectura edita el status message y
  devuelve ``None`` (el flujo decide si reintenta/finaliza). Las URLs de
  contenido no se loguean; la de un video no enviable se le muestra al dueño
  único (chat privado), nunca se publica fuera de ese chat.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.exceptions import TelegramBadRequest

from grokbot.application.events import ItemResult
from grokbot.repositories.base import GenerationRefsRepository
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.downloader import DownloadTooLargeError
from grokbot.telegram.formatters import (
    SENSITIVE_DOWNLOAD_WARNING,
    format_result_caption,
)
from grokbot.telegram.keyboards import image_regenerate_keyboard
from grokbot.telegram.media import MAX_MEDIA_BYTES
from grokbot.telegram.ports import (
    MediaDownloader,
    OutboundMedia,
    SentMessage,
    TelegramGateway,
)

# --- Copy de errores de lectura/descarga (grok, user-safe R6/R10) ------------
_ERR_NO_IMAGE = "No se pudo leer la imagen generada."
_ERR_NO_VIDEO = "No se pudo leer el video generado."
_ERR_NO_ALBUM = "No se pudieron leer las imágenes generadas."
_ERR_NO_URL = "Error: el modelo no devolvio ninguna URL. Intenta con otro prompt."
_ERR_VIDEO_REJECTED = (
    "No se pudo enviar el video por Telegram. "
    "Prueba con otro modelo o una duración/resolución menor."
)
_GENERIC_DOWNLOAD_ERROR = "No se pudo descargar el archivo. Intenta de nuevo más tarde."

_COMFYUI_FILENAME = "comfyui.png"
_GENERATED_FILENAME = "generated.png"
_GENERATED_VIDEO_FILENAME = "generated.mp4"


def _owner_uid(item: ItemResult, owner_uid: int | None) -> int | None:
    """Dueño de la generación para persistir en el ref (R8).

    Prioriza el ``owner_uid`` explícito que hiloan los presenters (el user que
    corrió el flujo); si un call site no lo pasa, cae al ``user_id`` que
    ``generate_image`` estampa en TODO ``regen_context`` de imagen (nunca
    fail-open en producción).
    """
    if owner_uid is not None:
        return int(owner_uid)
    ctx = item.regen_context or {}
    uid = ctx.get("user_id")
    return int(uid) if uid is not None else None


@dataclass(frozen=True)
class SentItem:
    """Lo que se envió para un ítem: mensajes + forma (single/álbum/video)."""

    primary: SentMessage
    sent: tuple[SentMessage, ...]
    is_album: bool = False
    kind: str = "image"  # "image" | "album" | "video"


def is_album_item(item: ItemResult) -> bool:
    """True cuando el resultado es un álbum (ComfyUI local con N>1 imágenes).

    Las URLs múltiples NO son álbum (grok las envía como N photos separadas).
    """
    meta = item.result.meta or {}
    paths = meta.get("file_paths") or ([item.result.file_path] if item.result.file_path else [])
    return len(paths) > 1


def caption_model_from_request(item: ItemResult) -> dict | None:
    """Reconstruir un dict de modelo para el caption desde ``ItemResult.request``.

    Devuelve la key ``comfyui_model`` (id de flujo) para resultados ComfyUI (el
    formato que espera ``formatters.format_model_caption``); None para el resto
    (el caption muestra el prefijo/variante).
    """
    req = item.request
    if req is None or req.provider != "comfyui":
        return None
    params = req.params or {}
    model = params.get("model")
    if model is None:
        return None
    return {"comfyui_model": model}


class ResultSender:
    """Envía resultados (fan-out URL/local/video/álbum) y guarda refs post-send."""

    def __init__(
        self,
        *,
        gateway: TelegramGateway,
        downloader: MediaDownloader,
        refs: GenerationRefsRepository,
    ) -> None:
        self._gateway = gateway
        self._downloader = downloader
        self._refs = refs

    # ------------------------------------------------------------------ image
    async def send_image(
        self,
        ui: ChatUI,
        item: ItemResult,
        prefix: str,
        *,
        status_id: int | None = None,
        delete_status: bool = True,
        caption_model: dict | None = None,
        caption_prompt: bool = False,
        reply_markup=None,
        save_ref: bool = True,
        owner_uid: int | None = None,
    ) -> SentItem | None:
        """Enviar el resultado de IMAGEN del ítem (single/álbum/multi-URL).

        Devuelve ``None`` cuando no se pudo enviar nada (el status ya refleja el
        error user-safe). ``status_id``/``delete_status`` controlan el status del
        flujo (paridad grok: batch edita, single borra). ``owner_uid`` (R8) es el
        user que corrió el flujo; se persiste en el ref para scopear Regenerar.
        """
        result = item.result
        meta = result.meta or {}
        elapsed = meta.get("elapsed_sec")
        allowlist = meta.get("download_allowlist")
        caption_model = caption_model if caption_model is not None else caption_model_from_request(item)
        owner = _owner_uid(item, owner_uid)

        # Rama local (ComfyUI): 1 → photo, N → álbum.
        paths = meta.get("file_paths") or ([result.file_path] if result.file_path else [])
        if paths:
            return await self._send_local_images(
                ui, item, prefix, paths,
                status_id=status_id, delete_status=delete_status,
                caption_model=caption_model, caption_prompt=caption_prompt,
                reply_markup=reply_markup, save_ref=save_ref,
                owner_uid=owner,
            )

        urls = meta.get("urls") or ([result.remote_url] if result.remote_url else [])
        urls = [u for u in urls if u]
        if not urls:
            if status_id is not None:
                await ui.edit_text(status_id, _ERR_NO_URL, reply_markup=None)
            return None

        if len(urls) == 1:
            data = await self._download(ui, urls[0], allowlist=allowlist, status_id=status_id)
            if data is None:
                return None
            caption = format_result_caption(
                prefix, elapsed, model=caption_model,
                prompt=item.prompt if caption_prompt else None,
            )
            kb = image_regenerate_keyboard() if reply_markup is None else reply_markup
            sent = await ui.send_photo(
                data, filename=_GENERATED_FILENAME, caption=caption, reply_markup=kb
            )
            if save_ref:
                self._save_image_ref(ui.chat_id, sent, meta, item, index=meta.get("index", 0), owner_uid=owner)
            if delete_status and status_id is not None:
                await ui.delete(status_id)
            return SentItem(primary=sent, sent=(sent,), kind="image")

        # Multi-URL → N photos separadas con variante i/N (nunca álbum).
        sent_all: list[SentMessage] = []
        for i, url in enumerate(urls):
            data = await self._download(ui, url, allowlist=allowlist, status_id=status_id)
            if data is None:
                break
            caption = format_result_caption(
                prefix, elapsed, variant=f"{i + 1}/{len(urls)}",
                model=caption_model, prompt=item.prompt if caption_prompt else None,
            )
            kb = image_regenerate_keyboard() if reply_markup is None else reply_markup
            sent = await ui.send_photo(
                data, filename=_GENERATED_FILENAME, caption=caption, reply_markup=kb
            )
            sent_all.append(sent)
            if save_ref:
                self._save_image_ref(ui.chat_id, sent, meta, item, index=i, owner_uid=owner)
        if not sent_all:
            return None
        if delete_status and status_id is not None:
            await ui.delete(status_id)
        return SentItem(primary=sent_all[0], sent=tuple(sent_all), kind="image")

    # ------------------------------------------------------------------ video
    async def send_video(
        self,
        ui: ChatUI,
        item: ItemResult,
        prefix: str,
        *,
        status_id: int | None = None,
        delete_status: bool = True,
        caption_model: dict | None = None,
        owner_uid: int | None = None,
    ) -> SentItem | None:
        """Enviar el resultado de VIDEO (local ComfyUI o URL remota).

        Video remoto > tope de Telegram → fallback de texto con la URL y el
        warning (grok bot.py 5402-5407); nunca se reenvía por otro medio.
        ``owner_uid`` (R8): user que corrió el flujo, persistido en el ref.
        """
        result = item.result
        meta = result.meta or {}
        elapsed = meta.get("elapsed_sec")
        caption_model = caption_model if caption_model is not None else caption_model_from_request(item)
        owner = _owner_uid(item, owner_uid)

        paths = meta.get("file_paths") or ([result.file_path] if result.file_path else [])
        if paths:
            data = self._read_local(paths[0])
            if data is None:
                if status_id is not None:
                    await ui.edit_text(status_id, _ERR_NO_VIDEO, reply_markup=None)
                return None
            caption = format_result_caption(
                prefix, elapsed, model=caption_model,
                prompt=item.prompt if meta.get("caption_prompt") else None,
            )
            try:
                sent = await ui.send_video(
                    data, filename=_GENERATED_VIDEO_FILENAME,
                    caption=caption, reply_markup=image_regenerate_keyboard(),
                )
            except TelegramBadRequest:
                # C9c/R10: Telegram rechaza el video local (códec/formato o >
                # MAX_MEDIA_BYTES) → degradar user-safe, sin exponer path.
                await self._degrade_video_rejected(ui, status_id)
                return None
            if item.regen_context is not None:
                self._refs.save(
                    ui.chat_id, sent.message_id,
                    provider="comfyui", kind="video",
                    prompt=item.prompt, regen=item.regen_context,
                    owner_uid=owner,
                )
            if delete_status and status_id is not None:
                await ui.delete(status_id)
            return SentItem(primary=sent, sent=(sent,), kind="video")

        url = result.remote_url or (meta.get("urls") or [None])[0]
        if not url:
            if status_id is not None:
                await ui.edit_text(status_id, "Error: el modelo no devolvió URL de video. Intenta con otro prompt.", reply_markup=None)
            return None

        caption = format_result_caption(prefix, elapsed, model=caption_model)
        try:
            data = await self._downloader.download(
                url, allowlist=meta.get("download_allowlist")
            )
        except DownloadTooLargeError:
            # R10: el video supera MAX_MEDIA_BYTES → Telegram no lo acepta. La
            # URL firmada es el camino de recuperación (chat privado del dueño);
            # no se descarga el cuerpo del archivo.
            await self._offer_remote_video_link(
                ui, url, status_id=status_id,
                reason="El video es demasiado grande para Telegram.",
            )
            return None
        except Exception as exc:  # noqa: BLE001 — errores de descarga user-safe
            message = getattr(exc, "user_message", None) or _GENERIC_DOWNLOAD_ERROR
            if status_id is not None:
                await ui.edit_text(status_id, str(message), reply_markup=None)
            return None
        if len(data) > MAX_MEDIA_BYTES:
            # Invariante con AiohttpMediaDownloader (corta con la misma cota);
            # cubre downloaders que no la apliquen. Misma URL de recuperación.
            await self._offer_remote_video_link(
                ui, url, status_id=status_id,
                reason="El video es demasiado grande para Telegram.",
            )
            return None

        try:
            sent = await ui.send_video(
                data, filename=_GENERATED_VIDEO_FILENAME, caption=caption
            )
        except TelegramBadRequest:
            # R10: la Bot API rechaza el envío (códec/duración) pese a estar bajo
            # el tope → la URL firmada sigue siendo el camino de recuperación.
            await self._offer_remote_video_link(
                ui, url, status_id=status_id,
                reason="No se pudo enviar el video por Telegram.",
            )
            return None
        if delete_status and status_id is not None:
            await ui.delete(status_id)
        return SentItem(primary=sent, sent=(sent,), kind="video")

    # --------------------------------------------------------------- helpers
    async def _send_local_images(
        self,
        ui: ChatUI,
        item: ItemResult,
        prefix: str,
        paths: list[str],
        *,
        status_id: int | None,
        delete_status: bool,
        caption_model: dict | None,
        caption_prompt: bool,
        reply_markup,
        save_ref: bool,
        owner_uid: int | None,
    ) -> SentItem | None:
        meta = item.result.meta or {}
        elapsed = meta.get("elapsed_sec")

        if len(paths) == 1:
            data = self._read_local(paths[0])
            if data is None:
                if status_id is not None:
                    await ui.edit_text(status_id, _ERR_NO_IMAGE, reply_markup=None)
                return None
            caption = format_result_caption(
                prefix, elapsed, model=caption_model,
                prompt=item.prompt if caption_prompt else None,
            )
            kb = image_regenerate_keyboard() if reply_markup is None else reply_markup
            sent = await ui.send_photo(
                data, filename=_COMFYUI_FILENAME, caption=caption, reply_markup=kb
            )
            if save_ref:
                self._refs.save(
                    ui.chat_id, sent.message_id,
                    provider="comfyui", kind="image",
                    prompt=item.prompt, regen=item.regen_context,
                    owner_uid=owner_uid,
                )
            if delete_status and status_id is not None:
                await ui.delete(status_id)
            return SentItem(primary=sent, sent=(sent,), kind="image")

        media: list[OutboundMedia] = []
        for i, path in enumerate(paths[:10]):
            data = self._read_local(path)
            if data is None:
                continue
            caption = (
                format_result_caption(
                    prefix, elapsed, model=caption_model,
                    prompt=item.prompt if caption_prompt else None,
                )
                if i == 0 else None
            )
            media.append(
                OutboundMedia(
                    media=data, filename=f"comfyui_{i}.png",
                    caption=caption, kind="photo",
                )
            )
        if not media:
            if status_id is not None:
                await ui.edit_text(status_id, _ERR_NO_ALBUM, reply_markup=None)
            return None
        sent_group = await ui.send_media_group(media)
        if save_ref and sent_group:
            self._refs.save(
                ui.chat_id, sent_group[0].message_id,
                provider="comfyui", kind="image",
                prompt=item.prompt, regen=item.regen_context,
                owner_uid=owner_uid,
            )
        if delete_status and status_id is not None:
            await ui.delete(status_id)
        return SentItem(
            primary=sent_group[0], sent=tuple(sent_group), is_album=True, kind="album"
        )

    async def _degrade_video_rejected(self, ui: ChatUI, status_id: int | None) -> None:
        """Degrada un video LOCAL no enviable: edita status o manda texto user-safe.

        Sin URL: un video local de ComfyUI no tiene link de recuperación (C9c).
        """
        if status_id is not None:
            await ui.edit_text(status_id, _ERR_VIDEO_REJECTED, reply_markup=None)
        else:
            await ui.send_text(_ERR_VIDEO_REJECTED)

    async def _offer_remote_video_link(
        self,
        ui: ChatUI,
        url: str,
        *,
        reason: str,
        status_id: int | None,
    ) -> None:
        """Degrada un video REMOTO no enviable ofreciendo la URL de recuperación.

        Chat privado del dueño único (R10, paridad grok): la URL firmada es el
        camino para descargar un video que Telegram no acepta (supera el tope o
        la API lo rechaza). ``reason`` es el encabezado user-safe y se compone
        con el enlace + el warning; nunca se loguea la URL.
        """
        text = f"{reason}\nDescárgalo aquí:\n{url}{SENSITIVE_DOWNLOAD_WARNING}"
        if status_id is not None:
            await ui.edit_text(status_id, text, reply_markup=None)
        else:
            await ui.send_text(text)

    @staticmethod
    def _read_local(path: str) -> bytes | None:
        try:
            with open(path, "rb") as f:
                return f.read()
        except (OSError, TypeError):
            return None

    async def _download(
        self,
        ui: ChatUI,
        url: str,
        *,
        allowlist: str | None,
        status_id: int | None,
    ) -> bytes | None:
        """Descarga URL → bytes; error user-safe al status. Nunca loguea URL."""
        try:
            return await self._downloader.download(url, allowlist=allowlist)
        except Exception as exc:  # noqa: BLE001 — todos los errores son user-safe
            message = getattr(exc, "user_message", None) or _GENERIC_DOWNLOAD_ERROR
            if status_id is not None:
                await ui.edit_text(status_id, str(message), reply_markup=None)
            return None

    def _save_image_ref(
        self,
        chat_id: int,
        sent: SentMessage,
        meta: dict,
        item: ItemResult,
        *,
        index: int,
        owner_uid: int | None = None,
    ) -> None:
        """Persiste generation_ref POST-envío (single: índice 0; multi: i)."""
        kie_task_id = meta.get("task_id") if meta.get("provider") == "kie" else None
        if kie_task_id is not None:
            provider = meta.get("provider", "kie")
        else:
            regen = item.regen_context or {}
            provider = regen.get("provider", "unknown")
        self._refs.save(
            chat_id,
            sent.message_id,
            kie_task_id=kie_task_id,
            kie_index=index,
            provider=provider,
            kind="image",
            prompt=item.prompt,
            regen=item.regen_context,
            owner_uid=owner_uid,
        )


__all__ = ["ResultSender", "SentItem", "is_album_item", "caption_model_from_request"]
