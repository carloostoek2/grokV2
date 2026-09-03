"""Integration tests for ReplicateProvider with an injected fake client.run."""

from __future__ import annotations

import io

import pytest

from grokbot.domain.catalog import MODELS
from grokbot.domain.generation import (
    GenerationRequest,
    MediaType,
)
from grokbot.providers.replicate_provider import ReplicateProvider

API_TOKEN = "r8-token-secret"


class FakeClient:
    """Records ``run`` calls and returns configurable output."""

    def __init__(self, output=None):
        self.calls = []
        self.output = output or ["https://replicate.delivery/out.png"]

    def run(self, model_id, input=None, **kwargs):
        self.calls.append((model_id, dict(input or {}), kwargs))
        return self.output


GROK_REPLICATE_ID = "xai/grok-imagine-image-quality"  # quality variant replicate mirror


def _req(model_key: str, *, provider="replicate", media_type=MediaType.IMAGE, **overrides):
    spec = MODELS[model_key]
    model_id = GROK_REPLICATE_ID if model_key == "grok" else spec["id"]
    base = {
        "provider": provider,
        "model_id": model_id,
        "media_type": media_type,
        "prompt": "hello",
    }
    base.update(overrides)
    return GenerationRequest(**base)


@pytest.mark.asyncio
async def test_grok_t2i_adds_default_aspect_ratio():
    client = FakeClient()
    prov = ReplicateProvider(API_TOKEN, client=client)
    result = await prov.generate(_req("grok"))
    assert result.remote_url == "https://replicate.delivery/out.png"
    assert result.meta["download_allowlist"] is None
    model_id, input_data, kwargs = client.calls[0]
    assert model_id == GROK_REPLICATE_ID
    assert input_data == {"prompt": "hello", "aspect_ratio": "9:16"}
    assert kwargs == {}


@pytest.mark.asyncio
async def test_grok_t2i_uses_request_aspect_ratio_when_set():
    client = FakeClient()
    prov = ReplicateProvider(API_TOKEN, client=client)
    await prov.generate(_req("grok", aspect_ratio="1:1"))
    model_id, input_data, kwargs = client.calls[0]
    assert input_data == {"prompt": "hello", "aspect_ratio": "1:1"}


@pytest.mark.asyncio
async def test_seedream_t2i_has_no_aspect_ratio():
    client = FakeClient()
    prov = ReplicateProvider(API_TOKEN, client=client)
    await prov.generate(_req("seedream"))
    model_id, input_data, kwargs = client.calls[0]
    assert input_data == {"prompt": "hello"}


@pytest.mark.asyncio
async def test_seedream_i2i_uses_image_input_and_2k():
    client = FakeClient()
    prov = ReplicateProvider(API_TOKEN, client=client)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    await prov.generate(_req("seedream"), source_image=png)
    model_id, input_data, kwargs = client.calls[0]
    assert input_data["prompt"] == "hello"
    assert input_data["size"] == "2K"
    assert isinstance(input_data["image_input"], list)
    assert input_data["image_input"][0].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_faceswap_i2i_uses_bytesio_and_base64_strategy():
    client = FakeClient()
    prov = ReplicateProvider(API_TOKEN, client=client)
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    await prov.generate(_req("faceswap"), source_image=jpg)
    model_id, input_data, kwargs = client.calls[0]
    assert isinstance(input_data["image"], io.BytesIO)
    assert kwargs == {"file_encoding_strategy": "base64"}


@pytest.mark.asyncio
async def test_multi_url_output_normalization():
    client = FakeClient(output=["https://a.io/1.png", "https://a.io/2.png"])
    prov = ReplicateProvider(API_TOKEN, client=client)
    result = await prov.generate(_req("grok"))
    assert result.remote_url == "https://a.io/1.png"
    assert result.meta["urls"] == ["https://a.io/1.png", "https://a.io/2.png"]
