"""Inyección de LoRAs de usuario en un grafo API-format de ComfyUI.

El usuario apila LoRAs **encima** del flujo: los nodos horneados en la plantilla
(modelo, LoRA de estilo, LoRA de identidad, destilados de pocos pasos) no se
tocan. Cada entrada se inserta al final de la cadena de modelo —entre el último
productor de MODEL y el sampler—, de modo que un stack vacío deja el grafo
byte a byte igual al original y un fallo aquí nunca modifica la plantilla
(el provider trabaja siempre sobre la copia de :func:`resolver.render`).

El ancla se descubre del propio grafo: se busca el nodo cuyo MODEL consume el
sampler principal. Para desempatar entre varios samplers (p. ej. el de SeedVR2
en los flujos HQ) se prioriza el que está aguas abajo del nodo positivo y el
que no cuelga de un UNET ``seedvr``. Un sampler puede consumir MÁS de un MODEL
(``model`` + ``edit_model``): en ese caso el ancla es el ancestro común más
cercano de ambas ramas, para que el stack no se aplique a medias. Si un grafo
exótico no expone ningún enlace de modelo, se lanza :class:`LoraInjectionError`
y el provider lo traduce a un error user-safe: nunca se encola un grafo a medias.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from grokbot.domain.user_config import LoraStackEntry

# Nodos que producen un MODEL base. Se usan solo para detectar la rama
# secundaria de upscale (SeedVR2) al desempatar el ancla.
_MODEL_LOADER_CLASSES = frozenset(
    {"UNETLoader", "UnetLoaderGGUF", "CheckpointLoaderSimple"}
)
_POSITIVE_INPUT_KEYS = ("positive", "negative", "conditioning", "guider")
# Inputs que transportan el MODEL de difusión. ``edit_model`` lo usan los nodos
# Donut (sampler/detailers) además de ``model``; NO se incluyen ``upscale_model``
# ni ``sam_model_opt``, que son modelos de otra arquitectura.
_MODEL_INPUT_KEYS = ("model", "edit_model")
_SAMPLER_HINTS = ("Sampler", "Guider", "Detailer")
_SEEDVR_TOKEN = "seedvr"
# Ids nuevos en un rango que las plantillas no usan; el server trata los ids
# del prompt como claves opacas, pero se mantienen numéricos por seguridad.
_FIRST_FRESH_NODE_ID = 900000
_MAX_TRACE_NODES = 512


class LoraInjectionError(ValueError):
    """El grafo no ofrece un enlace MODEL donde insertar el stack."""


def _link(value: Any) -> str | None:
    """``[node_id, slot]`` → ``node_id``; cualquier otra cosa → None."""
    if (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
    ):
        return value[0]
    return None


def _upstream(node: dict) -> Iterator[str]:
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return
    for value in inputs.values():
        source = _link(value)
        if source is not None:
            yield source


def _reachable_upstream(graph: dict, start: str) -> set[str]:
    """``start`` y todos los nodos alcanzables siguiendo sus enlaces de entrada."""
    seen: set[str] = set()
    stack = [start]
    while stack and len(seen) < _MAX_TRACE_NODES:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = graph.get(node_id)
        if isinstance(node, dict):
            stack.extend(src for src in _upstream(node) if src not in seen)
    return seen


def _ancestors(graph: dict, start: str, target: str) -> bool:
    """True si ``target`` es ``start`` o está aguas arriba de él."""
    return target in _reachable_upstream(graph, start)


def _distance_upstream(graph: dict, start: str, target: str) -> int | None:
    """Pasos desde ``start`` hasta ``target`` aguas arriba (BFS), o None."""
    frontier: list[tuple[str, int]] = [(start, 0)]
    seen: set[str] = set()
    while frontier and len(seen) < _MAX_TRACE_NODES:
        node_id, distance = frontier.pop(0)
        if node_id == target:
            return distance
        if node_id in seen:
            continue
        seen.add(node_id)
        node = graph.get(node_id)
        if isinstance(node, dict):
            frontier.extend(
                (src, distance + 1)
                for src in _upstream(node)
                if src not in seen
            )
    return None


def _model_producer_ids(graph: dict) -> set[str]:
    """Ids que producen un MODEL (fuente de un enlace ``model``/``edit_model``)."""
    producers: set[str] = set()
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type") or "") in _MODEL_LOADER_CLASSES:
            producers.add(str(node_id))
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            for key in _MODEL_INPUT_KEYS:
                source = _link(inputs.get(key))
                if source is not None:
                    producers.add(source)
    return producers


def _model_sources(inputs: dict) -> list[str]:
    """Sources MODEL del nodo, en orden de ``_MODEL_INPUT_KEYS``, sin duplicar."""
    sources: list[str] = []
    for key in _MODEL_INPUT_KEYS:
        source = _link(inputs.get(key))
        if source is not None and source not in sources:
            sources.append(source)
    return sources


def _has_seedvr_root(graph: dict, start: str) -> bool:
    """True si la rama del modelo cuelga de un UNET de SeedVR2 (upscale)."""
    stack = [start]
    seen: set[str] = set()
    while stack and len(seen) < _MAX_TRACE_NODES:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type") or "") in _MODEL_LOADER_CLASSES:
            inputs = node.get("inputs") or {}
            for key in ("unet_name", "ckpt_name"):
                name = inputs.get(key)
                if isinstance(name, str) and _SEEDVR_TOKEN in name.lower():
                    return True
        stack.extend(src for src in _upstream(node) if src not in seen)
    return False


def _is_sampler_like(class_type: Any) -> bool:
    name = str(class_type or "")
    return any(hint in name for hint in _SAMPLER_HINTS)


def _is_positive_consumer(graph: dict, inputs: dict, positive_node: str | None) -> bool:
    """True si el nodo consume la condición positiva del flujo."""
    if not positive_node:
        return False
    for key in _POSITIVE_INPUT_KEYS:
        source = _link(inputs.get(key))
        if source is not None and _ancestors(graph, source, positive_node):
            return True
    return any(
        _ancestors(graph, source, positive_node)
        for source in _model_sources(inputs)
    )


def _candidates(graph: dict) -> list[tuple[str, dict, str, dict]]:
    """``(node_id, node, first_model_source, inputs)`` de cada consumidor de MODEL."""
    found: list[tuple[str, dict, str, dict]] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        sources = _model_sources(inputs)
        if sources:
            found.append((str(node_id), node, sources[0], inputs))
    return found


def find_model_anchor(graph: dict, positive_node: str | None = None) -> str | None:
    """Nodo cuyo MODEL alimenta al sampler principal (o ``None``).

    Puntúa cada consumidor de MODEL: +4 si consume la condición positiva, +2 si
    su clase es de sampler/guider/detailer y +1 si su rama no es el UNET de
    SeedVR2. En empate gana el primero en el orden del grafo, que en las
    plantillas es el sampler de generación (el de upscale va después).
    """
    candidates = _candidates(graph)
    if not candidates:
        return None

    def score(item: tuple[str, dict, str, dict]) -> int:
        _, node, source, inputs = item
        value = 0
        if _is_positive_consumer(graph, inputs, positive_node):
            value += 4
        if _is_sampler_like(node.get("class_type")):
            value += 2
        if not _has_seedvr_root(graph, source):
            value += 1
        return value

    _, _, first_source, inputs = max(candidates, key=score)
    sources = _model_sources(inputs)
    if not sources:
        return first_source
    if len(sources) == 1:
        return sources[0]
    # Varias ramas MODEL (p. ej. ``model`` + ``edit_model`` en Donut): el stack
    # debe entrar en el ancestro común más cercano para no aplicarse a medias.
    common = set.intersection(
        *(_reachable_upstream(graph, source) for source in sources)
    )
    # El ancestro debe producir MODEL: un CLIP/VAE compartido más cercano no
    # sirve como punto de inserción de un LoraLoaderModelOnly.
    common &= _model_producer_ids(graph)
    if not common:
        return sources[0]

    def distance(node_id: str) -> tuple[int, str]:
        steps = [
            _distance_upstream(graph, source, node_id) for source in sources
        ]
        total = sum(step for step in steps if step is not None)
        return (total, node_id)

    return min(common, key=distance)


def _model_consumers(graph: dict, source_id: str) -> list[tuple[dict, str]]:
    """Nodos que leen ``source_id`` por ``model`` o ``edit_model``."""
    consumers: list[tuple[dict, str]] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key in _MODEL_INPUT_KEYS:
            if _link(inputs.get(key)) == source_id:
                consumers.append((node, key))
    return consumers


def _fresh_node_id(graph: dict) -> str:
    candidate = _FIRST_FRESH_NODE_ID
    while str(candidate) in graph:
        candidate += 1
    return str(candidate)


def apply_lora_stack(
    graph: dict,
    entries: Sequence[LoraStackEntry],
    *,
    positive_node: str | None = None,
) -> dict:
    """Insertar ``entries`` al final de la cadena de modelo (in place).

    El grafo recibido es la copia por request del provider; la plantilla en
    disco no se toca. ``LoraLoaderModelOnly`` se usa siempre: aplica al UNET y
    no exige recablear el CLIP, así que funciona en cualquier arquitectura.
    """
    stack = tuple(entries)
    if not stack:
        return graph
    if not isinstance(graph, dict):
        raise LoraInjectionError("el grafo no es un objeto JSON")

    previous = find_model_anchor(graph, positive_node=positive_node)
    if previous is None:
        raise LoraInjectionError("el grafo no expone ningún enlace 'model'")

    for entry in stack:
        consumers = _model_consumers(graph, previous)
        if not consumers:
            raise LoraInjectionError(
                "el ancla del LoRA no alimenta ningún consumidor MODEL"
            )
        node_id = _fresh_node_id(graph)
        graph[node_id] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": [previous, 0],
                "lora_name": entry.name,
                "strength_model": float(entry.strength),
            },
        }
        for node, key in consumers:
            node["inputs"][key] = [node_id, 0]
        previous = node_id
    return graph


__all__ = [
    "LoraInjectionError",
    "apply_lora_stack",
    "find_model_anchor",
]
