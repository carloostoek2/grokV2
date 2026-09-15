"""Replicate provider — Grok Imagine + Nano Banana + Seedream + Face Swap.

Semantic reference: grok/bot.py ``_get_replicate_client`` (3220-3227),
``_generate_replicate`` (3835-3852), ``_normalize_image_urls`` (4962-4970).
Nano Banana uses official ``google/nano-banana*`` models (pinned like Face Swap).
Inputs are transcribed from that reference. The Replicate client is created
lazily with an explicit ``api_token`` (never via env); tests inject a fake
``client`` whose ``run()`` records the input payload.
"""

from __future__ import annotations

import asyncio
import io

from grokbot.domain.catalog import MODELS
from grokbot.domain.generation import (
    GenerationRequest,
    GenerationResult,
    MediaType,
)
from grokbot.providers.base import (
    DEFAULT_IMAGE_ASPECT_RATIO,
    ProviderAuthenticationError,
    ProviderError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    bytes_to_data_uri,
    detect_image_mime,
)

_SEEDREAM_ID = MODELS["seedream"]["id"]
_FACESWAP_ID = MODELS["faceswap"]["id"]
_GROK_REPLICATE_PREFIX = "xai/grok-imagine-"
_NANO_BANANA_REPLICATE_PREFIX = "google/nano-banana"
_FACESWAP_WAIT_SEC = 60  # parity grok/bot.py:192 REPLICATE_WAIT_SEC


def _replicate_kind(model_id: str) -> str:
    """Classify a Replicate model id for input-shape branching.

    Returns ``"seedream"``, ``"faceswap"``, ``"grok"``, ``"nano_banana"`` or
    ``"other"``. Ids are compared against the domain catalog registry (never
    loose substring matching on the wire id beyond documented prefixes).
    """
    if model_id == _SEEDREAM_ID:
        return "seedream"
    if model_id == _FACESWAP_ID:
        return "faceswap"
    if model_id.startswith(_GROK_REPLICATE_PREFIX):
        return "grok"
    # Pinned ids look like ``google/nano-banana-2:<hash>``.
    base = model_id.split(":", 1)[0]
    if base.startswith(_NANO_BANANA_REPLICATE_PREFIX):
        return "nano_banana"
    return "other"


def _nano_banana_replicate_input(
    request: GenerationRequest,
    source_image: bytes | None,
) -> dict:
    """Build Replicate input for google/nano-banana* (t2i + i2i via image_input)."""
    input_data: dict = {
        "prompt": request.prompt,
        "output_format": request.params.get("output_format", "png"),
    }
    if source_image is not None:
        input_data["image_input"] = [bytes_to_data_uri(source_image)]
    else:
        input_data["image_input"] = []
        input_data["aspect_ratio"] = request.aspect_ratio or DEFAULT_IMAGE_ASPECT_RATIO
    base = request.model_id.split(":", 1)[0]
    # Classic Replicate schema has no resolution; banana2/pro accept 1K|2K|4K.
    if base in ("google/nano-banana-2", "google/nano-banana-pro"):
        resolution = request.params.get("resolution")
        if resolution in ("1K", "2K", "4K"):
            input_data["resolution"] = resolution
        elif base == "google/nano-banana-pro":
            input_data["resolution"] = "2K"
        else:
            input_data["resolution"] = "1K"
    return input_data


def _named_image_buffer(data: bytes) -> io.BytesIO:
    """Wrap image bytes so Replicate infers jpeg/png/webp, not ``application/octet-stream``.

    The SDK's ``base64_encode_file`` guesses MIME from ``file.name``. A nameless
    ``BytesIO`` becomes ``.bin``, which Grok Imagine rejects (``Invalid image
    format '.bin'``). grok/bot.py sets ``image_data.name = "image.jpg"`` on the
    Telegram download; we set the extension from magic bytes instead.
    """
    _mime, ext = detect_image_mime(data)
    buf = io.BytesIO(data)
    buf.name = f"image.{ext}"
    return buf


def _wrap_run_error(exc: Exception, *, user_message: str) -> ProviderError:
    """Map SDK/network exceptions to the typed provider hierarchy (R8).

    A failed *prediction* (``ModelError``, duck-typed via ``.prediction``) is
    terminal: retrying invalid-input failures created extra 429s. HTTP 429 stays
    retryable; 401/403 is auth; everything else stays transient unavailable.
    """
    status = getattr(exc, "status", None)
    if status == 429:
        return ProviderRateLimitError(
            f"Replicate rate-limited: {type(exc).__name__}",
            user_message=user_message,
        )
    if status in (401, 403):
        return ProviderAuthenticationError(
            f"Replicate auth failed: {type(exc).__name__}",
            user_message=user_message,
        )
    if getattr(exc, "prediction", None) is not None:
        return ProviderGenerationError(
            f"Replicate prediction failed: {type(exc).__name__}",
            user_message=user_message,
        )
    return ProviderUnavailableError(
        f"Replicate run failed: {type(exc).__name__}",
        user_message=user_message,
    )


def _normalize_output_urls(output) -> list[str]:
    """Flatten a Replicate output (FileOutput, str, or list/tuple of them) to URLs."""
    raw = output if isinstance(output, (list, tuple)) else [output]
    urls: list[str] = []
    for item in raw:
        if hasattr(item, "url"):
            item = item.url
        if item is not None:
            urls.append(str(item))
    return urls


class ReplicateProvider:
    """Replicate — implements :class:`ImageProvider` only (video falls back to xAI).

    ``api_token`` is required by the constructor; when no ``client`` is injected
    a default ``replicate.Client(api_token=api_token)`` is created lazily on the
    first generation (the ``replicate`` import happens then, not at module load).
    """

    def __init__(self, api_token: str, *, client=None):
        self._api_token = api_token
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self._api_token)

    def supports(self, request: GenerationRequest) -> bool:
        return request.provider == "replicate" and request.media_type is MediaType.IMAGE

    def _resolve_client(self):
        if self._client is not None:
            return self._client
        import replicate  # lazy: replicate is an optional heavy dep

        return replicate.Client(api_token=self._api_token)

    async def generate(
        self,
        request: GenerationRequest,
        *,
        source_image: bytes | None = None,
    ) -> GenerationResult:
        kind = _replicate_kind(request.model_id)
        if kind == "faceswap":
            raise ProviderInputError(
                "El modelo Face Swap no se genera por prompt: usa swap_face().",
                user_message="Face Swap no se puede generar por texto. Envia una foto para hacer el swap.",
            )
        extra_kwargs: dict = {}

        if kind == "nano_banana":
            input_data = _nano_banana_replicate_input(request, source_image)
        else:
            input_data = {"prompt": request.prompt}
            if source_image is not None:
                if kind == "seedream":
                    input_data["image_input"] = [bytes_to_data_uri(source_image)]
                    input_data["size"] = "2K"
                else:
                    input_data["image"] = _named_image_buffer(source_image)
                    extra_kwargs["file_encoding_strategy"] = "base64"
            elif kind == "grok":
                input_data["aspect_ratio"] = request.aspect_ratio or DEFAULT_IMAGE_ASPECT_RATIO

        client = self._resolve_client()
        try:
            output = await asyncio.to_thread(
                client.run, request.model_id, input=input_data, **extra_kwargs
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise _wrap_run_error(
                exc,
                user_message="Error en la generación. Intenta de nuevo más tarde.",
            ) from exc

        urls = _normalize_output_urls(output)
        if not urls:
            raise ProviderGenerationError(
                "Replicate no devolvió URL de imagen.",
                user_message="Error en la generación. Intenta de nuevo más tarde.",
            )
        return GenerationResult(
            provider="replicate",
            model_id=request.model_id,
            media_type=MediaType.IMAGE,
            remote_url=urls[0],
            # Replicate assets are served by replicate.delivery CDNs; the shared
            # downloader applies no host allowlist for replicate (D4/D5).
            meta={"urls": urls, "download_allowlist": None},
        )

    async def swap_face(
        self,
        *,
        swap_image: bytes,
        input_image: bytes,
    ) -> GenerationResult:
        """Swap the source face onto a target image (parity grok ``_faceswap_replicate_single``).

        Two images and no prompt: ``swap_image`` is the reference face and
        ``input_image`` the target. Sent as base64 bytes with a 60s wait, mirroring
        grok/bot.py:3255-3271.
        """
        client = self._resolve_client()
        input_data = {
            "swap_image": _named_image_buffer(swap_image),
            "input_image": _named_image_buffer(input_image),
        }
        try:
            output = await asyncio.to_thread(
                client.run,
                _FACESWAP_ID,
                input=input_data,
                file_encoding_strategy="base64",
                wait=_FACESWAP_WAIT_SEC,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise _wrap_run_error(
                exc,
                user_message="Error en el face swap. Intenta de nuevo más tarde.",
            ) from exc

        urls = _normalize_output_urls(output)
        if not urls:
            raise ProviderGenerationError(
                "Replicate no devolvió URL de imagen.",
                user_message="Error en el face swap. Intenta de nuevo más tarde.",
            )
        return GenerationResult(
            provider="replicate",
            model_id=_FACESWAP_ID,
            media_type=MediaType.IMAGE,
            remote_url=urls[0],
            meta={"urls": urls, "download_allowlist": None},
        )
