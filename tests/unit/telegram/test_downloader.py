"""Tests del downloader real (R6): https/allowlist/redirect/size/timeout.

Usa ``aioresponses`` (sin red). Los mensajes user-safe no exponen URLs.
"""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from grokbot.telegram.downloader import (
    AiohttpMediaDownloader,
    DownloadError,
    DownloadTooLargeError,
)
from grokbot.telegram.media import MAX_MEDIA_BYTES

# aioresponses 0.7.9 predata aiohttp 3.14 (el ctor de ClientResponse ahora exige
# ``stream_writer``). Mismo shim test-only que tests/integration/providers, pero
# sin ``unittest.mock`` (regla item 5): el fake stream_writer solo expone
# ``output_size``. No toca código de producto ni constraints.
import aioresponses.core as _aioresponses_core  # noqa: E402
from aiohttp.client import ClientResponse as _AiohttpClientResponse  # noqa: E402


class _FakeStreamWriter:
    output_size = 0


class _CompatClientResponse(_AiohttpClientResponse):
    def __init__(
        self,
        method,
        url,
        *,
        writer=None,
        continue100=None,
        timer=None,
        request_info=None,
        traces=None,
        loop=None,
        session=None,
        stream_writer=None,
    ):
        super().__init__(
            method,
            url,
            writer=writer,
            continue100=continue100,
            timer=timer,
            request_info=request_info,
            traces=traces,
            loop=loop,
            session=session,
            stream_writer=stream_writer if stream_writer is not None else _FakeStreamWriter(),
        )


_aioresponses_core.ClientResponse = _CompatClientResponse

# Hosts ficticios de prueba (R8): nunca URLs de contenido real.
_XAI_OK = "https://files.x.ai/img.png"
_XAI_SUB = "https://storage.xai.com/a.png"
_KIE_OK = "https://static.aiquickdraw.com/out.png"
_KIE_TMP = "https://tempfile.redpandaai.co/out.png"
_EVIL = "https://evil.example.invalid/x.png"
_HTTP = "http://files.x.ai/img.png"

PAYLOAD = b"fake-image-bytes"


@pytest.mark.asyncio
async def test_download_ok_xai_allowlist():
    with aioresponses() as m:
        m.get(_XAI_OK, status=200, body=PAYLOAD)
        data = await AiohttpMediaDownloader().download(_XAI_OK, allowlist="xai")
    assert data == PAYLOAD


@pytest.mark.asyncio
async def test_download_ok_xai_subdomain():
    with aioresponses() as m:
        m.get(_XAI_SUB, status=200, body=PAYLOAD)
        data = await AiohttpMediaDownloader().download(_XAI_SUB, allowlist="xai")
    assert data == PAYLOAD


@pytest.mark.asyncio
async def test_download_ok_kie_allowlist_exact_and_suffix():
    with aioresponses() as m:
        m.get(_KIE_OK, status=200, body=PAYLOAD)
        m.get(_KIE_TMP, status=200, body=PAYLOAD)
        dl = AiohttpMediaDownloader()
        assert await dl.download(_KIE_OK, allowlist="kie") == PAYLOAD
        assert await dl.download(_KIE_TMP, allowlist="kie") == PAYLOAD


@pytest.mark.asyncio
async def test_download_ok_no_allowlist_any_https_host():
    with aioresponses() as m:
        m.get(_EVIL, status=200, body=PAYLOAD)
        data = await AiohttpMediaDownloader().download(_EVIL, allowlist=None)
    assert data == PAYLOAD


@pytest.mark.asyncio
async def test_download_rejects_http_scheme():
    dl = AiohttpMediaDownloader()
    with pytest.raises(DownloadError) as exc:
        await dl.download(_HTTP, allowlist="xai")
    assert exc.value.user_message == "No se pudo descargar el archivo (URL no permitida)."


@pytest.mark.asyncio
async def test_download_rejects_host_outside_allowlist():
    with aioresponses() as m:
        m.get(_EVIL, status=200, body=PAYLOAD)
        dl = AiohttpMediaDownloader()
        with pytest.raises(DownloadError) as exc:
            await dl.download(_EVIL, allowlist="xai")
    assert exc.value.user_message == "No se pudo descargar el archivo (URL no permitida)."


@pytest.mark.asyncio
async def test_download_rechecks_redirect_target():
    redirect_url = "https://files.x.ai/redir"
    with aioresponses() as m:
        m.get(redirect_url, status=301, headers={"Location": _EVIL})
        m.get(_EVIL, status=200, body=PAYLOAD)
        dl = AiohttpMediaDownloader()
        with pytest.raises(DownloadError) as exc:
            await dl.download(redirect_url, allowlist="xai")
    assert exc.value.user_message == "No se pudo descargar el archivo (URL no permitida)."


@pytest.mark.asyncio
async def test_download_rejects_oversize_by_content_length():
    """R10: supera el tope único → DownloadTooLargeError (tipado, user-safe)."""
    mb = MAX_MEDIA_BYTES // (1024 * 1024)
    with aioresponses() as m:
        m.get(_XAI_OK, status=200, body=PAYLOAD, headers={"Content-Length": "999999999"})
        dl = AiohttpMediaDownloader()
        with pytest.raises(DownloadTooLargeError) as exc:
            await dl.download(_XAI_OK, allowlist="xai")
    assert exc.value.user_message == (
        f"El archivo supera el límite de {mb} MB y no se puede enviar por Telegram."
    )


@pytest.mark.asyncio
async def test_download_rejects_oversize_by_stream_chunks():
    """R10: sin Content-Length, el tope se aplica acumulando chunks."""
    dl = AiohttpMediaDownloader(max_bytes=8)
    with aioresponses() as m:
        m.get(_XAI_OK, status=200, body=PAYLOAD)
        with pytest.raises(DownloadTooLargeError) as exc:
            await dl.download(_XAI_OK, allowlist="xai")
    assert "supera el límite" in exc.value.user_message


@pytest.mark.asyncio
async def test_download_empty_body_is_error():
    with aioresponses() as m:
        m.get(_XAI_OK, status=200, body=b"")
        dl = AiohttpMediaDownloader()
        with pytest.raises(DownloadError) as exc:
            await dl.download(_XAI_OK, allowlist="xai")
    assert exc.value.user_message == "No se pudo descargar el archivo. Intenta de nuevo más tarde."


@pytest.mark.asyncio
async def test_download_http_error_status_is_generic():
    with aioresponses() as m:
        m.get(_XAI_OK, status=500, body=b"boom")
        dl = AiohttpMediaDownloader()
        with pytest.raises(DownloadError) as exc:
            await dl.download(_XAI_OK, allowlist="xai")
    assert exc.value.user_message == "No se pudo descargar el archivo. Intenta de nuevo más tarde."


@pytest.mark.asyncio
async def test_connection_error_maps_to_generic():
    dl = AiohttpMediaDownloader(timeout_seconds=1)
    with aioresponses() as m:
        m.get(_XAI_OK, exception=aiohttp.ClientConnectionError("boom"))
        with pytest.raises(DownloadError) as exc:
            await dl.download(_XAI_OK, allowlist="xai")
    assert exc.value.user_message == "No se pudo descargar el archivo. Intenta de nuevo más tarde."
