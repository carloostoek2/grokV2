"""Integration tests for KieProvider against aioresponses (no real network).

Covers: t2i create+poll (task_id meta, host-filtered urls), no-key config error,
upload->i2i normal, KieTaskRef->spicy i2i, D8 spicy-without-ref rejection,
video aspect validation, video 1.5 i2v slug, transient 429 recovery.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from aioresponses import aioresponses

import grokbot.providers.kie_provider as kie_mod
from grokbot.domain.generation import (
    GenerationRequest,
    KieTaskRef,
    MediaType,
)
from grokbot.providers.base import (
    ProviderInputError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from grokbot.providers.kie_provider import KieProvider

# aioresponses 0.7.9 predates aiohttp 3.14's ClientResponse signature. Test-only
# compat shim (same as test_xai_provider.py) — see item SUMMARY for the residual.
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

KIE_BASE = "https://api.kie.ai"
KIE_UPLOAD_BASE = "https://kieai.redpandaai.co"
KIE_IMAGE_I2I = "grok-imagine/image-to-image"
KIE_VIDEO_15_I2V = "grok-imagine-video-1-5-preview"
API_KEY = "kie-secret-key"

PNG_1x1 = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
GOOD_URL = "https://static.aiquickdraw.com/out.png"
EVIL_URL = "https://evil.com/steal.png"


def _req(**overrides):
    base = {
        "provider": "kie",
        "model_id": "grok-imagine-image-2-0/text-to-image",
        "media_type": MediaType.IMAGE,
        "prompt": "hola",
    }
    base.update(overrides)
    return GenerationRequest(**base)


def _record_info_payload(state: str, urls: list[str]) -> dict:
    return {
        "code": 200,
        "data": {"state": state, "resultJson": json.dumps({"resultUrls": urls})},
    }


def _find_recorded(m, method: str, url_part: str):
    for (meth, url), calls in m.requests.items():
        if meth.upper() == method.upper() and url_part in str(url):
            assert calls, f"no recorded {method} calls for {url_part}"
            return calls[0].kwargs
    raise AssertionError(f"no recorded {method} call containing {url_part}")


async def _no_sleep(*_args, **_kwargs):
    return None


def test_t2i_create_poll_success_with_filtered_urls():
    import asyncio

    prov = KieProvider(API_KEY)
    req = _req()
    with aioresponses() as m:
        m.post(
            f"{KIE_BASE}/api/v1/jobs/createTask",
            status=200,
            payload={"code": 200, "data": {"taskId": "task-1"}},
        )
        m.get(
            f"{KIE_BASE}/api/v1/jobs/recordInfo?taskId=task-1",
            status=200,
            payload=_record_info_payload("success", [GOOD_URL, EVIL_URL]),
        )
        result = asyncio.run(prov.generate(req))

    assert result.remote_url == GOOD_URL
    assert result.meta["urls"] == [GOOD_URL]
    assert result.meta["task_id"] == "task-1"
    assert result.meta["provider"] == "kie"
    assert result.meta["download_allowlist"] == "kie"

    kwargs = _find_recorded(m, "POST", "createTask")
    body = kwargs["json"]
    assert body["model"] == req.model_id
    inp = body["input"]
    assert inp["prompt"] == "hola"
    assert inp["aspect_ratio"] == "9:16"
    assert inp["enable_pro"] is True
    assert inp["nsfw_checker"] is False
    # R2: token never in the serialized body; it lives in the Authorization header.
    assert API_KEY not in json.dumps(body)
    auth = kwargs.get("headers", {}).get("Authorization")
    assert auth == f"Bearer {API_KEY}"


def test_no_key_raises_not_configured():
    import asyncio

    prov = KieProvider("")
    with pytest.raises(ProviderNotConfiguredError):
        asyncio.run(prov.generate(_req()))


def test_upload_i2i_normal_mode():
    import asyncio

    prov = KieProvider(API_KEY)
    with aioresponses() as m:
        m.post(
            f"{KIE_UPLOAD_BASE}/api/file-base64-upload",
            status=200,
            payload={"success": True, "code": 200, "data": {"fileUrl": "https://kieai.redpandaai.co/up.png"}},
        )
        m.post(
            f"{KIE_BASE}/api/v1/jobs/createTask",
            status=200,
            payload={"code": 200, "data": {"taskId": "task-i2i"}},
        )
        m.get(
            f"{KIE_BASE}/api/v1/jobs/recordInfo?taskId=task-i2i",
            status=200,
            payload=_record_info_payload("success", [GOOD_URL]),
        )
        result = asyncio.run(prov.generate(_req(), source_image=PNG_1x1))

    assert result.remote_url == GOOD_URL
    kwargs = _find_recorded(m, "POST", "createTask")
    body = kwargs["json"]
    assert body["model"] == KIE_IMAGE_I2I
    inp = body["input"]
    assert inp["image_urls"] == ["https://kieai.redpandaai.co/up.png"]
    assert inp["mode"] == "normal"


def test_kie_task_ref_i2i_spicy_mode():
    import asyncio

    prov = KieProvider(API_KEY)
    req = _req(source=KieTaskRef("task-source", index=1), params={"mode": "spicy"})
    with aioresponses() as m:
        m.get(
            f"{KIE_BASE}/api/v1/jobs/recordInfo?taskId=task-source",
            status=200,
            payload=_record_info_payload("success", ["https://tempfile.aiquickdraw.com/src.png", GOOD_URL]),
        )
        m.post(
            f"{KIE_BASE}/api/v1/jobs/createTask",
            status=200,
            payload={"code": 200, "data": {"taskId": "task-spicy"}},
        )
        m.get(
            f"{KIE_BASE}/api/v1/jobs/recordInfo?taskId=task-spicy",
            status=200,
            payload=_record_info_payload("success", [GOOD_URL]),
        )
        result = asyncio.run(prov.generate(req))

    assert result.remote_url == GOOD_URL
    kwargs = _find_recorded(m, "POST", "createTask")
    body = kwargs["json"]
    assert body["model"] == KIE_IMAGE_I2I
    inp = body["input"]
    # index=1 resolves the source URL at the second position.
    assert inp["image_urls"] == [GOOD_URL]
    assert inp["mode"] == "spicy"


def test_spicy_without_ref_raises_input_error_image_and_video():
    import asyncio

    prov = KieProvider(API_KEY)
    img_req = _req(params={"mode": "spicy"})
    with pytest.raises(ProviderInputError) as ei:
        asyncio.run(prov.generate(img_req))
    assert "spicy" in str(ei.value)

    vid_req = _req(
        media_type=MediaType.VIDEO,
        model_id="grok-imagine-video",
        params={"mode": "spicy"},
        aspect_ratio="16:9",
    )
    with pytest.raises(ProviderInputError) as ev:
        asyncio.run(prov.generate(vid_req))
    assert "spicy" in str(ev.value)


def test_video_invalid_aspect_raises_input_error():
    import asyncio

    prov = KieProvider(API_KEY)
    req = _req(
        media_type=MediaType.VIDEO,
        model_id="grok-imagine-video",
        aspect_ratio="4:3",
    )
    with pytest.raises(ProviderInputError) as exc:
        asyncio.run(prov.generate(req))
    assert "aspecto" in str(exc.value).lower() or "aspecto" in exc.value.user_message.lower()


@pytest.mark.asyncio
async def test_video_15_i2v_slug_no_mode(monkeypatch):
    monkeypatch.setattr(kie_mod.asyncio, "sleep", _no_sleep)
    prov = KieProvider(API_KEY)
    req = _req(
        media_type=MediaType.VIDEO,
        model_id="grok-imagine-video-1.5",
        aspect_ratio="16:9",
        video_duration=10,
        video_resolution="720p",
    )
    with aioresponses() as m:
        m.post(
            f"{KIE_UPLOAD_BASE}/api/file-base64-upload",
            status=200,
            payload={"success": True, "code": 200, "data": {"fileUrl": "https://tempfile.redpandaai.co/v.png"}},
        )
        m.post(
            f"{KIE_BASE}/api/v1/jobs/createTask",
            status=200,
            payload={"code": 200, "data": {"taskId": "task-v15"}},
        )
        m.get(
            f"{KIE_BASE}/api/v1/jobs/recordInfo?taskId=task-v15",
            status=200,
            payload=_record_info_payload("success", [GOOD_URL]),
        )
        result = await prov.generate(req, source_image=PNG_1x1)

    assert result.media_type is MediaType.VIDEO
    assert result.remote_url == GOOD_URL
    kwargs = _find_recorded(m, "POST", "createTask")
    body = kwargs["json"]
    assert body["model"] == KIE_VIDEO_15_I2V
    inp = body["input"]
    assert inp["duration"] == 10
    assert inp["image_urls"] == ["https://tempfile.redpandaai.co/v.png"]
    assert "mode" not in inp  # 1.5 i2v omits mode entirely


def test_create_task_http_400_raises_input_error_not_unavailable():
    import asyncio

    prov = KieProvider(API_KEY)
    with aioresponses() as m:
        m.post(
            f"{KIE_BASE}/api/v1/jobs/createTask",
            status=400,
            body=b"bad payload",
        )
        with pytest.raises(ProviderInputError):
            asyncio.run(prov.generate(_req()))


def test_create_task_http_500_raises_unavailable():
    import asyncio

    prov = KieProvider(API_KEY)
    with aioresponses() as m:
        m.post(
            f"{KIE_BASE}/api/v1/jobs/createTask",
            status=500,
            body=b"boom",
        )
        with pytest.raises(ProviderUnavailableError):
            asyncio.run(prov.generate(_req()))


def test_create_task_business_code_400_raises_input_error():
    """M3: a terminal 4xx *business* code (HTTP 200 body) is not retryable."""
    import asyncio

    prov = KieProvider(API_KEY)
    with aioresponses() as m:
        m.post(
            f"{KIE_BASE}/api/v1/jobs/createTask",
            status=200,
            payload={"code": 400, "data": {}},
        )
        with pytest.raises(ProviderInputError):
            asyncio.run(prov.generate(_req()))


def test_create_task_business_code_429_raises_rate_limit():
    """M3: a 429 *business* code stays transient (retryable)."""
    import asyncio

    prov = KieProvider(API_KEY)
    with aioresponses() as m:
        m.post(
            f"{KIE_BASE}/api/v1/jobs/createTask",
            status=200,
            payload={"code": 429, "data": {}},
        )
        with pytest.raises(ProviderRateLimitError):
            asyncio.run(prov.generate(_req()))


@pytest.mark.asyncio
async def test_poll_transient_429_recovery(monkeypatch):
    monkeypatch.setattr(kie_mod.asyncio, "sleep", _no_sleep)
    prov = KieProvider(API_KEY)
    req = _req()
    poll_url = f"{KIE_BASE}/api/v1/jobs/recordInfo?taskId=task-retry"
    with aioresponses() as m:
        m.post(
            f"{KIE_BASE}/api/v1/jobs/createTask",
            status=200,
            payload={"code": 200, "data": {"taskId": "task-retry"}},
        )
        m.get(poll_url, status=429)
        m.get(poll_url, status=429)
        m.get(poll_url, status=200, payload=_record_info_payload("success", [GOOD_URL]))
        result = await prov.generate(req)

    assert result.remote_url == GOOD_URL
