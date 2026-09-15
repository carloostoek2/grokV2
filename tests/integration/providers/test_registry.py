"""ProviderRegistry resolution tests (no network — resolve does not generate)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from grokbot.domain.catalog import MODELS
from grokbot.domain.generation import MediaType
from grokbot.domain.user_config import UserConfig, VideoConfig
from grokbot.providers.base import (
    ImageProvider,
    ProviderInputError,
    ProviderNotConfiguredError,
    VideoProvider,
)
from grokbot.providers.comfyui.provider import ComfyUIProvider
from grokbot.providers.kie_provider import KieProvider
from grokbot.providers.registry import ProviderRegistry
from grokbot.providers.replicate_provider import ReplicateProvider
from grokbot.providers.xai_provider import XaiProvider


def _registry(**overrides):
    defaults = {
        "xai": XaiProvider("x-key"),
        "replicate": ReplicateProvider("r-key"),
        "kie": KieProvider("k-key"),
        "comfyui": ComfyUIProvider("box"),
    }
    defaults.update(overrides)
    return ProviderRegistry(**defaults)


def _cfg(**over):
    return replace(UserConfig.defaults(), **over)


def test_image_grok_routes_to_xai():
    reg = _registry()
    cfg = _cfg(model="grok", grok_imagine_provider="xai", grok_imagine_variant="quality")

    res = reg.resolve_image(cfg)

    assert res.name == "xai"
    assert res.model_id == "grok-imagine-image-quality"
    assert res.available is True
    assert isinstance(res.provider, XaiProvider)
    assert isinstance(res.provider, ImageProvider)


def test_image_grok_routes_to_replicate():
    reg = _registry()
    cfg = _cfg(model="grok", grok_imagine_provider="replicate", grok_imagine_variant="standard")

    res = reg.resolve_image(cfg)

    assert res.name == "replicate"
    assert res.model_id == "xai/grok-imagine-image"
    assert isinstance(res.provider, ReplicateProvider)


def test_image_grok_routes_to_kie():
    reg = _registry()
    cfg = _cfg(model="grok", grok_imagine_provider="kie")

    res = reg.resolve_image(cfg)

    assert res.name == "kie"
    assert res.model_id == "grok-imagine-image-2-0/text-to-image"
    assert isinstance(res.provider, KieProvider)


@pytest.mark.parametrize("key", ["seedream", "faceswap"])
def test_image_seedream_and_faceswap_route_to_replicate(key):
    reg = _registry()
    cfg = _cfg(model=key)

    res = reg.resolve_image(cfg)

    assert res.name == "replicate"
    assert res.model_id == MODELS[key]["id"]


def test_image_comfyui_routes_to_comfyui():
    reg = _registry()
    res = reg.resolve_image(_cfg(model="comfyui"))

    assert res.name == "comfyui"
    assert res.model_id == "comfyui"
    assert isinstance(res.provider, ComfyUIProvider)


def test_image_grok_video_raises_input_error():
    reg = _registry()
    with pytest.raises(ProviderInputError):
        reg.resolve_image(_cfg(model="grok_video"))


def test_video_grok_video_replicate_routes_to_xai():
    reg = _registry()
    cfg = _cfg(model="grok_video", grok_imagine_provider="replicate")

    res = reg.resolve_video(cfg)

    assert res.name == "xai"  # replicate has no video backend (bot.py:664-669)
    assert res.model_id == "grok-imagine-video"
    assert isinstance(res.provider, VideoProvider)


def test_video_grok_video_kie_uses_cfg_video_model():
    reg = _registry()
    cfg = _cfg(
        model="grok_video",
        grok_imagine_provider="kie",
        video=VideoConfig(model="grok-imagine-video-1.5"),
    )

    res = reg.resolve_video(cfg)

    assert res.name == "kie"
    assert res.model_id == "grok-imagine-video-1.5"


def test_video_comfyui():
    reg = _registry()
    res = reg.resolve_video(_cfg(model="comfyui"))

    assert res.name == "comfyui"
    assert res.model_id == "comfyui"


def test_resolve_dispatches_by_media_type():
    reg = _registry()
    img_cfg = _cfg(model="grok", grok_imagine_provider="xai")
    vid_cfg = _cfg(model="grok_video", grok_imagine_provider="replicate")

    assert reg.resolve(img_cfg, MediaType.IMAGE) == reg.resolve_image(img_cfg)
    assert reg.resolve(vid_cfg, MediaType.VIDEO) == reg.resolve_video(vid_cfg)
    with pytest.raises(ProviderInputError):
        reg.resolve(img_cfg, None)


def test_unsupported_model_for_media_raises():
    reg = _registry()
    with pytest.raises(ProviderInputError):
        reg.resolve_image(_cfg(model="bogus_model"))
    with pytest.raises(ProviderInputError):
        reg.resolve_video(_cfg(model="seedream"))


def test_resolve_face_swap_resolves_replicate():
    reg = _registry()

    res = reg.resolve_face_swap()

    assert res.name == "replicate"
    assert res.model_id == MODELS["faceswap"]["id"]
    assert res.available is True
    assert isinstance(res.provider, ReplicateProvider)


def test_resolve_face_swap_raises_when_replicate_missing():
    reg = _registry(replicate=None)
    with pytest.raises(ProviderNotConfiguredError):
        reg.resolve_face_swap()


def test_kie_not_configured_raises_not_configured():
    reg = _registry(kie=KieProvider(""))
    cfg = _cfg(model="grok", grok_imagine_provider="kie")

    assert reg.is_available("kie") is False
    assert reg.provider("kie") is not None
    with pytest.raises(ProviderNotConfiguredError):
        reg.resolve_image(cfg)


def test_missing_provider_raises_not_configured():
    reg = _registry(xai=None)
    with pytest.raises(ProviderNotConfiguredError):
        reg.resolve_image(_cfg(model="grok", grok_imagine_provider="xai"))


def test_comfyui_empty_host_raises_not_configured():
    reg = _registry(comfyui=ComfyUIProvider(""))

    assert reg.is_available("comfyui") is False
    with pytest.raises(ProviderNotConfiguredError):
        reg.resolve_image(_cfg(model="comfyui"))
    with pytest.raises(ProviderNotConfiguredError):
        reg.resolve_video(_cfg(model="comfyui"))


def test_provider_accessor():
    reg = _registry(kie=KieProvider(""))
    assert isinstance(reg.provider("xai"), XaiProvider)
    assert reg.provider("kie").available is False
    assert reg.provider("nonexistent") is None
    assert reg.is_available("nonexistent") is False


def test_image_nano_banana_routes_to_kie_by_default():
    reg = _registry()
    cfg = _cfg(model="nano_banana")

    res = reg.resolve_image(cfg)

    assert res.name == "kie"
    assert res.model_id == "nano-banana-2"


def test_image_nano_banana_routes_to_replicate_pro():
    reg = _registry()
    cfg = _cfg(
        model="nano_banana",
        nano_banana_provider="replicate",
        nano_banana_variant="pro",
    )

    res = reg.resolve_image(cfg)

    assert res.name == "replicate"
    assert res.model_id.startswith("google/nano-banana-pro:")
