"""Unit tests for Kie provider pure helpers (no network)."""

from __future__ import annotations

from grokbot.providers.kie_provider import (
    KIE_BASE_VIDEO_ASPECT_RATIOS,
    KIE_15_VIDEO_ASPECT_RATIOS,
    KIE_VIDEO_15_I2V,
    KIE_VIDEO_I2V,
    KIE_VIDEO_T2V,
    _is_allowed_kie_asset_url,
    _kie_aspect_ratios_for_model,
    _kie_map_duration,
    _kie_poll_error_is_transient,
    _kie_video_slug,
    _sanitize_kie_fail_log,
)


def test_video_slug_base_and_15():
    assert _kie_video_slug("grok-imagine-video", image_to_video=False) == KIE_VIDEO_T2V
    assert _kie_video_slug("grok-imagine-video", image_to_video=True) == KIE_VIDEO_I2V
    # 1.5 has no t2v slug -> falls back to base t2v.
    assert _kie_video_slug("grok-imagine-video-1.5", image_to_video=False) == KIE_VIDEO_T2V
    assert _kie_video_slug("grok-imagine-video-1.5", image_to_video=True) == KIE_VIDEO_15_I2V


def test_aspect_ratios_for_model():
    assert _kie_aspect_ratios_for_model("grok-imagine-video") == KIE_BASE_VIDEO_ASPECT_RATIOS
    assert _kie_aspect_ratios_for_model("grok-imagine-video-1.5") == KIE_15_VIDEO_ASPECT_RATIOS
    # Unknown model -> base constraints.
    assert _kie_aspect_ratios_for_model("unknown") == KIE_BASE_VIDEO_ASPECT_RATIOS


def test_map_duration_clamp_6_to_30():
    assert _kie_map_duration(3) == 6
    assert _kie_map_duration(5) == 6
    assert _kie_map_duration(6) == 6
    assert _kie_map_duration(10) == 10
    assert _kie_map_duration(30) == 30
    assert _kie_map_duration(60) == 30


def test_sanitize_fail_log():
    assert _sanitize_kie_fail_log(None) == ""
    assert _sanitize_kie_fail_log("") == ""
    assert _sanitize_kie_fail_log("line1\nline2") == "line1 line2"
    long_msg = "x" * 200
    sanitized = _sanitize_kie_fail_log(long_msg)
    assert len(sanitized) <= 81
    assert sanitized.endswith("…")


def test_allowed_kie_asset_url_exact_hosts():
    for url in (
        "https://kieai.redpandaai.co/file.png",
        "https://static.aiquickdraw.com/file.png",
        "https://tempfile.redpandaai.co/file.png",
        "https://tempfile.aiquickdraw.com/file.png",
        "https://file.aiquickdraw.com/file.png",
    ):
        assert _is_allowed_kie_asset_url(url), url


def test_allowed_kie_asset_url_subdomains():
    assert _is_allowed_kie_asset_url("https://cdn.aiquickdraw.com/x.png")
    assert _is_allowed_kie_asset_url("https://deep.redpandaai.co/x.png")


def test_blocked_kie_asset_urls():
    for url in (
        "http://kieai.redpandaai.co/file.png",  # http scheme
        "https://evil.com/file.png",
        "https://example.com/x.png",
        "https://redpandaai.co.evil.com/x.png",
        "https://notredpandaai.co/x.png",
        "https://aiquickdraw.com/x.png",  # bare apex not in exact set/suffix
    ):
        assert not _is_allowed_kie_asset_url(url), url


def test_poll_error_is_transient():
    assert _kie_poll_error_is_transient(404)
    assert _kie_poll_error_is_transient(422)
    assert _kie_poll_error_is_transient(429)
    assert _kie_poll_error_is_transient(500)
    assert _kie_poll_error_is_transient(200, api_code=422)
    assert _kie_poll_error_is_transient(200, api_code=429)
    assert not _kie_poll_error_is_transient(200)
    assert not _kie_poll_error_is_transient(200, api_code=400)
    assert not _kie_poll_error_is_transient(400)
