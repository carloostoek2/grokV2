"""Unit tests for grokbot.domain.user_config (D2/R4/A5)."""

from __future__ import annotations

from grokbot.domain.user_config import ComfyUIConfig, UserConfig, VideoConfig

# Session record anchored to grok HEAD 81832a5 (sessions.json, user 6181290784).
REAL_GROK_SESSION_REC = {
    "source_path": "/home/ubuntu/repos/grok/sources/6181290784.jpg",
    "integrate_ref_path": None,
    "state": "IDLE",
    "model": "grok",
    "grok_imagine_provider": "kie",
    "grok_imagine_variant": "standard",
    "video_duration": 5,
    "video_aspect_ratio": "16:9",
    "video_resolution": "480p",
    "video_model": "grok-imagine-video",
    "video_mode": "normal",
    "video_hourly_timestamps": [],
    "comfyui_model": "krea2_moody",
    "comfyui_lora": "none",
    "comfyui_refine": "0",
}


def test_defaults_mirror_default_session_record():
    uc = UserConfig.defaults()
    assert uc.model == "grok"
    assert uc.grok_imagine_provider == "kie"
    assert uc.grok_imagine_variant == "quality"
    assert uc.video.duration == 5
    assert uc.video.aspect_ratio == "16:9"
    assert uc.video.resolution == "720p"
    assert uc.video.model == "grok-imagine-video"
    assert uc.video.mode == "normal"
    assert uc.comfyui.model == "krea2"
    assert uc.comfyui.lora == "none"
    assert uc.comfyui.refine == "1"
    assert uc.source_path is None
    assert uc.integrate_ref_path is None
    assert uc.state == "IDLE"


def test_from_record_empty_or_minimal_is_defaults():
    assert UserConfig.from_record({}) == UserConfig.defaults()
    assert UserConfig.from_record({"model": "grok"}) == UserConfig.defaults()


def test_legacy_grok_provider_alias():
    uc = UserConfig.from_record({"grok_provider": "xai"})
    assert uc.grok_imagine_provider == "xai"
    assert uc.grok_imagine_variant == "quality"  # default when alias only
    rec = uc.to_record()
    assert "grok_provider" not in rec
    assert rec["grok_imagine_provider"] == "xai"


def test_video_config_coercion_and_valid_sets():
    assert UserConfig.from_record({"video_duration": "10"}).video.duration == 10
    assert UserConfig.from_record({"video_duration": "99"}).video.duration == 5
    assert UserConfig.from_record({"video_model": "grok-imagine-video-99"}).video.model == "grok-imagine-video"
    assert UserConfig.from_record({"video_resolution": "1080p"}).video.resolution == "720p"
    assert UserConfig.from_record({"video_aspect_ratio": "3:2"}).video.aspect_ratio == "3:2"


def test_comfyui_refine_preserved_as_str():
    uc0 = UserConfig.from_record({"comfyui_refine": "0"})
    assert uc0.comfyui.refine == "0"
    assert uc0.comfyui.refine_enabled is False
    uc1 = UserConfig.from_record({"comfyui_refine": "1"})
    assert uc1.comfyui.refine == "1"
    assert uc1.comfyui.refine_enabled is True
    ucb = UserConfig.from_record({"comfyui_refine": "banana"})
    assert ucb.comfyui.refine == "1"


def test_obsolete_comfyui_model_falls_back():
    uc = UserConfig.from_record({"comfyui_model": "realvisxl"})
    assert uc.comfyui.model == "krea2"


def test_unknown_keys_ignored():
    uc = UserConfig.from_record(
        {
            "model": "grok",
            "source_path": "/tmp/x.jpg",
            "video_hourly_timestamps": [1234.0],
            "pending_prompt": "not-a-domain-field",
        }
    )
    assert uc.model == "grok"
    assert uc.source_path == "/tmp/x.jpg"
    assert "video_hourly_timestamps" not in uc.to_record()
    assert "pending_prompt" not in uc.to_record()


def test_round_trip_canonical_record():
    rec = dict(REAL_GROK_SESSION_REC)
    del rec["video_hourly_timestamps"]
    assert UserConfig.from_record(rec).to_record() == rec


def test_real_grok_session_record_loads_without_loss():
    uc = UserConfig.from_record(REAL_GROK_SESSION_REC)
    assert uc.video.duration == 5
    assert uc.video.resolution == "480p"
    assert uc.video.model == "grok-imagine-video"
    assert uc.video.mode == "normal"
    assert uc.comfyui.model == "krea2_moody"
    assert uc.comfyui.refine == "0"
    assert uc.comfyui.refine_enabled is False
    assert uc.grok_imagine_provider == "kie"
    assert uc.grok_imagine_variant == "standard"
    assert uc.source_path == "/home/ubuntu/repos/grok/sources/6181290784.jpg"
    assert uc.state == "IDLE"


def test_video_config_and_comfyui_config_standalone():
    vc = VideoConfig.from_record({"duration": "15", "aspect_ratio": "3:2", "resolution": "480p",
                                  "model": "grok-imagine-video-1.5", "mode": "fun"})
    assert vc.to_record() == {"duration": 15, "aspect_ratio": "3:2", "resolution": "480p",
                              "model": "grok-imagine-video-1.5", "mode": "fun"}
    cc = ComfyUIConfig.from_record({"model": "wan_i2v", "lora": "lightx2v", "refine": "1"})
    assert cc.to_record() == {"model": "wan_i2v", "lora": "lightx2v", "refine": "1"}
    assert cc.refine_enabled is True
