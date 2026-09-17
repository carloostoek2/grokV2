"""Persisted user configuration value objects.

Models ONLY the portion of a session record that grok persists to
``sessions.json`` (mirror of ``_default_session_record``). Ephemeral FSM state
(pending prompts, collected edit file ids, ...) is owned by the
application/telegram layers (items 4-5). Extra keys a real record may carry
(legacy/repo-owned) are tolerated but ignored here (R4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grokbot.domain.catalog import (
    DEFAULT_GROK_IMAGINE_PROVIDER,
    DEFAULT_GROK_IMAGINE_VARIANT,
    DEFAULT_MODEL,
    DEFAULT_NANO_BANANA_PROVIDER,
    DEFAULT_NANO_BANANA_VARIANT,
    VALID_GROK_IMAGINE_PROVIDERS,
    VALID_GROK_IMAGINE_VARIANTS,
    VALID_MODELS,
    VALID_NANO_BANANA_PROVIDERS,
    VALID_NANO_BANANA_VARIANTS,
    resolve_grok_config,
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
# ComfyUI se maneja por FLUJOS: cada flujo es un workflow API-format de ComfyUI
# con su modelo/LoRA horneados en los nodos (un archivo por flujo en
# ``providers/comfyui/workflows/templates/``). ``ComfyUIConfig.model`` guarda el
# **id de flujo**; al seleccionar un flujo se manda ese workflow, sin elegir
# modelo/LoRA por separado. Para añadir un flujo: 1) registrar aquí su (id,
# nombre) y 2) dejar caer el template ``<id>.json`` (con ``_meta``).
COMFYUI_FLOWS = (
    ("grok_style", "Grok Style"),
    ("donut_face", "Donut Face"),
    ("agil_solo", "Ágil solo"),
    ("agil_nsfw", "Ágil NSFW"),
    ("agil_moody", "Ágil Moody"),
    ("agil_edit_qwen", "Ágil Edit"),
    ("agil_edit_nsfw", "Ágil Edit NSFW"),
    ("wan_i2v", "Wan I2V"),
)
DEFAULT_COMFYUI_MODEL = COMFYUI_FLOWS[0][0]
VALID_COMFYUI_MODELS = tuple(_id for _id, _label in COMFYUI_FLOWS)
COMFYUI_FLOW_LABELS = dict(COMFYUI_FLOWS)
# ``lora``/``refine`` quedaron dormidos tras el slice HTTP/WS: el LoRA forma
# parte del workflow y refine no se ofrece. Se conservan en la config persistida
# por compat (from_record/to_record) pero no participan en la resolución.
DEFAULT_COMFYUI_LORA = "none"
DEFAULT_COMFYUI_REFINE = "1"  # "1" = final refine ON (JSON parity: str "0"/"1")

IDLE_STATE = "IDLE"
AWAITING_SOURCE = "AWAITING_SOURCE"

# --- ComfyUI video-only models (D10, item 4). ---
# Models whose ComfyUI workflow produces MP4 (video), never a still image.
# Mirrors the private provider constant ``providers/comfyui/provider.py`` but
# lives in domain so the application layer can validate media_type (M2) without
# importing a transport provider (layering rule).
COMFY_VIDEO_MODELS = ("wan_i2v", "minimax_i2v")


def is_comfy_video_model(model: str) -> bool:
    """True when ``model`` is a ComfyUI video-only model (produces MP4)."""
    return model in COMFY_VIDEO_MODELS


# --- Kie.ai video aspect-ratio sets (D12, item 4). ---
# Transcribed from grok bot.py:1107-1108 / kie_provider.py:70-71.
KIE_BASE_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "3:2", "2:3")
KIE_15_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3")


def video_provider_for_config(cfg: "UserConfig") -> str | None:
    """Effective video backend for ``cfg``, without touching the registry.

    For ``grok`` / ``grok_video`` the Grok Imagine provider is used as-is
    (xAI, Replicate, or Kie). ``comfyui`` maps to itself; any other model
    returns None (no video backend).
    """
    if cfg.model == "comfyui":
        return "comfyui"
    if cfg.model not in ("grok", "grok_video"):
        return None
    resolved = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
    return resolved["provider"]


def kie_video_aspect_ratios(video_model: str) -> tuple[str, ...]:
    """Allowed Kie.ai aspect ratios for a video model id (grok bot.py:1111-1114)."""
    if video_model == "grok-imagine-video-1.5":
        return KIE_15_VIDEO_ASPECT_RATIOS
    return KIE_BASE_VIDEO_ASPECT_RATIOS


def kie_aspect_ratio_fallback(cfg: "UserConfig", *, video_model: str | None = None) -> str | None:
    """New aspect ratio to show when the persisted one is invalid for Kie video.

    Pure mirror of grok ``_maybe_reset_kie_aspect_ratio`` (bot.py:1117-1136) for
    the application layer: returns None unless the effective video provider is
    Kie and the current aspect ratio is outside the model's allowed set. The
    fallback is the domain default when allowed, else the first allowed ratio.
    """
    if video_provider_for_config(cfg) != "kie":
        return None
    model = video_model or cfg.video.model
    allowed = kie_video_aspect_ratios(model)
    if cfg.video.aspect_ratio in allowed:
        return None
    return DEFAULT_VIDEO_ASPECT_RATIO if DEFAULT_VIDEO_ASPECT_RATIO in allowed else allowed[0]


def _prefix_group(rec: Mapping, prefix: str) -> dict:
    """Extract the ``prefix*`` keys of a flat record into a plain-key dict.

    ``video_duration`` -> ``{"duration": ...}``. Keys without a matching field
    in the target config (legacy/unknown) are simply never read downstream."""
    return {
        key[len(prefix):]: value
        for key, value in rec.items()
        if key.startswith(prefix)
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
    """Persisted per-user ComfyUI **flow** selection.

    ``model`` es el id de flujo (p. ej. ``"grok_style"``); el workflow de ese
    flujo trae su modelo/LoRA horneados. ``lora``/``refine`` quedan dormidos
    (persistidos por compat; no participan en la resolución).
    """

    model: str = DEFAULT_COMFYUI_MODEL
    lora: str = DEFAULT_COMFYUI_LORA
    refine: str = DEFAULT_COMFYUI_REFINE  # "0"/"1" as str (JSON parity)

    @classmethod
    def from_record(cls, rec: Mapping) -> "ComfyUIConfig":
        """Build from a plain-key mapping; obsolete/invalid values fall back.

        Cualquier valor legacy (retired ``realvisxl``, ``krea2``/``qwen``/
        ``minimax_i2v``/... del catálogo anterior) no está en ``VALID_COMFYUI_MODELS``
        → cae al flujo default. ``lora`` se normaliza a ``"none"`` (dormido).
        """
        model = rec.get("model", DEFAULT_COMFYUI_MODEL)
        if model not in VALID_COMFYUI_MODELS:
            model = DEFAULT_COMFYUI_MODEL

        lora = DEFAULT_COMFYUI_LORA  # dormido: el LoRA vive en el workflow.

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
    nano_banana_provider: str = DEFAULT_NANO_BANANA_PROVIDER
    nano_banana_variant: str = DEFAULT_NANO_BANANA_VARIANT
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

        nb_provider = rec.get("nano_banana_provider", DEFAULT_NANO_BANANA_PROVIDER)
        if nb_provider not in VALID_NANO_BANANA_PROVIDERS:
            nb_provider = DEFAULT_NANO_BANANA_PROVIDER
        nb_variant = rec.get("nano_banana_variant", DEFAULT_NANO_BANANA_VARIANT)
        if nb_variant not in VALID_NANO_BANANA_VARIANTS:
            nb_variant = DEFAULT_NANO_BANANA_VARIANT

        video = VideoConfig.from_record(_prefix_group(rec, "video_"))
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
            nano_banana_provider=nb_provider,
            nano_banana_variant=nb_variant,
            video=video,
            comfyui=comfyui,
            source_path=source_path,
            integrate_ref_path=integrate_ref_path,
            state=state,
        )

    def to_record(self) -> dict:
        """Flat session record (no ``grok_provider`` legacy key)."""
        return {
            "source_path": self.source_path,
            "integrate_ref_path": self.integrate_ref_path,
            "state": self.state,
            "model": self.model,
            "grok_imagine_provider": self.grok_imagine_provider,
            "grok_imagine_variant": self.grok_imagine_variant,
            "nano_banana_provider": self.nano_banana_provider,
            "nano_banana_variant": self.nano_banana_variant,
            "video_duration": self.video.duration,
            "video_aspect_ratio": self.video.aspect_ratio,
            "video_resolution": self.video.resolution,
            "video_model": self.video.model,
            "video_mode": self.video.mode,
            "comfyui_model": self.comfyui.model,
            "comfyui_lora": self.comfyui.lora,
            "comfyui_refine": self.comfyui.refine,
        }
