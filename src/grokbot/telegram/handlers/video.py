"""Orquestación de video (handlers, item 5).

Reimplementa ``_do_generate_video`` de grok (bot.py 1633-1699) sobre el caso de
uso :class:`GenerateVideoUseCase` + :func:`present_video`. Sin job (A3): el video
es single-attempt y no muestra botón Cancelar. Cubre text→video (grok_video) y
foto/reply→video (imagen a video) que los handlers de generation/config deciden.

El status inicial se crea acá con el copy exacto de grok: texto → ``_video_start_message``;
imagen a video → "Animando imagen con <b>{video_model}</b>...". El ``prefix`` de
caption es "Prompt" para texto y "Edit" cuando hay imagen fuente.

``register_video`` no registra updates propios: los flujos de video se rutean por
generation (text/foto/reply) y config (ComfyUI video), que llaman a
:func:`run_video_generation` — el módulo expone la orquestación reutilizable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import partial
from typing import TYPE_CHECKING

from aiogram import Dispatcher, types

from grokbot.application.events import ItemFailed, ItemResult
from grokbot.domain.user_config import UserConfig
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.formatters import (
    VIDEO_MODEL_LABELS,
    escape,
    video_start_message,
)
from grokbot.telegram.handlers._common import make_sender
from grokbot.telegram.stream_presenter import present_video

if TYPE_CHECKING:
    from grokbot.domain.generation import ImageSource

__all__ = [
    "is_video_cfg",
    "video_model_id",
    "video_model_label",
    "run_video_generation",
    "register_video",
]


def is_video_cfg(cfg: UserConfig) -> bool:
    """True cuando la config genera video: grok_video o ComfyUI con modelo video."""
    if cfg.model == "grok_video":
        return True
    if cfg.model == "comfyui":
        from grokbot.domain.user_config import is_comfy_video_model

        return is_comfy_video_model(cfg.comfyui.model)
    return False


def video_model_id(cfg: UserConfig) -> str:
    """Id crudo del modelo de video de la config (``cfg.video.model``)."""
    return cfg.video.model


def video_model_label(cfg: UserConfig) -> str:
    """Label de UI del modelo de video (parity grok ``VIDEO_MODEL_LABELS``)."""
    return VIDEO_MODEL_LABELS.get(cfg.video.model, cfg.video.model)


def _i2v_status_text(video_model_id: str, prompt: str) -> str:
    """Status de imagen a video (grok bot.py 1660-1663, HTML)."""
    return (
        f"Animando imagen con <b>{escape(video_model_id)}</b>...\n\n"
        f"<i>{escape(prompt)}</i>"
    )


async def run_video_generation(
    deps: BotDeps,
    message: types.Message,
    *,
    uid: int,
    cfg: UserConfig,
    prompt: str,
    source_image: bytes | None = None,
    source: "ImageSource | None" = None,
    prefix: str = "Prompt",
    status_id: int | None = None,
    status_text: str | None = None,
    delete_status: bool = True,
    reply_to: int | None = None,
) -> None:
    """Corre la generación de video y presenta el resultado (single-attempt).

    ``status_id`` permite reusar un mensaje ya editado (confirm de grok_video);
    si es ``None`` se crea el status con el texto de arranque apropiado
    (texto vs imagen a video). Devuelve cuando el flujo termina; un
    ``ItemFailed`` (aun ``terminal=False``) finaliza (O3). ``reply_to`` fija el
    mensaje al que responde el video (default: el mensaje que lo invocó).
    """
    ui = ChatUI.for_message(deps.gateway, message)
    sender = make_sender(deps)
    raw_model = cfg.video.model
    label = video_model_label(cfg)

    if status_id is None:
        if status_text is None:
            if source_image is not None or source is not None:
                status_text = _i2v_status_text(raw_model, prompt)
            else:
                status_text = video_start_message(raw_model, prompt)
        sent = await ui.send_text(status_text)
        status_id = sent.message_id

    events = deps.generate_video.run(
        user_id=uid,
        prompt=prompt,
        source_image=source_image,
        source=source,
    )
    await present_video(
        ui,
        events,
        model_id=raw_model,
        prompt=prompt,
        sender=sender,
        prefix=prefix,
        caption_model={"name": label},
        status_id=status_id,
        delete_status=delete_status,
        user_id=uid,
        reply_to=reply_to if reply_to is not None else message.message_id,
    )


def register_video(dp: Dispatcher, deps: BotDeps) -> None:
    """Sin registros propios: los flujos de video entran por generation/config.

    Mantiene la interfaz ``register_*(dp, deps)`` esperada por ``register_all``;
    la orquestación vive en :func:`run_video_generation`.
    """
    _ = (dp, deps, partial)


# Re-export para los tests de routing (vocabulario de eventos tipado).
__events__ = (ItemFailed, ItemResult)
