"""Provider registry — pure resolution from ``UserConfig`` + ``MediaType`` (D6).

Semantic reference: grok bot.py ``get_model``/``get_grok_imagine_config``
(848-922) and ``get_video_provider_for_user`` (664-669). Providers and their
availability are injected by constructor (no settings/env reads here); the app
layer (item 6) wires real instances. Resolution raises
:class:`ProviderNotConfiguredError` (user-facing, R9) when the resolved provider
is missing or ``available is False``, and :class:`ProviderInputError` for
model/media combinations that cannot produce that media type.
"""

from __future__ import annotations

from dataclasses import dataclass

from grokbot.domain.catalog import MODELS, resolve_grok_config
from grokbot.domain.generation import MediaType
from grokbot.domain.user_config import UserConfig
from grokbot.providers.base import ProviderInputError, ProviderNotConfiguredError

_NOT_CONFIGURED_MESSAGES = {
    "xai": "xAI no está disponible en este momento. Contacta al administrador del bot.",
    "replicate": "Replicate no está disponible en este momento. Contacta al administrador del bot.",
    "kie": "Kie.ai no está disponible en este momento. Contacta al administrador del bot.",
    "comfyui": "ComfyUI no está disponible en este momento. Contacta al administrador del bot.",
}
_GENERIC_NOT_CONFIGURED = "El proveedor seleccionado no está disponible. Intenta de nuevo más tarde."

_IMAGE_ONLY_GENERATORS = ("seedream", "faceswap")
_COMFYUI_ID = "comfyui"
_VIDEO_ONLY_MODEL = "grok_video"


@dataclass(frozen=True)
class ProviderResolution:
    """Result of resolving a config+media request to a concrete provider."""

    name: str  # "xai" | "replicate" | "kie" | "comfyui"
    provider: object  # ImageProvider | VideoProvider concrete instance
    model_id: str  # wire model id for the request
    available: bool


class ProviderRegistry:
    """Hold provider instances and resolve them from ``UserConfig`` by media."""

    def __init__(self, *, xai=None, replicate=None, kie=None, comfyui=None):
        self._providers = {
            "xai": xai,
            "replicate": replicate,
            "kie": kie,
            "comfyui": comfyui,
        }

    # -- introspection -----------------------------------------------------
    def provider(self, name: str):
        return self._providers.get(name)

    def is_available(self, name: str) -> bool:
        provider = self._providers.get(name)
        return bool(provider) and bool(provider.available)

    # -- resolution --------------------------------------------------------
    def resolve_image(self, cfg: UserConfig) -> ProviderResolution:
        if cfg.model == "grok":
            resolved = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
            return self._resolve(resolved["provider"], resolved["id"])
        if cfg.model in _IMAGE_ONLY_GENERATORS:
            return self._resolve("replicate", MODELS[cfg.model]["id"])
        if cfg.model == _COMFYUI_ID:
            return self._resolve("comfyui", _COMFYUI_ID)
        if cfg.model == _VIDEO_ONLY_MODEL:
            raise ProviderInputError(
                "Grok Imagine Video genera video, no imágenes.",
                user_message="Grok Imagine Video genera video, no imágenes.",
            )
        raise ProviderInputError(
            f"El modelo {cfg.model!r} no genera imágenes.",
            user_message="El modelo seleccionado no genera imágenes.",
        )

    def resolve_video(self, cfg: UserConfig) -> ProviderResolution:
        if cfg.model == "grok_video":
            resolved = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
            name = resolved["provider"]
            # Replicate has no video backend — grok routes video to xAI (bot.py:664-669).
            if name == "replicate":
                name = "xai"
            return self._resolve(name, cfg.video.model)
        if cfg.model == _COMFYUI_ID:
            return self._resolve("comfyui", _COMFYUI_ID)
        raise ProviderInputError(
            f"El modelo {cfg.model!r} no genera video.",
            user_message="El modelo seleccionado no genera video.",
        )

    def resolve(
        self, cfg: UserConfig, media_type: MediaType
    ) -> ProviderResolution:
        if media_type is MediaType.IMAGE:
            return self.resolve_image(cfg)
        if media_type is MediaType.VIDEO:
            return self.resolve_video(cfg)
        raise ProviderInputError(
            f"Tipo de media no soportado: {media_type!r}.",
            user_message="Tipo de media no soportado.",
        )

    # -- helpers -----------------------------------------------------------
    def _resolve(self, name: str, model_id: str) -> ProviderResolution:
        provider = self._providers.get(name)
        if provider is None or not provider.available:
            msg = _NOT_CONFIGURED_MESSAGES.get(name, _GENERIC_NOT_CONFIGURED)
            raise ProviderNotConfiguredError(msg, user_message=msg)
        return ProviderResolution(
            name=name,
            provider=provider,
            model_id=model_id,
            available=True,
        )
