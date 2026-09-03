"""Generation providers (xAI, Replicate, Kie.ai, ComfyUI) behind a common contract.

Concrete providers are added by their own modules and re-exported here. Since
item 6 (R7), the package re-exports lazily via PEP 562 (module ``__getattr__``):
importing ``grokbot.providers`` or any layer that imports it does NOT load the
concrete providers (xai/kie/replicate/comfyui+ssh) nor their transport deps
until a public name is actually accessed. ``__all__`` es idéntico al eager
anterior — la API pública no cambia, solo se aplaza la carga.
"""

from __future__ import annotations

import importlib

__all__ = [
    "DEFAULT_IMAGE_ASPECT_RATIO",
    "I2V_MAX_IMAGE_BYTES",
    "IMAGE_MAX_POLL_SEC",
    "POLL_MAX_RETRIES",
    "POLL_RETRY_BACKOFF_SEC",
    "VIDEO_MAX_POLL_SEC",
    "VIDEO_POLL_INTERVAL_SEC",
    "ImageProvider",
    "VideoProvider",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderInputError",
    "ProviderContentError",
    "ProviderAuthenticationError",
    "ProviderGenerationError",
    "ProviderRateLimitError",
    "ProviderUnavailableError",
    "ProviderTimeoutError",
    "bytes_to_data_uri",
    "detect_image_mime",
    "validate_image_for_i2v",
    "XaiProvider",
    "ReplicateProvider",
    "KieProvider",
    "ComfyUIProvider",
    "SshClient",
    "ProviderRegistry",
    "ProviderResolution",
]

# Mapa nombre público → submódulo que lo define (carga lazy bajo demanda).
_SOURCES = {
    "grokbot.providers.base": [
        "DEFAULT_IMAGE_ASPECT_RATIO",
        "I2V_MAX_IMAGE_BYTES",
        "IMAGE_MAX_POLL_SEC",
        "POLL_MAX_RETRIES",
        "POLL_RETRY_BACKOFF_SEC",
        "VIDEO_MAX_POLL_SEC",
        "VIDEO_POLL_INTERVAL_SEC",
        "ImageProvider",
        "VideoProvider",
        "ProviderError",
        "ProviderNotConfiguredError",
        "ProviderInputError",
        "ProviderContentError",
        "ProviderAuthenticationError",
        "ProviderGenerationError",
        "ProviderRateLimitError",
        "ProviderUnavailableError",
        "ProviderTimeoutError",
        "bytes_to_data_uri",
        "detect_image_mime",
        "validate_image_for_i2v",
    ],
    "grokbot.providers.comfyui.provider": ["ComfyUIProvider"],
    "grokbot.providers.comfyui.ssh_client": ["SshClient"],
    "grokbot.providers.kie_provider": ["KieProvider"],
    "grokbot.providers.registry": ["ProviderRegistry", "ProviderResolution"],
    "grokbot.providers.replicate_provider": ["ReplicateProvider"],
    "grokbot.providers.xai_provider": ["XaiProvider"],
}
_NAMES = {name: mod for mod, names in _SOURCES.items() for name in names}


def __getattr__(name: str):
    """Resolver un nombre público del paquete importando su submódulo (PEP 562).

    Para cualquier otro nombre (submódulos como ``base``/``registry``, dunder,
    etc.) se levanta ``AttributeError`` para que el import system resuelva los
    submódulos con normalidad y los proxies de herramienta no rompan.
    """
    module = _NAMES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__():
    return sorted(set(globals()) | set(__all__))
