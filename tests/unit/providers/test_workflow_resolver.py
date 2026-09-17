"""workflows.resolver tests: flujo por id (get_flow) + render de prompt/seeds."""

from __future__ import annotations

import json
import logging

import pytest

from grokbot.domain.generation import MediaType
from grokbot.providers.comfyui.workflows import resolver as resolver_mod
from grokbot.providers.comfyui.workflows.resolver import (
    configure_workflow_source,
    flows,
    get_flow,
    render,
    reset_workflow_source,
)


def test_get_flow_grok_style_returns_flow():
    flow = get_flow("grok_style")
    assert flow is not None
    assert flow.name == "Grok Style"
    assert flow.media_type is MediaType.IMAGE
    assert flow.graph["4"]["class_type"] == "CLIPTextEncode"
    assert flow.save_nodes == ("19",)
    assert flow.supports_source is False


def test_flows_returns_registered_flows():
    ids = {f.id for f in flows()}
    assert ids == {"grok_style", "donut_face", "agil_solo", "agil_nsfw", "agil_moody", "agil_edit_qwen", "agil_edit_nsfw"}


def test_get_flow_donut_face():
    flow = get_flow("donut_face")
    assert flow is not None
    assert flow.name == "Donut Face"
    assert flow.media_type is MediaType.IMAGE
    # El prompt va a un Wildcard Processor (input "prompt"), no a CLIPTextEncode.
    assert flow.positive_node == "56"
    assert flow.positive_input == "prompt"
    assert flow.save_nodes == ("914",)


def test_render_donut_face_patches_prompt_input_and_seed():
    flow = get_flow("donut_face")
    graph = render(flow, "una chica sonriendo en un espejo", lambda: 12345)
    assert graph["56"]["inputs"]["prompt"] == "una chica sonriendo en un espejo"
    assert graph["195"]["inputs"]["seed"] == 12345
    # Ni el _meta de nodo (title del export) ni el top-level viajan al grafo.
    assert "_meta" not in graph
    assert all("_meta" not in node for node in graph.values())
    # La plantilla cacheada no se muta.
    assert flow.graph["56"]["inputs"]["prompt"].startswith("hourglass")


def test_get_flow_unknown_is_none():
    # Combos legacy / no existentes ya no resuelven a ninguna plantilla.
    for unknown in ("qwen", "qwen_aio", "wan_i2v", "minimax_i2v", "krea2", "realvisxl"):
        assert get_flow(unknown) is None


def test_render_patches_prompt_and_seeds_without_mutating_template():
    flow = get_flow("grok_style")
    seed_values = iter([111, 222])

    graph = render(flow, "un gato rojo", lambda: next(seed_values))

    assert graph["4"]["inputs"]["text"] == "un gato rojo"
    assert graph["1"]["inputs"]["seed"] == 111
    assert graph["17"]["inputs"]["seed"] == 222
    # ``_meta`` nunca viaja en el grafo que se encola a ComfyUI.
    assert "_meta" not in graph
    # El template cacheado queda intacto.
    assert flow.graph["4"]["inputs"]["text"] == ""
    assert flow.graph["1"]["inputs"]["seed"] == 0


def test_get_flow_agil_solo():
    flow = get_flow("agil_solo")
    assert flow is not None
    assert flow.name == "Ágil solo"
    assert flow.media_type is MediaType.IMAGE
    assert flow.positive_node == "5"
    assert flow.seed_nodes == ("8",)
    assert flow.save_nodes == ("10",)
    assert flow.supports_source is False
    # Variante base: sin LoRA (no existe el nodo LoraLoader "4").
    assert "4" not in flow.graph
    assert not any(
        isinstance(n, dict) and n.get("class_type") == "LoraLoader"
        for n in flow.graph.values()
    )


def test_render_agil_solo_patches_prompt_and_seed():
    flow = get_flow("agil_solo")
    graph = render(flow, "un paisaje al atardecer", lambda: 12345)
    assert graph["5"]["inputs"]["text"] == "un paisaje al atardecer"
    assert graph["8"]["inputs"]["seed"] == 12345
    # Resolución FIJA 896x1600, sin ResolutionSelector.
    assert graph["7"]["inputs"]["width"] == 896
    assert graph["7"]["inputs"]["height"] == 1600
    # ``_meta`` nunca viaja en el grafo que se encola a ComfyUI.
    assert "_meta" not in graph
    assert all("_meta" not in node for node in graph.values())
    # La plantilla cacheada no se muta.
    assert flow.graph["5"]["inputs"]["text"] == ""
    assert flow.graph["8"]["inputs"]["seed"] == 0


def test_get_flow_agil_nsfw_has_lora():
    flow = get_flow("agil_nsfw")
    assert flow is not None
    assert flow.name == "Ágil NSFW"
    assert flow.media_type is MediaType.IMAGE
    assert flow.positive_node == "5"
    assert flow.seed_nodes == ("8",)
    assert flow.save_nodes == ("10",)
    assert flow.supports_source is False
    lora = flow.graph["4"]
    assert lora["class_type"] == "LoraLoader"
    assert lora["inputs"]["lora_name"] == "Krea2NSFWV4.safetensors"
    assert lora["inputs"]["strength_model"] == 1.0
    assert lora["inputs"]["strength_clip"] == 1.0
    assert flow.graph["8"]["inputs"]["model"] == ["4", 0]
    assert flow.graph["5"]["inputs"]["clip"] == ["4", 1]


def test_render_agil_nsfw_patches_prompt_and_seed():
    flow = get_flow("agil_nsfw")
    graph = render(flow, "retrato editorial", lambda: 987)
    assert graph["5"]["inputs"]["text"] == "retrato editorial"
    assert graph["8"]["inputs"]["seed"] == 987
    assert "_meta" not in graph
    assert all("_meta" not in node for node in graph.values())
    # El LoRA sigue cableado tras el render y la plantilla no se muta.
    assert graph["8"]["inputs"]["model"] == ["4", 0]
    assert flow.graph["8"]["inputs"]["seed"] == 0


def test_get_flow_agil_moody_matches_platform_graph():
    """Ágil Moody must mirror the Vast UI Moody workflow (not the old minimal KSampler)."""
    flow = get_flow("agil_moody")
    assert flow is not None
    assert flow.name == "Ágil Moody"
    assert flow.media_type is MediaType.IMAGE
    assert flow.positive_node == "627"
    assert flow.seed_nodes == ("851",)
    assert flow.save_nodes == ("732",)
    assert flow.supports_source is False
    g = flow.graph
    assert g["761"]["class_type"] == "UNETLoader"
    assert g["761"]["inputs"]["unet_name"] == (
        "Moody-Krea-Mix-v4.1G_00001__clean_nvfp4.safetensors"
    )
    assert g["599"]["class_type"] == "KSamplerAdvanced"
    assert g["599"]["inputs"]["sampler_name"] == "euler_ancestral"
    assert g["599"]["inputs"]["scheduler"] == "beta"
    assert g["599"]["inputs"]["steps"] == 8
    assert g["599"]["inputs"]["cfg"] == 1.0
    assert g["763"]["class_type"] == "ConditioningZeroOut"
    assert g["857"]["class_type"] == "ResolutionSelector"
    assert g["857"]["inputs"]["aspect_ratio"] == "9:16 (Portrait Widescreen)"
    assert g["857"]["inputs"]["megapixels"] == 2.0
    assert g["857"]["inputs"]["multiple"] == 32
    assert g["851"]["class_type"] == "SeedNode"
    assert g["698"]["inputs"]["width"] == ["857", 0]
    assert g["698"]["inputs"]["height"] == ["857", 1]
    # UI LoRA is bypassed; bot graph must not include it.
    assert not any(
        isinstance(n, dict) and n.get("class_type") == "LoraLoaderModelOnly"
        for n in g.values()
    )


def test_render_agil_moody_patches_prompt_and_seednode():
    flow = get_flow("agil_moody")
    graph = render(flow, "retrato moody", lambda: 4242)
    assert graph["627"]["inputs"]["text"] == "retrato moody"
    assert graph["851"]["inputs"]["seed"] == 4242
    assert graph["599"]["inputs"]["noise_seed"] == ["851", 0]
    assert graph["732"]["inputs"]["filename_prefix"] == "grokbot/comfyui"
    assert "_meta" not in graph
    assert all("_meta" not in node for node in graph.values())
    assert flow.graph["627"]["inputs"]["text"] == ""
    assert flow.graph["851"]["inputs"]["seed"] == 0


# ---------------------------------------------------------------------------
# Remote workflow source (Vast SSH / injected fetch) + embed fallback
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_workflow_source_after_each():
    """Keep remote config from leaking across tests."""
    yield
    reset_workflow_source()
    resolver_mod._remote_cache.clear()


def _minimal_agil_moody_payload(*, unet_name: str = "REMOTE_UNET.safetensors") -> dict:
    """Small API-format graph compatible with agil_moody _meta (prompt/seed/save)."""
    return {
        "_meta": {
            "id": "agil_moody",
            "name": "Ágil Moody",
            "media_type": "image",
            "positive_node": "627",
            "seed_nodes": ["851"],
            "save_nodes": ["732"],
            "supports_source": False,
        },
        "627": {"inputs": {"text": "", "clip": ["x", 0]}, "class_type": "CLIPTextEncode"},
        "851": {"inputs": {"seed": 0}, "class_type": "SeedNode"},
        "732": {
            "inputs": {"filename_prefix": "grokbot/comfyui", "images": ["y", 0]},
            "class_type": "SaveImage",
        },
        "761": {
            "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
            "class_type": "UNETLoader",
        },
    }


def test_remote_load_uses_injected_fetch_and_sets_origin():
    payload = _minimal_agil_moody_payload()
    calls = {"n": 0}

    def fetch(flow_id: str) -> str | None:
        calls["n"] += 1
        assert flow_id == "agil_moody"
        return json.dumps(payload)

    configure_workflow_source(
        source="remote",
        host="vast.example",
        workflows_dir="/workspace/ComfyUI/user/default/api_workflows",
        cache_ttl=60,
        fetch=fetch,
    )
    flow = get_flow("agil_moody")
    assert flow is not None
    assert flow.origin == "remote"
    assert flow.graph["761"]["inputs"]["unet_name"] == "REMOTE_UNET.safetensors"
    # Cache hit: second call must not re-fetch.
    flow2 = get_flow("agil_moody")
    assert flow2 is flow or flow2.graph["761"]["inputs"]["unet_name"] == "REMOTE_UNET.safetensors"
    assert calls["n"] == 1
    graph = render(flow, "hola remoto", lambda: 99)
    assert graph["627"]["inputs"]["text"] == "hola remoto"
    assert graph["851"]["inputs"]["seed"] == 99
    assert "_meta" not in graph


def test_remote_missing_falls_back_to_embed(caplog):
    def fetch(flow_id: str) -> str | None:
        return None

    configure_workflow_source(
        source="remote",
        host="vast.example",
        cache_ttl=30,
        fetch=fetch,
    )
    with caplog.at_level(logging.WARNING):
        flow = get_flow("agil_moody")
    assert flow is not None
    assert flow.origin == "embed"
    # Embed template keeps the platform Moody UNET pin from the repo.
    assert "Moody-Krea-Mix" in flow.graph["761"]["inputs"]["unet_name"]
    assert any("falling back to embed" in r.message for r in caplog.records)


def test_embed_source_ignores_remote_fetch():
    def fetch(flow_id: str) -> str | None:
        raise AssertionError("fetch must not be called in embed mode")

    configure_workflow_source(
        source="embed",
        host="vast.example",
        fetch=fetch,
    )
    flow = get_flow("agil_moody")
    assert flow is not None
    assert flow.origin == "embed"


def test_remote_cache_expires_and_refetches():
    payloads = [
        _minimal_agil_moody_payload(unet_name="FIRST.safetensors"),
        _minimal_agil_moody_payload(unet_name="SECOND.safetensors"),
    ]
    calls = {"n": 0}

    def fetch(flow_id: str) -> str | None:
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        return json.dumps(payloads[idx])

    configure_workflow_source(
        source="remote",
        host="vast.example",
        cache_ttl=0,  # expire immediately
        fetch=fetch,
    )
    f1 = get_flow("agil_moody")
    f2 = get_flow("agil_moody")
    assert f1 is not None and f2 is not None
    assert f1.graph["761"]["inputs"]["unet_name"] == "FIRST.safetensors"
    assert f2.graph["761"]["inputs"]["unet_name"] == "SECOND.safetensors"
    assert calls["n"] == 2


def test_remote_rejects_path_traversal_flow_id():
    def fetch(flow_id: str) -> str | None:
        raise AssertionError(f"must not fetch unsafe id {flow_id!r}")

    configure_workflow_source(
        source="remote",
        host="vast.example",
        fetch=fetch,
    )
    assert get_flow("../etc/passwd") is None
    assert get_flow("agil_moody/../../x") is None


def test_get_flow_agil_edit_qwen_supports_source():
    flow = get_flow("agil_edit_qwen")
    assert flow is not None
    assert flow.id == "agil_edit_qwen"
    assert flow.supports_source is True
    assert flow.source_node == "1"
    assert flow.positive_input == "prompt"


def test_get_flow_agil_edit_nsfw_supports_source():
    flow = get_flow("agil_edit_nsfw")
    assert flow is not None
    assert flow.id == "agil_edit_nsfw"
    assert flow.supports_source is True
    assert flow.source_node == "2"
