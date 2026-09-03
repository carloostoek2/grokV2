"""Unit tests for grokbot.settings (R1/R3/R5/R8/D6 from the impact report)."""

from __future__ import annotations

from pathlib import Path

import pydantic
import pytest

from grokbot.settings import Settings, get_settings

REQUIRED = {
    "TELEGRAM_BOT_TOKEN": "tok",
    "REPLICATE_API_TOKEN": "rep",
    "XAI_API_KEY": "xai",
}


def _set_required(monkeypatch) -> None:
    for var, val in REQUIRED.items():
        monkeypatch.setenv(var, val)


def test_missing_required_fails_fast(monkeypatch):
    """R3: with the 3 required vars absent, boot raises ValidationError."""
    # conftest scrubbed every env var already; required vars are all missing.
    with pytest.raises(pydantic.ValidationError) as exc:
        Settings()
    msg = str(exc.value)
    for field in ("telegram_bot_token", "replicate_api_token", "xai_api_key"):
        assert field in msg


def test_required_present_and_defaults(monkeypatch):
    _set_required(monkeypatch)
    settings = get_settings()
    assert settings.telegram_bot_token == "tok"
    assert settings.replicate_api_token == "rep"
    assert settings.xai_api_key == "xai"
    # Optional defaults exactly as in the surface table.
    assert settings.kie_api_key == ""
    assert settings.comfyui_host == ""
    assert settings.comfyui_port == 22
    assert isinstance(settings.comfyui_port, int)
    assert settings.refine_confirm_timeout == 300
    assert settings.allowed_telegram_ids is None
    assert settings.variables_admin_ids is None


def test_allowlist_csv(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "1, 2,3")
    settings = Settings()
    assert settings.allowed_telegram_ids == {1, 2, 3}


def test_allowlist_blank_is_none(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "")
    assert Settings().allowed_telegram_ids is None
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "   ")
    assert Settings().allowed_telegram_ids is None


def test_allowlist_invalid_raises(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "abc")
    with pytest.raises(pydantic.ValidationError):
        Settings()
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "1,abc")
    with pytest.raises(pydantic.ValidationError):
        Settings()


def test_admin_ids_independent(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("VARIABLES_ADMIN_IDS", "10, 20")
    settings = Settings()
    assert settings.variables_admin_ids == {10, 20}
    assert settings.allowed_telegram_ids is None


def test_comfyui_port_int(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("COMFYUI_PORT", "19956")
    monkeypatch.setenv("REFINE_CONFIRM_TIMEOUT", "10")
    settings = Settings()
    assert settings.comfyui_port == 19956
    assert isinstance(settings.comfyui_port, int)
    assert settings.refine_confirm_timeout == 10


def test_data_dir_default_and_derived_paths(monkeypatch):
    """D1: GROK_DATA_DIR absent -> Path('data') and 4 derived repo paths."""
    _set_required(monkeypatch)
    settings = Settings()
    assert settings.data_dir == Path("data")
    assert settings.sessions_file == Path("data") / "sessions.json"
    assert settings.variables_file == Path("data") / "variables_lists.json"
    assert settings.generation_refs_file == Path("data") / "generation_refs.json"
    assert settings.packages_dir == Path("data") / "variables_packages"


def test_data_dir_env_override(monkeypatch):
    """D1: GROK_DATA_DIR overrides the default data_dir and derived paths."""
    _set_required(monkeypatch)
    monkeypatch.setenv("GROK_DATA_DIR", "/tmp/grokdata")
    settings = Settings()
    assert settings.data_dir == Path("/tmp/grokdata")
    assert settings.sessions_file == Path("/tmp/grokdata") / "sessions.json"
    assert settings.packages_dir == Path("/tmp/grokdata") / "variables_packages"


def test_import_does_not_instantiate(monkeypatch):
    """Importing grokbot.settings never builds Settings; get_settings() fails fast."""
    import grokbot.settings as settings_mod  # noqa: F401  (module already cached by imports)

    assert hasattr(settings_mod, "Settings")
    with pytest.raises(pydantic.ValidationError):
        get_settings()


def test_get_settings_cached_and_cache_clear(monkeypatch):
    _set_required(monkeypatch)
    first = get_settings()
    second = get_settings()
    assert first is second

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok2")
    get_settings.cache_clear()
    third = get_settings()
    assert third is not first
    assert third.telegram_bot_token == "tok2"
