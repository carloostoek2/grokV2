"""Resolvedor de flujos de workflow ComfyUI (API-format) para el provider.

Cada **flujo** es un archivo JSON en ``templates/``: un grafo API-format de
ComfyUI (con su modelo/LoRA **horneados** en los nodos) más una clave top-level
``_meta`` que lo describe y declara qué nodos parchear por request::

    "_meta": {
      "id": "grok_style",
      "name": "Grok Style",
      "media_type": "image",          # "image" | "video"
      "positive_node": "4",           # nodo cuyo input "text" recibe el prompt
      "negative_node": "27",          # (o null si no hay)
      "seed_nodes": ["1", "17"],      # nodos KSampler a randomizar
      "save_nodes": ["19"],           # nodos cuya media final se descarga
      "supports_source": false        # True si admite img2img/i2v (LoadImage)
    }

``_meta`` se separa del grafo al cargar y **nunca** viaja a ComfyUI en el
enqueue. Añadir un flujo = dejar caer ``templates/<id>.json`` con su ``_meta``
(el resolver lo descubre solo). El id debe estar además en
``domain.user_config.VALID_COMFYUI_MODELS`` para que la config lo acepte.

v1: un solo flujo ``grok_style`` (``krea2_t2i.json``) — imagen txt2img.
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
    # Nodo cuyo input ``text`` recibe el prompt positivo del usuario.
    positive_node: str = "4"
    # Nodo cuyo input ``text`` guarda el negativo (default de plantilla).
    negative_node: str | None = None
    # Nodos KSampler cuyo input ``seed`` se randomiza por request.
    seed_nodes: tuple[str, ...] = ()
    # Nodos cuya media final (images/videos) se descarga vía ``/view``.
    save_nodes: tuple[str, ...] = ()
    # True cuando la plantilla admite img2img/i2v (carga foto fuente).
    supports_source: bool = False


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
    flow = Flow(
        id=str(meta["id"]),
        name=str(meta.get("name") or meta["id"]),
        file=file_name,
        graph=raw,
        media_type=MediaType(str(meta["media_type"])),
        positive_node=str(meta.get("positive_node", "4")),
        negative_node=str(meta["negative_node"]) if meta.get("negative_node") else None,
        seed_nodes=tuple(str(x) for x in meta.get("seed_nodes", ())),
        save_nodes=tuple(str(x) for x in meta.get("save_nodes", ())),
        supports_source=bool(meta.get("supports_source", False)),
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
    """Copia el grafo y parchea prompt + seeds para un request (sin ``_meta``)."""
    graph = copy.deepcopy(flow.graph)
    pos = graph.get(flow.positive_node)
    if pos is None or "text" not in pos.get("inputs", {}):
        raise ValueError(f"template {flow.file}: falta nodo prompt {flow.positive_node}")
    pos["inputs"]["text"] = prompt
    for node_id in flow.seed_nodes:
        node = graph.get(node_id)
        if node is not None and "seed" in node.get("inputs", {}):
            node["inputs"]["seed"] = seed_factory()
    return graph
