"""Gestión de configuración del usuario — UpdateUserConfigUseCase (item 4).

Setters sync con patrón get → ``dataclasses.replace`` → save (O3): la dataclass
frozen se copia con el cambio y solo se persiste si mutó. El reset de aspect Kie
usa el helper puro de dominio ``kie_aspect_ratio_fallback`` (D12) — nunca llama
``registry.resolve_video`` (lanza si el provider no está configurado). Espejo de
grok config_flow.py 609-846 y del reset de aspect bot.py 1117-1136.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from grokbot.domain.catalog import MODELS, VALID_GROK_IMAGINE_PROVIDERS, VALID_GROK_IMAGINE_VARIANTS
from grokbot.domain.user_config import (
    VALID_COMFYUI_MODELS,
    VALID_VIDEO_ASPECT_RATIOS,
    VALID_VIDEO_DURATIONS,
    VALID_VIDEO_MODELS,
    VALID_VIDEO_MODES,
    VALID_VIDEO_RESOLUTIONS,
    kie_aspect_ratio_fallback,
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
