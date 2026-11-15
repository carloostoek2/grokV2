"""Perfiles de pesos ComfyUI por tarjeta GPU (fuente única de verdad).

Un **perfil** describe qué flujos puede correr una tarjeta y con qué pesos:

- ``flows``: ids de flujo permitidos en el menú y en el resolver.
- ``substitutions``: reemplazo exacto de nombres de archivo sobre la plantilla
  base. Sirve para el mismo grafo con otro peso (p. ej. Krea fp8 → bf16 en la
  A100, o Krea safetensors → GGUF en la T4) sin duplicar el JSON.
- ``weights``: lista de descargas (ruta relativa, URL, auth, tamaño mínimo).
- ``symlinks``: enlaces que ComfyUI necesita dentro de ``models/``.
- ``node_patches``: parches idempotentes a custom nodes (p. ej. añadir
  ``krea2`` a la lista de arquitecturas de ComfyUI-GGUF).
- ``min_vram_gb`` (opcional): ``flow_id`` → VRAM mínima en GB para ofrecer ese
  flujo dentro del perfil. Sirve cuando un perfil cubre tarjetas de distinta
  VRAM (``l4`` va de 20 a 39 GB) y un flujo no cabe en las más chicas. Con VRAM
  desconocida (perfil forzado a mano o ``/system_stats`` sin dato) el flujo se
  oculta: nunca se ofrece algo que puede quedarse sin memoria. Ver
  :meth:`Profile.for_vram`.

Cuando una plantilla acaba apuntando a un ``.gguf`` en un ``UNETLoader``,
:func:`apply_profile` cambia el nodo a ``UnetLoaderGGUF`` y quita
``weight_dtype`` (el loader GGUF solo acepta ``unet_name``).

Este módulo no importa el resto de ``grokbot`` (solo stdlib) para que el
generador del cuaderno de Colab lo cargue por ruta sin arrastrar dependencias.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKFLOWS_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WORKFLOWS_DIR / "templates"
PROFILES_DIR = WORKFLOWS_DIR / "profiles"

DEFAULT_PROFILE_ID = "a100"
ARCH_GGUF = "UnetLoaderGGUF"


@dataclass(frozen=True)
class Profile:
    """Perfil de pesos de una familia de tarjetas."""

    id: str
    label: str
    default_flow: str
    flows: tuple[str, ...]
    weights: tuple[dict[str, Any], ...] = ()
    symlinks: tuple[tuple[str, str], ...] = ()
    substitutions: dict[str, str] = field(default_factory=dict)
    node_patches: tuple[dict[str, Any], ...] = ()
    min_vram_gb: dict[str, float] = field(default_factory=dict)

    def allows(self, flow_id: str) -> bool:
        return flow_id in self.flows

    def for_vram(self, vram_gb: float | None) -> Profile:
        """Copia del perfil sin los flujos que piden más VRAM que la tarjeta.

        ``vram_gb`` ``None`` o ``0`` significa VRAM desconocida: se quitan
        todos los flujos con requisito en ``min_vram_gb``. Sin requisitos
        devuelve el mismo perfil.
        """
        if not self.min_vram_gb:
            return self
        vram = float(vram_gb or 0.0)
        flows = tuple(
            flow_id
            for flow_id in self.flows
            if flow_id not in self.min_vram_gb
            or (vram > 0 and vram >= self.min_vram_gb[flow_id])
        )
        if flows == self.flows:
            return self
        return replace(self, flows=flows)


def _profile_from_raw(raw: dict[str, Any], file_name: str) -> Profile:
    if not isinstance(raw, dict) or not raw.get("id"):
        raise ValueError(f"perfil {file_name}: falta 'id'")
    flows = tuple(str(x) for x in raw.get("flows", ()))
    if not flows:
        raise ValueError(f"perfil {file_name}: 'flows' vacío")
    default = str(raw.get("default_flow") or flows[0])
    if default not in flows:
        raise ValueError(
            f"perfil {file_name}: default_flow {default!r} no está en flows"
        )
    substitutions = {
        str(k): str(v) for k, v in (raw.get("substitutions") or {}).items()
    }
    weights = tuple(
        dict(w) for w in (raw.get("weights") or ()) if isinstance(w, dict)
    )
    for weight in weights:
        for key in ("path", "url"):
            if not weight.get(key):
                raise ValueError(f"perfil {file_name}: peso sin '{key}'")
    symlinks = tuple(
        (str(a), str(b)) for a, b in (raw.get("symlinks") or ())
    )
    patches = tuple(
        dict(p) for p in (raw.get("node_patches") or ()) if isinstance(p, dict)
    )
    min_vram: dict[str, float] = {}
    for flow_id, value in (raw.get("min_vram_gb") or {}).items():
        if flow_id not in flows:
            raise ValueError(
                f"perfil {file_name}: min_vram_gb de {flow_id!r}, que no está en flows"
            )
        if flow_id == default:
            raise ValueError(
                f"perfil {file_name}: el default_flow {default!r} no puede pedir VRAM mínima"
            )
        gb = float(value)
        if gb <= 0:
            raise ValueError(f"perfil {file_name}: min_vram_gb de {flow_id!r} debe ser > 0")
        min_vram[str(flow_id)] = gb
    return Profile(
        id=str(raw["id"]),
        label=str(raw.get("label") or raw["id"]),
        default_flow=default,
        flows=flows,
        weights=weights,
        symlinks=symlinks,
        substitutions=substitutions,
        node_patches=patches,
        min_vram_gb=min_vram,
    )


def load_profiles(profiles_dir: Path | None = None) -> dict[str, Profile]:
    """Cargar todos los ``profiles/*.json`` (orden estable por nombre)."""
    base = Path(profiles_dir) if profiles_dir is not None else PROFILES_DIR
    if not base.is_dir():
        raise FileNotFoundError(f"No existe el directorio de perfiles: {base}")
    perfiles: dict[str, Profile] = {}
    for path in sorted(base.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        profile = _profile_from_raw(raw, path.name)
        if profile.id in perfiles:
            raise ValueError(f"perfil duplicado: {profile.id}")
        perfiles[profile.id] = profile
    if not perfiles:
        raise ValueError(f"No hay perfiles en {base}")
    return perfiles


def get_profile(profile_id: str, profiles_dir: Path | None = None) -> Profile | None:
    return load_profiles(profiles_dir).get(profile_id)


def profile_for_device(
    name: str,
    vram_gb: float = 0.0,
    profiles: dict[str, Profile] | None = None,
) -> Profile:
    """Mapear tarjeta real → perfil.

    La GPU manda sobre el nombre: una A100 80 GB, una L4 24 GB y una T4 16 GB
    tienen perfiles distintos. Si el nombre no reconoce la familia, decide la
    VRAM; sin datos cae al perfil más pequeño (nunca descarga 100 GB por error).
    """
    perfiles = profiles or load_profiles()
    nombre = (name or "").upper()
    if "H100" in nombre or "A100" in nombre or vram_gb >= 40:
        return perfiles["a100"]
    if "L4" in nombre or "L40" in nombre or vram_gb >= 20:
        return perfiles["l4"]
    return perfiles["t4"]


def _substitute(value: Any, substitutions: dict[str, str]) -> Any:
    if isinstance(value, str):
        return substitutions.get(value, value)
    if isinstance(value, list):
        return [_substitute(v, substitutions) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, substitutions) for k, v in value.items()}
    return value


def apply_profile(graph: dict[str, Any], profile: Profile) -> dict[str, Any]:
    """Copia el grafo base ya adaptado a los pesos del perfil.

    - Reemplaza nombres de archivo exactos según ``substitutions``.
    - Si el UNET de un nodo acaba en ``.gguf``, lo cambia a ``UnetLoaderGGUF``.
    """
    data = _substitute(copy.deepcopy(graph), profile.substitutions)
    for node in data.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "UNETLoader":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        unet = inputs.get("unet_name")
        if isinstance(unet, str) and unet.lower().endswith(".gguf"):
            node["class_type"] = ARCH_GGUF
            inputs.pop("weight_dtype", None)
    return data


def template_index(templates_dir: Path | None = None) -> dict[str, Path]:
    """``flow_id`` → archivo base (descubierto por ``_meta.id``)."""
    base = Path(templates_dir) if templates_dir is not None else TEMPLATES_DIR
    index: dict[str, Path] = {}
    for path in sorted(base.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        meta = raw.get("_meta") or {}
        flow_id = meta.get("id")
        if flow_id and flow_id not in index:
            index[str(flow_id)] = path
    return index


def flow_graphs(
    profile: Profile,
    templates_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Grafos efectivos del perfil (id → JSON adaptado), en orden del perfil."""
    index = template_index(templates_dir)
    graphs: dict[str, dict[str, Any]] = {}
    for flow_id in profile.flows:
        path = index.get(flow_id)
        if path is None:
            logger.warning("perfil %s: no existe plantilla para %s", profile.id, flow_id)
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        graphs[flow_id] = apply_profile(raw, profile)
    return graphs


def model_filenames(graph: dict[str, Any]) -> set[str]:
    """Nombres de pesos referenciados por un grafo (para el test cruzado)."""
    keys = (
        "unet_name",
        "clip_name",
        "vae_name",
        "ckpt_name",
        "lora_name",
        "model_name",
        "control_net_name",
    )
    names: set[str] = set()
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        for key in keys:
            value = inputs.get(key)
            if isinstance(value, str) and value:
                names.add(value)
    return names


__all__ = [
    "DEFAULT_PROFILE_ID",
    "PROFILES_DIR",
    "TEMPLATES_DIR",
    "Profile",
    "apply_profile",
    "flow_graphs",
    "get_profile",
    "load_profiles",
    "model_filenames",
    "profile_for_device",
    "template_index",
]
