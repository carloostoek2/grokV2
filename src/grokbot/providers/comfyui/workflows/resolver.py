"""Resolvedor de flujos de workflow ComfyUI (API-format) para el provider.

Cada **flujo** es un archivo JSON en ``templates/``: un grafo API-format de
ComfyUI (con su modelo/LoRA **horneados** en los nodos) más una clave top-level
``_meta`` que lo describe y declara qué nodos parchear por request::

    "_meta": {
      "id": "grok_style",
      "name": "Grok Style",
      "media_type": "image",          # "image" | "video"
      "positive_node": "56",          # nodo cuyo input recibe el prompt del usuario
      "positive_input": "prompt",     # input del nodo (default "text", p. ej. CLIPTextEncode)
      "negative_node": "27",          # (o null si no hay)
      "seed_nodes": ["195"],          # nodos con input "seed" (ints) a randomizar
      "save_nodes": ["914"],          # nodos cuya media final se descarga
      "supports_source": false        # True si admite img2img/i2v (LoadImage)
    }

``_meta`` (top-level) se separa del grafo al cargar y **nunca** viaja a ComfyUI
en el enqueue; el ``_meta`` de *nodo* (title del export del canvas) también se
descarta. Añadir un flujo = dejar caer ``templates/<id>.json`` con su ``_meta``
(el resolver lo descubre solo). El id debe estar además en
``domain.user_config.VALID_COMFYUI_MODELS`` para que la config lo acepte.

v1: ``grok_style`` (``krea2_t2i.json``, txt2img simple) y ``donut_face``
(``donut_face.json``, pipeline Krea2 con upscale + Face Detailer).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from grokbot.domain.generation import MediaType

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class Flow:
    """Un flujo de ComfyUI: template API-format + mapa de nodos a parchear."""

    id: str
    name: str
    file: str
    graph: dict
    media_type: MediaType
    # Nodo cuyo input recibe el prompt positivo del usuario.
    positive_node: str = "4"
    # Input de ``positive_node`` que recibe el prompt (default "text").
    positive_input: str = "text"
    # Nodo cuyo input ``text`` guarda el negativo (default de plantilla).
    negative_node: str | None = None
    # Nodos con input "seed" (int) a randomizar por request.
    seed_nodes: tuple[str, ...] = ()
    # Nodos cuya media final (images/videos) se descarga vía ``/view``.
    save_nodes: tuple[str, ...] = ()
    # True cuando la plantilla admite img2img/i2v (carga foto fuente).
    supports_source: bool = False
    # Timeout de generación en segundos (override del default por media_type).
    timeout: int | None = None


def _load_template(file_name: str) -> tuple[Flow, dict]:
    """Cargar un template: separa ``_meta`` (→ Flow) del grafo limpio.

    El grafo devuelto NO contiene ``_meta``: es lo que se encola a ComfyUI.
    """
    raw = json.loads((TEMPLATES_DIR / file_name).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"template {file_name}: debe ser un objeto JSON")
    meta = raw.pop("_meta", None)
    if not isinstance(meta, dict) or not meta.get("id"):
        raise ValueError(f"template {file_name}: falta '_meta.id'")
    # Descartar el ``_meta`` por nodo (title) que trae el export del canvas.
    for node in raw.values():
        if isinstance(node, dict):
            node.pop("_meta", None)
    flow = Flow(
        id=str(meta["id"]),
        name=str(meta.get("name") or meta["id"]),
        file=file_name,
        graph=raw,
        media_type=MediaType(str(meta["media_type"])),
        positive_node=str(meta.get("positive_node", "4")),
        positive_input=str(meta.get("positive_input", "text")),
        negative_node=str(meta["negative_node"]) if meta.get("negative_node") else None,
        seed_nodes=tuple(str(x) for x in meta.get("seed_nodes", ())),
        save_nodes=tuple(str(x) for x in meta.get("save_nodes", ())),
        supports_source=bool(meta.get("supports_source", False)),
        timeout=int(meta["timeout"]) if meta.get("timeout") else None,
    )
    return flow, raw


@lru_cache(maxsize=None)
def _flows_cached() -> tuple[Flow, ...]:
    """Descubrir los flujos de ``templates/`` (un ``.json`` = un flujo)."""
    flows: list[Flow] = []
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        flow, _graph = _load_template(path.name)
        flows.append(flow)
    return tuple(flows)


def flows() -> tuple[Flow, ...]:
    """Todos los flujos disponibles (orden estable por nombre de archivo)."""
    return _flows_cached()


def get_flow(flow_id: str) -> Flow | None:
    """Resolver un flujo por su id, o None si no hay template con ese id."""
    for flow in flows():
        if flow.id == flow_id:
            return flow
    return None


def render(flow: Flow, prompt: str, seed_factory) -> dict:
    """Copia el grafo y parchea prompt + seeds para un request (sin ``_meta``).

    El prompt se escribe en ``flow.positive_node`` / ``flow.positive_input``
    (default ``"text"`` para CLIPTextEncode; p. ej. ``"prompt"`` en un
    Wildcard Processor).
    """
    graph = copy.deepcopy(flow.graph)
    pos = graph.get(flow.positive_node)
    if pos is None or flow.positive_input not in pos.get("inputs", {}):
        raise ValueError(
            f"template {flow.file}: falta nodo prompt {flow.positive_node}"
            f"/{flow.positive_input}"
        )
    pos["inputs"][flow.positive_input] = prompt
    for node_id in flow.seed_nodes:
        node = graph.get(node_id)
        if node is not None and "seed" in node.get("inputs", {}):
            node["inputs"]["seed"] = seed_factory()
    return graph
