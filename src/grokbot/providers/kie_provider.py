"""Kie.ai provider — image t2i/i2i, video t2v/i2v, host allowlist, D8 spicy rule.

Semantic reference: grok/bot.py ``KIE_BASE``/``KIE_UPLOAD_BASE``/slugs
(4592-4598), ``_kie_video_slug``/headers/upload (4607-4658),
``_kie_create_task``/``_kie_poll_task``/``_kie_poll_once``/``_kie_get_result_url_at_index``
(4661-4855), ``_generate_kie_once`` (4882-4959), ``_generate_kie_video``
(5300-5370), aspect/duration/sanitize helpers (1107-1157). Tokens/config arrive
via the constructor only; no ``os.environ``/``settings``; no secrets in errors.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse

import aiohttp

from grokbot.domain.generation import (
    GenerationRequest,
    GenerationResult,
    KieTaskRef,
    MediaType,
)
from grokbot.domain.user_config import (
    DEFAULT_VIDEO_ASPECT_RATIO,
    DEFAULT_VIDEO_DURATION,
    DEFAULT_VIDEO_RESOLUTION,
)
from grokbot.providers.base import (
    DEFAULT_IMAGE_ASPECT_RATIO,
    IMAGE_MAX_POLL_SEC,
    POLL_MAX_RETRIES,
    POLL_RETRY_BACKOFF_SEC,
    VIDEO_MAX_POLL_SEC,
    VIDEO_POLL_INTERVAL_SEC,
    ProviderAuthenticationError,
    ProviderError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    bytes_to_data_uri,
    detect_image_mime,
    validate_image_for_i2v,
)

KIE_BASE = "https://api.kie.ai"
KIE_UPLOAD_BASE = "https://kieai.redpandaai.co"
KIE_IMAGE_I2I = "grok-imagine/image-to-image"
KIE_VIDEO_T2V = "grok-imagine/text-to-video"
KIE_VIDEO_I2V = "grok-imagine/image-to-video"
KIE_VIDEO_15_I2V = "grok-imagine-video-1-5-preview"

# Kie.ai result/upload CDN hosts (grok bot.py:180-187).
KIE_DOWNLOAD_HOSTS = frozenset(
    {
        "kieai.redpandaai.co",
        "static.aiquickdraw.com",
        "tempfile.redpandaai.co",
        "tempfile.aiquickdraw.com",
        "file.aiquickdraw.com",
    }
)
KIE_DOWNLOAD_HOST_SUFFIXES = (".aiquickdraw.com", ".redpandaai.co")

KIE_BASE_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "3:2", "2:3")
KIE_15_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3")

_KIE_NOT_CONFIGURED_MSG = (
    "Kie.ai no está disponible en este momento. Contacta al administrador del bot."
)
_KIE_SPICY_NO_REF_MSG = (
    "El modo spicy solo está disponible al editar imágenes generadas por el bot."
)
_KIE_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=120)

# User-safe generic messages.
_USER_ERROR = "Error en la generación. Intenta de nuevo más tarde."
_POLL_USER_ERROR = "Error en la consulta de tarea. Intenta de nuevo más tarde."


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without network)
# ---------------------------------------------------------------------------
def _kie_video_slug(video_model: str, *, image_to_video: bool) -> str:
    """Map a video model id to the Kie.ai model slug (grok bot.py:4607-4614)."""
    if video_model == "grok-imagine-video-1.5":
        if image_to_video:
            return KIE_VIDEO_15_I2V
        # Kie 1.5 has no t2v slug; fall back to base text-to-video.
        return KIE_VIDEO_T2V
    return KIE_VIDEO_I2V if image_to_video else KIE_VIDEO_T2V


def _kie_aspect_ratios_for_model(video_model: str) -> tuple[str, ...]:
    if video_model == "grok-imagine-video-1.5":
        return KIE_15_VIDEO_ASPECT_RATIOS
    return KIE_BASE_VIDEO_ASPECT_RATIOS


def _kie_map_duration(duration: int) -> int:
    """Kie.ai accepts 6-30 seconds for base text-to-video (clamp)."""
    return max(6, min(duration, 30))


def _sanitize_kie_fail_log(fail_msg: str | None, limit: int = 80) -> str:
    if not fail_msg:
        return ""
    text = str(fail_msg).replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _is_allowed_kie_download_host(host: str) -> bool:
    host = (host or "").lower()
    if host in KIE_DOWNLOAD_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in KIE_DOWNLOAD_HOST_SUFFIXES)


def _is_allowed_kie_asset_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False
    return _is_allowed_kie_download_host(parsed.hostname or "")


def _kie_poll_error_is_transient(http_status: int, api_code: int | None = None) -> bool:
    if http_status in (404, 422, 429):
        return True
    if api_code in (422, 429):
        return True
    return http_status >= 500


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class KieProvider:
    """Kie.ai — implements both :class:`ImageProvider` and :class:`VideoProvider`.

    ``api_key`` is required by the constructor; an empty key disables the
    provider (``available is False``) and ``generate`` raises
    :class:`ProviderNotConfiguredError`. Images sourced from a previous
    bot-generated Kie task use ``KieTaskRef`` as ``request.source`` and resolve
    the source URL through Kie's own ``recordInfo`` API (D5 — no SSRF).
    """

    def __init__(self, api_key: str):
        self._api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def supports(self, request: GenerationRequest) -> bool:
        return request.provider == "kie" and request.media_type in (
            MediaType.IMAGE,
            MediaType.VIDEO,
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _effective_mode(self, request: GenerationRequest) -> str:
        return request.params.get("mode", request.video_mode or "normal")

    async def generate(
        self,
        request: GenerationRequest,
        *,
        source_image: bytes | None = None,
    ) -> GenerationResult:
        if not self._api_key:
            raise ProviderNotConfiguredError(
                _KIE_NOT_CONFIGURED_MSG, user_message=_KIE_NOT_CONFIGURED_MSG
            )
        # D8: spicy mode requires a KieTaskRef source (image and video alike);
        # no silent downgrade.
        is_kie_ref = isinstance(request.source, KieTaskRef)
        if self._effective_mode(request) == "spicy" and not is_kie_ref:
            raise ProviderInputError(_KIE_SPICY_NO_REF_MSG, user_message=_KIE_SPICY_NO_REF_MSG)

        if request.media_type is MediaType.VIDEO:
            return await self._generate_video(request, source_image)
        return await self._generate_image(request, source_image)

    # --- image ---------------------------------------------------------
    async def _generate_image(
        self,
        request: GenerationRequest,
        source_image: bytes | None,
    ) -> GenerationResult:
        async with aiohttp.ClientSession(timeout=_KIE_REQUEST_TIMEOUT) as session:
            if isinstance(request.source, KieTaskRef):
                source_url, ref_err = await self._get_result_url_at_index(
                    session, request.source.task_id, request.source.index
                )
                if ref_err:
                    raise ref_err
                input_data: dict = {
                    "image_urls": [source_url],
                    "prompt": request.prompt,
                    "enable_pro": True,
                    "nsfw_checker": False,
                    "mode": self._effective_mode(request),
                }
                model_slug = KIE_IMAGE_I2I
                poll_timeout = IMAGE_MAX_POLL_SEC
            elif source_image is not None:
                size_err = validate_image_for_i2v(source_image)
                if size_err:
                    raise ProviderInputError(size_err, user_message=size_err)
                image_url = await self._upload_image(session, source_image)
                input_data = {
                    "image_urls": [image_url],
                    "prompt": request.prompt,
                    "enable_pro": True,
                    "nsfw_checker": False,
                    "mode": "normal",
                }
                model_slug = KIE_IMAGE_I2I
                poll_timeout = IMAGE_MAX_POLL_SEC
            else:
                input_data = {
                    "prompt": request.prompt,
                    "aspect_ratio": request.aspect_ratio or DEFAULT_IMAGE_ASPECT_RATIO,
                    "enable_pro": True,
                    "nsfw_checker": False,
                }
                model_slug = request.model_id
                poll_timeout = VIDEO_MAX_POLL_SEC

            task_id = await self._create_task(session, model_slug, input_data)
            urls = await self._poll_task(
                session, task_id, prompt=request.prompt, max_poll_sec=poll_timeout
            )
            return GenerationResult(
                provider="kie",
                model_id=request.model_id,
                media_type=MediaType.IMAGE,
                remote_url=urls[0],
                meta={
                    "urls": urls,
                    "task_id": task_id,
                    "index": 0,
                    "provider": "kie",
                    "download_allowlist": "kie",
                },
            )

    # --- video ---------------------------------------------------------
    async def _generate_video(
        self,
        request: GenerationRequest,
        source_image: bytes | None,
    ) -> GenerationResult:
        aspect_ratio = request.aspect_ratio or DEFAULT_VIDEO_ASPECT_RATIO
        video_model = request.model_id
        allowed_aspects = _kie_aspect_ratios_for_model(video_model)
        if aspect_ratio not in allowed_aspects:
            supported = ", ".join(allowed_aspects)
            raise ProviderInputError(
                f"Relación de aspecto no compatible con Kie.ai. Usa: {supported}",
                user_message=f"Relación de aspecto no compatible con Kie.ai. Usa: {supported}",
            )

        has_image = source_image is not None or isinstance(request.source, KieTaskRef)
        input_data: dict = {
            "prompt": request.prompt,
            "aspect_ratio": aspect_ratio,
            "duration": _kie_map_duration(request.video_duration or DEFAULT_VIDEO_DURATION),
            "resolution": request.video_resolution or DEFAULT_VIDEO_RESOLUTION,
            "nsfw_checker": False,
        }

        async with aiohttp.ClientSession(timeout=_KIE_REQUEST_TIMEOUT) as session:
            if isinstance(request.source, KieTaskRef):
                input_data["task_id"] = request.source.task_id
                input_data["index"] = request.source.index
            elif has_image:
                size_err = validate_image_for_i2v(source_image)
                if size_err:
                    raise ProviderInputError(size_err, user_message=size_err)
                image_url = await self._upload_image(session, source_image)
                input_data["image_urls"] = [image_url]

            model_slug = _kie_video_slug(video_model, image_to_video=has_image)
            if model_slug != KIE_VIDEO_15_I2V:
                mode = self._effective_mode(request)
                if isinstance(request.source, KieTaskRef):
                    input_data["mode"] = mode
                else:
                    # D8 already rejected spicy without a ref; other modes pass.
                    input_data["mode"] = "normal" if mode == "spicy" else mode

            task_id = await self._create_task(session, model_slug, input_data)
            urls = await self._poll_task(
                session, task_id, prompt=request.prompt, max_poll_sec=VIDEO_MAX_POLL_SEC
            )
            return GenerationResult(
                provider="kie",
                model_id=request.model_id,
                media_type=MediaType.VIDEO,
                remote_url=urls[0],
                meta={
                    "urls": urls,
                    "task_id": task_id,
                    "index": 0,
                    "provider": "kie",
                    "download_allowlist": "kie",
                },
            )

    # --- upload / create / poll ---------------------------------------
    async def _upload_image(self, session: aiohttp.ClientSession, image_data: bytes) -> str:
        mime, ext = detect_image_mime(image_data)
        data_uri = bytes_to_data_uri(image_data, mime=mime)
        body = {
            "base64Data": data_uri,
            "uploadPath": "grok-bot",
            "fileName": f"upload-{int(time.time())}.{ext}",
        }
        try:
            async with session.post(
                f"{KIE_UPLOAD_BASE}/api/file-base64-upload",
                headers=self._headers(),
                json=body,
            ) as resp:
                if resp.status != 200:
                    await resp.text()
                    self._raise_http_status(resp.status, context="subida de imagen")
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ProviderUnavailableError(
                f"Kie upload failed: {exc}",
                user_message="Error en la subida de imagen. Intenta de nuevo más tarde.",
            ) from exc

        if data.get("success") is False or data.get("code") not in (None, 200):
            raise ProviderUnavailableError(
                f"Kie upload rejected code={data.get('code')}",
                user_message="Error en la subida de imagen. Intenta de nuevo más tarde.",
            )
        payload = data.get("data") or {}
        file_url = payload.get("fileUrl") or payload.get("downloadUrl")
        if not file_url:
            raise ProviderUnavailableError(
                "Kie upload returned no URL",
                user_message="No se pudo subir la imagen. Intenta de nuevo.",
            )
        if not _is_allowed_kie_asset_url(file_url):
            raise ProviderUnavailableError(
                f"Kie upload blocked host: {urllib.parse.urlparse(file_url).hostname}",
                user_message="Error en la subida de imagen. Intenta de nuevo más tarde.",
            )
        return file_url

    async def _create_task(self, session: aiohttp.ClientSession, model_slug: str, input_data: dict) -> str:
        body = {"model": model_slug, "input": input_data}
        try:
            async with session.post(
                f"{KIE_BASE}/api/v1/jobs/createTask",
                headers=self._headers(),
                json=body,
            ) as resp:
                if resp.status != 200:
                    await resp.text()
                    self._raise_http_status(resp.status, context="inicio de tarea")
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ProviderUnavailableError(
                f"Kie createTask failed: {exc}",
                user_message="Error en el inicio de tarea. Intenta de nuevo más tarde.",
            ) from exc
        if data.get("code") != 200:
            self._raise_api_code(data.get("code"))
        task_id = (data.get("data") or {}).get("taskId")
        if not task_id:
            raise ProviderGenerationError(
                "No se pudo iniciar la generación. Intenta de nuevo.",
                user_message="No se pudo iniciar la generación. Intenta de nuevo.",
            )
        return task_id

    async def _poll_task(
        self,
        session: aiohttp.ClientSession,
        task_id: str,
        *,
        prompt: str,
        max_poll_sec: int,
    ) -> list[str]:
        started = time.monotonic()
        while time.monotonic() - started < max_poll_sec:
            data, keep_waiting = await self._poll_once(session, task_id)
            if data is None:
                # transient condition — keep waiting until the outer deadline.
                if keep_waiting:
                    await asyncio.sleep(VIDEO_POLL_INTERVAL_SEC)
                    continue
                raise ProviderGenerationError(
                    _POLL_USER_ERROR, user_message=_POLL_USER_ERROR
                )

            state = (data.get("data") or {}).get("state", "unknown")
            if state == "success":
                result_json_raw = (data.get("data") or {}).get("resultJson")
                if not result_json_raw:
                    raise ProviderGenerationError(
                        "No se recibió resultado. Intenta de nuevo.",
                        user_message="No se recibió resultado. Intenta de nuevo.",
                    )
                result_json = (
                    json.loads(result_json_raw)
                    if isinstance(result_json_raw, str)
                    else result_json_raw
                )
                urls = result_json.get("resultUrls") or []
                allowed = [u for u in urls if _is_allowed_kie_asset_url(u)]
                if not allowed:
                    raise ProviderGenerationError(
                        _POLL_USER_ERROR, user_message=_POLL_USER_ERROR
                    )
                return allowed

            if state == "fail":
                fail_data = data.get("data") or {}
                fail_code = fail_data.get("failCode")
                fail_msg = _sanitize_kie_fail_log(fail_data.get("failMsg"))
                raise ProviderGenerationError(
                    f"Kie task failed code={fail_code} msg={fail_msg}",
                    user_message=_USER_ERROR,
                )

            await asyncio.sleep(VIDEO_POLL_INTERVAL_SEC)

        raise ProviderTimeoutError(
            "Kie poll timeout.",
            user_message="Tiempo de espera agotado. Intenta de nuevo más tarde.",
        )

    async def _poll_once(
        self, session: aiohttp.ClientSession, task_id: str
    ) -> tuple[dict | None, bool]:
        """Poll once. Returns ``(None, True)`` when the caller should keep polling."""
        url = f"{KIE_BASE}/api/v1/jobs/recordInfo?taskId={urllib.parse.quote(task_id)}"
        for attempt in range(POLL_MAX_RETRIES + 1):
            try:
                async with session.get(url, headers=self._headers()) as poll_resp:
                    if poll_resp.status in (404, 422):
                        if attempt < POLL_MAX_RETRIES:
                            await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                            continue
                        return None, True
                    if poll_resp.status == 429:
                        if attempt < POLL_MAX_RETRIES:
                            await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                            continue
                        raise ProviderRateLimitError(
                            "Kie rate limit",
                            user_message="Demasiadas solicitudes. Intenta de nuevo más tarde.",
                        )
                    if poll_resp.status >= 500:
                        if attempt < POLL_MAX_RETRIES:
                            await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                            continue
                        raise ProviderUnavailableError(
                            f"Kie poll HTTP {poll_resp.status}",
                            user_message=_POLL_USER_ERROR,
                        )
                    if poll_resp.status not in (200,):
                        await poll_resp.text()
                        self._raise_http_status(poll_resp.status, context="consulta de tarea")
                    data = await poll_resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < POLL_MAX_RETRIES:
                    await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                    continue
                raise ProviderUnavailableError(
                    f"Kie poll transient: {exc}",
                    user_message=_POLL_USER_ERROR,
                ) from exc

            api_code = data.get("code")
            if api_code != 200:
                transient = _kie_poll_error_is_transient(200, api_code)
                if transient and attempt < POLL_MAX_RETRIES:
                    await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                    continue
                if transient:
                    return None, True
                self._raise_api_code(api_code)
            return data, False
        return None, True

    async def _get_result_url_at_index(
        self, session: aiohttp.ClientSession, task_id: str, index: int
    ) -> tuple[str, ProviderError | None]:
        """Resolve a result URL from a completed Kie task (i2i from bot images)."""
        data, _keep_waiting = await self._poll_once(session, task_id)
        if data is None:
            return "", ProviderInputError(
                "La imagen de referencia no está disponible. Intenta de nuevo.",
                user_message="La imagen de referencia no está disponible. Intenta de nuevo.",
            )
        state = (data.get("data") or {}).get("state")
        if state != "success":
            return "", ProviderInputError(
                "La imagen de referencia no está disponible. Intenta de nuevo.",
                user_message="La imagen de referencia no está disponible. Intenta de nuevo.",
            )
        result_json_raw = (data.get("data") or {}).get("resultJson")
        if not result_json_raw:
            return "", ProviderInputError(
                "No se recibió resultado de la imagen de referencia.",
                user_message="No se recibió resultado de la imagen de referencia.",
            )
        try:
            result_json = (
                json.loads(result_json_raw) if isinstance(result_json_raw, str) else result_json_raw
            )
        except (json.JSONDecodeError, TypeError):
            return "", ProviderInputError(
                "No se pudo interpretar la imagen de referencia.",
                user_message="No se pudo interpretar la imagen de referencia.",
            )
        urls = result_json.get("resultUrls") or []
        if not urls:
            return "", ProviderInputError(
                "No se encontró URL de la imagen de referencia.",
                user_message="No se encontró URL de la imagen de referencia.",
            )
        idx = max(0, min(int(index), len(urls) - 1))
        result_url = urls[idx]
        if not _is_allowed_kie_asset_url(result_url):
            return "", ProviderInputError(
                "URL de referencia bloqueada.",
                user_message="Error en la generación. Intenta de nuevo más tarde.",
            )
        return result_url, None

    def _raise_http_status(self, status: int, *, context: str) -> None:
        """Map a non-ok HTTP status to a typed, user-safe ProviderError.

        D3/M3: terminal 4xx (except 404/422 handled as transient inside the poll
        loop) surface as :class:`ProviderInputError`; only 5xx map to
        :class:`ProviderUnavailableError` (retryable).
        """
        if status in (401, 403):
            raise ProviderAuthenticationError(
                f"Kie authentication failed ({context}).",
                user_message=_USER_ERROR,
            )
        if status == 429:
            raise ProviderRateLimitError(
                f"Kie rate limit ({context}).",
                user_message="Demasiadas solicitudes. Intenta de nuevo en unos segundos.",
            )
        if 400 <= status < 500:
            raise ProviderInputError(
                f"Kie HTTP {status} ({context}).",
                user_message=_USER_ERROR,
            )
        raise ProviderUnavailableError(
            f"Kie HTTP {status} ({context}).",
            user_message=_USER_ERROR,
        )

    def _raise_api_code(self, api_code: int | None) -> None:
        """Map a non-200 Kie business code to a terminal (non-retryable) error."""
        if api_code == 429:
            raise ProviderRateLimitError(
                "Kie rate limit",
                user_message="Demasiadas solicitudes. Intenta de nuevo en unos segundos.",
            )
        if isinstance(api_code, int) and 400 <= api_code < 500:
            raise ProviderInputError(
                f"Kie api code {api_code}",
                user_message=_USER_ERROR,
            )
        raise ProviderGenerationError(
            f"Kie api code {api_code}",
            user_message=_USER_ERROR,
        )
