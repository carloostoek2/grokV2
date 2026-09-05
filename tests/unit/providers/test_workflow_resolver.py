"""workflows.resolver tests: registry lookup + prompt/seed rendering."""

from __future__ import annotations

from grokbot.domain.generation import MediaType
from grokbot.providers.comfyui.workflows.resolver import has, lookup, render


def test_lookup_krea2_none_image_returns_template():
    spec = lookup("krea2", "none", MediaType.IMAGE)
    assert spec is not None
    assert spec.graph["4"]["class_type"] == "CLIPTextEncode"
    assert spec.save_nodes == ("19",)
    assert spec.supports_source is False


def test_lookup_unknown_combos_are_none():
    assert lookup("qwen", "none", MediaType.IMAGE) is None
    assert lookup("krea2", "none", MediaType.VIDEO) is None
    assert lookup("wan_i2v", "none", MediaType.VIDEO) is None


def test_has_mirrors_lookup():
    assert has("krea2", "none", MediaType.IMAGE) is True
    assert has("krea2", "none", MediaType.VIDEO) is False


def test_render_patches_prompt_and_seeds_without_mutating_template():
    spec = lookup("krea2", "none", MediaType.IMAGE)
    seed_values = iter([111, 222])

    graph = render(spec, "un gato rojo", lambda: next(seed_values))

    assert graph["4"]["inputs"]["text"] == "un gato rojo"
    assert graph["1"]["inputs"]["seed"] == 111
    assert graph["17"]["inputs"]["seed"] == 222
    # The cached template itself is untouched.
    assert spec.graph["4"]["inputs"]["text"] == ""
    assert spec.graph["1"]["inputs"]["seed"] == 0
