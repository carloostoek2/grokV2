"""workflows.resolver tests: flujo por id (get_flow) + render de prompt/seeds."""

from __future__ import annotations

from grokbot.domain.generation import MediaType
from grokbot.providers.comfyui.workflows.resolver import flows, get_flow, render


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
    assert ids == {"grok_style", "donut_face"}


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
