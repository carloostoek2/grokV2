"""xAI (Grok Imagine) provider — image t2i/i2i, video t2v/i2v + 2-image edits.

Semantic reference: grok/bot.py ``XAI_BASE`` (4530), ``_generate_xai``
(4533-4589), ``_generate_xai_video`` (5164-5266), ``_poll_video_once``
(5269-5297). Payloads are transcribed from that reference; this module never
touches ``os.environ``/``settings`` and never logs secrets or request bodies.
"""

from __future__ import annotations

import asyncio
import time

import aiohttp

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
    POLL_MAX_RETRIES,
    POLL_RETRY_BACKOFF_SEC,
    VIDEO_MAX_POLL_SEC,
    VIDEO_POLL_INTERVAL_SEC,
    ProviderContentError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    bytes_to_data_uri,
    validate_image_for_i2v,
)
from grokbot.providers.error_mapping import (
    CONTENT_MODERATION_MSG,
    USER_MSG_GENERIC,
    classify_provider_error,
    log_mapped_error,
    to_provider_error,
)

XAI_BASE = "https://api.x.ai/v1"
_CONTENT_MODERATION_MSG = CONTENT_MODERATION_MSG
_NO_REQUEST_ID_MSG = "No se pudo iniciar la generación de video. Intenta de nuevo."
_NO_VIDEO_URL_MSG = "No se recibió URL de video. Intenta de nuevo."
_NO_IMAGE_URL_MSG = "xAI no devolvió URL de imagen."
_TIMEOUT_MSG = "Tiempo de espera agotado (10 min). Intenta de nuevo."


def _xai_http_ok(status: int) -> bool:
    """xAI video endpoints may return 200 or 202 (Accepted) while work is in flight."""
    return status in (200, 202)


def _raise_for_http_status(
    status: int,
    *,
    context: str = "generación",
    body: str | None = None,
) -> None:
    """Map a non-ok HTTP status (+ optional body) to a typed ProviderError.

    Shared :mod:`error_mapping` classifies moderation / billing / rate_limit /
    etc. so credits-403 and similar get clear Spanish UX instead of the generic
    fallback. Terminal 4xx stay non-retryable; 5xx stay unavailable/retryable.
    """
    mapped = classify_provider_error(status_code=status, body=body, message=context)
    log_mapped_error(mapped, provider="xai")
    raise to_provider_error(
        mapped,
        technical=f"xAI HTTP {status} ({context})",
        status_code=status,
        fallback_user_message=USER_MSG_GENERIC,
        unavailable_if_unknown=status >= 500,
    )


def _raise_input_error(msg: str) -> None:
    raise ProviderInputError(msg, user_message=msg)


class XaiProvider:
    """xAI Grok Imagine — implements both :class:`ImageProvider` and :class:`VideoProvider`.

    Tokens/config arrive via the constructor only. ``generate`` routes on
    ``request.media_type`` and performs a single attempt (submit + poll for
    video). ``edit_with_reference`` (D7) is an extra 2-image method outside the
    Protocols.
    """

    def __init__(self, api_key: str):
        self._api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def supports(self, request: GenerationRequest) -> bool:
        return request.provider == "xai" and request.media_type in (
            MediaType.IMAGE,
            MediaType.VIDEO,
        )

    async def generate(
        self,
        request: GenerationRequest,
        *,
        source_image: bytes | None = None,
    ) -> GenerationResult:
        if request.media_type is MediaType.VIDEO:
            return await self._generate_video(request, source_image)
        return await self._generate_image(request, source_image)

    # --- image ---------------------------------------------------------
    async def _generate_image(
        self,
        request: GenerationRequest,
        source_image: bytes | None,
    ) -> GenerationResult:
        headers = self._headers()
        if source_image is not None:
            size_err = validate_image_for_i2v(source_image)
            if size_err:
                _raise_input_error(size_err)
            body = {
                "model": request.model_id,
                "prompt": request.prompt,
                "image": {
                    "url": bytes_to_data_uri(source_image),
                    "type": "image_url",
                },
            }
            url = f"{XAI_BASE}/images/edits"
        else:
            body = {
                "model": request.model_id,
                "prompt": request.prompt,
                "n": 1,
                "aspect_ratio": request.aspect_ratio or DEFAULT_IMAGE_ASPECT_RATIO,
            }
            url = f"{XAI_BASE}/images/generations"

        urls = await self._post_json(url, headers, body, context="generación de imagen")
        if not urls:
            raise ProviderGenerationError(_NO_IMAGE_URL_MSG, user_message=_NO_IMAGE_URL_MSG)
        return GenerationResult(
            provider="xai",
            model_id=request.model_id,
            media_type=MediaType.IMAGE,
            remote_url=urls[0],
            meta={"urls": urls, "download_allowlist": "xai"},
        )

    async def edit_with_reference(
        self,
        request: GenerationRequest,
        source_image: bytes,
        reference_image: bytes,
    ) -> GenerationResult:
        """xAI 2-image edit (``POST /images/edits`` with ``images:[...]``).

        Outside the Protocols (D7); used by the edit-with-reference flow.
        """
        for img in (source_image, reference_image):
            size_err = validate_image_for_i2v(img)
            if size_err:
                _raise_input_error(size_err)
        body = {
            "model": request.model_id,
            "prompt": request.prompt,
            "images": [
                {"url": bytes_to_data_uri(source_image), "type": "image_url"},
                {"url": bytes_to_data_uri(reference_image), "type": "image_url"},
            ],
        }
        urls = await self._post_json(
            f"{XAI_BASE}/images/edits", self._headers(), body, context="generación de imagen"
        )
        if not urls:
            raise ProviderGenerationError(_NO_IMAGE_URL_MSG, user_message=_NO_IMAGE_URL_MSG)
        return GenerationResult(
            provider="xai",
            model_id=request.model_id,
            media_type=MediaType.IMAGE,
            remote_url=urls[0],
            meta={"urls": urls, "download_allowlist": "xai"},
        )

    # --- video ---------------------------------------------------------
    async def _generate_video(
        self,
        request: GenerationRequest,
        source_image: bytes | None,
    ) -> GenerationResult:
        body: dict = {
            "model": request.model_id,
            "prompt": request.prompt,
            "duration": request.video_duration or DEFAULT_VIDEO_DURATION,
            "aspect_ratio": request.aspect_ratio or DEFAULT_VIDEO_ASPECT_RATIO,
            "resolution": request.video_resolution or DEFAULT_VIDEO_RESOLUTION,
        }
        if source_image is not None:
            size_err = validate_image_for_i2v(source_image)
            if size_err:
                _raise_input_error(size_err)
            body["image"] = {"url": bytes_to_data_uri(source_image)}

        headers = self._headers()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{XAI_BASE}/videos/generations", headers=headers, json=body
            ) as resp:
                if not _xai_http_ok(resp.status):
                    body = await resp.text()
                    _raise_for_http_status(resp.status, context="generación de video", body=body)
                data = await resp.json()

            request_id = data.get("request_id")
            if not request_id:
                raise ProviderGenerationError(_NO_REQUEST_ID_MSG, user_message=_NO_REQUEST_ID_MSG)

            started = time.monotonic()
            while time.monotonic() - started < VIDEO_MAX_POLL_SEC:
                poll_data = await self._poll_video_once(session, request_id, headers)
                status = poll_data.get("status", "unknown")

                if status == "done":
                    video = poll_data.get("video") or {}
                    respect_moderation = video.get(
                        "respect_moderation",
                        poll_data.get("respect_moderation"),
                    )
                    if respect_moderation is False:
                        raise ProviderContentError(
                            _CONTENT_MODERATION_MSG, user_message=_CONTENT_MODERATION_MSG
                        )
                    video_url = video.get("url")
                    if not video_url:
                        raise ProviderGenerationError(
                            _NO_VIDEO_URL_MSG, user_message=_NO_VIDEO_URL_MSG
                        )
                    return GenerationResult(
                        provider="xai",
                        model_id=request.model_id,
                        media_type=MediaType.VIDEO,
                        remote_url=video_url,
                        meta={"urls": [video_url], "download_allowlist": "xai"},
                    )

                if status in ("failed", "expired"):
                    raise ProviderGenerationError(
                        f"xAI video {status}",
                        user_message="Error en la generación de video. Intenta de nuevo más tarde.",
                    )

                await asyncio.sleep(VIDEO_POLL_INTERVAL_SEC)

        raise ProviderTimeoutError(_TIMEOUT_MSG, user_message=_TIMEOUT_MSG)

    async def _poll_video_once(
        self,
        session: aiohttp.ClientSession,
        request_id: str,
        headers: dict,
    ) -> dict:
        """Poll video status once with retries on transient errors."""
        url = f"{XAI_BASE}/videos/{request_id}"
        for attempt in range(POLL_MAX_RETRIES + 1):
            try:
                async with session.get(url, headers=headers) as poll_resp:
                    if poll_resp.status >= 500:
                        if attempt < POLL_MAX_RETRIES:
                            await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                            continue
                        body = await poll_resp.text()
                        _raise_for_http_status(poll_resp.status, context="consulta de video", body=body)
                    if not _xai_http_ok(poll_resp.status):
                        body = await poll_resp.text()
                        _raise_for_http_status(poll_resp.status, context="consulta de video", body=body)
                    return await poll_resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < POLL_MAX_RETRIES:
                    await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                    continue
                raise ProviderUnavailableError(
                    f"xAI poll transient: {exc}",
                    user_message="Error en la consulta de video. Intenta de nuevo más tarde.",
                ) from exc
        raise ProviderUnavailableError(
            "xAI poll failed",
            user_message="Error en la consulta de video. Intenta de nuevo más tarde.",
        )

    # --- shared HTTP ---------------------------------------------------
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def _post_json(self, url: str, headers: dict, body: dict, *, context: str) -> list[str]:
        """POST JSON and return the list of ``data[].url`` strings."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=body) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _raise_for_http_status(resp.status, context=context, body=body)
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ProviderUnavailableError(
                f"xAI request failed: {exc}",
                user_message="Error en la generación. Intenta de nuevo más tarde.",
            ) from exc
        return [
            r["url"] for r in data.get("data", []) if isinstance(r, dict) and r.get("url")
        ]
