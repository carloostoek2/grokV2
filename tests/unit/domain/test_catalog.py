"""Unit tests for grokbot.domain.catalog (D1)."""

from __future__ import annotations

from grokbot.domain.catalog import (
    DEFAULT_GROK_IMAGINE_PROVIDER,
    DEFAULT_GROK_IMAGINE_VARIANT,
    DEFAULT_MODEL,
    DEFAULT_NANO_BANANA_PROVIDER,
    DEFAULT_NANO_BANANA_VARIANT,
    GROK_IMAGINE_VARIANTS,
    MODELS,
    NANO_BANANA_VARIANTS,
    VALID_GROK_IMAGINE_PROVIDERS,
    VALID_GROK_IMAGINE_VARIANTS,
    VALID_MODELS,
    VALID_NANO_BANANA_PROVIDERS,
    VALID_NANO_BANANA_VARIANTS,
    is_kie_nano_banana_model,
    model_spec,
    resolve_grok_config,
    resolve_model_id,
    resolve_nano_banana_config,
)


def test_models_exact_keys_and_required_spec_fields():
    assert set(MODELS) == set(VALID_MODELS)
    assert VALID_MODELS == ("grok", "nano_banana", "seedream", "faceswap", "grok_video", "comfyui")
    for key, spec in MODELS.items():
        assert spec["key"] == key
        assert isinstance(spec["id"], str) and spec["id"]
        assert isinstance(spec["provider"], str) and spec["provider"]


def test_model_spec_falls_back_to_default():
    assert model_spec("no_existe") is MODELS[DEFAULT_MODEL]
    assert model_spec("no_existe")["id"] == "grok-imagine-image-quality"
    assert model_spec("seedream")["id"] == "bytedance/seedream-5-lite"


def test_resolve_grok_xai_standard():
    assert resolve_grok_config("xai", "standard") == {
        "provider": "xai",
        "variant": "standard",
        "id": "grok-imagine-image",
    }
    assert resolve_model_id("grok", provider="xai", variant="standard") == "grok-imagine-image"


def test_resolve_grok_replicate_quality():
    assert resolve_grok_config("replicate", "quality") == {
        "provider": "replicate",
        "variant": "quality",
        "id": "xai/grok-imagine-image-quality",
    }
    assert resolve_model_id("grok", provider="replicate", variant="quality") == "xai/grok-imagine-image-quality"


def test_resolve_grok_kie_standard():
    assert resolve_model_id("grok", provider="kie", variant="standard") == "grok-imagine-image-2-0/text-to-image"


def test_invalid_provider_or_variant_falls_back():
    # invalid variant -> quality (default)
    cfg = resolve_grok_config("xai", "bogus")
    assert cfg["variant"] == DEFAULT_GROK_IMAGINE_VARIANT == "quality"
    assert cfg["id"] == "grok-imagine-image-quality"
    # invalid provider -> kie (default)
    cfg2 = resolve_grok_config("bogus", "standard")
    assert cfg2["provider"] == DEFAULT_GROK_IMAGINE_PROVIDER == "kie"
    assert cfg2["id"] == "grok-imagine-image-2-0/text-to-image"
    # provider/variant None -> defaults
    cfg3 = resolve_grok_config(None, None)
    assert cfg3 == {"provider": "kie", "variant": "quality", "id": "grok-imagine-image-2-0/text-to-image"}


def test_validate_constants_against_variants():
    assert set(VALID_GROK_IMAGINE_PROVIDERS) == {"xai", "replicate", "kie"}
    assert set(VALID_GROK_IMAGINE_VARIANTS) == {"standard", "quality"}
    assert set(GROK_IMAGINE_VARIANTS) == set(VALID_GROK_IMAGINE_VARIANTS)


def test_resolve_non_grok_models():
    assert resolve_model_id("comfyui") == "comfyui"
    assert resolve_model_id("grok_video") == "grok-imagine-video"
    assert resolve_model_id("seedream") == "bytedance/seedream-5-lite"


def test_resolve_nano_banana_defaults_to_kie_banana2():
    cfg = resolve_nano_banana_config(None, None)
    assert cfg["provider"] == DEFAULT_NANO_BANANA_PROVIDER == "kie"
    assert cfg["variant"] == DEFAULT_NANO_BANANA_VARIANT == "banana2"
    assert cfg["id"] == "nano-banana-2"
    assert resolve_model_id("nano_banana") == "nano-banana-2"


def test_resolve_nano_banana_replicate_pro_pinned():
    cfg = resolve_nano_banana_config("replicate", "pro")
    assert cfg["provider"] == "replicate"
    assert cfg["variant"] == "pro"
    assert cfg["id"].startswith("google/nano-banana-pro:")
    assert resolve_model_id("nano_banana", provider="replicate", variant="pro") == cfg["id"]


def test_resolve_nano_banana_classic_kie_and_edit_slug():
    cfg = resolve_nano_banana_config("kie", "classic")
    assert cfg["id"] == "google/nano-banana"
    assert cfg["kie_edit_id"] == "google/nano-banana-edit"
    assert cfg["default_resolution"] is None


def test_invalid_nano_banana_falls_back():
    cfg = resolve_nano_banana_config("bogus", "ultra")
    assert cfg["provider"] == "kie"
    assert cfg["variant"] == "banana2"
    assert cfg["id"] == "nano-banana-2"


def test_nano_banana_constants():
    assert set(VALID_NANO_BANANA_PROVIDERS) == {"kie", "replicate"}
    assert set(VALID_NANO_BANANA_VARIANTS) == {"classic", "banana2", "pro"}
    assert set(NANO_BANANA_VARIANTS) == set(VALID_NANO_BANANA_VARIANTS)


def test_is_kie_nano_banana_model():
    assert is_kie_nano_banana_model("nano-banana-2")
    assert is_kie_nano_banana_model("nano-banana-pro")
    assert is_kie_nano_banana_model("google/nano-banana")
    assert is_kie_nano_banana_model("google/nano-banana-edit")
    assert not is_kie_nano_banana_model("grok-imagine-image-2-0/text-to-image")
    assert not is_kie_nano_banana_model("grok-imagine/image-to-image")
