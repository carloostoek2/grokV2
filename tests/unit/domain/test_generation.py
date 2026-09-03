"""Unit tests for grokbot.domain.generation (D3)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from grokbot.domain.generation import (
    GenerationRequest,
    GenerationResult,
    KieTaskRef,
    LocalPathRef,
    MediaType,
    TelegramFileRef,
    UrlRef,
)


def test_media_type_values():
    assert MediaType.IMAGE.value == "image"
    assert MediaType.VIDEO.value == "video"


def test_generation_request_defaults_and_frozen():
    req = GenerationRequest(
        provider="xai",
        model_id="grok-imagine-image-quality",
        media_type=MediaType.IMAGE,
        prompt="hola",
    )
    assert req.provider == "xai"
    assert req.model_id == "grok-imagine-image-quality"
    assert req.media_type is MediaType.IMAGE
    assert req.prompt == "hola"
    assert req.source is None
    assert req.aspect_ratio is None
    assert req.video_duration is None
    assert req.video_resolution is None
    assert req.video_mode is None
    with pytest.raises(FrozenInstanceError):
        req.prompt = "nope"


def test_generation_request_with_source_and_video_fields():
    req = GenerationRequest(
        provider="replicate",
        model_id="xai/grok-imagine-image",
        media_type=MediaType.VIDEO,
        prompt="p",
        source=TelegramFileRef("AgAA..."),
        aspect_ratio="16:9",
        video_duration=5,
        video_resolution="720p",
        video_mode="normal",
    )
    assert req.source == TelegramFileRef("AgAA...")
    assert req.aspect_ratio == "16:9"
    assert req.video_duration == 5
    assert req.video_resolution == "720p"
    assert req.video_mode == "normal"


def test_image_source_ref_kinds():
    assert TelegramFileRef("f").file_id == "f"
    assert LocalPathRef("/tmp/a.jpg").path == "/tmp/a.jpg"
    assert UrlRef("https://x.ai/1").url == "https://x.ai/1"
    assert KieTaskRef("task-1").task_id == "task-1"
    assert KieTaskRef("task-1", index=2).index == 2


def test_generation_result_with_data_meta_and_structural_equality():
    r1 = GenerationResult(
        provider="kie",
        model_id="grok-imagine-image-2-0/text-to-image",
        media_type=MediaType.IMAGE,
        data=b"bytes",
        meta={"kie_task_id": "x"},
    )
    r2 = GenerationResult(
        provider="kie",
        model_id="grok-imagine-image-2-0/text-to-image",
        media_type=MediaType.IMAGE,
        data=b"bytes",
        meta={"kie_task_id": "x"},
    )
    assert r1 == r2
    assert r1.data == b"bytes"
    assert r1.meta == {"kie_task_id": "x"}
    assert r1.mime_type is None
