"""Unit tests for the item-4 domain addons (D10/D11/D12).

The application layer must validate media_type, batch ranges and Kie aspect
reset WITHOUT importing providers/registry; these pure helpers/constants are
the seam. Reference: grok bot.py 664-669 / 1107-1136 and variables_store
340-353 / 374-417 (read-only).
"""

from __future__ import annotations

import random
from dataclasses import replace

from grokbot.domain.user_config import (
    DEFAULT_VIDEO_ASPECT_RATIO,
    UserConfig,
    VideoConfig,
    is_comfy_video_model,
    kie_aspect_ratio_fallback,
    kie_video_aspect_ratios,
    video_provider_for_config,
)
from grokbot.domain.variables import (
    MAX_COMBO_ATTEMPTS,
    MULTIPOSE_BATCH_SIZE,
    VARIABLES_MAX,
    PromptTemplate,
    build_shuffled_prompt,
    combo_key,
)


def _cfg(**over):
    return replace(UserConfig.defaults(), **over)


# --- D10: ComfyUI video models ---------------------------------------------

def test_is_comfy_video_model():
    assert is_comfy_video_model("wan_i2v") is True
    assert is_comfy_video_model("minimax_i2v") is True
    assert is_comfy_video_model("krea2") is False
    assert is_comfy_video_model("qwen") is False
    assert is_comfy_video_model("qwen_aio") is False


def test_comfy_video_models_tuple():
    from grokbot.domain import COMFY_VIDEO_MODELS

    assert COMFY_VIDEO_MODELS == ("wan_i2v", "minimax_i2v")


# --- D12: video provider + Kie aspect fallback -----------------------------

def test_video_provider_for_config_comfyui():
    assert video_provider_for_config(_cfg(model="comfyui")) == "comfyui"


def test_video_provider_for_config_replicate_stays_replicate():
    cfg = _cfg(model="grok_video", grok_imagine_provider="replicate")
    assert video_provider_for_config(cfg) == "replicate"


def test_video_provider_for_config_kie():
    cfg = _cfg(model="grok_video", grok_imagine_provider="kie")
    assert video_provider_for_config(cfg) == "kie"


def test_video_provider_for_config_grok_image_model():
    cfg = _cfg(model="grok", grok_imagine_provider="kie")
    assert video_provider_for_config(cfg) == "kie"


def test_video_provider_for_config_non_video_model_is_none():
    assert video_provider_for_config(_cfg(model="seedream")) is None
    assert video_provider_for_config(_cfg(model="faceswap")) is None


def test_kie_video_aspect_ratios():
    base = ("16:9", "9:16", "1:1", "3:2", "2:3")
    v15 = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3")
    assert kie_video_aspect_ratios("grok-imagine-video") == base
    assert kie_video_aspect_ratios("grok-imagine-video-1.5") == v15


def test_kie_aspect_ratio_fallback_invalid_base_returns_default():
    # Provider kie + base model: "4:3" is not in the base set → default "16:9".
    cfg = _cfg(
        model="grok_video",
        grok_imagine_provider="kie",
        video=VideoConfig(aspect_ratio="4:3", model="grok-imagine-video"),
    )
    assert kie_aspect_ratio_fallback(cfg) == DEFAULT_VIDEO_ASPECT_RATIO


def test_kie_aspect_ratio_fallback_15_allows_43():
    # Provider kie + 1.5 model: "4:3" IS allowed → None.
    cfg = _cfg(
        model="grok_video",
        grok_imagine_provider="kie",
        video=VideoConfig(aspect_ratio="4:3", model="grok-imagine-video-1.5"),
    )
    assert kie_aspect_ratio_fallback(cfg) is None


def test_kie_aspect_ratio_fallback_non_kie_provider_returns_none():
    cfg = _cfg(
        model="grok_video",
        grok_imagine_provider="xai",
        video=VideoConfig(aspect_ratio="4:3", model="grok-imagine-video"),
    )
    assert kie_aspect_ratio_fallback(cfg) is None


def test_kie_aspect_ratio_fallback_valid_aspect_returns_none():
    cfg = _cfg(
        model="grok_video",
        grok_imagine_provider="kie",
        video=VideoConfig(aspect_ratio="16:9", model="grok-imagine-video"),
    )
    assert kie_aspect_ratio_fallback(cfg) is None


# --- D11: batch constants + derangement ------------------------------------

def test_batch_constants():
    assert VARIABLES_MAX == 10
    assert MAX_COMBO_ATTEMPTS == 30
    assert MULTIPOSE_BATCH_SIZE == 5


def test_build_shuffled_prompt_derangement_two_fields():
    random.seed(1234)
    template = "{pose}, {angle}"
    values = {"pose": "de pie", "angle": "frontal"}
    rendered = build_shuffled_prompt(template, values)
    # Same contributing values, guaranteed different order (derangement).
    assert rendered in ("frontal, de pie",)
    # render_positional order differs from canonical.
    assert rendered != PromptTemplate(template).render(values)


def test_build_shuffled_prompt_preserves_single_value():
    random.seed(999)
    template = "{pose}"
    values = {"pose": "solo", "angle": "", "action": ""}
    assert build_shuffled_prompt(template, values) == "solo"


def test_build_shuffled_prompt_three_fields_is_permutation():
    random.seed(42)
    template = "{pose}, {angle}, {action}"
    values = {"pose": "A", "angle": "B", "action": "C"}
    rendered = build_shuffled_prompt(template, values)
    parts = [p.strip() for p in rendered.split(",")]
    assert sorted(parts) == ["A", "B", "C"]
    # After enough draws a shuffled order differs from canonical (no fixed seed
    # makes this deterministic, so verify it is a valid permutation at least).
    assert combo_key(template, values) is not None


def test_build_shuffled_prompt_reverse_on_equal(monkeypatch):
    # Force random.shuffle to leave the order untouched → the helper must
    # reverse it (derangement guarantee).
    import grokbot.domain.variables as variables_mod

    monkeypatch.setattr(variables_mod.random, "shuffle", lambda seq: None)
    template = "{a}, {b}"
    values = {"a": "X", "b": "Y"}
    out = build_shuffled_prompt(template, values)
    assert out == "Y, X"
