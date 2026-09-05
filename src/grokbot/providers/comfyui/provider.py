"""ComfyUI provider — image/video generation via ComfyUI's native HTTP/WS API.

The provider no longer runs scripts or scp on the box (the ``gen_comfy.py``
path). It reaches ComfyUI through an SSH local-forward tunnel
(:mod:`transport`) and drives the native API with :class:`ComfyApiClient`
(:mod:`client`): pick an API-format workflow template (model/lora/media →
:func:`workflows.resolver.lookup`), patch prompt/seeds, enqueue, wait for the
run, and download the outputs to a local ``tmpdir`` following the
``GenerationResult`` convention (``file_path`` + ``meta["file_paths"]``).

Refine is NOT part of this provider: results never carry
``meta["comfyui_remotes"]``, so the legacy refine offer in the telegram layer
stays dormant. Preconditions and user-safe copy mirror the former SSH provider.
"""

from __future__ import annotations

import os
import random
import time
import uuid
from pathlib import Path
from typing import Callable

from grokbot.domain.generation import GenerationRequest, GenerationResult, MediaType
from grokbot.domain.user_config import (
    DEFAULT_COMFYUI_LORA,
    DEFAULT_COMFYUI_MODEL,
    VALID_COMFYUI_LORAS,
    VALID_COMFYUI_MODELS,
)
from grokbot.providers.base import (
    ProviderGenerationError,
    ProviderInputError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
)
from grokbot.providers.comfyui.client import ComfyApiClient
from grokbot.providers.comfyui.transport import SshLocalForward, TunnelConfig
from grokbot.providers.comfyui.workflows.resolver import TemplateSpec, lookup, render

# Video ComfyUI models return MP4; everything else is an image (bot parity).
COMFY_VIDEO_MODELS = ("wan_i2v", "minimax_i2v")

_COMFY_IMAGE_TIMEOUT = 600
_COMFY_VIDEO_TIMEOUT = 1500

_COMFY_NOT_CONFIGURED_MSG = (
    "ComfyUI no está disponible en este momento. Contacta al administrador del bot."
)
_NO_IMAGE_RETURNED_MSG = (
    "ComfyUI no devolvió imagen. Revisa la GPU remota e intenta de nuevo."
)
_NO_DOWNLOAD_MSG = "No se pudo descargar la imagen del box. Intenta de nuevo."
_GENERIC_ERR = "Error en la generación. Intenta de nuevo más tarde."
_EDIT_UNAVAILABLE_MSG = (
    "La edición con foto aún no está disponible en esta integración. "
    "Intenta con un prompt de texto."
)
_COMBO_UNAVAILABLE_MSG = "Configuración de ComfyUI aún no disponible."

# Extensiones aceptadas al escribir el archivo local (outputs del box).
_ALLOWED_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".gif")

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_TMPDIR = _REPO_ROOT / "tmp" / "comfyui"


def _is_comfy_video_model(comfyui_model: str) -> bool:
    return comfyui_model in COMFY_VIDEO_MODELS


def _comfy_precondition_error(comfyui_model: str, lora: str) -> str | None:
    """Photo-required precondition message when generating WITHOUT a source.

    Mirrors the former SSH provider (bot.py 3971-3995). Returns ``None`` when
    the combo is valid for text-to-image.
    """
    if comfyui_model in COMFY_VIDEO_MODELS:
        return (
            "El generador de video necesita una foto de entrada:\n"
            "envía una foto con el prompt, o responde a una foto con el texto."
        )
    if comfyui_model == "qwen_aio":
        return (
            "El modo Qwen AIO (edición nativa) necesita una foto de entrada:\n"
            "envía la foto con /variables (o responde a una foto)."
        )
    if comfyui_model == "qwen" and lora == "multipose_batch":
        return (
            "El modo Multi-pose necesita una foto de entrada:\n"
            "envía la foto con /variables (o responde a una foto)."
        )
    return None


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
        cm = str(request.params.get("model") or DEFAULT_COMFYUI_MODEL)
        cl = str(request.params.get("lora") or DEFAULT_COMFYUI_LORA)
        if request.media_type is MediaType.IMAGE:
            return lookup(cm, cl, MediaType.IMAGE) is not None
        if request.media_type is MediaType.VIDEO:
            return _is_comfy_video_model(cm) and lookup(cm, cl, MediaType.VIDEO) is not None
        return False

    # -- internals --------------------------------------------------------
    def _raise_if_not_configured(self) -> None:
        if not self._transport.is_configured:
            raise ProviderNotConfiguredError(
                _COMFY_NOT_CONFIGURED_MSG, user_message=_COMFY_NOT_CONFIGURED_MSG
            )

    @staticmethod
    def _model_lora(request: GenerationRequest) -> tuple[str, str]:
        """Resolve comfyui model/lora and validate against the known sets.

        Values only ever come from the catalog (M1 hardening); unknown/malicious
        ``params`` values are rejected before reaching the API.
        """
        cm = str(request.params.get("model") or DEFAULT_COMFYUI_MODEL)
        cl = str(request.params.get("lora") or DEFAULT_COMFYUI_LORA)
        if cm not in VALID_COMFYUI_MODELS or cl not in VALID_COMFYUI_LORAS:
            raise ProviderInputError(
                "Configuración de ComfyUI inválida.",
                user_message=_GENERIC_ERR,
            )
        return cm, cl

    def _media_type_for(self, comfyui_model: str, request_media: MediaType) -> MediaType:
        if _is_comfy_video_model(comfyui_model):
            return MediaType.VIDEO
        return request_media

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
        comfyui_model: str,
        locals_: list[str],
    ) -> GenerationResult:
        if not locals_:
            raise ProviderUnavailableError(_NO_DOWNLOAD_MSG, user_message=_NO_DOWNLOAD_MSG)
        media_type = self._media_type_for(comfyui_model, request.media_type)
        return GenerationResult(
            provider="comfyui",
            model_id=request.model_id,
            media_type=media_type,
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
        cm, cl = self._model_lora(request)
        # M2: a VIDEO request with an image-only comfyui model is a caller seam.
        if request.media_type is MediaType.VIDEO and not _is_comfy_video_model(cm):
            raise ProviderInputError(
                "El modelo ComfyUI configurado no genera video.",
                user_message="El modelo ComfyUI configurado no genera video.",
            )
        if source_image is None:
            err = _comfy_precondition_error(cm, cl)
            if err:
                raise ProviderInputError(err, user_message=err)
        spec = lookup(cm, cl, request.media_type)
        if spec is None:
            raise ProviderInputError(
                f"Sin plantilla para comfyui ({cm}/{cl}).",
                user_message=_COMBO_UNAVAILABLE_MSG,
            )
        if source_image is not None and not spec.supports_source:
            raise ProviderInputError(
                "La plantilla no admite foto de entrada.",
                user_message=_EDIT_UNAVAILABLE_MSG,
            )

        graph = render(spec, request.prompt, self._seed)
        run_timeout = _COMFY_VIDEO_TIMEOUT if _is_comfy_video_model(cm) else _COMFY_IMAGE_TIMEOUT

        base_url = await self._transport.ensure()
        client = self._client_factory(base_url)
        try:
            prompt_id = await client.run_workflow(graph, timeout=run_timeout)
            refs = self._collect_media(await client.history(prompt_id), spec)
            if not refs:
                raise ProviderUnavailableError(
                    _NO_IMAGE_RETURNED_MSG, user_message=_NO_IMAGE_RETURNED_MSG
                )
            locals_ = await self._download_outputs(client, refs, spec.media_type)
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()
        return self._build_result(request, cm, locals_)

    @staticmethod
    def _collect_media(history: dict, spec: TemplateSpec) -> list[dict]:
        """Extract media refs (``{filename, subfolder, type}``) for the save nodes."""
        entry = next(iter((history or {}).values()), {}) or {}
        outputs = entry.get("outputs") or {}
        refs: list[dict] = []
        for node_id in spec.save_nodes:
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
