"""Tests del motor de inyección de LoRAs (puro, sin red).

Usa las plantillas reales del repo: el contrato es que los LoRAs horneados del
flujo no se tocan y el stack del usuario se inserta al final de la cadena de
modelo. Los flujos HQ/SDXL son los casos interesantes (varias ramas, switches).
"""

from __future__ import annotations

import copy
import json

import pytest

from grokbot.domain.user_config import LoraStackEntry
from grokbot.providers.comfyui.workflows.loras import (
    LoraInjectionError,
    apply_lora_stack,
    find_model_anchor,
)
from grokbot.providers.comfyui.workflows.resolver import TEMPLATES_DIR


def _graph(file_name: str) -> dict:
    raw = json.loads((TEMPLATES_DIR / file_name).read_text(encoding="utf-8"))
    meta = raw.pop("_meta", {})
    for node in raw.values():
        if isinstance(node, dict):
            node.pop("_meta", None)
    return raw, meta


def _entry(name: str = "style.safetensors", strength: float = 0.7) -> LoraStackEntry:
    return LoraStackEntry(name=name, strength=strength)


def test_empty_stack_returns_the_same_graph():
    graph, _ = _graph("agil_solo.json")
    before = copy.deepcopy(graph)

    out = apply_lora_stack(graph, ())

    assert out is graph
    assert graph == before


def test_injects_after_the_unet_when_the_flow_has_no_lora():
    graph, meta = _graph("agil_solo.json")

    apply_lora_stack(graph, (_entry(),), positive_node=meta["positive_node"])

    node = graph["900000"]
    assert node["class_type"] == "LoraLoaderModelOnly"
    assert node["inputs"]["model"] == ["1", 0]
    assert node["inputs"]["lora_name"] == "style.safetensors"
    assert node["inputs"]["strength_model"] == 0.7
    # El sampler ahora consume el LoRA; el UNET original sigue intacto.
    assert graph["8"]["inputs"]["model"] == ["900000", 0]
    assert graph["1"]["class_type"] == "UNETLoader"


def test_keeps_baked_loras_and_appends_after_the_last_model_node():
    graph, meta = _graph("grok_edit.json")
    baked = {nid: copy.deepcopy(graph[nid]) for nid in ("5", "6", "7")}

    apply_lora_stack(graph, (_entry(),), positive_node=meta["positive_node"])

    # Las LoRAs del flujo (identidad + estilo + patch de edición) no cambian.
    assert {nid: graph[nid] for nid in baked} == baked
    # El nodo nuevo cuelga del último productor de MODEL y el sampler lo lee.
    assert graph["900000"]["inputs"]["model"] == ["7", 0]
    assert graph["13"]["inputs"]["model"] == ["900000", 0]


def test_multiple_entries_chain_in_order():
    graph, meta = _graph("agil_solo.json")

    apply_lora_stack(
        graph,
        (_entry("a.safetensors", 0.5), _entry("b.safetensors", 1.0)),
        positive_node=meta["positive_node"],
    )

    assert graph["900000"]["inputs"]["model"] == ["1", 0]
    assert graph["900001"]["inputs"]["model"] == ["900000", 0]
    assert graph["8"]["inputs"]["model"] == ["900001", 0]


def test_rewires_all_samplers_that_share_the_anchor():
    graph, meta = _graph("krea2_t2i.json")

    apply_lora_stack(graph, (_entry(),), positive_node=meta["positive_node"])

    # Grok Style tiene sampler base (1) y refine (17); ambos consumían la LoRA
    # horneada (100) y ahora consumen el stack del usuario.
    assert graph["1"]["inputs"]["model"] == ["900000", 0]
    assert graph["17"]["inputs"]["model"] == ["900000", 0]
    assert graph["100"]["inputs"]["lora_name"] == "grokstyle_krea2_v2.safetensors"


def test_does_not_touch_the_seedvr_branch_of_hq_flows():
    graph, meta = _graph("agil_edit_hq.json")

    apply_lora_stack(graph, (_entry(),), positive_node=meta["positive_node"])

    # La rama principal (switch de modo) recibe el LoRA...
    assert graph["433:3"]["inputs"]["model"] == ["900000", 0]
    assert graph["900000"]["inputs"]["model"] == ["433:440", 0]
    # ...y el sampler de restauración SeedVR2 sigue con su UNET, sin el LoRA.
    assert graph["sv:54"]["inputs"]["model"] == ["sv:52", 0]
    assert "900000" not in graph["sv:54"]["inputs"]["model"]


def test_anchor_detection_prefers_the_positive_sampler():
    graph, meta = _graph("flux1_dev_t2i.json")

    assert find_model_anchor(graph, positive_node=meta["positive_node"]) == "2"


def test_no_model_link_raises():
    graph = {"1": {"class_type": "EmptyLatentImage", "inputs": {}}}

    with pytest.raises(LoraInjectionError):
        apply_lora_stack(graph, (_entry(),))


def test_fresh_ids_never_collide_with_existing_nodes():
    graph = {
        "900000": {"class_type": "UNETLoader", "inputs": {"unet_name": "x"}},
        "2": {"class_type": "KSampler", "inputs": {"model": ["900000", 0]}},
    }

    apply_lora_stack(graph, (_entry(),))

    assert "900001" in graph
    assert graph["2"]["inputs"]["model"] == ["900001", 0]


def test_donut_face_applies_to_both_model_paths():
    graph, meta = _graph("donut_face.json")

    apply_lora_stack(graph, (_entry(),), positive_node=meta["positive_node"])

    # El sampler y los detailers consumen DOS modelos: ``model`` (guidance) y
    # ``edit_model`` (LoRA de identidad horneada). El ancla es el tronco común
    # (DonutApplyLoRAStack, 1055), así que ambas ramas reciben el stack extra.
    assert graph["900000"]["inputs"]["model"] == ["1055", 0]
    assert graph["1014:1031"]["inputs"]["model"] == ["900000", 0]
    assert graph["883"]["inputs"]["model"] == ["900000", 0]
    # El sampler sigue leyendo las mismas ramas; el LoRA extra va dentro.
    assert graph["1014:993"]["inputs"]["model"] == ["1014:1031", 0]
    assert graph["1014:993"]["inputs"]["edit_model"] == ["883", 0]
