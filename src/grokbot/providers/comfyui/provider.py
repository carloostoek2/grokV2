"""ComfyUI provider — text/image-to-image generation on a Vast GPU box.

Semantic reference: grok bot.py ``_generate_comfyui`` (3956-4040),
``_generate_comfyui_refine`` (4041-4084), ``_comfyui_is_video`` (4139-4141).
All shell/SCP boundaries live in ``SshClient``; the provider only composes the
remote command, encodes the payload, and normalizes results (D4). ``model`` and
``lora`` come from ``request.params`` with fallback to ``domain/user_config``
defaults. ``refine`` (D7) is intentionally outside the ImageProvider Protocol.
"""

from __future__ import annotations

import base64
import json
import re

from grokbot.domain.generation import (
    GenerationRequest,
    GenerationResult,
    MediaType,
)
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
from grokbot.providers.comfyui.ssh_client import SshClient

# Video ComfyUI models return MP4; everything else is an image (bot.py 4139).
COMFY_VIDEO_MODELS = ("wan_i2v", "minimax_i2v")
# Krea identity-edit loras require a source photo when on a krea2* base.
_COMFY_EDIT_LORA_BASES = ("krea2", "krea2_raw", "krea2_moody")

# Refine: the box spends REFINE_TIMEOUT_PER_BASE seconds PER base image.
REFINE_TIMEOUT_PER_BASE = 1200
_COMFY_IMAGE_TIMEOUT = 600
_COMFY_VIDEO_TIMEOUT = 1500
_REFINE_REMOTE_PATH_RE = re.compile(r"^/workspace/[A-Za-z0-9_./-]{1,300}$")

_COMFY_NOT_CONFIGURED_MSG = (
    "ComfyUI no está disponible en este momento. Contacta al administrador del bot."
)
_NO_IMAGE_RETURNED_MSG = (
    "ComfyUI no devolvió imagen. Revisa la GPU remota e intenta de nuevo."
)
_NO_DOWNLOAD_MSG = "No se pudo descargar la imagen del box. Intenta de nuevo."
_GENERIC_ERR = "Error en la generación. Intenta de nuevo más tarde."


def _comfy_precondition_error(comfyui_model: str, lora: str) -> str | None:
    """Photo-required precondition message when generating WITHOUT a source.

    Mirrors grok bot.py:3971-3995. Returns ``None`` when the combo is valid for
    text-to-image.
    """
    if comfyui_model in COMFY_VIDEO_MODELS:
        return (
            "El generador de video necesita una foto de entrada:\n"
            "envía una foto con el prompt, o responde a una foto con el texto."
        )
    if comfyui_model in _COMFY_EDIT_LORA_BASES and lora.startswith("krea_edit"):
        return (
            "La edición de identidad necesita una foto de entrada:\n"
            "envía la foto de la persona + el prompt de edición (o responde a una foto)."
        )
    if comfyui_model == "qwen" and lora == "multipose_batch":
        return (
            "El modo Multi-pose necesita una foto de entrada:\n"
            "envía la foto con /variables (o responde a una foto)."
        )
    if comfyui_model == "qwen_aio":
        return (
            "El modo Qwen AIO (edición nativa) necesita una foto de entrada:\n"
            "envía la foto con /variables (o responde a una foto)."
        )
    return None


def _validate_refine_remote_path(p: str) -> bool:
    """Remote paths come from the box's stdout; restrict the charset so they can
    be embedded in the shell command safely (no quotes / meta-characters)."""
    return bool(p) and len(p) <= 300 and _REFINE_REMOTE_PATH_RE.fullmatch(p) is not None


def _is_comfy_video_model(comfyui_model: str) -> bool:
    return comfyui_model in COMFY_VIDEO_MODELS


class ComfyUIProvider:
    """Implements :class:`ImageProvider` and (for wan/minimax) :class:`VideoProvider`.

    ``host``/``port`` identify the SSH box; an ``SshClient`` may be injected
    (tests). An empty host disables the provider (``available is False``) and
    ``generate``/``refine`` raise :class:`ProviderNotConfiguredError`.
    """

    def __init__(self, host: str, port: int = 22, *, ssh: SshClient | None = None):
        self._host = host or ""
        self._port = int(port or 22)
        self._ssh = ssh if ssh is not None else SshClient(self._host, self._port)

    # -- state ------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._ssh.is_configured

    def supports(self, request: GenerationRequest) -> bool:
        if request.provider != "comfyui":
            return False
        if request.media_type is MediaType.IMAGE:
            return True
        if request.media_type is MediaType.VIDEO:
            cm = request.params.get("model") or DEFAULT_COMFYUI_MODEL
            return _is_comfy_video_model(cm)
        return False

    # -- internals --------------------------------------------------------
    def _model_lora(self, request: GenerationRequest) -> tuple[str, str]:
        """Resolve comfyui model/lora and validate against the known sets.

        Values are interpolated into a remote shell command; only catalog-valid
        identifiers are accepted so a crafted ``params`` value (quote, ``;``,
        ``$()``) can never break out of the single quotes (M1 hardening).
        """
        cm = str(request.params.get("model") or DEFAULT_COMFYUI_MODEL)
        cl = str(request.params.get("lora") or DEFAULT_COMFYUI_LORA)
        if cm not in VALID_COMFYUI_MODELS or cl not in VALID_COMFYUI_LORAS:
            raise ProviderInputError(
                "Configuración de ComfyUI inválida.",
                user_message="Error en la generación. Intenta de nuevo más tarde.",
            )
        return cm, cl

    @staticmethod
    def _gen_command(comfyui_model: str, lora: str) -> str:
        return f"MODEL='{comfyui_model}' LORA='{lora}' python3 /workspace/gen_comfy.py"

    def _raise_if_not_configured(self) -> None:
        if not self._ssh.is_configured:
            raise ProviderNotConfiguredError(
                _COMFY_NOT_CONFIGURED_MSG, user_message=_COMFY_NOT_CONFIGURED_MSG
            )

    async def _pull_and_build_result(
        self,
        request: GenerationRequest,
        comfyui_model: str,
        remotes: list[str],
    ) -> GenerationResult:
        locals_: list[str] = []
        for rp in remotes:
            local = await self._ssh.pull(rp)
            if local:
                locals_.append(local)
        if not locals_:
            raise ProviderUnavailableError(_NO_DOWNLOAD_MSG, user_message=_NO_DOWNLOAD_MSG)
        return GenerationResult(
            provider="comfyui",
            model_id=request.model_id,
            media_type=(
                MediaType.VIDEO if _is_comfy_video_model(comfyui_model) else MediaType.IMAGE
            ),
            file_path=locals_[0],
            meta={
                "file_paths": locals_,
                "comfyui_remotes": remotes,
                "download_allowlist": None,
            },
        )

    # -- generate / refine -----------------------------------------------
    async def generate(
        self,
        request: GenerationRequest,
        *,
        source_image: bytes | None = None,
    ) -> GenerationResult:
        self._raise_if_not_configured()
        cm, cl = self._model_lora(request)
        # M2: a VIDEO request with an image-only comfyui model is a caller seam —
        # never silently run an image generation and report IMAGE.
        if request.media_type is MediaType.VIDEO and not _is_comfy_video_model(cm):
            raise ProviderInputError(
                "El modelo ComfyUI configurado no genera video.",
                user_message="El modelo ComfyUI configurado no genera video.",
            )
        run_timeout = _COMFY_VIDEO_TIMEOUT if _is_comfy_video_model(cm) else _COMFY_IMAGE_TIMEOUT
        cmd = self._gen_command(cm, cl)

        if source_image is None:
            err = _comfy_precondition_error(cm, cl)
            if err:
                raise ProviderInputError(err, user_message=err)
            payload: str = request.prompt
        else:
            payload_obj: dict = {
                "prompt": request.prompt,
                "image_b64": base64.b64encode(source_image).decode("ascii"),
            }
            prompts = request.params.get("prompts")
            if prompts:
                payload_obj["prompts"] = prompts
            payload = json.dumps(payload_obj)

        remotes, _rc = await self._ssh.run_remote(cmd, payload, timeout=run_timeout)
        if not remotes:
            raise ProviderUnavailableError(
                _NO_IMAGE_RETURNED_MSG, user_message=_NO_IMAGE_RETURNED_MSG
            )
        return await self._pull_and_build_result(request, cm, remotes)

    async def refine(
        self,
        request: GenerationRequest,
        remote_paths: list[str],
    ) -> GenerationResult:
        """Second-stage refine of already-generated bases (``REFINE_ONLY=1``)."""
        self._raise_if_not_configured()
        if not remote_paths:
            raise ProviderInputError(
                "No hay imágenes base para refinar.",
                user_message="No hay imágenes base para refinar.",
            )
        invalid = [p for p in remote_paths if not _validate_refine_remote_path(p)]
        if invalid:
            raise ProviderInputError(
                "Paths de refino inválidos.",
                user_message="Error en la generación. Intenta de nuevo más tarde.",
            )
        cm, cl = self._model_lora(request)
        refine_timeout = REFINE_TIMEOUT_PER_BASE * len(remote_paths) + 300
        cmd = (
            f"MODEL='{cm}' LORA='{cl}' REFINE_ONLY='1' "
            f"REFINE_INPUT='{','.join(remote_paths)}' python3 /workspace/gen_comfy.py"
        )
        remotes, rc = await self._ssh.run_remote(cmd, request.prompt, timeout=refine_timeout)
        if rc == 2:
            raise ProviderInputError(
                "Configuración de refino inválida en el box.",
                user_message=_GENERIC_ERR,
            )
        if rc == 3:
            raise ProviderGenerationError(
                "El refino no produjo imágenes.",
                user_message=_GENERIC_ERR,
            )
        if not remotes:
            raise ProviderUnavailableError(
                _NO_IMAGE_RETURNED_MSG, user_message=_NO_IMAGE_RETURNED_MSG
            )
        return await self._pull_and_build_result(request, cm, remotes)
