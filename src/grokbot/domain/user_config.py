"""Persisted user configuration value objects.

Models ONLY the portion of a session record that grok persists to
``sessions.json`` (mirror of ``_default_session_record``). Ephemeral FSM state
(pending prompts, collected edit file ids, ...) is owned by the
application/telegram layers (items 4-5). Extra keys a real record may carry
(legacy/repo-owned) are tolerated but ignored here (R4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

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
    ("grok_edit", "Grok Style Edit"),
    # Dirty* (dirty_realism/dirty_edit y sus variantes HQ) retirados
    # temporalmente el 2026-11-07: sus templates viven en
    # ``workflows/templates/_disabled/``. Reactivar = devolver las entradas a
    # esta tupla y mover los JSON de vuelta a ``templates/``.
    ("grok_style_hq", "Grok Style (HQ A100)"),
    ("agil_solo_hq", "Ágil Solo (HQ A100)"),
    ("agil_nsfw_hq", "Ágil NSFW (HQ A100)"),
    ("agil_moody_hq", "Ágil Moody (HQ A100)"),
    ("grok_edit_hq", "Grok Edit (HQ A100)"),
    ("agil_edit_hq", "Ágil Edit (HQ A100)"),
    ("wan_i2v", "Wan I2V"),
    ("qwen21_t2i", "Qwen 2.1"),
    ("flux1_dev_t2i", "Flux.1 Dev"),
    ("minimax_h3_i2v", "MiniMax H3"),
    ("face_detail_impact", "Face Detail"),
    ("instantid_sdxl", "InstantID SDXL"),
    ("ohwx_krea2", "Ohwx"),
    ("ohwx_edit", "Ohwx Edit"),
)
DEFAULT_COMFYUI_MODEL = COMFYUI_FLOWS[0][0]
VALID_COMFYUI_MODELS = tuple(_id for _id, _label in COMFYUI_FLOWS)
COMFYUI_FLOW_LABELS = dict(COMFYUI_FLOWS)
# Still valid saved configs (see VALID_COMFYUI_MODELS). Hidden from the
# Telegram config keyboard by explicit list; the HQ block stays visible because
# it is the active menu.
COMFYUI_MENU_HIDDEN_FLOWS = frozenset({
    "donut_face",
    "wan_i2v",
    "qwen21_t2i",
    "flux1_dev_t2i",
    "minimax_h3_i2v",
    "face_detail_impact",
    "instantid_sdxl",
    "ohwx_krea2",
    "ohwx_edit",
})

# Result-chain stages (Telegram buttons under a ComfyUI image). The callback
# does not generate; it only selects the destination flow.
PIPE_EDIT = "edit"
PIPE_DETAIL = "detail"
PIPE_RETAKE = "retake"
PIPE_STAGES = (PIPE_EDIT, PIPE_DETAIL, PIPE_RETAKE)
FACE_DETAIL_FLOW_ID = "face_detail_impact"
# T2I id → edit sibling. No entry means the Editar button stays hidden.
COMFYUI_EDIT_SIBLINGS = {
    "grok_style": "grok_edit",
    "grok_style_hq": "grok_edit_hq",
    "agil_solo_hq": "agil_edit_hq",
    "agil_moody_hq": "agil_edit_hq",
    "agil_nsfw_hq": "agil_edit_nsfw",
    "qwen21_t2i": "agil_edit_qwen",
    "agil_solo": "agil_edit_qwen",
    "agil_moody": "agil_edit_qwen",
    "agil_nsfw": "agil_edit_nsfw",
    "ohwx_krea2": "ohwx_edit",
}


def comfy_chain_stages(
    flow_id: str,
    *,
    supports_source: bool,
    requires_source: bool,
    media_type: str = "image",
) -> tuple[tuple[str, str], ...]:
    """Visible ``(stage, dest_flow_id)`` pairs for a ComfyUI result.

    Editar only when ``flow_id`` has a mapped edit sibling that is still a
    valid catalog id. Detalle cara when ``face_detail_impact`` is registered
    and this result is a Comfy image that is not already that flow. Otra toma
    only for T2I (no source support and source not required). Unknown or
    non-image flows get nothing.
    """
    if flow_id not in VALID_COMFYUI_MODELS or media_type != "image":
        return ()
    stages: list[tuple[str, str]] = []
    sibling = COMFYUI_EDIT_SIBLINGS.get(flow_id)
    if sibling and sibling in VALID_COMFYUI_MODELS:
        stages.append((PIPE_EDIT, sibling))
    if FACE_DETAIL_FLOW_ID in VALID_COMFYUI_MODELS and flow_id != FACE_DETAIL_FLOW_ID:
        stages.append((PIPE_DETAIL, FACE_DETAIL_FLOW_ID))
    if not supports_source and not requires_source:
        stages.append((PIPE_RETAKE, flow_id))
    return tuple(stages)


def comfy_chain_destination(
    flow_id: str,
    stage: str,
    *,
    supports_source: bool,
    requires_source: bool,
    media_type: str = "image",
) -> str | None:
    """Destination flow for ``stage``, or None when that button must not exist."""
    for name, dest in comfy_chain_stages(
        flow_id,
        supports_source=supports_source,
        requires_source=requires_source,
        media_type=media_type,
    ):
        if name == stage:
            return dest
    return None
# ``lora``/``refine`` (escalares legacy) quedaron dormidos tras el slice HTTP/WS:
# el LoRA horneado forma parte del workflow y refine no se ofrece. Se conservan en
# la config persistida por compat (from_record/to_record) pero no participan en la
# resolución. El stack NUEVO del usuario vive en ``ComfyUIConfig.lora_stacks``
# (por flujo) y sí se aplica al generar.
DEFAULT_COMFYUI_LORA = "none"
DEFAULT_COMFYUI_REFINE = "1"  # "1" = final refine ON (JSON parity: str "0"/"1")

# --- ComfyUI user LoRA stack ------------------------------------------------
# El usuario APILA LoRAs encima del workflow: los horneados en el template no se
# tocan (una LoRA de identidad o de destilado es estructural), y el stack se
# inserta al final de la cadena de modelo. Cada flujo mantiene su propio stack.
MAX_COMFYUI_LORAS_PER_FLOW = 5
DEFAULT_COMFYUI_LORA_STRENGTH = 0.8
COMFYUI_LORA_STRENGTH_MIN = 0.0
COMFYUI_LORA_STRENGTH_MAX = 2.0
_COMFYUI_LORA_NAME_MAX = 200


@dataclass(frozen=True)
class LoraStackEntry:
    """Un LoRA extra del usuario: nombre de archivo + intensidad."""

    name: str
    strength: float = DEFAULT_COMFYUI_LORA_STRENGTH

    def to_record(self) -> dict:
        return {"name": self.name, "strength": self.strength}


def normalize_lora_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > _COMFYUI_LORA_NAME_MAX:
        return None
    if any(ord(ch) < 32 for ch in name):
        return None
    return name


def normalize_lora_strength(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    strength = float(value)
    if strength != strength:  # NaN
        return None
    if not (COMFYUI_LORA_STRENGTH_MIN <= strength <= COMFYUI_LORA_STRENGTH_MAX):
        return None
    return round(strength, 2)


def parse_lora_stack(raw: object) -> tuple[LoraStackEntry, ...]:
    """Normalizar el stack de UN flujo: descarta inválidos, dedupe, tope.

    Acepta ``[{"name", "strength"}]`` (record) o ``[str]`` (legacy). Un JSON
    corrupto nunca rompe la lectura: se ignoran las entradas que no encajan.
    """
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[LoraStackEntry] = []
    seen: set[str] = set()
    for item in raw:
        if len(out) >= MAX_COMFYUI_LORAS_PER_FLOW:
            break
        if isinstance(item, Mapping):
            name = normalize_lora_name(item.get("name"))
            strength = normalize_lora_strength(
                item.get("strength", DEFAULT_COMFYUI_LORA_STRENGTH)
            )
        elif isinstance(item, str):
            name = normalize_lora_name(item)
            strength = DEFAULT_COMFYUI_LORA_STRENGTH
        else:
            continue
        if name is None or strength is None or name in seen:
            continue
        seen.add(name)
        out.append(LoraStackEntry(name=name, strength=strength))
    return tuple(out)


def _normalize_lora_stacks(raw: object) -> tuple[tuple[str, tuple[LoraStackEntry, ...]], ...]:
    """Normalizar ``{flow_id: [entries]}`` conservando flujos desconocidos.

    Un flujo temporalmente retirado del catálogo (p. ej. los Dirty\\*) mantiene
    su stack para cuando vuelva; solo se descartan claves no textuales o vacías.
    """
    if not isinstance(raw, Mapping):
        return ()
    out: list[tuple[str, tuple[LoraStackEntry, ...]]] = []
    for flow_id, entries in raw.items():
        if not isinstance(flow_id, str) or not flow_id.strip():
            continue
        parsed = parse_lora_stack(entries)
        if parsed:
            out.append((flow_id, parsed))
    return tuple(out)

# --- ComfyUI seed policy ---------------------------------------------------
# One seed per image ("random"), one seed shared by a batch ("batch"), or a
# user-pinned value ("fixed"). The range is the 64-bit signed max ComfyUI
# accepts; keep every validator copy (domain/application/provider/telegram) on
# these constants so the accepted and persisted ranges cannot drift apart.
COMFYUI_SEED_LIMIT = 1 << 63  # exclusive: 0 .. 2^63-1
VALID_COMFYUI_SEED_MODES = ("random", "batch", "fixed")


def is_valid_comfyui_seed(value: object) -> bool:
    """True for a plain int in ``[0, COMFYUI_SEED_LIMIT)`` (``bool`` rejected)."""
    return type(value) is int and 0 <= value < COMFYUI_SEED_LIMIT


IDLE_STATE = "IDLE"
AWAITING_SOURCE = "AWAITING_SOURCE"

# --- ComfyUI video-only models (D10, item 4). ---
# Models whose ComfyUI workflow produces MP4 (video), never a still image.
# Mirrors the private provider constant ``providers/comfyui/provider.py`` but
# lives in domain so the application layer can validate media_type (M2) without
# importing a transport provider (layering rule).
COMFY_VIDEO_MODELS = ("wan_i2v", "minimax_i2v", "minimax_h3_i2v")


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

    ``lora_stacks`` es el stack **adicional** por flujo que el usuario arma
    desde Telegram: se inyecta al final de la cadena de modelo y nunca sustituye
    lo horneado. Un flujo sin entrada no lleva LoRAs extra.
    """

    model: str = DEFAULT_COMFYUI_MODEL
    lora: str = DEFAULT_COMFYUI_LORA
    refine: str = DEFAULT_COMFYUI_REFINE  # "0"/"1" as str (JSON parity)
    seed_mode: str = VALID_COMFYUI_SEED_MODES[0]
    seed: int = 0
    # Tuple of (flow_id, entries): frozen + ordered, same semantics as a dict.
    lora_stacks: tuple[tuple[str, tuple[LoraStackEntry, ...]], ...] = ()

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

        seed_mode = rec.get("seed_mode", VALID_COMFYUI_SEED_MODES[0])
        if seed_mode not in VALID_COMFYUI_SEED_MODES:
            seed_mode = VALID_COMFYUI_SEED_MODES[0]
        seed = rec.get("seed", 0)
        if not is_valid_comfyui_seed(seed):
            seed = 0
        lora_stacks = _normalize_lora_stacks(rec.get("lora_stacks"))
        return cls(
            model=model,
            lora=lora,
            refine=refine,
            seed_mode=seed_mode,
            seed=seed,
            lora_stacks=lora_stacks,
        )

    def to_record(self) -> dict:
        return {
            "model": self.model,
            "lora": self.lora,
            "refine": self.refine,
            "seed_mode": self.seed_mode,
            "seed": self.seed,
            "lora_stacks": {
                flow_id: [entry.to_record() for entry in entries]
                for flow_id, entries in self.lora_stacks
            },
        }

    @property
    def refine_enabled(self) -> bool:
        return self.refine == "1"

    def loras_for(self, flow_id: str) -> tuple[LoraStackEntry, ...]:
        """Stack adicional del flujo (vacío si no tiene)."""
        for stored_id, entries in self.lora_stacks:
            if stored_id == flow_id:
                return entries
        return ()

    def with_lora_stack(
        self,
        flow_id: str,
        entries: Sequence[LoraStackEntry],
    ) -> "ComfyUIConfig":
        """Copia con el stack de ``flow_id`` reemplazado (inmutable).

        Un stack vacío elimina la entrada del flujo, para que el record no
        acumule claves muertas.
        """
        kept = tuple(
            (stored_id, stored) for stored_id, stored in self.lora_stacks
            if stored_id != flow_id
        )
        new_entries = tuple(entries)
        if new_entries:
            kept = kept + ((flow_id, new_entries),)
        return replace(self, lora_stacks=kept)


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
            "comfyui_seed_mode": self.comfyui.seed_mode,
            "comfyui_seed": self.comfyui.seed,
            "comfyui_model": self.comfyui.model,
            "comfyui_lora": self.comfyui.lora,
            "comfyui_refine": self.comfyui.refine,
            "comfyui_lora_stacks": {
                flow_id: [entry.to_record() for entry in entries]
                for flow_id, entries in self.comfyui.lora_stacks
            },
        }
