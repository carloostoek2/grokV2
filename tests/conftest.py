"""Shared pytest fixtures for the grokbot unit suite.

The autouse fixture below keeps every test deterministic with respect to the
environment: it scrubs every settings env var and clears the ``get_settings``
cache before and after each test, so tests never leak state into each other
and no ``.env`` file on disk can influence results.
"""

from __future__ import annotations

import pytest

SETTINGS_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "REPLICATE_API_TOKEN",
    "XAI_API_KEY",
    "KIE_API_KEY",
    "COMFYUI_HOST",
    "COMFYUI_PORT",
    "ALLOWED_TELEGRAM_IDS",
    "VARIABLES_ADMIN_IDS",
    "REFINE_CONFIRM_TIMEOUT",
)


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch):
    from grokbot.settings import get_settings

    for var in SETTINGS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
