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
from grokbot.domain.user_config import (
    DEFAULT_VIDEO_ASPECT_RATIO,
    DEFAULT_VIDEO_DURATION,
    DEFAULT_VIDEO_RESOLUTION,
)
from grokbot.providers.base import (
    DEFAULT_IMAGE_ASPECT_RATIO,
    ProviderError,
    ProviderGenerationError,
    ProviderInputError,
    bytes_to_data_uri,
    detect_image_mime,
)
from grokbot.providers.error_mapping import (
    USER_MSG_GENERIC,
    classify_provider_error,
    log_mapped_error,
    to_provider_error,
)

_SEEDREAM_ID = MODELS["seedream"]["id"]
_FACESWAP_ID = MODELS["faceswap"]["id"]
_GROK_REPLICATE_PREFIX = "xai/grok-imagine-"
_NANO_BANANA_REPLICATE_PREFIX = "google/nano-banana"
_FACESWAP_WAIT_SEC = 60  # parity grok/bot.py:192 REPLICATE_WAIT_SEC


def _replicate_kind(model_id: str) -> str:
    """Classify a Replicate model id for input-shape branching.

    Returns ``"seedream"``, ``"faceswap"``, ``"grok"``, ``"grok_video"``,
    ``"nano_banana"`` or ``"other"``. Ids are compared against the domain
    catalog registry (never loose substring matching on the wire id beyond
    documented prefixes).
    """
    if model_id == _SEEDREAM_ID:
        return "seedream"
    if model_id == _FACESWAP_ID:
        return "faceswap"
    base = model_id.split(":", 1)[0]
    if base.startswith("xai/grok-imagine-video"):
        return "grok_video"
    if model_id.startswith(_GROK_REPLICATE_PREFIX):
        return "grok"
    # Pinned ids look like ``google/nano-banana-2:<hash>``.
    if base.startswith(_NANO_BANANA_REPLICATE_PREFIX):
        return "nano_banana"
    return "other"


def _grok_video_replicate_input(
    request: GenerationRequest,
    source_image: bytes | None,
) -> tuple[dict, dict]:
    """Build Replicate input (+ run kwargs) for ``xai/grok-imagine-video*``.

    Returns ``(input_data, extra_kwargs)``. ``grok-imagine-video-1.5`` requires
    an image (i2v-only on Replicate); base model supports t2v and i2v.
    """
    base = request.model_id.split(":", 1)[0]
    needs_image = base == "xai/grok-imagine-video-1.5"
    if needs_image and source_image is None:
        raise ProviderInputError(
            "Grok Imagine Video 1.5 en Replicate requiere una imagen (image-to-video).",
            user_message=(
                "Grok Imagine Video 1.5 requiere una imagen. "
                "Envía o responde a una foto para animarla."
            ),
        )

    input_data: dict = {
        "prompt": request.prompt,
        "duration": request.video_duration or DEFAULT_VIDEO_DURATION,
        "resolution": request.video_resolution or DEFAULT_VIDEO_RESOLUTION,
        "aspect_ratio": request.aspect_ratio or DEFAULT_VIDEO_ASPECT_RATIO,
    }
    extra_kwargs: dict = {}
    if source_image is not None:
        input_data["image"] = _named_image_buffer(source_image)
        extra_kwargs["file_encoding_strategy"] = "base64"
    return input_data, extra_kwargs


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


def _wrap_run_error(
    exc: Exception,
    *,
    fallback_user_message: str | None = None,
    user_message: str | None = None,
) -> ProviderError:
    """Map SDK/network exceptions to the typed provider hierarchy (R8).

    Uses the shared :mod:`error_mapping` heuristics so moderation (E005 /
    sensitive), billing, 429, etc. get clear Spanish UX. A failed *prediction*
    (``ModelError``, duck-typed via ``.prediction``) is terminal. ``user_message``
    is accepted as an alias of ``fallback_user_message`` (UNKNOWN only).
    """
    fallback = fallback_user_message or user_message or USER_MSG_GENERIC
    status = getattr(exc, "status", None)
    prediction = getattr(exc, "prediction", None)
    pred_error = getattr(prediction, "error", None) if prediction is not None else None
    if pred_error is not None:
        pred_error = str(pred_error)
    mapped = classify_provider_error(
        exc=exc,
        status_code=status if isinstance(status, int) else None,
        prediction_error=pred_error,
    )
    log_mapped_error(mapped, provider="replicate")
    return to_provider_error(
        mapped,
        technical=f"Replicate {type(exc).__name__}: {mapped.detail}",
        status_code=status if isinstance(status, int) else None,
        has_prediction=prediction is not None,
        fallback_user_message=fallback,
        unavailable_if_unknown=prediction is None,
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
    """Replicate — :class:`ImageProvider` + Grok Imagine :class:`VideoProvider`.

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
        return request.provider == "replicate" and request.media_type in (
            MediaType.IMAGE,
            MediaType.VIDEO,
        )

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
        if request.media_type is MediaType.VIDEO:
            return await self._generate_video(request, source_image)

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
                fallback_user_message="Error en la generación. Intenta de nuevo más tarde.",
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

    async def _generate_video(
        self,
        request: GenerationRequest,
        source_image: bytes | None,
    ) -> GenerationResult:
        kind = _replicate_kind(request.model_id)
        if kind != "grok_video":
            raise ProviderInputError(
                f"Replicate model {request.model_id!r} no genera video.",
                user_message="El modelo seleccionado no genera video en Replicate.",
            )
        input_data, extra_kwargs = _grok_video_replicate_input(request, source_image)
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
                fallback_user_message="Error en la generación de video. Intenta de nuevo más tarde.",
            ) from exc

        urls = _normalize_output_urls(output)
        if not urls:
            raise ProviderGenerationError(
                "Replicate no devolvió URL de video.",
                user_message="Error en la generación de video. Intenta de nuevo más tarde.",
            )
        return GenerationResult(
            provider="replicate",
            model_id=request.model_id,
            media_type=MediaType.VIDEO,
            remote_url=urls[0],
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
                fallback_user_message="Error en el face swap. Intenta de nuevo más tarde.",
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
