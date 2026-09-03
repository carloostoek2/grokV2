"""Integration tests for XaiProvider against aioresponses (no real network).

Covers: t2i body + token-not-in-body, i2i single-image, 2-image edit_with_reference,
oversized i2v rejection, video create/poll done/failed/moderation/timeout.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from aioresponses import aioresponses

import grokbot.providers.xai_provider as xai_mod
from grokbot.domain.generation import (
    GenerationRequest,
    MediaType,
)
from grokbot.providers.base import (
    I2V_MAX_IMAGE_BYTES,
    ProviderContentError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from grokbot.providers.xai_provider import XaiProvider

# aioresponses 0.7.9 predates aiohttp 3.14's ``ClientResponse`` signature
# (``stream_writer`` is now required). Test-only compat shim: subclass the
# real response class so the mock transport can keep building responses.
# Reported as a tooling residual in the item SUMMARY (does not touch product
# code or dependency constraints).
import aioresponses.core as _aioresponses_core  # noqa: E402
from aiohttp.client import ClientResponse as _AiohttpClientResponse  # noqa: E402


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
            stream_writer=stream_writer if stream_writer is not None else Mock(),
        )


_aioresponses_core.ClientResponse = _CompatClientResponse

XAI_BASE = "https://api.x.ai/v1"
API_KEY = "sk-test-xai-secret"
PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 4 + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
)


def _img_request(media_type=MediaType.IMAGE, **overrides):
    base = {
        "provider": "xai",
        "model_id": "grok-imagine-image-quality",
        "media_type": media_type,
        "prompt": "a cat",
    }
    base.update(overrides)
    return GenerationRequest(**base)


def _find_recorded(m, method: str, url_part: str):
    """Return the kwargs of the first recorded call to a matching URL."""
    for (meth, url), calls in m.requests.items():
        if meth.upper() == method.upper() and url_part in str(url):
            assert calls, f"no recorded {method} calls for {url_part}"
            return calls[0].kwargs
    raise AssertionError(f"no recorded {method} call containing {url_part}")


def test_t2i_multi_url_normalization():
    import asyncio

    prov = XaiProvider(API_KEY)
    with aioresponses() as m:
        m.post(
            f"{XAI_BASE}/images/generations",
            status=200,
            payload={"data": [{"url": "https://x.ai/1.png"}, {"url": "https://x.ai/2.png"}]},
        )
        result = asyncio.run(prov.generate(_img_request()))

    assert result.remote_url == "https://x.ai/1.png"
    assert result.meta["urls"] == ["https://x.ai/1.png", "https://x.ai/2.png"]
    assert result.meta["download_allowlist"] == "xai"


def test_t2i_body_ok_and_token_not_in_body():
    import asyncio

    prov = XaiProvider(API_KEY)
    with aioresponses() as m:
        m.post(f"{XAI_BASE}/images/generations", status=200, payload={"data": [{"url": "https://x.ai/o.png"}]})
        asyncio.run(prov.generate(_img_request(prompt="a cat", aspect_ratio="16:9")))
        kwargs = _find_recorded(m, "POST", "/images/generations")
        body = kwargs.get("json")
        assert body["model"] == "grok-imagine-image-quality"
        assert body["prompt"] == "a cat"
        assert body["n"] == 1
        assert body["aspect_ratio"] == "16:9"
        # R2: the API key must never travel in the serialized request body.
        body_str = json.dumps(body)
        assert API_KEY not in body_str
        auth = kwargs.get("headers", {}).get("Authorization")
        assert auth == f"Bearer {API_KEY}"


def test_t2i_defaults_aspect_ratio_when_none():
    import asyncio

    prov = XaiProvider(API_KEY)
    with aioresponses() as m:
        m.post(f"{XAI_BASE}/images/generations", status=200, payload={"data": [{"url": "https://x.ai/o.png"}]})
        asyncio.run(prov.generate(_img_request(aspect_ratio=None)))
        kwargs = _find_recorded(m, "POST", "/images/generations")
        assert kwargs["json"]["aspect_ratio"] == "9:16"


def test_i2i_single_image_body_data_uri():
    import asyncio

    prov = XaiProvider(API_KEY)
    with aioresponses() as m:
        m.post(f"{XAI_BASE}/images/edits", status=200, payload={"data": [{"url": "https://x.ai/o.png"}]})
        asyncio.run(prov.generate(_img_request(), source_image=PNG_1x1))
        kwargs = _find_recorded(m, "POST", "/images/edits")
        body = kwargs["json"]
        assert body["model"] == "grok-imagine-image-quality"
        img = body["image"]
        assert img["type"] == "image_url"
        assert img["url"].startswith("data:image/png;base64,")
        body_str = json.dumps(body)
        assert API_KEY not in body_str


def test_http_400_raises_input_error_not_unavailable():
    import asyncio

    prov = XaiProvider(API_KEY)
    with aioresponses() as m:
        m.post(f"{XAI_BASE}/images/generations", status=400, body=b"bad request")
        with pytest.raises(ProviderInputError):
            asyncio.run(prov.generate(_img_request()))


def test_http_500_raises_unavailable():
    import asyncio

    prov = XaiProvider(API_KEY)
    with aioresponses() as m:
        m.post(f"{XAI_BASE}/images/generations", status=500, body=b"boom")
        with pytest.raises(ProviderUnavailableError):
            asyncio.run(prov.generate(_img_request()))


def test_i2v_oversized_image_raises_input_error():
    import asyncio

    prov = XaiProvider(API_KEY)
    oversized = b"\x00" * (I2V_MAX_IMAGE_BYTES + 1)
    with pytest.raises(ProviderInputError):
        asyncio.run(prov.generate(_img_request(), source_image=oversized))


def test_edit_with_reference_two_images():
    import asyncio

    prov = XaiProvider(API_KEY)
    ref = b"\xff\xd8\xff\xe0" + b"\x00" * 16
    with aioresponses() as m:
        m.post(f"{XAI_BASE}/images/edits", status=200, payload={"data": [{"url": "https://x.ai/o.png"}]})
        result = asyncio.run(prov.edit_with_reference(_img_request(), source_image=PNG_1x1, reference_image=ref))
        kwargs = _find_recorded(m, "POST", "/images/edits")
        body = kwargs["json"]
        images = body["images"]
        assert len(images) == 2
        assert images[0]["url"].startswith("data:image/png;base64,")
        assert images[1]["url"].startswith("data:image/jpeg;base64,")
        assert all(img["type"] == "image_url" for img in images)
    assert result.remote_url == "https://x.ai/o.png"


async def _video_request(**overrides):
    base = {
        "provider": "xai",
        "model_id": "grok-imagine-video",
        "media_type": MediaType.VIDEO,
        "prompt": "waves",
        "aspect_ratio": "16:9",
        "video_duration": 5,
        "video_resolution": "720p",
    }
    base.update(overrides)
    return GenerationRequest(**base)


def _assert_video_create_body(m):
    kwargs = _find_recorded(m, "POST", "/videos/generations")
    body = kwargs["json"]
    assert body["model"] == "grok-imagine-video"
    assert body["prompt"] == "waves"
    assert body["duration"] == 5
    assert body["aspect_ratio"] == "16:9"
    assert body["resolution"] == "720p"
    body_str = json.dumps(body)
    assert API_KEY not in body_str


async def _no_sleep(*_args, **_kwargs):
    """No-op awaitable used to neutralise ``asyncio.sleep`` in poll tests."""
    return None


@pytest.mark.asyncio
async def test_video_create_pending_processing_done(monkeypatch):
    monkeypatch.setattr(xai_mod.asyncio, "sleep", _no_sleep)
    prov = XaiProvider(API_KEY)
    req = await _video_request()
    gen_url = f"{XAI_BASE}/videos/generations"
    poll_url = f"{XAI_BASE}/videos/vid-1"
    with aioresponses() as m:
        m.post(gen_url, status=202, payload={"request_id": "vid-1"})
        m.get(poll_url, status=200, payload={"status": "pending"})
        m.get(poll_url, status=200, payload={"status": "processing"})
        m.get(
            poll_url,
            status=200,
            payload={
                "status": "done",
                "video": {"url": "https://x.ai/vid.mp4", "respect_moderation": True},
            },
        )
        result = await prov.generate(req)

    assert result.remote_url == "https://x.ai/vid.mp4"
    assert result.meta["urls"] == ["https://x.ai/vid.mp4"]
    assert result.meta["download_allowlist"] == "xai"
    _assert_video_create_body(m)


@pytest.mark.asyncio
async def test_video_failed_raises_generation_error(monkeypatch):
    monkeypatch.setattr(xai_mod.asyncio, "sleep", _no_sleep)
    prov = XaiProvider(API_KEY)
    req = await _video_request()
    with aioresponses() as m:
        m.post(f"{XAI_BASE}/videos/generations", status=200, payload={"request_id": "vid-fail"})
        m.get(f"{XAI_BASE}/videos/vid-fail", status=200, payload={"status": "failed"})
        with pytest.raises(ProviderGenerationError):
            await prov.generate(req)


@pytest.mark.asyncio
async def test_video_moderation_false_raises_content_error(monkeypatch):
    monkeypatch.setattr(xai_mod.asyncio, "sleep", _no_sleep)
    prov = XaiProvider(API_KEY)
    req = await _video_request()
    with aioresponses() as m:
        m.post(f"{XAI_BASE}/videos/generations", status=200, payload={"request_id": "vid-mod"})
        m.get(
            f"{XAI_BASE}/videos/vid-mod",
            status=200,
            payload={"status": "done", "video": {"url": "https://x.ai/x.mp4", "respect_moderation": False}},
        )
        with pytest.raises(ProviderContentError):
            await prov.generate(req)


@pytest.mark.asyncio
async def test_video_timeout_raises_timeout_error(monkeypatch):
    monkeypatch.setattr(xai_mod.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(xai_mod, "VIDEO_MAX_POLL_SEC", 0)
    prov = XaiProvider(API_KEY)
    req = await _video_request()
    with aioresponses() as m:
        m.post(f"{XAI_BASE}/videos/generations", status=200, payload={"request_id": "vid-t"})
        with pytest.raises(ProviderTimeoutError):
            await prov.generate(req)
