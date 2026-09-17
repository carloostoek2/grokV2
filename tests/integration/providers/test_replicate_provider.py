"""Integration tests for ReplicateProvider with an injected fake client.run."""

from __future__ import annotations

import io
import json

import pytest

from grokbot.domain.catalog import MODELS
from grokbot.domain.generation import (
    GenerationRequest,
    MediaType,
)
from grokbot.providers.base import (
    ProviderAuthenticationError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderRateLimitError,
    ProviderUnavailableError,
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
    # R2: the api token never travels in the serialized model input.
    assert API_TOKEN not in json.dumps({"model_id": model_id, "input": input_data, **kwargs})


@pytest.mark.asyncio
async def test_grok_t2i_uses_request_aspect_ratio_when_set():
    client = FakeClient()
    prov = ReplicateProvider(API_TOKEN, client=client)
    await prov.generate(_req("grok", aspect_ratio="1:1"))
    model_id, input_data, kwargs = client.calls[0]
    assert input_data == {"prompt": "hello", "aspect_ratio": "1:1"}


@pytest.mark.asyncio
async def test_grok_i2i_names_buffer_from_magic_bytes():
    """Replicate infers MIME from file.name; a nameless BytesIO becomes .bin."""
    client = FakeClient()
    prov = ReplicateProvider(API_TOKEN, client=client)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    await prov.generate(_req("grok"), source_image=png)
    model_id, input_data, kwargs = client.calls[0]
    assert model_id == GROK_REPLICATE_ID
    assert isinstance(input_data["image"], io.BytesIO)
    assert input_data["image"].name == "image.png"
    assert kwargs == {"file_encoding_strategy": "base64"}
    assert "aspect_ratio" not in input_data


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
async def test_swap_face_sends_two_bytesio_without_prompt():
    client = FakeClient()
    prov = ReplicateProvider(API_TOKEN, client=client)
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    await prov.swap_face(swap_image=jpg, input_image=jpg)
    model_id, input_data, kwargs = client.calls[0]
    assert model_id == MODELS["faceswap"]["id"]
    assert kwargs == {"file_encoding_strategy": "base64", "wait": 60}
    assert isinstance(input_data["swap_image"], io.BytesIO)
    assert isinstance(input_data["input_image"], io.BytesIO)
    assert input_data["swap_image"].name == "image.jpg"
    assert input_data["input_image"].name == "image.jpg"
    assert "prompt" not in input_data
    assert "image" not in input_data


@pytest.mark.asyncio
async def test_swap_face_no_url_raises_generation_error():
    client = FakeClient()
    client.output = []  # FakeClient.__init__ treats [] as "no override"
    prov = ReplicateProvider(API_TOKEN, client=client)
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    with pytest.raises(ProviderGenerationError):
        await prov.swap_face(swap_image=jpg, input_image=jpg)


@pytest.mark.asyncio
async def test_swap_face_sdk_exception_maps_to_unavailable():
    prov = ReplicateProvider(API_TOKEN, client=_FailingClient(RuntimeError("boom")))
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    with pytest.raises(ProviderUnavailableError):
        await prov.swap_face(swap_image=jpg, input_image=jpg)


@pytest.mark.asyncio
async def test_generate_faceswap_raises_input_error():
    # Anti-footgun: faceswap is a two-image operation with no prompt; calling
    # generate() with that model is a caller bug (must use swap_face()).
    client = FakeClient()
    prov = ReplicateProvider(API_TOKEN, client=client)
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    with pytest.raises(ProviderInputError):
        await prov.generate(_req("faceswap"), source_image=jpg)
    assert client.calls == []


@pytest.mark.asyncio
async def test_multi_url_output_normalization():
    client = FakeClient(output=["https://a.io/1.png", "https://a.io/2.png"])
    prov = ReplicateProvider(API_TOKEN, client=client)
    result = await prov.generate(_req("grok"))
    assert result.remote_url == "https://a.io/1.png"
    assert result.meta["urls"] == ["https://a.io/1.png", "https://a.io/2.png"]


class _FailingClient:
    """Fake replicate client whose ``run`` always raises the configured error."""

    def __init__(self, exc):
        self._exc = exc

    def run(self, model_id, input=None, **kwargs):
        raise self._exc


class _ModelError(Exception):
    """Duck-type of replicate.exceptions.ModelError (has ``prediction``)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.prediction = object()


class _HttpError(Exception):
    """Duck-type of replicate.exceptions.ReplicateError (has ``status``)."""

    def __init__(self, status: int):
        super().__init__(f"http {status}")
        self.status = status


@pytest.mark.asyncio
async def test_sdk_exception_maps_to_unavailable():
    prov = ReplicateProvider(API_TOKEN, client=_FailingClient(RuntimeError("boom")))
    with pytest.raises(ProviderUnavailableError):
        await prov.generate(_req("grok"))


@pytest.mark.asyncio
async def test_prediction_failure_maps_to_generation_error_not_retryable():
    """Unknown prediction failures stay ProviderGenerationError (terminal)."""
    prov = ReplicateProvider(
        API_TOKEN,
        client=_FailingClient(_ModelError("upstream predictor crashed")),
    )
    with pytest.raises(ProviderGenerationError) as excinfo:
        await prov.generate(_req("grok"))
    assert excinfo.value.retryable is False


@pytest.mark.asyncio
async def test_prediction_invalid_input_maps_to_input_error():
    """Invalid-image prediction text classifies as invalid_input (still terminal)."""
    from grokbot.providers.error_mapping import USER_MSG_INVALID_INPUT

    prov = ReplicateProvider(
        API_TOKEN,
        client=_FailingClient(_ModelError("Invalid image format '.bin'")),
    )
    with pytest.raises(ProviderInputError) as excinfo:
        await prov.generate(_req("grok"))
    assert excinfo.value.retryable is False
    assert excinfo.value.user_message == USER_MSG_INVALID_INPUT


@pytest.mark.asyncio
async def test_prediction_sensitive_maps_to_content_error():
    from grokbot.providers.base import ProviderContentError
    from grokbot.providers.error_mapping import USER_MSG_MODERATION

    class _SensitivePred:
        error = (
            "The input or output was flagged as sensitive "
            "content by a content safety classifier (E005). "
            "Flagged categories: sexual"
        )

    class _SensitiveModelError(Exception):
        def __init__(self):
            super().__init__(_SensitivePred.error)
            self.prediction = _SensitivePred()

    prov = ReplicateProvider(API_TOKEN, client=_FailingClient(_SensitiveModelError()))
    with pytest.raises(ProviderContentError) as excinfo:
        await prov.generate(_req("grok"))
    assert excinfo.value.retryable is False
    assert excinfo.value.user_message == USER_MSG_MODERATION


@pytest.mark.asyncio
async def test_http_429_maps_to_rate_limit():
    prov = ReplicateProvider(API_TOKEN, client=_FailingClient(_HttpError(429)))
    with pytest.raises(ProviderRateLimitError) as excinfo:
        await prov.generate(_req("grok"))
    assert excinfo.value.retryable is True


@pytest.mark.asyncio
async def test_http_401_maps_to_auth_error():
    prov = ReplicateProvider(API_TOKEN, client=_FailingClient(_HttpError(401)))
    with pytest.raises(ProviderAuthenticationError) as excinfo:
        await prov.generate(_req("grok"))
    assert excinfo.value.retryable is False


@pytest.mark.asyncio
async def test_provider_error_passthrough_not_wrapped():
    # A typed ProviderError raised by the SDK/client must propagate untouched
    # (the retryability decision belongs to the caller, R8), not be wrapped as
    # a transient ProviderUnavailableError.
    prov = ReplicateProvider(
        API_TOKEN,
        client=_FailingClient(ProviderInputError("bad payload", user_message="bad")),
    )
    with pytest.raises(ProviderInputError):
        await prov.generate(_req("grok"))
