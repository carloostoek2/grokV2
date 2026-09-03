"""Unit tests for Replicate provider pure helpers (no replicate import)."""

from __future__ import annotations

from dataclasses import dataclass

from grokbot.domain.catalog import MODELS
from grokbot.providers.replicate_provider import (
    _normalize_output_urls,
    _replicate_kind,
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


def test_kind_seedream_faceswap_grok_other():
    assert _replicate_kind(MODELS["seedream"]["id"]) == "seedream"
    assert _replicate_kind(MODELS["faceswap"]["id"]) == "faceswap"
    assert _replicate_kind("xai/grok-imagine-image-quality") == "grok"
    assert _replicate_kind("xai/grok-imagine-image") == "grok"
    assert _replicate_kind("some/other-model") == "other"
