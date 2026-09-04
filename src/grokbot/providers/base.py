"""Provider contracts, typed errors and wire helpers (stdlib + domain only).

This module is the single place that fixes the provider interface consumed by
the application layer (item 4) and the provider implementations in this
package. It deliberately imports nothing but the stdlib and the pure domain
entities so the contract stays free of transport details (aiohttp, replicate,
SSH, telegram).

Multi-output convention (D4)
----------------------------
``GenerationResult`` carries ONE primary media in its top-level field
(``remote_url`` for providers that return URLs, ``file_path`` for local
ComfyUI outputs). Every additional/derived output lives in ``meta``:

- URL providers (xai/replicate/kie): ``meta["urls"] = [u1, u2, ...]`` with
  primary == ``remote_url`` == ``urls[0]``.
- ComfyUI local: ``meta["file_paths"] = [local...]`` and
  ``meta["comfyui_remotes"] = [/workspace/... paths]``.
- Kie: additionally ``meta["task_id"]``, ``meta["provider"] = "kie"`` and
  ``meta["index"]`` (when applicable).

Download allowlist (R7/D5)
--------------------------
A provider that returns remote URLs sets ``meta["download_allowlist"]`` to a
provider key (``"xai"`` | ``"kie"``) or ``None``. The shared downloader
(items 4/6) enforces host allowlists before downloading media; providers never
download arbitrary URLs themselves in item 2.
"""

from __future__ import annotations

import base64
from typing import Protocol, runtime_checkable

from grokbot.domain.generation import GenerationRequest, GenerationResult

# Shared default aspect-ratio for text-to-image wire payloads (grok
# sessions.py:21). It is a transport default, NOT a persisted user setting, so
# it lives here rather than in domain/.
DEFAULT_IMAGE_ASPECT_RATIO = "9:16"

# Polling/backoff constants (grok bot.py:161-169).
POLL_MAX_RETRIES = 3
POLL_RETRY_BACKOFF_SEC = (2, 4, 8)
VIDEO_POLL_INTERVAL_SEC = 5
IMAGE_MAX_POLL_SEC = 120  # 2 minutes — image tasks rarely need more
VIDEO_MAX_POLL_SEC = 600  # 10 minutes
I2V_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # image-to-video upload cap (5 MB)


# ---------------------------------------------------------------------------
# Image/Video helpers (bytes-based; mirror grok bot.py 593-623)
# ---------------------------------------------------------------------------
def detect_image_mime(data: bytes) -> tuple[str, str]:
    """Return ``(mime_type, file_extension)`` from image magic bytes.

    Mirrors grok ``_detect_image_mime``: jpeg/png/webp are detected from their
    header; anything else falls back to ``("image/jpeg", "jpg")``.
    """
    header = data[:16]
    if header[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp", "webp"
    return "image/jpeg", "jpg"


def bytes_to_data_uri(data: bytes, mime: str | None = None) -> str:
    """Encode raw image bytes as a ``data:<mime>;base64,...`` URI."""
    if mime is None:
        mime, _ = detect_image_mime(data)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def validate_image_for_i2v(data: bytes) -> str | None:
    """Return an error message when ``data`` exceeds the i2v size cap, else None."""
    if len(data) > I2V_MAX_IMAGE_BYTES:
        max_mb = I2V_MAX_IMAGE_BYTES // 1024 // 1024
        got_mb = max(1, len(data) // 1024 // 1024)
        return f"La imagen es demasiado grande ({got_mb} MB). Máximo {max_mb} MB."
    return None


# ---------------------------------------------------------------------------
# Typed error hierarchy (D3)
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    """Base for every provider error.

    ``retryable`` distinguishes transient network/HTTP conditions (429/5xx —
    the application layer of item 4 may retry those) from terminal states
    (preconditions, policy, timeout) that must NOT be retried.

    ``user_message`` is the safe, user-facing text; it defaults to the
    technical ``message`` for callers that only provide one string.
    """

    retryable = True

    def __init__(self, message: str = "", *, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message if user_message is not None else message


class ProviderNotConfiguredError(ProviderError):
    """Provider is disabled/misconfigured (empty key/host). Terminal."""

    retryable = False


class ProviderInputError(ProviderError):
    """Caller precondition failed (bad params, missing source, oversized image)."""

    retryable = False


class ProviderContentError(ProviderError):
    """Provider rejected the content (moderation/policy). Terminal."""

    retryable = False


class ProviderAuthenticationError(ProviderError):
    """Credentials rejected by the provider (401/403). Terminal."""

    retryable = False


class ProviderGenerationError(ProviderError):
    """Provider reported a terminal generation failure (failed/expired/no output)."""

    retryable = False


class ProviderRateLimitError(ProviderError):
    """HTTP 429 / rate limit. Transient — safe to retry after backoff."""

    retryable = True


class ProviderUnavailableError(ProviderError):
    """Provider unreachable (5xx, network, SSH box down). Transient."""

    retryable = True


class ProviderTimeoutError(ProviderError):
    """Polling/create exceeded the deadline. Terminal for a single attempt."""

    retryable = False


# ---------------------------------------------------------------------------
# Provider Protocols
# ---------------------------------------------------------------------------
@runtime_checkable
class ImageProvider(Protocol):
    """Contract for image generation/editing providers."""

    @property
    def available(self) -> bool:
        """True when the provider is configured and ready to generate."""
        ...

    def supports(self, request: GenerationRequest) -> bool:
        """True when this provider can serve ``request`` (media type + model)."""
        ...

    async def generate(
        self,
        request: GenerationRequest,
        *,
        source_image: bytes | None = None,
    ) -> GenerationResult:
        """Generate one image (t2i/i2i). Single attempt; raises ProviderError."""
        ...


@runtime_checkable
class FaceSwapProvider(Protocol):
    """Contract for face-swap providers (two input images, no prompt)."""

    @property
    def available(self) -> bool:
        """True when the provider is configured and ready to swap."""
        ...

    async def swap_face(
        self,
        *,
        swap_image: bytes,
        input_image: bytes,
    ) -> GenerationResult:
        """Swap the face in ``input_image`` using ``swap_image`` as the source face."""
        ...


@runtime_checkable
class VideoProvider(Protocol):
    """Contract for video generation providers.

    Note (SPEC §5.3 deviation): no public ``poll()`` is exposed in this item —
    ``generate`` performs submit+poll internally (grok blocks up to 600s) and
    the application layer cancels by cancelling the asyncio task that runs it.
    """

    @property
    def available(self) -> bool:
        ...

    def supports(self, request: GenerationRequest) -> bool:
        ...

    async def generate(
        self,
        request: GenerationRequest,
        *,
        source_image: bytes | None = None,
    ) -> GenerationResult:
        """Generate one video (t2v/i2v), blocking until complete or timeout."""
        ...
