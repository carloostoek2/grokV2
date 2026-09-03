"""Persisted user configuration value objects.

Models ONLY the portion of a session record that grok persists to
``sessions.json`` (mirror of ``_default_session_record``). Ephemeral FSM state
(pending prompts, collected edit file ids, ...) is owned by the
application/telegram layers (items 4-5), and ``video_hourly_timestamps`` (usage
quota data) belongs to the session repository (item 3) — tolerated but ignored
here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grokbot.domain.catalog import (
    DEFAULT_GROK_IMAGINE_PROVIDER,
    DEFAULT_GROK_IMAGINE_VARIANT,
    DEFAULT_MODEL,
    VALID_GROK_IMAGINE_PROVIDERS,
    VALID_GROK_IMAGINE_VARIANTS,
    VALID_MODELS,
)

# --- Video generation constants (grok sessions.py:16-39). ---
DEFAULT_VIDEO_DURATION = 5
DEFAULT_VIDEO_ASPECT_RATIO = "16:9"
DEFAULT_VIDEO_RESOLUTION = "720p"
DEFAULT_VIDEO_MODEL = "grok-imagine-video"
DEFAULT_VIDEO_MODE = "normal"
VALID_VIDEO_DURATIONS = (3, 5, 10, 15)
VALID_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3")
VALID_VIDEO_RESOLUTIONS = ("480p", "720p")
VALID_VIDEO_MODELS = ("grok-imagine-video", "grok-imagine-video-1.5")
VALID_VIDEO_MODES = ("fun", "normal", "spicy")

# --- ComfyUI config constants. ---
DEFAULT_COMFYUI_MODEL = "krea2"
DEFAULT_COMFYUI_LORA = "none"
DEFAULT_COMFYUI_REFINE = "1"  # "1" = final refine ON (JSON parity: str "0"/"1")
VALID_COMFYUI_MODELS = (
    "qwen",
    "qwen_aio",
    "krea2",
    "krea2_raw",
    "krea2_moody",
    "wan_i2v",
    "minimax_i2v",
)
VALID_COMFYUI_LORAS = (
    "none",
    "lightning",
    "krea_nsfw",
    "krea_snapshot",
    "krea_both",
    "krea_reddit",
    "lightx2v",
    "dr34ml4y",
    "multiangle",
    "multiangle_batch",
    "multipose_batch",
    "krea_edit",
    "krea_edit_nsfw",
    "krea_edit_snapshot",
    "krea_edit_both",
    "krea_snofs",
    "qwen_snofs",
)

IDLE_STATE = "IDLE"


def _prefix_group(rec: Mapping, prefix: str, *, skip: frozenset[str] = frozenset()) -> dict:
    """Extract the ``prefix*`` keys of a flat record into a plain-key dict.

    ``video_duration`` -> ``{"duration": ...}``; ``video_hourly_timestamps`` is
    skipped (repo-owned usage data, A5)."""
    return {
        key[len(prefix):]: value
        for key, value in rec.items()
        if key.startswith(prefix) and key not in skip
    }


@dataclass(frozen=True)
class VideoConfig:
    """Persisted per-user video generation defaults (xAI constraints)."""

    duration: int = DEFAULT_VIDEO_DURATION
    aspect_ratio: str = DEFAULT_VIDEO_ASPECT_RATIO
    resolution: str = DEFAULT_VIDEO_RESOLUTION
    model: str = DEFAULT_VIDEO_MODEL
    mode: str = DEFAULT_VIDEO_MODE

    @classmethod
    def from_record(cls, rec: Mapping) -> "VideoConfig":
        """Build from a mapping keyed by plain field names (see :func:`_prefix_group`).

        Tolerant coercion mirrors grok ``sessions.get_video_config``: duration may
        arrive as a str, and any value outside the valid set falls back to default.
        """
        try:
            duration = int(rec.get("duration", DEFAULT_VIDEO_DURATION))
        except (TypeError, ValueError):
            duration = DEFAULT_VIDEO_DURATION
        if duration not in VALID_VIDEO_DURATIONS:
            duration = DEFAULT_VIDEO_DURATION

        aspect_ratio = rec.get("aspect_ratio", DEFAULT_VIDEO_ASPECT_RATIO)
        if aspect_ratio not in VALID_VIDEO_ASPECT_RATIOS:
            aspect_ratio = DEFAULT_VIDEO_ASPECT_RATIO

        resolution = rec.get("resolution", DEFAULT_VIDEO_RESOLUTION)
        if resolution not in VALID_VIDEO_RESOLUTIONS:
            resolution = DEFAULT_VIDEO_RESOLUTION

        model = rec.get("model", DEFAULT_VIDEO_MODEL)
        if model not in VALID_VIDEO_MODELS:
            model = DEFAULT_VIDEO_MODEL

        mode = rec.get("mode", DEFAULT_VIDEO_MODE)
        if mode not in VALID_VIDEO_MODES:
            mode = DEFAULT_VIDEO_MODE

        return cls(duration=duration, aspect_ratio=aspect_ratio, resolution=resolution, model=model, mode=mode)

    def to_record(self) -> dict:
        return {
            "duration": self.duration,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "model": self.model,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class ComfyUIConfig:
    """Persisted per-user ComfyUI model/lora/refine configuration."""

    model: str = DEFAULT_COMFYUI_MODEL
    lora: str = DEFAULT_COMFYUI_LORA
    refine: str = DEFAULT_COMFYUI_REFINE  # "0"/"1" as str (JSON parity)

    @classmethod
    def from_record(cls, rec: Mapping) -> "ComfyUIConfig":
        """Build from a plain-key mapping; obsolete/invalid values fall back.

        Mirrors grok ``sessions.get_comfyui_config`` (e.g. retired ``realvisxl``
        model -> default ``krea2``).
        """
        model = rec.get("model", DEFAULT_COMFYUI_MODEL)
        if model not in VALID_COMFYUI_MODELS:
            model = DEFAULT_COMFYUI_MODEL

        lora = rec.get("lora", DEFAULT_COMFYUI_LORA)
        if lora not in VALID_COMFYUI_LORAS:
            lora = DEFAULT_COMFYUI_LORA

        refine = rec.get("refine", DEFAULT_COMFYUI_REFINE)
        if refine not in ("0", "1"):
            refine = DEFAULT_COMFYUI_REFINE

        return cls(model=model, lora=lora, refine=refine)

    def to_record(self) -> dict:
        return {"model": self.model, "lora": self.lora, "refine": self.refine}

    @property
    def refine_enabled(self) -> bool:
        return self.refine == "1"


@dataclass(frozen=True)
class UserConfig:
    """Persisted per-user session configuration (top model + grok imagine + video + comfyui).

    Mirrors the non-ephemeral fields of ``sessions._default_session_record``.
    """

    model: str = DEFAULT_MODEL
    grok_imagine_provider: str = DEFAULT_GROK_IMAGINE_PROVIDER
    grok_imagine_variant: str = DEFAULT_GROK_IMAGINE_VARIANT
    video: VideoConfig = VideoConfig()
    comfyui: ComfyUIConfig = ComfyUIConfig()
    source_path: str | None = None
    integrate_ref_path: str | None = None
    state: str = IDLE_STATE

    @classmethod
    def defaults(cls) -> "UserConfig":
        """A UserConfig equal to grok's ``_default_session_record`` (minus repo-owned data)."""
        return cls()

    @classmethod
    def from_record(cls, rec: Mapping) -> "UserConfig":
        """Build from a session JSON record, tolerating legacy/missing keys (R4/A5)."""
        model = rec.get("model", DEFAULT_MODEL)
        if model not in VALID_MODELS:
            model = DEFAULT_MODEL

        provider = rec.get("grok_imagine_provider")
        if provider is None and "grok_provider" in rec:
            provider = rec["grok_provider"]  # legacy single-provider alias
        if provider not in VALID_GROK_IMAGINE_PROVIDERS:
            provider = DEFAULT_GROK_IMAGINE_PROVIDER

        variant = rec.get("grok_imagine_variant", DEFAULT_GROK_IMAGINE_VARIANT)
        if variant not in VALID_GROK_IMAGINE_VARIANTS:
            variant = DEFAULT_GROK_IMAGINE_VARIANT

        video = VideoConfig.from_record(
            _prefix_group(rec, "video_", skip=frozenset({"video_hourly_timestamps"}))
        )
        comfyui = ComfyUIConfig.from_record(_prefix_group(rec, "comfyui_"))

        source_path = rec.get("source_path")
        if not isinstance(source_path, (str, type(None))):
            source_path = None
        integrate_ref_path = rec.get("integrate_ref_path")
        if not isinstance(integrate_ref_path, (str, type(None))):
            integrate_ref_path = None
        state = rec.get("state")
        if not isinstance(state, str):
            state = IDLE_STATE

        return cls(
            model=model,
            grok_imagine_provider=provider,
            grok_imagine_variant=variant,
            video=video,
            comfyui=comfyui,
            source_path=source_path,
            integrate_ref_path=integrate_ref_path,
            state=state,
        )

    def to_record(self) -> dict:
        """Flat session record (no ``grok_provider`` legacy key, no ``video_hourly_timestamps``)."""
        return {
            "source_path": self.source_path,
            "integrate_ref_path": self.integrate_ref_path,
            "state": self.state,
            "model": self.model,
            "grok_imagine_provider": self.grok_imagine_provider,
            "grok_imagine_variant": self.grok_imagine_variant,
            "video_duration": self.video.duration,
            "video_aspect_ratio": self.video.aspect_ratio,
            "video_resolution": self.video.resolution,
            "video_model": self.video.model,
            "video_mode": self.video.mode,
            "comfyui_model": self.comfyui.model,
            "comfyui_lora": self.comfyui.lora,
            "comfyui_refine": self.comfyui.refine,
        }
