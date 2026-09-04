"""JSON session repository — per-user config.

Behavior parity with grok ``sessions.py`` (HEAD 81832a5). ``get_config``
persists the default record for a brand-new user; ``save_config`` merges the
``UserConfig`` into the raw record WITHOUT rebuilding the document, so extra
keys the domain does not model are preserved (R4). R9: la cuota horaria de
video se eliminó — el key legacy ``video_hourly_timestamps`` se purga en
``_ensure_full`` al primer load. Legacy ``grok_provider`` migrates to
``grok_imagine_provider`` only when the canonical key is absent (hardened A4 —
grok overwrites the canonical key).
"""

from __future__ import annotations

import json
from pathlib import Path

from grokbot.domain.catalog import (
    DEFAULT_GROK_IMAGINE_PROVIDER,
    DEFAULT_GROK_IMAGINE_VARIANT,
    DEFAULT_MODEL,
)
from grokbot.domain.user_config import (
    DEFAULT_COMFYUI_LORA,
    DEFAULT_COMFYUI_MODEL,
    DEFAULT_COMFYUI_REFINE,
    DEFAULT_VIDEO_ASPECT_RATIO,
    DEFAULT_VIDEO_DURATION,
    DEFAULT_VIDEO_MODE,
    DEFAULT_VIDEO_MODEL,
    DEFAULT_VIDEO_RESOLUTION,
    UserConfig,
)

from grokbot.repositories.base import write_json_atomic


class JsonSessionRepository:
    """``sessions.json`` backend implementing the :class:`SessionRepository` contract."""

    def __init__(self, path: Path) -> None:
        self._path = path

    # -- public API ---------------------------------------------------------

    def get_config(self, user_id: int) -> UserConfig:
        """Return the full persisted config, creating a default record if needed."""
        uid = str(user_id)
        data = self._load()
        if uid not in data:
            data[uid] = self._default_record()
            self._save(data)
        else:
            rec = data[uid]
            if not isinstance(rec, dict):
                raise ValueError(f"{self._path} record for user {uid!r} must be a JSON object")
            if self._ensure_full(rec):
                self._save(data)
        return UserConfig.from_record(data[uid])

    def save_config(self, user_id: int, config: UserConfig) -> None:
        """Merge ``config`` into the raw record (non-destructive key merge)."""
        uid = str(user_id)
        data = self._load()
        rec = data.get(uid)
        if not isinstance(rec, dict):
            rec = self._default_record()
        rec.update(config.to_record())
        rec.pop("grok_provider", None)
        data[uid] = rec
        self._save(data)

    # -- private helpers ----------------------------------------------------

    @staticmethod
    def _default_record(**overrides) -> dict:
        """Full session record: domain defaults (+ optional overrides)."""
        rec = UserConfig.defaults().to_record()
        rec.update(overrides)
        return rec

    def _load(self) -> dict:
        """Load the top-level dict; missing file -> {}, non-dict top-level -> ValueError."""
        if not self._path.exists():
            return {}
        with open(self._path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{self._path} must contain a JSON object")
        return data

    def _save(self, data: dict) -> None:
        write_json_atomic(self._path, data)

    def _ensure_full(self, rec: dict) -> bool:
        """Fill missing persisted fields. Returns True when changed (caller may save).

        Hardened vs grok ``sessions._ensure_full``: legacy ``grok_provider`` only
        migrates into ``grok_imagine_provider`` when the canonical key is absent
        (A4); when both exist the canonical value wins and the legacy key is dropped.
        """
        changed = False
        if "grok_provider" in rec:
            if "grok_imagine_provider" not in rec:
                rec["grok_imagine_provider"] = rec["grok_provider"]
            if "grok_imagine_variant" not in rec:
                rec["grok_imagine_variant"] = DEFAULT_GROK_IMAGINE_VARIANT
            rec.pop("grok_provider", None)
            changed = True
        if "model" not in rec:
            rec["model"] = DEFAULT_MODEL
            changed = True
        if "grok_imagine_provider" not in rec:
            rec["grok_imagine_provider"] = DEFAULT_GROK_IMAGINE_PROVIDER
            changed = True
        if "grok_imagine_variant" not in rec:
            rec["grok_imagine_variant"] = DEFAULT_GROK_IMAGINE_VARIANT
            changed = True
        if "video_duration" not in rec:
            rec["video_duration"] = DEFAULT_VIDEO_DURATION
            changed = True
        if "video_aspect_ratio" not in rec:
            rec["video_aspect_ratio"] = DEFAULT_VIDEO_ASPECT_RATIO
            changed = True
        if "video_resolution" not in rec:
            rec["video_resolution"] = DEFAULT_VIDEO_RESOLUTION
            changed = True
        # R9: cuota horaria de video eliminada — purga el key legacy de archivos
        # viejos en el primer load (una sola vez; los records nuevos nunca lo traen).
        if "video_hourly_timestamps" in rec:
            rec.pop("video_hourly_timestamps", None)
            changed = True
        if "video_model" not in rec:
            rec["video_model"] = DEFAULT_VIDEO_MODEL
            changed = True
        if "video_mode" not in rec:
            rec["video_mode"] = DEFAULT_VIDEO_MODE
            changed = True
        if "comfyui_model" not in rec:
            rec["comfyui_model"] = DEFAULT_COMFYUI_MODEL
            changed = True
        if "comfyui_lora" not in rec:
            rec["comfyui_lora"] = DEFAULT_COMFYUI_LORA
            changed = True
        if "comfyui_refine" not in rec:
            rec["comfyui_refine"] = DEFAULT_COMFYUI_REFINE
            changed = True
        if "integrate_ref_path" not in rec:
            rec["integrate_ref_path"] = None
            changed = True
        return changed
