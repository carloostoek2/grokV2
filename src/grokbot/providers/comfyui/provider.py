"""ComfyUI provider — image/video generation via ComfyUI's native HTTP/WS API.

The provider no longer runs scripts or scp on the box (the ``gen_comfy.py``
path). It reaches ComfyUI through an SSH local-forward tunnel
(:mod:`transport`) and drives the native API with :class:`ComfyApiClient`
(:mod:`client`): resolver un flujo por id (``workflows.resolver.get_flow`` —
cada flujo es un workflow API-format con su modelo/LoRA horneados), parchear
prompt/seeds, encolar, esperar el run y descargar los outputs a un ``tmpdir``
local siguiendo la convención ``GenerationResult`` (``file_path`` +
``meta["file_paths"]``).

Refine is NOT part of this provider: results never carry
``meta["comfyui_remotes"]``, so the legacy refine offer in the telegram layer
stays dormant. Preconditions and user-safe copy mirror the former SSH provider.
"""

from __future__ import annotations

import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

from grokbot.domain.generation import GenerationRequest, GenerationResult, MediaType
from grokbot.domain.user_config import DEFAULT_COMFYUI_MODEL
from grokbot.providers.base import (
    ProviderInputError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
)
from grokbot.providers.comfyui.client import ComfyApiClient
from grokbot.providers.comfyui.transport import SshLocalForward, TunnelConfig
from grokbot.providers.comfyui.workflows.resolver import (
    Flow,
    configure_workflow_source,
    get_flow,
    render,
)

_COMFY_IMAGE_TIMEOUT = 600
_COMFY_VIDEO_TIMEOUT = 1500

_COMFY_NOT_CONFIGURED_MSG = (
    "ComfyUI no está disponible en este momento. Contacta al administrador del bot."
)
_NO_IMAGE_RETURNED_MSG = (
    "ComfyUI no devolvió imagen. Revisa la GPU remota e intenta de nuevo."
)
_NO_DOWNLOAD_MSG = "No se pudo descargar la imagen del box. Intenta de nuevo."
_EDIT_UNAVAILABLE_MSG = (
    "La edición con foto aún no está disponible en esta integración. "
    "Intenta con un prompt de texto."
)
_COMBO_UNAVAILABLE_MSG = "Configuración de ComfyUI aún no disponible."

# Extensiones aceptadas al escribir el archivo local (outputs del box).
_ALLOWED_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".gif")

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_TMPDIR = _REPO_ROOT / "tmp" / "comfyui"


class ComfyUIProvider:
    """Implements :class:`ImageProvider` / :class:`VideoProvider` over ComfyUI HTTP.

    ``host``/``port`` are the SSH endpoint of the box; the tunnel
    (:class:`SshLocalForward`) is injectable, as is the client factory (tests).
    An empty host disables the provider (``available is False``).
    """

    def __init__(
        self,
        host: str,
        port: int = 22,
        *,
        remote_port: int = 18188,
        tunnel_local_port: int = 0,
        workflow_source: str = "remote",
        workflows_dir: str = "/workspace/ComfyUI/user/default/api_workflows",
        workflow_cache_ttl: float = 45.0,
        transport: SshLocalForward | None = None,
        client_factory: Callable[[str], ComfyApiClient] | None = None,
        tmpdir: str | Path | None = None,
        seed_factory: Callable[[], int] | None = None,
    ) -> None:
        self._transport = transport if transport is not None else SshLocalForward(
            TunnelConfig(
                host=host or "",
                ssh_port=int(port or 22),
                remote_port=int(remote_port or 18188),
                local_port=int(tunnel_local_port or 0),
            )
        )
        # Source-of-truth for API graphs: Vast api_workflows/ (SSH) with embed fallback.
        configure_workflow_source(
            source=workflow_source,
            host=host or "",
            ssh_port=int(port or 22),
            workflows_dir=workflows_dir,
            cache_ttl=workflow_cache_ttl,
        )
        self._client_factory = client_factory or (lambda url: ComfyApiClient(url))
        self._tmpdir = Path(tmpdir) if tmpdir is not None else _DEFAULT_TMPDIR
        self._seed = seed_factory or (lambda: random.randrange(1 << 63))

    # -- state ------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._transport.is_configured

    def supports(self, request: GenerationRequest) -> bool:
        if request.provider != "comfyui":
            return False
        flow = get_flow(str(request.params.get("model") or DEFAULT_COMFYUI_MODEL))
        return flow is not None and flow.media_type is request.media_type

    # -- internals --------------------------------------------------------
    def _raise_if_not_configured(self) -> None:
        if not self._transport.is_configured:
            raise ProviderNotConfiguredError(
                _COMFY_NOT_CONFIGURED_MSG, user_message=_COMFY_NOT_CONFIGURED_MSG
            )

    def _resolve_flow(self, request: GenerationRequest) -> Flow:
        """Resolver el flujo por su id (``params["model"]``) y validarlo.

        M1 (hardening): el id solo se usa para mirar el template registrado;
        jamás se interpola a shell. Un id desconocido/malicioso se rechaza aquí,
        antes de tocar el transporte.
        """
        flow = get_flow(str(request.params.get("model") or DEFAULT_COMFYUI_MODEL))
        if flow is None:
            raise ProviderInputError(
                "Flujo de ComfyUI no disponible.",
                user_message=_COMBO_UNAVAILABLE_MSG,
            )
        logger.info(
            "ComfyUI flow resolved id=%s origin=%s file=%s",
            flow.id,
            flow.origin,
            flow.file,
        )
        return flow

    async def _download_outputs(
        self,
        client: ComfyApiClient,
        media_refs: list[dict],
        media_type: MediaType,
    ) -> list[str]:
        """Download each output via ``/view`` to a local tmp file."""
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        locals_: list[str] = []
        for ref in media_refs:
            filename = str(ref.get("filename") or "")
            if not filename:
                continue
            try:
                data = await client.view(
                    filename=filename,
                    subfolder=str(ref.get("subfolder") or ""),
                    type_=str(ref.get("type") or "output"),
                )
            except ProviderUnavailableError:
                continue
            if not data:
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in _ALLOWED_EXTS:
                ext = ".mp4" if media_type is MediaType.VIDEO else ".png"
            local = self._tmpdir / (
                f"comfyui_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
            )
            local.write_bytes(data)
            locals_.append(str(local))
        return locals_

    def _build_result(
        self,
        request: GenerationRequest,
        flow: Flow,
        locals_: list[str],
    ) -> GenerationResult:
        if not locals_:
            raise ProviderUnavailableError(_NO_DOWNLOAD_MSG, user_message=_NO_DOWNLOAD_MSG)
        return GenerationResult(
            provider="comfyui",
            model_id=request.model_id,
            media_type=flow.media_type,
            file_path=locals_[0],
            meta={
                "file_paths": locals_,
                "download_allowlist": None,
            },
        )

    # -- generate ---------------------------------------------------------
    async def generate(
        self,
        request: GenerationRequest,
        *,
        source_image: bytes | None = None,
    ) -> GenerationResult:
        self._raise_if_not_configured()
        flow = self._resolve_flow(request)  # M1: id validado antes de tocar transporte.
        # M2: un request VIDEO contra un flujo de imagen es un seam del caller.
        if request.media_type is MediaType.VIDEO and flow.media_type is not MediaType.VIDEO:
            raise ProviderInputError(
                "El modelo ComfyUI configurado no genera video.",
                user_message="El modelo ComfyUI configurado no genera video.",
            )
        if source_image is not None and not flow.supports_source:
            raise ProviderInputError(
                "La plantilla no admite foto de entrada.",
                user_message=_EDIT_UNAVAILABLE_MSG,
            )

        graph = render(flow, request.prompt, self._seed)
        run_timeout = flow.timeout or (
            _COMFY_VIDEO_TIMEOUT if flow.media_type is MediaType.VIDEO else _COMFY_IMAGE_TIMEOUT
        )

        base_url = await self._transport.ensure()
        client = self._client_factory(base_url)
        try:
            if source_image is not None:
                graph = await self._inject_source_image(client, flow, graph, source_image)
            prompt_id = await client.run_workflow(graph, timeout=run_timeout)
            refs = self._collect_media(await client.history(prompt_id), flow)
            if not refs:
                raise ProviderUnavailableError(
                    _NO_IMAGE_RETURNED_MSG, user_message=_NO_IMAGE_RETURNED_MSG
                )
            locals_ = await self._download_outputs(client, refs, flow.media_type)
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()
        return self._build_result(request, flow, locals_)


    async def _inject_source_image(
        self,
        client: ComfyApiClient,
        flow: Flow,
        graph: dict,
        source_image: bytes,
    ) -> dict:
        """Upload ``source_image`` and patch the flow's LoadImage node filename."""
        source_node = flow.source_node
        if not source_node:
            # Auto-detect first LoadImage node when meta omits source_node.
            for nid, node in graph.items():
                if isinstance(node, dict) and node.get("class_type") == "LoadImage":
                    source_node = str(nid)
                    break
        if not source_node or source_node not in graph:
            raise ProviderInputError(
                "La plantilla i2i no declara nodo de imagen fuente.",
                user_message=_EDIT_UNAVAILABLE_MSG,
            )
        node = graph[source_node]
        inputs = node.setdefault("inputs", {})
        if flow.source_input not in inputs and "image" not in inputs:
            # Still allow patching even if placeholder missing.
            pass
        filename = f"grokbot_src_{uuid.uuid4().hex[:12]}.png"
        uploaded = await client.upload_image(filename, source_image)
        inputs[flow.source_input] = uploaded
        return graph

    @staticmethod
    def _collect_media(history: dict, flow: Flow) -> list[dict]:
        """Extract media refs (``{filename, subfolder, type}``) for the save nodes."""
        entry = next(iter((history or {}).values()), {}) or {}
        outputs = entry.get("outputs") or {}
        refs: list[dict] = []
        for node_id in flow.save_nodes:
            node_out = outputs.get(node_id) or {}
            for media_key in ("images", "gifs", "videos"):
                for media in node_out.get(media_key) or []:
                    if isinstance(media, dict) and media.get("filename"):
                        refs.append(
                            {
                                "filename": media["filename"],
                                "subfolder": media.get("subfolder", ""),
                                "type": media.get("type", "output"),
                            }
                        )
        return refs
