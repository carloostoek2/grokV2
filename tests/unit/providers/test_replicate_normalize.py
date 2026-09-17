"""Unit tests for Replicate provider pure helpers (no replicate import)."""

from __future__ import annotations

from dataclasses import dataclass

from grokbot.domain.catalog import MODELS
from grokbot.providers.base import (
    ProviderGenerationError,
    ProviderInputError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from grokbot.domain.generation import GenerationRequest, MediaType
from grokbot.providers.replicate_provider import (
    _grok_video_replicate_input,
    _named_image_buffer,
    _nano_banana_replicate_input,
    _normalize_output_urls,
    _replicate_kind,
    _wrap_run_error,
)


@dataclass
class _FakeFileOutput:
    url: str


def test_normalize_file_output_object():
    out = _FakeFileOutput(url="https://replicate.delivery/out.png")
    assert _normalize_output_urls(out) == ["https://replicate.delivery/out.png"]


def test_normalize_str_single():
    assert _normalize_output_urls("https://x.ai/1.png") == ["https://x.ai/1.png"]


def test_normalize_list_of_mixed():
    out = [
        _FakeFileOutput(url="https://replicate.delivery/a.png"),
        "https://replicate.delivery/b.png",
        None,
    ]
    assert _normalize_output_urls(out) == [
        "https://replicate.delivery/a.png",
        "https://replicate.delivery/b.png",
    ]


def test_normalize_none_and_empty():
    assert _normalize_output_urls(None) == []
    assert _normalize_output_urls([]) == []


def test_named_image_buffer_sets_extension_from_magic():
    png = _named_image_buffer(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    jpeg = _named_image_buffer(b"\xff\xd8\xff\xe0" + b"\x00" * 8)
    webp = _named_image_buffer(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 4)
    assert png.name == "image.png"
    assert jpeg.name == "image.jpg"
    assert webp.name == "image.webp"
    assert png.getvalue().startswith(b"\x89PNG")


def test_wrap_run_error_prediction_vs_network():
    class _Pred(Exception):
        prediction = object()

    class _Limited(Exception):
        status = 429

    user = "safe"
    gen = _wrap_run_error(_Pred("bad"), user_message=user)
    assert isinstance(gen, ProviderGenerationError)
    assert gen.retryable is False
    assert gen.user_message == user
    limited = _wrap_run_error(_Limited("slow"), user_message=user)
    assert isinstance(limited, ProviderRateLimitError)
    net = _wrap_run_error(RuntimeError("boom"), user_message=user)
    assert isinstance(net, ProviderUnavailableError)
    assert net.retryable is True


def test_kind_seedream_faceswap_grok_other():
    assert _replicate_kind(MODELS["seedream"]["id"]) == "seedream"
    assert _replicate_kind(MODELS["faceswap"]["id"]) == "faceswap"
    assert _replicate_kind("xai/grok-imagine-image-quality") == "grok"
    assert _replicate_kind("xai/grok-imagine-image") == "grok"
    assert _replicate_kind("some/other-model") == "other"


def test_kind_nano_banana_pinned_and_unpinned():
    assert _replicate_kind(MODELS["nano_banana"]["replicate_id"]) == "nano_banana"
    assert _replicate_kind("google/nano-banana-2:deadbeef") == "nano_banana"
    assert _replicate_kind("google/nano-banana-pro:abc") == "nano_banana"
    assert _replicate_kind("google/nano-banana:abc") == "nano_banana"
    assert _replicate_kind("xai/grok-imagine-image") != "nano_banana"


def test_nano_banana_replicate_input_t2i_i2i():
    req = GenerationRequest(
        provider="replicate",
        model_id=MODELS["nano_banana"]["replicate_id"],
        media_type=MediaType.IMAGE,
        prompt="hola",
        aspect_ratio="1:1",
        params={"resolution": "1K", "output_format": "png"},
    )
    t2i = _nano_banana_replicate_input(req, None)
    assert t2i["prompt"] == "hola"
    assert t2i["image_input"] == []
    assert t2i["aspect_ratio"] == "1:1"
    assert t2i["resolution"] == "1K"
    assert "image" not in t2i

    i2i = _nano_banana_replicate_input(req, b"\xff\xd8\xff")
    assert len(i2i["image_input"]) == 1
    assert i2i["image_input"][0].startswith("data:")
    assert "aspect_ratio" not in i2i


def test_kind_grok_video():
    assert _replicate_kind("xai/grok-imagine-video") == "grok_video"
    assert _replicate_kind("xai/grok-imagine-video-1.5") == "grok_video"
    assert _replicate_kind("xai/grok-imagine-image") == "grok"


def test_grok_video_input_t2v():
    req = GenerationRequest(
        provider="replicate",
        model_id="xai/grok-imagine-video",
        media_type=MediaType.VIDEO,
        prompt="a cat walks",
        aspect_ratio="16:9",
        video_duration=5,
        video_resolution="480p",
    )
    inp, extra = _grok_video_replicate_input(req, None)
    assert inp["prompt"] == "a cat walks"
    assert inp["duration"] == 5
    assert inp["resolution"] == "480p"
    assert inp["aspect_ratio"] == "16:9"
    assert "image" not in inp
    assert extra == {}


def test_grok_video_15_requires_image():
    req = GenerationRequest(
        provider="replicate",
        model_id="xai/grok-imagine-video-1.5",
        media_type=MediaType.VIDEO,
        prompt="animate",
    )
    try:
        _grok_video_replicate_input(req, None)
        raise AssertionError("expected ProviderInputError")
    except ProviderInputError:
        pass

    inp, extra = _grok_video_replicate_input(req, b"\xff\xd8\xff")
    assert "image" in inp
    assert extra.get("file_encoding_strategy") == "base64"
