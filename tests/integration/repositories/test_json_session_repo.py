"""Integration tests for JsonSessionRepository (item 3, task 3).

Fixtures are 100% anonymized (user 111111111, /tmp/anon paths, dummy values)
with the SAME structural shape as grok's real sessions.json records.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from grokbot.domain.user_config import UserConfig
from grokbot.repositories.json_session_repo import JsonSessionRepository

UID = 111111111
UID2 = 222222222


def canonical_record() -> dict:
    """Real-shape anonymized session record (mirror of grok HEAD 81832a5)."""
    return {
        "source_path": "/tmp/anon/sources/111.jpg",
        "integrate_ref_path": None,
        "state": "IDLE",
        "model": "grok",
        "grok_imagine_provider": "kie",
        "grok_imagine_variant": "quality",
        "video_duration": 5,
        "video_aspect_ratio": "16:9",
        "video_resolution": "720p",
        "video_model": "grok-imagine-video",
        "video_mode": "normal",
        "video_hourly_timestamps": [1710000000.0, 1710003600.0],
        "comfyui_model": "krea2",
        "comfyui_lora": "none",
        "comfyui_refine": "1",
        "_extra_user_key": "keep",
    }


def legacy_record() -> dict:
    """Canonical shape but using the legacy single-provider key."""
    rec = canonical_record()
    del rec["grok_imagine_provider"]
    rec["grok_provider"] = "xai"
    return rec


def coexistence_record() -> dict:
    """Both canonical and legacy present (hardened A4: canonical wins)."""
    rec = canonical_record()
    rec["grok_provider"] = "xai"
    return rec


def write(path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_get_config_new_user_creates_full_record(tmp_path):
    path = tmp_path / "sessions.json"
    repo = JsonSessionRepository(path)
    config = repo.get_config(UID)
    assert config == UserConfig.defaults()
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))[str(UID)]
    assert raw["video_hourly_timestamps"] == []
    assert raw["source_path"] is None
    assert raw["model"] == "grok"
    assert raw["grok_imagine_provider"] == "kie"
    assert raw["state"] == "IDLE"


def test_get_config_loads_real_shape(tmp_path):
    path = tmp_path / "sessions.json"
    write(path, {str(UID): canonical_record()})
    config = JsonSessionRepository(path).get_config(UID)
    assert config.model == "grok"
    assert config.grok_imagine_provider == "kie"
    assert config.grok_imagine_variant == "quality"
    assert config.video.duration == 5
    assert config.video.aspect_ratio == "16:9"
    assert config.video.resolution == "720p"
    assert config.video.model == "grok-imagine-video"
    assert config.comfyui.model == "krea2"
    assert config.comfyui.lora == "none"
    assert config.comfyui.refine == "1"
    assert config.source_path == "/tmp/anon/sources/111.jpg"


def test_save_config_preserves_repo_owned_and_extra_keys(tmp_path):
    path = tmp_path / "sessions.json"
    write(path, {str(UID): canonical_record()})
    repo = JsonSessionRepository(path)
    config = repo.get_config(UID)
    config = replace(config, model="seedream")
    repo.save_config(UID, config)
    raw = json.loads(path.read_text(encoding="utf-8"))[str(UID)]
    assert raw["model"] == "seedream"
    assert raw["_extra_user_key"] == "keep"
    assert raw["video_hourly_timestamps"] == [1710000000.0, 1710003600.0]
    assert raw["comfyui_model"] == "krea2"


def test_legacy_grok_provider_migrates_and_is_dropped_on_save(tmp_path):
    path = tmp_path / "sessions.json"
    write(path, {str(UID): legacy_record()})
    repo = JsonSessionRepository(path)
    config = repo.get_config(UID)
    assert config.grok_imagine_provider == "xai"
    repo.save_config(UID, config)
    raw = json.loads(path.read_text(encoding="utf-8"))[str(UID)]
    assert "grok_provider" not in raw
    assert raw["grok_imagine_provider"] == "xai"


def test_canonical_wins_over_legacy_when_both_present(tmp_path):
    path = tmp_path / "sessions.json"
    write(path, {str(UID): coexistence_record()})
    repo = JsonSessionRepository(path)
    config = repo.get_config(UID)
    assert config.grok_imagine_provider == "kie"  # canonical preserved (A4)
    raw = json.loads(path.read_text(encoding="utf-8"))[str(UID)]
    assert "grok_provider" not in raw
    assert raw["grok_imagine_provider"] == "kie"


def test_record_video_hourly_usage_prunes_old_timestamps(tmp_path):
    path = tmp_path / "sessions.json"
    repo = JsonSessionRepository(path)
    repo.record_video_hourly_usage(UID, now=1000.0)
    repo.record_video_hourly_usage(UID, now=5000.0)
    raw = json.loads(path.read_text(encoding="utf-8"))[str(UID)]
    assert raw["video_hourly_timestamps"] == [5000.0]


def test_count_video_hourly_usage_persists_prune(tmp_path):
    path = tmp_path / "sessions.json"
    rec = canonical_record()
    rec["video_hourly_timestamps"] = [1000.0]
    write(path, {str(UID): rec})
    repo = JsonSessionRepository(path)
    assert repo.count_video_hourly_usage(UID, now=5000.0) == 0
    raw = json.loads(path.read_text(encoding="utf-8"))[str(UID)]
    assert raw["video_hourly_timestamps"] == []


def test_count_global_video_hourly_usage_sums_without_writing(tmp_path):
    path = tmp_path / "sessions.json"
    data = {
        str(UID): {"video_hourly_timestamps": [1000.0]},  # expired at now=5000
        str(UID2): {"video_hourly_timestamps": [4500.0, 4600.0]},  # fresh
    }
    write(path, data)
    repo = JsonSessionRepository(path)
    assert repo.count_global_video_hourly_usage(now=5000.0) == 2
    assert json.loads(path.read_text(encoding="utf-8")) == data


def test_missing_file_get_config_defaults_and_creates(tmp_path):
    path = tmp_path / "sub" / "sessions.json"
    repo = JsonSessionRepository(path)
    config = repo.get_config(UID)
    assert config == UserConfig.defaults()
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))[str(UID)]
    assert raw["source_path"] is None
    assert raw["video_hourly_timestamps"] == []


def test_corrupt_file_propagates_and_non_dict_top_level_raises(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("{invalid", encoding="utf-8")
    repo = JsonSessionRepository(path)
    with pytest.raises(json.JSONDecodeError):
        repo.get_config(UID)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        repo.get_config(UID)


def test_round_trip_parity(tmp_path):
    path = tmp_path / "sessions.json"
    write(path, {str(UID): canonical_record()})
    repo = JsonSessionRepository(path)
    config = repo.get_config(UID)
    repo.save_config(UID, config)
    assert json.loads(path.read_text(encoding="utf-8"))[str(UID)] == canonical_record()


def test_count_video_hourly_usage_missing_user_creates_default(tmp_path):
    """Parity _get_or_create_full: counting a brand-new user persists a default record and returns 0."""
    path = tmp_path / "sessions.json"
    repo = JsonSessionRepository(path)
    assert repo.count_video_hourly_usage(UID, now=5000.0) == 0
    raw = json.loads(path.read_text(encoding="utf-8"))[str(UID)]
    assert raw["model"] == "grok"
    assert raw["video_hourly_timestamps"] == []
    assert raw["source_path"] is None


def test_write_json_atomic_ensure_ascii_default_true(tmp_path):
    path = tmp_path / "nested" / "sessions.json"
    repo = JsonSessionRepository(path)
    rec = canonical_record()
    rec["_extra_note"] = "café"
    data = {str(UID): rec}
    repo._save(data)  # noqa: SLF001  (white-box: dump-flag parity, D9)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "caf\\u00e9" in text  # ensure_ascii default True (D9)
    assert json.loads(text)[str(UID)]["_extra_note"] == "café"
