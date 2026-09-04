"""Descarga de media remota con allowlist de hosts (R7/D5, item 5).

Reimplementa las reglas de grok download (bot.py 5066-5110 y 626-661): https
obligatorio, allowlist ``"xai"``/``"kie"`` con hosts exactos + sufijos, los
redirects se re-chequean con la misma allowlist, tope de media único
(``media.MAX_MEDIA_BYTES``, R10) y timeout 120 s. Nunca loguea la URL
descargada (R8).

``MediaDownloader`` (Protocol) vive en ports.py; acá está el adaptador real con
aiohttp. Los tests usan ``aioresponses`` (sin red).
"""

from __future__ import annotations

import asyncio
import urllib.parse

import aiohttp

# Hosts exactos de descarga de Kie.ai (bot.py:180-187).
KIE_DOWNLOAD_HOSTS = frozenset({
    "kieai.redpandaai.co",
    "static.aiquickdraw.com",
    "tempfile.redpandaai.co",
    "tempfile.aiquickdraw.com",
    "file.aiquickdraw.com",
})
_XAI_SUFFIXES = (".x.ai", ".xai.com")
_KIE_SUFFIXES = (".aiquickdraw.com", ".redpandaai.co")

from grokbot.telegram.media import MAX_MEDIA_BYTES

_DOWNLOAD_TIMEOUT_SEC = 120

_NOT_ALLOWED_MSG = "No se pudo descargar el archivo (URL no permitida)."
_GENERIC_ERROR_MSG = "No se pudo descargar el archivo. Intenta de nuevo más tarde."


class DownloadError(Exception):
    """Error de descarga con ``user_message`` user-safe (R6)."""

    def __init__(self, user_message: str = _GENERIC_ERROR_MSG) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class DownloadTooLargeError(DownloadError):
    """El contenido excede ``MAX_MEDIA_BYTES`` (no se puede enviar por Telegram).

    Tipado aparte para que el sender distinga "demasiado grande" de otros
    errores de descarga y pueda ofrecer la URL firmada de recuperación (R10) sin
    haber descargado el cuerpo completo.
    """


def _is_allowed_xai_host(host: str) -> bool:
    host = (host or "").lower()
    if host == "x.ai":
        return True
    return any(host.endswith(suffix) for suffix in _XAI_SUFFIXES)


def _is_allowed_kie_host(host: str) -> bool:
    host = (host or "").lower()
    if host in KIE_DOWNLOAD_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in _KIE_SUFFIXES)


def _is_host_allowed(host: str, allowlist: str | None) -> bool:
    if allowlist == "xai":
        return _is_allowed_xai_host(host)
    if allowlist == "kie":
        return _is_allowed_kie_host(host)
    # allowlist None → sin chequeo de host (solo https).
    return True


def _validate_url(url: str, allowlist: str | None) -> None:
    """Valida scheme https + host de la allowlist; lanza DownloadError si no."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise DownloadError(_NOT_ALLOWED_MSG)
    host = parsed.hostname or ""
    if not _is_host_allowed(host, allowlist):
        raise DownloadError(_NOT_ALLOWED_MSG)


class AiohttpMediaDownloader:
    """``MediaDownloader`` real con aiohttp (timeout 120s, tope MAX_MEDIA_BYTES)."""

    def __init__(
        self,
        *,
        timeout_seconds: int = _DOWNLOAD_TIMEOUT_SEC,
        max_bytes: int = MAX_MEDIA_BYTES,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_bytes = max_bytes

    def _too_large_error(self) -> DownloadTooLargeError:
        mb = self._max_bytes // (1024 * 1024)
        return DownloadTooLargeError(
            f"El archivo supera el límite de {mb} MB y no se puede enviar por Telegram."
        )

    async def download(self, url: str, *, allowlist: str | None = None) -> bytes:
        _validate_url(url, allowlist)
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    # Re-chequear el destino final tras redirects (misma allowlist).
                    final = resp.url
                    if final.scheme != "https" or not _is_host_allowed(
                        final.host or "", allowlist
                    ):
                        raise DownloadError(_NOT_ALLOWED_MSG)
                    if not 200 <= resp.status < 300:
                        raise DownloadError(_GENERIC_ERROR_MSG)
                    declared = resp.content_length
                    if declared is not None and declared > self._max_bytes:
                        raise self._too_large_error()
                    chunks = bytearray()
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        chunks.extend(chunk)
                        if len(chunks) > self._max_bytes:
                            raise self._too_large_error()
            if not chunks:
                raise DownloadError(_GENERIC_ERROR_MSG)
            return bytes(chunks)
        except DownloadError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            raise DownloadError(_GENERIC_ERROR_MSG) from None
