"""Gestión de configuración del usuario — UpdateUserConfigUseCase (item 4).

Setters sync con patrón get → ``dataclasses.replace`` → save (O3): la dataclass
frozen se copia con el cambio y solo se persiste si mutó. El reset de aspect Kie
usa el helper puro de dominio ``kie_aspect_ratio_fallback`` (D12) — nunca llama
``registry.resolve_video`` (lanza si el provider no está configurado). Espejo de
grok config_flow.py 609-846 y del reset de aspect bot.py 1117-1136.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from grokbot.domain.catalog import (
    MODELS,
    VALID_GROK_IMAGINE_PROVIDERS,
    VALID_GROK_IMAGINE_VARIANTS,
    VALID_NANO_BANANA_PROVIDERS,
    VALID_NANO_BANANA_VARIANTS,
)
from grokbot.domain.user_config import (
    COMFYUI_LORA_STRENGTH_MAX,
    COMFYUI_LORA_STRENGTH_MIN,
    DEFAULT_COMFYUI_LORA_STRENGTH,
    MAX_COMFYUI_LORAS_PER_FLOW,
    VALID_COMFYUI_MODELS,
    VALID_COMFYUI_SEED_MODES,
    VALID_VIDEO_ASPECT_RATIOS,
    VALID_VIDEO_DURATIONS,
    VALID_VIDEO_MODELS,
    VALID_VIDEO_MODES,
    VALID_VIDEO_RESOLUTIONS,
    LoraStackEntry,
    is_valid_comfyui_seed,
    kie_aspect_ratio_fallback,
    normalize_lora_name,
    normalize_lora_strength,
)
from grokbot.repositories.base import SessionRepository

_VALID_VIDEO_FIELDS = {
    "model": VALID_VIDEO_MODELS,
    "duration": VALID_VIDEO_DURATIONS,
    "aspect_ratio": VALID_VIDEO_ASPECT_RATIOS,
    "resolution": VALID_VIDEO_RESOLUTIONS,
    "mode": VALID_VIDEO_MODES,
}

# ComfyUI se configura por flujo: solo el id de flujo (``model``) es editable.
# ``lora``/``refine`` quedaron dormidos (no se ofrecen ni se aceptan).
_VALID_COMFYUI_FIELDS = {
    "model": VALID_COMFYUI_MODELS,
}


def _normalize_stack_entries(
    entries: object,
) -> tuple[LoraStackEntry, ...] | None:
    """Validar el stack completo; ``None`` si alguna entrada es inválida.

    El stack se reemplaza entero (no se mergea por índice) para que la UI pueda
    mandar lo que muestra y la persistencia no quede a medias si algo no encaja.
    """
    if not isinstance(entries, (list, tuple)):
        return None
    if len(entries) > MAX_COMFYUI_LORAS_PER_FLOW:
        return None
    out: list[LoraStackEntry] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, LoraStackEntry):
            return None
        name = normalize_lora_name(entry.name)
        strength = normalize_lora_strength(entry.strength)
        if name is None or strength is None or name in seen:
            return None
        seen.add(name)
        out.append(LoraStackEntry(name=name, strength=strength))
    return tuple(out)


@dataclass(frozen=True)
class ConfigResult:
    """Resultado de un setter de config (ítem 5 formatea el mensaje).

    ``ok=False`` → valor inválido; ``changed=False, ok=True`` → no-op ("ya
    activo"); ``changed=True`` → se persistió. ``aspect_reset_to`` avisa el nuevo
    aspect cuando el proveedor efectivo es Kie y el actual no era válido.
    """

    ok: bool
    changed: bool
    field: str
    value: object
    aspect_reset_to: str | None = None


class UpdateUserConfigUseCase:
    """Setters de ``UserConfig`` con validación y patrón load-then-save (O3)."""

    def __init__(self, *, sessions: SessionRepository) -> None:
        self._sessions = sessions

    # -- top model / grok imagine -------------------------------------------

    def set_model(self, user_id: int, model_key: str) -> ConfigResult:
        return self._set_top(user_id, field="model", value=model_key, valid=tuple(MODELS))

    def set_grok_imagine_provider(self, user_id: int, provider: str) -> ConfigResult:
        return self._set_top(
            user_id,
            field="grok_imagine_provider",
            value=provider,
            valid=VALID_GROK_IMAGINE_PROVIDERS,
        )

    def set_grok_imagine_variant(self, user_id: int, variant: str) -> ConfigResult:
        return self._set_top(
            user_id,
            field="grok_imagine_variant",
            value=variant,
            valid=VALID_GROK_IMAGINE_VARIANTS,
        )

    def set_nano_banana_provider(self, user_id: int, provider: str) -> ConfigResult:
        return self._set_top(
            user_id,
            field="nano_banana_provider",
            value=provider,
            valid=VALID_NANO_BANANA_PROVIDERS,
        )

    def set_nano_banana_variant(self, user_id: int, variant: str) -> ConfigResult:
        return self._set_top(
            user_id,
            field="nano_banana_variant",
            value=variant,
            valid=VALID_NANO_BANANA_VARIANTS,
        )

    def _set_top(self, user_id: int, *, field: str, value: str, valid: tuple) -> ConfigResult:
        if value not in valid:
            return ConfigResult(ok=False, changed=False, field=field, value=value)
        cfg = self._sessions.get_config(user_id)
        if getattr(cfg, field) == value:
            return ConfigResult(ok=True, changed=False, field=field, value=value)
        new_cfg = replace(cfg, **{field: value})
        aspect = kie_aspect_ratio_fallback(new_cfg)
        if aspect is not None:
            new_cfg = replace(new_cfg, video=replace(new_cfg.video, aspect_ratio=aspect))
        self._sessions.save_config(user_id, new_cfg)
        return ConfigResult(ok=True, changed=True, field=field, value=value, aspect_reset_to=aspect)

    # -- video --------------------------------------------------------------

    def set_video(self, user_id: int, **field) -> ConfigResult:
        """Cambiar UN campo de video por llamada (``model=``, ``duration=``, ...)."""
        if len(field) != 1:
            return ConfigResult(ok=False, changed=False, field="video", value=field)
        (name, value), = field.items()
        allowed = _VALID_VIDEO_FIELDS.get(name)
        if allowed is None or value not in allowed:
            return ConfigResult(ok=False, changed=False, field=f"video.{name}", value=value)
        cfg = self._sessions.get_config(user_id)
        video = cfg.video
        if getattr(video, name) == value:
            return ConfigResult(ok=True, changed=False, field=f"video.{name}", value=value)
        new_cfg = replace(cfg, video=replace(video, **{name: value}))
        aspect = None
        # Reset de aspect Kie tras cambiar el modelo de video (paridad bot 1117-1136).
        if name == "model":
            aspect = kie_aspect_ratio_fallback(new_cfg, video_model=value)
            if aspect is not None:
                new_cfg = replace(new_cfg, video=replace(new_cfg.video, aspect_ratio=aspect))
        self._sessions.save_config(user_id, new_cfg)
        return ConfigResult(
            ok=True,
            changed=True,
            field=f"video.{name}",
            value=value,
            aspect_reset_to=aspect,
        )

    def set_comfyui_seed(self, user_id: int, mode: str, seed: int | None = None) -> ConfigResult:
        """Persist the seed policy atomically, keeping the last fixed seed."""
        if mode not in VALID_COMFYUI_SEED_MODES:
            return ConfigResult(ok=False, changed=False, field="comfyui.seed", value=mode)
        if seed is not None and not is_valid_comfyui_seed(seed):
            return ConfigResult(ok=False, changed=False, field="comfyui.seed", value=mode)
        cfg = self._sessions.get_config(user_id)
        cc = replace(cfg.comfyui, seed_mode=mode, seed=cfg.comfyui.seed if seed is None else seed)
        if cc == cfg.comfyui:
            return ConfigResult(ok=True, changed=False, field="comfyui.seed", value=mode)
        self._sessions.save_config(user_id, replace(cfg, comfyui=cc))
        return ConfigResult(ok=True, changed=True, field="comfyui.seed", value=mode)

    # -- comfyui ------------------------------------------------------------

    def set_comfyui(self, user_id: int, **field) -> ConfigResult:
        """Cambiar el flujo ComfyUI (``model=<id de flujo>``). lora/refine dormidos."""
        if len(field) != 1:
            return ConfigResult(ok=False, changed=False, field="comfyui", value=field)
        (name, value), = field.items()
        allowed = _VALID_COMFYUI_FIELDS.get(name)
        if allowed is None or value not in allowed:
            return ConfigResult(ok=False, changed=False, field=f"comfyui.{name}", value=value)
        cfg = self._sessions.get_config(user_id)
        comfyui = cfg.comfyui
        if getattr(comfyui, name) == value:
            return ConfigResult(ok=True, changed=False, field=f"comfyui.{name}", value=value)
        new_cfg = replace(cfg, comfyui=replace(comfyui, **{name: value}))
        self._sessions.save_config(user_id, new_cfg)
        return ConfigResult(ok=True, changed=True, field=f"comfyui.{name}", value=value)

    # -- comfyui LoRA stack -------------------------------------------------

    def set_comfyui_loras(
        self,
        user_id: int,
        flow_id: str,
        entries: object,
    ) -> ConfigResult:
        """Reemplazar el stack adicional de UN flujo (lista completa validada)."""
        if flow_id not in VALID_COMFYUI_MODELS:
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=flow_id
            )
        normalized = _normalize_stack_entries(entries)
        if normalized is None:
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=entries
            )
        cfg = self._sessions.get_config(user_id)
        if cfg.comfyui.loras_for(flow_id) == normalized:
            return ConfigResult(
                ok=True, changed=False, field="comfyui.loras", value=flow_id
            )
        new_cfg = replace(
            cfg, comfyui=cfg.comfyui.with_lora_stack(flow_id, normalized)
        )
        self._sessions.save_config(user_id, new_cfg)
        return ConfigResult(ok=True, changed=True, field="comfyui.loras", value=flow_id)

    def add_comfyui_lora(
        self,
        user_id: int,
        flow_id: str,
        name: object,
        strength: object = DEFAULT_COMFYUI_LORA_STRENGTH,
    ) -> ConfigResult:
        """Agregar UN LoRA al final del stack del flujo (idempotente por nombre)."""
        clean_name = normalize_lora_name(name)
        clean_strength = normalize_lora_strength(strength)
        if clean_name is None or clean_strength is None:
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=name
            )
        cfg = self._sessions.get_config(user_id)
        current = list(cfg.comfyui.loras_for(flow_id))
        if any(entry.name == clean_name for entry in current):
            return ConfigResult(
                ok=True, changed=False, field="comfyui.loras", value=clean_name
            )
        if len(current) >= MAX_COMFYUI_LORAS_PER_FLOW:
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=clean_name
            )
        current.append(LoraStackEntry(name=clean_name, strength=clean_strength))
        return self.set_comfyui_loras(user_id, flow_id, current)

    def remove_comfyui_lora(
        self,
        user_id: int,
        flow_id: str,
        index: object,
    ) -> ConfigResult:
        """Quitar el LoRA en ``index`` del stack del flujo."""
        if type(index) is not int or index < 0:
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=index
            )
        cfg = self._sessions.get_config(user_id)
        current = list(cfg.comfyui.loras_for(flow_id))
        if index >= len(current):
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=index
            )
        current.pop(index)
        return self.set_comfyui_loras(user_id, flow_id, current)

    def shift_comfyui_lora_strength(
        self,
        user_id: int,
        flow_id: str,
        index: object,
        delta: object,
    ) -> ConfigResult:
        """Sumar ``delta`` a la intensidad del LoRA en ``index`` (con clamp)."""
        if type(index) is not int or index < 0:
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=index
            )
        if type(delta) not in (int, float):
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=delta
            )
        cfg = self._sessions.get_config(user_id)
        current = list(cfg.comfyui.loras_for(flow_id))
        if index >= len(current):
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=index
            )
        entry = current[index]
        value = round(entry.strength + float(delta), 2)
        value = max(COMFYUI_LORA_STRENGTH_MIN, min(COMFYUI_LORA_STRENGTH_MAX, value))
        strength = normalize_lora_strength(value)
        if strength is None:
            return ConfigResult(
                ok=False, changed=False, field="comfyui.loras", value=value
            )
        if strength == entry.strength:
            return ConfigResult(
                ok=True, changed=False, field="comfyui.loras", value=strength
            )
        current[index] = LoraStackEntry(name=entry.name, strength=strength)
        return self.set_comfyui_loras(user_id, flow_id, current)

    def clear_comfyui_loras(self, user_id: int, flow_id: str) -> ConfigResult:
        """Vaciar el stack adicional del flujo (los horneados no se tocan)."""
        return self.set_comfyui_loras(user_id, flow_id, ())
