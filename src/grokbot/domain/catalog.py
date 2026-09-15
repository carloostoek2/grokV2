"""Model catalog — static registry of generation models and Grok Imagine / Nano Banana variants.

Values are transcribed from grok/bot.py (MODELS / GROK_IMAGINE_VARIANTS) for data
parity; UI label maps (e.g. ``VIDEO_MODEL_LABELS``) intentionally live in the
telegram layer (item 5), not here.
"""

from __future__ import annotations

# Static model registry (spec records identical to grok bot.py:84-122 + Nano Banana).
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
    "nano_banana": {
        "key": "nano_banana",
        # Default/fallback ids (variant-specific resolved via resolve_nano_banana_config).
        "id": "nano-banana-2",
        "replicate_id": (
            "google/nano-banana-2:"
            "71516450bdbeafc41df33ad538bc8cc6a90f80038a563b1260531c02d694f4fd"
        ),
        "name": "Nano Banana",
        "desc": "Google Nano Banana (Gemini Flash/Pro Image)",
        "provider": "kie",
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

# Nano Banana family: Kie (default) + Replicate. Primary UX variant is banana2.
# Classic Kie i2i uses a separate edit slug (google/nano-banana-edit + image_urls);
# banana2/pro use the same slug for t2i and i2i with image_input.
NANO_BANANA_VARIANTS: dict[str, dict] = {
    "classic": {
        "kie_id": "google/nano-banana",
        "kie_edit_id": "google/nano-banana-edit",
        "replicate_id": (
            "google/nano-banana:"
            "0d02b13954d9a189e5145f11f7b4426a40dfb6e7f4ae83af85b977678b18f1de"
        ),
        "label": "Clásico",
        "desc": "Gemini 2.5 Flash Image — rápido (legacy)",
        "default_resolution": None,
    },
    "banana2": {
        "kie_id": "nano-banana-2",
        "kie_edit_id": "nano-banana-2",
        "replicate_id": (
            "google/nano-banana-2:"
            "71516450bdbeafc41df33ad538bc8cc6a90f80038a563b1260531c02d694f4fd"
        ),
        "label": "Nano Banana 2",
        "desc": "Gemini 3.1 Flash Image — velocidad/calidad/precio (recomendado)",
        "default_resolution": "1K",
    },
    "pro": {
        "kie_id": "nano-banana-pro",
        "kie_edit_id": "nano-banana-pro",
        "replicate_id": (
            "google/nano-banana-pro:"
            "9f57615b766710492f0887bec039aed69178c6db88839fca425ce6b78d858999"
        ),
        "label": "Pro",
        "desc": "Gemini 3 Pro Image — máxima fidelidad",
        "default_resolution": "2K",
    },
}

DEFAULT_MODEL = "grok"
VALID_MODELS = tuple(MODELS)

DEFAULT_GROK_IMAGINE_PROVIDER = "kie"
DEFAULT_GROK_IMAGINE_VARIANT = "quality"
VALID_GROK_IMAGINE_PROVIDERS = ("xai", "replicate", "kie")
VALID_GROK_IMAGINE_VARIANTS = ("standard", "quality")

DEFAULT_NANO_BANANA_PROVIDER = "kie"
DEFAULT_NANO_BANANA_VARIANT = "banana2"
VALID_NANO_BANANA_PROVIDERS = ("kie", "replicate")
VALID_NANO_BANANA_VARIANTS = ("classic", "banana2", "pro")

# Kie Nano Banana model slugs (t2i + classic edit). Used by KieProvider branching.
KIE_NANO_BANANA_MODELS = frozenset(
    {
        "google/nano-banana",
        "google/nano-banana-edit",
        "nano-banana-2",
        "nano-banana-pro",
    }
)


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


def resolve_nano_banana_config(provider: str | None, variant: str | None) -> dict:
    """Resolve Nano Banana (provider, variant) into a concrete wire model id.

    Same shape as :func:`resolve_grok_config`. Returns
    ``{"provider", "variant", "id", "kie_edit_id", "default_resolution"}``.
    ``id`` is the t2i / primary slug (Kie or Replicate); ``kie_edit_id`` is the
    Kie slug to use when editing with a source image (classic uses a separate
    edit model; banana2/pro reuse the same slug).
    """
    prov = (
        provider if provider in VALID_NANO_BANANA_PROVIDERS else DEFAULT_NANO_BANANA_PROVIDER
    )
    var = variant if variant in VALID_NANO_BANANA_VARIANTS else DEFAULT_NANO_BANANA_VARIANT
    spec = NANO_BANANA_VARIANTS[var]
    if prov == "replicate":
        model_id = spec["replicate_id"]
    else:
        model_id = spec["kie_id"]
    return {
        "provider": prov,
        "variant": var,
        "id": model_id,
        "kie_edit_id": spec["kie_edit_id"],
        "default_resolution": spec["default_resolution"],
    }


def is_kie_nano_banana_model(model_id: str) -> bool:
    """True when ``model_id`` is a Kie Nano Banana family slug (not Grok Imagine)."""
    return model_id in KIE_NANO_BANANA_MODELS


def resolve_model_id(key: str, *, provider: str | None = None, variant: str | None = None) -> str:
    """Return the concrete provider model id for a top-level model ``key``.

    For ``grok`` / ``nano_banana`` the id is resolved dynamically from the
    granular provider/variant config; every other model returns its static
    registry id.
    """
    if key == DEFAULT_MODEL:
        return resolve_grok_config(provider, variant)["id"]
    if key == "nano_banana":
        return resolve_nano_banana_config(provider, variant)["id"]
    return model_spec(key)["id"]
