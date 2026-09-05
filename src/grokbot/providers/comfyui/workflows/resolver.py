"""Resolvedor de plantillas de workflow ComfyUI (API-format) para el provider.

Cada entrada del registro declara QUÉ plantilla JSON aplicar para una tupla
``(model, lora, media_type)`` y QUÉ nodos/inputs son los puntos de parche
declarativos (prompt positivo, nodos de seed, nodos que guardan la media final).
El grafo viaja versionado en ``templates/*.json`` (API-format); el provider
copia la plantilla, parchea el prompt/seed por request y la encola vía el
``ComfyApiClient``.

v1 (validación de flujo): solo ``krea2``/``none``/imagen → ``krea2_t2i.json``.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from grokbot.domain.generation import MediaType

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Nodo por defecto de guardado de imagen dentro de ``krea2_t2i.json``.
_SAVE_NODE = "19"


@dataclass(frozen=True)
class TemplateSpec:
    """Plantilla API-format + mapa de nodos/inputs que el provider parchea."""

    key: str
    graph: dict
    media_type: MediaType
    # Nodo cuyo input ``text`` recibe el prompt positivo del usuario.
    positive_node: str = "4"
    # Nodo cuyo input ``text`` guarda el negativo (default de plantilla).
    negative_node: str | None = "27"
    # Nodos KSampler cuyo input ``seed`` se randomiza por request.
    seed_nodes: tuple[str, ...] = ("1", "17")
    # Nodos cuya media final (images/videos) se descarga vía ``/view``.
    save_nodes: tuple[str, ...] = (_SAVE_NODE,)
    # True cuando la plantilla admite img2img/i2v (carga foto fuente).
    supports_source: bool = False


# ``(model_id, lora, media_key)`` → archivo de plantilla + ids de parche.
_REGISTRY: dict[tuple[str, str, str], dict] = {
    ("krea2", "none", MediaType.IMAGE.value): {
        "file": "krea2_t2i.json",
        "positive_node": "4",
        "negative_node": "27",
        "seed_nodes": ("1", "17"),
        "save_nodes": (_SAVE_NODE,),
        "supports_source": False,
    },
}


@lru_cache(maxsize=None)
def _load_template(file_name: str) -> dict:
    with (TEMPLATES_DIR / file_name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def lookup(model: str, lora: str, media_type: MediaType) -> TemplateSpec | None:
    """Resolver la plantilla para ``(model, lora, media_type)``, o None."""
    entry = _REGISTRY.get((model, lora, media_type.value))
    if entry is None:
        return None
    return TemplateSpec(
        key=entry["file"],
        graph=_load_template(entry["file"]),
        media_type=media_type,
        positive_node=entry["positive_node"],
        negative_node=entry["negative_node"],
        seed_nodes=tuple(entry["seed_nodes"]),
        save_nodes=tuple(entry["save_nodes"]),
        supports_source=bool(entry["supports_source"]),
    )


def has(model: str, lora: str, media_type: MediaType) -> bool:
    return lookup(model, lora, media_type) is not None


def render(spec: TemplateSpec, prompt: str, seed_factory) -> dict:
    """Copia la plantilla y parchea prompt + seeds para un request."""
    graph = copy.deepcopy(spec.graph)
    pos = graph.get(spec.positive_node)
    if pos is None or "text" not in pos.get("inputs", {}):
        raise ValueError(f"template {spec.key}: falta nodo prompt {spec.positive_node}")
    pos["inputs"]["text"] = prompt
    for node_id in spec.seed_nodes:
        node = graph.get(node_id)
        if node is not None and "seed" in node.get("inputs", {}):
            node["inputs"]["seed"] = seed_factory()
    return graph
