"""Centralized fail-fast environment settings for the grokbot bot.

Loaded once via :func:`get_settings` (LRU-cached). Required variables missing at
boot raise ``pydantic.ValidationError``; ID-list semantics mirror the legacy
helpers in grok/bot.py: blank -> None (open), CSV -> set[int], non-numeric item
-> ValidationError (never silently lock out via empty set()).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")  # no dotenv (entrypoint owns .env loading)

    # Required credentials.
    telegram_bot_token: str
    replicate_api_token: str
    xai_api_key: str

    # Optional credentials (empty = provider not available).
    kie_api_key: str = ""

    # ComfyUI (Vast): the bot reaches the box over SSH and talks to ComfyUI's
    # native HTTP/WS API through a local-forward tunnel. ``comfyui_host`` /
    # ``comfyui_port`` are the SSH endpoint of the box (empty host = disabled).
    comfyui_host: str = ""
    comfyui_port: int = 22
    # ComfyUI HTTP port INSIDE the box (the tunnel forwards to 127.0.0.1:<this>)
    # and the LOCAL port the tunnel binds (0 = pick a free ephemeral port).
    comfyui_remote_port: int = 18188
    comfyui_tunnel_local_port: int = 0
    # Workflow graph source: remote (SSH-cat API JSON on Vast) or embed (repo templates/).
    # remote is the product default; embed is for offline/tests. Missing remote file
    # falls back to embed with a warning.
    comfyui_workflow_source: str = "remote"
    comfyui_workflows_dir: str = "/workspace/ComfyUI/user/default/api_workflows"
    comfyui_workflow_cache_ttl: float = 45.0

    # Telegram user ID allowlists. blank/whitespace -> None (open bot); None too.
    allowed_telegram_ids: Annotated[set[int] | None, NoDecode] = None
    variables_admin_ids: Annotated[set[int] | None, NoDecode] = None

    # Seconds to await the ComfyUI refine confirmation.
    refine_confirm_timeout: int = 300

    # Runtime data directory for repositories (sessions/variables/generation_refs + packages).
    # main.py (item 6) reads the derived paths below to build repos; repos never default to cwd.
    data_dir: Path = Field(default=Path("data"), alias="GROK_DATA_DIR")

    @property
    def sessions_file(self) -> Path:
        return self.data_dir / "sessions.json"

    @property
    def variables_file(self) -> Path:
        return self.data_dir / "variables_lists.json"

    @property
    def generation_refs_file(self) -> Path:
        return self.data_dir / "generation_refs.json"

    @property
    def packages_dir(self) -> Path:
        return self.data_dir / "variables_packages"

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def integrate_refs_dir(self) -> Path:
        return self.data_dir / "integrate_refs"

    @field_validator("allowed_telegram_ids", "variables_admin_ids", mode="before")
    @classmethod
    def _parse_id_set(cls, v: object) -> object:
        """Parse an ID-list env value: blank -> None, CSV -> set[int].

        Non-numeric items are NOT filtered out: ``int()`` raises ``ValueError``,
        which pydantic surfaces as ``ValidationError`` (fail-fast), so a typo can
        never lock the owner out by collapsing to an empty set.
        """
        if v is None or isinstance(v, set):
            return v
        if not isinstance(v, str):
            return v
        s = v.strip()
        if not s:
            return None
        return {int(part.strip()) for part in s.split(",") if part.strip()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings`, validating env on first call."""
    return Settings()
