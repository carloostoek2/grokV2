"""Provider-agnostic generation entities.

``GenerationRequest`` / ``GenerationResult`` deliberately carry no
provider-specific payload (data-URIs, BytesIO, ComfyUI uploads); those details
belong to the provider implementations (item 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


@dataclass(frozen=True)
class TelegramFileRef:
    """A Telegram ``file_id`` reference to a source image."""

    file_id: str


@dataclass(frozen=True)
class LocalPathRef:
    """A local filesystem path to a source image."""

    path: str


@dataclass(frozen=True)
class UrlRef:
    """A remote URL to a source image."""

    url: str


@dataclass(frozen=True)
class KieTaskRef:
    """A Kie.ai task id (used as image-to-image source from a previous result)."""

    task_id: str
    index: int = 0


ImageSource = TelegramFileRef | LocalPathRef | UrlRef | KieTaskRef


@dataclass(frozen=True)
class GenerationRequest:
    """A provider-agnostic request to generate image/video content."""

    provider: str
    model_id: str
    media_type: MediaType
    prompt: str
    source: ImageSource | None = None
    aspect_ratio: str | None = None
    video_duration: int | None = None
    video_resolution: str | None = None
    video_mode: str | None = None
    params: dict = field(default_factory=dict)  # provider/model extras (comfyui model/lora/refine, mode, prompts, key); read-only by convention


@dataclass(frozen=True)
class GenerationResult:
    """A normalized generation outcome (media bytes/path/URL + provider meta)."""

    provider: str
    model_id: str
    media_type: MediaType
    data: bytes | None = None
    file_path: str | None = None
    remote_url: str | None = None
    mime_type: str | None = None
    meta: dict = field(default_factory=dict)  # provider extras (e.g. kie_task_id)
