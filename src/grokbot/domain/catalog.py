"""Model catalog — static registry of generation models and Grok Imagine variants.

Values are transcribed from grok/bot.py (MODELS / GROK_IMAGINE_VARIANTS) for data
parity; UI label maps (e.g. ``VIDEO_MODEL_LABELS``) intentionally live in the
telegram layer (item 5), not here.
"""

from __future__ import annotations

# Static model registry (spec records identical to grok bot.py:84-122).
MODELS: dict[str, dict] = {
    "grok": {
        "key": "grok",
        # Base identifiers (variant-specific id/replicate_id resolved at runtime
        # via resolve_grok_config / resolve_model_id).
        "id": "grok-imagine-image-quality",           # default/fallback (xAI direct)
        "replicate_id": "xai/grok-imagine-image-quality",
        "name": "Grok Imagine",
        "desc": "xAI Grok Imagine",
        "provider": "xai",
    },
    "seedream": {
        "key": "seedream",
        "id": "bytedance/seedream-5-lite",
        "name": "Seedream 5.0",
        "desc": "ByteDance Seedream 5.0 Lite",
        "provider": "replicate",
    },
    "faceswap": {
        "key": "faceswap",
        "id": "cdingram/face-swap:d1d6ea8c8be89d664a07a457526f7128109dee7030fdac424788d762c71ed111",
        "name": "Face Swap",
        "desc": "Intercambio de caras (cdingram/face-swap)",
        "provider": "replicate",
    },
    "grok_video": {
        "key": "grok_video",
        "id": "grok-imagine-video",
        "name": "Grok Imagine Video",
        "desc": "Generación de video con xAI Grok Imagine",
        "provider": "xai",
    },
    "comfyui": {
        "key": "comfyui",
        "id": "comfyui",
        "name": "ComfyUI (GPU propia)",
        "desc": "Imagen con ComfyUI en tu GPU — por flujos (hoy: Grok Style)",
        "provider": "comfyui",
    },
}

# Granular Grok Imagine configuration: three providers (xAI direct / Replicate /
# Kie.ai) × two quality tiers (grok bot.py:141-156).
GROK_IMAGINE_VARIANTS: dict[str, dict] = {
    "standard": {
        "id": "grok-imagine-image",
        "replicate_id": "xai/grok-imagine-image",
        "kie_id": "grok-imagine-image-2-0/text-to-image",
        "label": "Estándar",
        "desc": "Rápido, ideal para prototipado y previews",
    },
    "quality": {
        "id": "grok-imagine-image-quality",
        "replicate_id": "xai/grok-imagine-image-quality",
        "kie_id": "grok-imagine-image-2-0/text-to-image",
        "label": "Alta calidad",
        "desc": "Mayor detalle, texto nítido, hasta 2K (recomendado para finales)",
    },
}

DEFAULT_MODEL = "grok"
VALID_MODELS = tuple(MODELS)  # ("grok", "seedream", "faceswap", "grok_video", "comfyui")

DEFAULT_GROK_IMAGINE_PROVIDER = "kie"
DEFAULT_GROK_IMAGINE_VARIANT = "quality"
VALID_GROK_IMAGINE_PROVIDERS = ("xai", "replicate", "kie")
VALID_GROK_IMAGINE_VARIANTS = ("standard", "quality")


def model_spec(key: str) -> dict:
    """Return the spec record for ``key``, falling back to the default model.

    Mirrors grok ``get_model`` base lookup (``MODELS.get(key, MODELS[DEFAULT_MODEL])``).
    """
    return MODELS.get(key, MODELS[DEFAULT_MODEL])


def resolve_grok_config(provider: str | None, variant: str | None) -> dict:
    """Resolve the (provider, variant) pair into a concrete model id.

    Provider/variant are normalized to VALID_* sets (falling back to the default
    when absent/invalid); the id is picked per provider from the variant spec,
    mirroring grok bot.py ``get_grok_imagine_config``.
    Returns ``{"provider", "variant", "id"}``.
    """
    prov = provider if provider in VALID_GROK_IMAGINE_PROVIDERS else DEFAULT_GROK_IMAGINE_PROVIDER
    var = variant if variant in VALID_GROK_IMAGINE_VARIANTS else DEFAULT_GROK_IMAGINE_VARIANT
    spec = GROK_IMAGINE_VARIANTS[var]
    if prov == "replicate":
        model_id = spec["replicate_id"]
    elif prov == "kie":
        model_id = spec["kie_id"]
    else:
        model_id = spec["id"]
    return {"provider": prov, "variant": var, "id": model_id}


def resolve_model_id(key: str, *, provider: str | None = None, variant: str | None = None) -> str:
    """Return the concrete provider model id for a top-level model ``key``.

    For ``grok`` the id is resolved dynamically from the granular
    provider/variant config; every other model returns its static registry id.
    """
    if key == DEFAULT_MODEL:
        return resolve_grok_config(provider, variant)["id"]
    return model_spec(key)["id"]
