"""Resolvedor de flujos de workflow ComfyUI (API-format) para el provider.

Cada **flujo** es un archivo JSON API-format de ComfyUI (modelo/LoRA horneados)
más una clave top-level ``_meta`` que lo describe y declara qué nodos parchear::

    "_meta": {
      "id": "grok_style",
      "name": "Grok Style",
      "media_type": "image",          # "image" | "video"
      "positive_node": "56",
      "positive_input": "prompt",     # default "text"
      "negative_node": "27",
      "seed_nodes": ["195"],
      "save_nodes": ["914"],
      "supports_source": false
    }

Fuente de verdad (runtime)
==========================
Por defecto el bot carga ``{flow_id}.json`` desde el box Vast vía SSH
(``COMFYUI_WORKFLOWS_DIR``, default
``/workspace/ComfyUI/user/default/api_workflows``), con cache TTL corta y
fallback a ``templates/`` embebidos en el repo si el remoto falta.

Stock ComfyUI ``POST /prompt`` **siempre** exige el grafo API-format en el
body — no hay endpoint "run by name". Actualizar un flujo = editar en la UI,
exportar API format (o copiar del history) y sobrescribir
``api_workflows/{flow_id}.json`` en Vast. **No** hay conversor UI→API.

``_meta`` (top-level) se separa del grafo al cargar y **nunca** viaja a
ComfyUI; el ``_meta`` de *nodo* (title del export) también se descarta.
El id debe estar además en ``domain.user_config.VALID_COMFYUI_MODELS``.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from grokbot.domain.generation import MediaType

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_DEFAULT_REMOTE_DIR = "/workspace/ComfyUI/user/default/api_workflows"
_DEFAULT_CACHE_TTL = 45.0
_FLOW_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SSH_CONNECT_TIMEOUT = 15


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
    # Origen del grafo: "remote" (Vast) o "embed" (templates/ del repo).
    origin: str = "embed"


@dataclass
class _RemoteConfig:
    host: str
    ssh_port: int = 22
    workflows_dir: str = _DEFAULT_REMOTE_DIR
    cache_ttl: float = _DEFAULT_CACHE_TTL
    ssh_user: str = "root"
    ssh_bin: str = "ssh"
    # Injectable: ``flow_id`` → raw JSON text, or None if missing.
    fetch: Callable[[str], str | None] | None = None


# Module-level source config (set at boot via ``configure_workflow_source``).
_source_mode: str = "embed"  # "remote" | "embed"
_remote: _RemoteConfig | None = None
# flow_id -> (monotonic_deadline, Flow)
_remote_cache: dict[str, tuple[float, Flow]] = {}


def configure_workflow_source(
    *,
    source: str = "remote",
    host: str = "",
    ssh_port: int = 22,
    workflows_dir: str = _DEFAULT_REMOTE_DIR,
    cache_ttl: float = _DEFAULT_CACHE_TTL,
    ssh_user: str = "root",
    ssh_bin: str = "ssh",
    fetch: Callable[[str], str | None] | None = None,
) -> None:
    """Configurar origen de workflows (remoto Vast vía SSH o embed).

    ``source``:
      - ``remote`` (default de producto): SSH-cat ``{workflows_dir}/{id}.json``
        en el box; fallback a ``templates/`` si falta o falla.
      - ``embed``: solo templates del repo (tests / offline).

    Sin ``host`` y ``source=remote`` se degrada a embed con warning.
    ``fetch`` permite mockear la lectura remota en tests unitarios.
    """
    global _source_mode, _remote
    mode = (source or "remote").strip().lower()
    if mode not in ("remote", "embed"):
        logger.warning("Unknown COMFYUI_WORKFLOW_SOURCE=%r; using remote", source)
        mode = "remote"
    _remote_cache.clear()
    if mode == "remote" and (host or fetch is not None):
        _source_mode = "remote"
        _remote = _RemoteConfig(
            host=host or "",
            ssh_port=int(ssh_port or 22),
            workflows_dir=(workflows_dir or _DEFAULT_REMOTE_DIR).rstrip("/"),
            cache_ttl=float(cache_ttl if cache_ttl is not None else _DEFAULT_CACHE_TTL),
            ssh_user=ssh_user or "root",
            ssh_bin=ssh_bin or "ssh",
            fetch=fetch,
        )
        logger.info(
            "ComfyUI workflow source=remote dir=%s cache_ttl=%.0fs host=%s",
            _remote.workflows_dir,
            _remote.cache_ttl,
            _remote.host or "(fetch-injected)",
        )
        return
    if mode == "remote" and not host:
        logger.warning(
            "COMFYUI_WORKFLOW_SOURCE=remote but COMFYUI_HOST empty; using embed templates"
        )
    _source_mode = "embed"
    _remote = None


def reset_workflow_source() -> None:
    """Volver a embed sin remoto (tests)."""
    configure_workflow_source(source="embed", host="")


def _flow_from_raw(raw: dict, file_name: str, *, origin: str) -> Flow:
    """Separar ``_meta`` → Flow y dejar el grafo limpio (sin ``_meta``)."""
    if not isinstance(raw, dict):
        raise ValueError(f"template {file_name}: debe ser un objeto JSON")
    data = copy.deepcopy(raw)
    meta = data.pop("_meta", None)
    if not isinstance(meta, dict) or not meta.get("id"):
        raise ValueError(f"template {file_name}: falta '_meta.id'")
    for node in data.values():
        if isinstance(node, dict):
            node.pop("_meta", None)
    return Flow(
        id=str(meta["id"]),
        name=str(meta.get("name") or meta["id"]),
        file=file_name,
        graph=data,
        media_type=MediaType(str(meta["media_type"])),
        positive_node=str(meta.get("positive_node", "4")),
        positive_input=str(meta.get("positive_input", "text")),
        negative_node=str(meta["negative_node"]) if meta.get("negative_node") else None,
        seed_nodes=tuple(str(x) for x in meta.get("seed_nodes", ())),
        save_nodes=tuple(str(x) for x in meta.get("save_nodes", ())),
        supports_source=bool(meta.get("supports_source", False)),
        timeout=int(meta["timeout"]) if meta.get("timeout") else None,
        origin=origin,
    )


def _load_template(file_name: str) -> tuple[Flow, dict]:
    """Cargar un template embebido: separa ``_meta`` (→ Flow) del grafo limpio."""
    raw = json.loads((TEMPLATES_DIR / file_name).read_text(encoding="utf-8"))
    flow = _flow_from_raw(raw, file_name, origin="embed")
    return flow, flow.graph


@lru_cache(maxsize=None)
def _flows_cached() -> tuple[Flow, ...]:
    """Descubrir los flujos de ``templates/`` (un ``.json`` = un flujo)."""
    flows_list: list[Flow] = []
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        flow, _graph = _load_template(path.name)
        flows_list.append(flow)
    return tuple(flows_list)


def flows() -> tuple[Flow, ...]:
    """Todos los flujos embebidos (catálogo; orden estable por nombre de archivo)."""
    return _flows_cached()


def _get_embed_flow(flow_id: str) -> Flow | None:
    for flow in flows():
        if flow.id == flow_id:
            return flow
    return None


def _ssh_cat(flow_id: str) -> str | None:
    """Leer ``{workflows_dir}/{flow_id}.json`` en Vast vía ``ssh … cat``."""
    cfg = _remote
    if cfg is None:
        return None
    if cfg.fetch is not None:
        return cfg.fetch(flow_id)
    if not cfg.host or not _FLOW_ID_RE.match(flow_id):
        return None
    remote_path = f"{cfg.workflows_dir}/{flow_id}.json"
    # Remote argv: avoid shell interpolation; quote path for the remote shell.
    remote_cmd = f"cat -- {shlex.quote(remote_path)}"
    argv = [
        cfg.ssh_bin,
        "-p",
        str(cfg.ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={_SSH_CONNECT_TIMEOUT}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{cfg.ssh_user}@{cfg.host}",
        remote_cmd,
    ]
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=_SSH_CONNECT_TIMEOUT + 10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("SSH fetch workflow %s failed: %s", flow_id, type(exc).__name__)
        return None
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        logger.warning(
            "SSH fetch workflow %s rc=%s: %s",
            flow_id,
            proc.returncode,
            err[0][:200],
        )
        return None
    text = proc.stdout or ""
    if not text.strip():
        logger.warning("SSH fetch workflow %s returned empty body", flow_id)
        return None
    return text


def _get_remote_flow(flow_id: str) -> Flow | None:
    """Cargar (o cachear) un flujo desde Vast. None si no existe / error."""
    if not _FLOW_ID_RE.match(flow_id):
        return None
    now = time.monotonic()
    cached = _remote_cache.get(flow_id)
    if cached is not None:
        deadline, flow = cached
        if now < deadline:
            return flow
        _remote_cache.pop(flow_id, None)

    raw_text = _ssh_cat(flow_id)
    if raw_text is None:
        return None
    try:
        raw = json.loads(raw_text)
        flow = _flow_from_raw(raw, f"{flow_id}.json", origin="remote")
    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        logger.warning(
            "Remote workflow %s invalid: %s", flow_id, type(exc).__name__
        )
        return None
    if flow.id != flow_id:
        logger.warning(
            "Remote workflow file %s.json has _meta.id=%s (mismatch); ignoring",
            flow_id,
            flow.id,
        )
        return None
    ttl = _remote.cache_ttl if _remote is not None else _DEFAULT_CACHE_TTL
    _remote_cache[flow_id] = (now + max(0.0, ttl), flow)
    logger.info(
        "Loaded ComfyUI workflow %s from remote (%s/%s.json)",
        flow_id,
        _remote.workflows_dir if _remote else "?",
        flow_id,
    )
    return flow


def get_flow(flow_id: str) -> Flow | None:
    """Resolver un flujo por id: remoto (si configurado) → fallback embed."""
    if _source_mode == "remote" and _remote is not None:
        flow = _get_remote_flow(flow_id)
        if flow is not None:
            return flow
        logger.warning(
            "Remote workflow %s missing/failed; falling back to embed templates",
            flow_id,
        )
    return _get_embed_flow(flow_id)


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
