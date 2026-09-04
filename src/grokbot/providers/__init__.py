"""Paquete de providers (xAI, Replicate, Kie.ai, ComfyUI) tras un contrato común.

Cada provider concreto vive en su propio módulo y se re-exporta aquí. Desde el
ítem 6 (R7), el paquete re-exporta de forma lazy vía PEP 562 (``__getattr__`` de
módulo): importar ``grokbot.providers`` (o cualquier capa que lo importe) NO
carga los providers concretos (xai/kie/replicate/comfyui+ssh) ni sus
dependencias de transporte hasta que se accede a un nombre público.
``__all__`` es idéntico al eager anterior — la API pública no cambia, solo se
aplaza la carga.
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
    "FaceSwapProvider",
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
        "FaceSwapProvider",
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
    """Resuelve un nombre público del paquete importando su submódulo (PEP 562).

    Para cualquier otro nombre (submódulos como ``base``/``registry``, dunder,
    etc.) se levanta ``AttributeError`` para que el import system resuelva los
    submódulos con normalidad y las herramientas no rompan.
    """
    module = _NAMES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__():
    return sorted(set(globals()) | set(__all__))
