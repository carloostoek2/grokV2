"""ComfyUI provider — image/video generation via ComfyUI's native HTTP/WS API.

The provider no longer runs scripts or scp on the box (the ``gen_comfy.py``
path). It reaches ComfyUI through an SSH local-forward tunnel
(:mod:`transport`) and drives the native API with :class:`ComfyApiClient`
(:mod:`client`): resolver un flujo por id (``workflows.resolver.get_flow`` —
cada flujo es un workflow API-format con su modelo/LoRA horneados), parchear
prompt/seeds, apilar los LoRAs **extra** del usuario sobre esa copia
(``workflows.loras``, sin tocar los horneados), encolar, esperar el run y
descargar los outputs a un ``tmpdir`` local siguiendo la convención
``GenerationResult`` (``file_path`` + ``meta["file_paths"]``).

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
from collections.abc import Mapping
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

from grokbot.domain.generation import GenerationRequest, GenerationResult, MediaType
from grokbot.domain.user_config import (
    COMFYUI_SEED_LIMIT,
    DEFAULT_COMFYUI_MODEL,
    is_valid_comfyui_seed,
    parse_lora_stack,
)
from grokbot.providers.base import (
    ProviderInputError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
)
from grokbot.providers.comfyui.client import ComfyApiClient
from grokbot.providers.comfyui.transport import SshLocalForward, TunnelConfig
from grokbot.providers.comfyui.workflows.loras import (
    LoraInjectionError,
    apply_lora_stack,
)
from grokbot.providers.comfyui.workflows.profiles import profile_for_device
from grokbot.providers.comfyui.workflows.resolver import (
    Flow,
    active_profile,
    configure_workflow_source,
    get_flow,
    patchable_seed_nodes,
    render,
    set_active_profile,
)

_COMFY_IMAGE_TIMEOUT = 600
_COMFY_VIDEO_TIMEOUT = 1500
# Cada cuánto se vuelve a preguntar la GPU al box (el perfil puede cambiar si
# el usuario recrea la sesión con otra tarjeta).
_PROFILE_REFRESH_SEC = 300.0
# Catálogo de LoRAs: cache corta para que navegar el menú no abra un túnel por tap.
_LORA_CACHE_TTL = 60.0

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
# Edit-only flow used WITHOUT a photo: otherwise the baked placeholder (e.g.
# ``example.png``) would be edited and a silently wrong result would be returned.
_SOURCE_REQUIRED_MSG = (
    "Este flujo edita una foto: envía la imagen con un caption describiendo "
    "el cambio que quieres."
)
_COMBO_UNAVAILABLE_MSG = "Configuración de ComfyUI aún no disponible."
_LORA_INJECTION_MSG = (
    "Este flujo no admite LoRAs adicionales. Intenta con otro flujo."
)

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
        weight_profile: str = "",
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
        # Perfil de pesos: "auto" se detecta de la GPU en el primer uso;
        # un id concreto ("a100"|"l4"|"t4") lo fuerza; "" no gestiona perfil.
        self._weight_profile = (weight_profile or "").strip().lower()
        self._profile_checked_at = 0.0
        # Source-of-truth for API graphs: Vast api_workflows/ (SSH) with embed fallback.
        configure_workflow_source(
            source=workflow_source,
            host=host or "",
            ssh_port=int(port or 22),
            workflows_dir=workflows_dir,
            cache_ttl=workflow_cache_ttl,
            profile=self._weight_profile or None,
        )
        self._client_factory = client_factory or (lambda url: ComfyApiClient(url))
        self._tmpdir = Path(tmpdir) if tmpdir is not None else _DEFAULT_TMPDIR
        self._seed = seed_factory or (lambda: random.randrange(COMFYUI_SEED_LIMIT))
        # (deadline monotonic, names) del catálogo de LoRAs del box.
        self._loras_cache: tuple[float, tuple[str, ...]] | None = None

    # -- state ------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._transport.is_configured

    async def refresh_profile(self, *, force: bool = False) -> bool:
        """Alinear el perfil de pesos con la GPU real del box.

        Devuelve ``True`` solo si cambió el perfil activo (el caller debe
        re-resolver el flujo). Solo actúa con ``weight_profile="auto"``. Se
        throttlea a ``_PROFILE_REFRESH_SEC`` para no consultar
        ``/system_stats`` en cada generación. Un fallo de red no rompe el
        request: se conserva el perfil anterior y se registra el motivo.
        """
        if self._weight_profile != "auto" or not self._transport.is_configured:
            return False
        now = time.monotonic()
        if not force and now - self._profile_checked_at < _PROFILE_REFRESH_SEC:
            return False
        # Nunca abrir un túnel desde la detección: si ComfyUI no está accesible
        # ya, se conserva el perfil y se reintenta en la próxima generación
        # (que sí hace ensure()). Evita reconexiones SSH innecesarias.
        base_url = self._transport.base_url
        if base_url is None:
            logger.debug("Sin túnel ComfyUI activo; se omite la detección de GPU.")
            return False
        self._profile_checked_at = now
        try:
            client = self._client_factory(base_url)
            try:
                stats = await client.system_stats()
            finally:
                close = getattr(client, "aclose", None)
                if close is not None:
                    await close()
        except Exception as exc:  # detección best-effort, nunca rompe generate
            logger.warning(
                "No se pudo detectar la GPU del box (%s); se conserva el perfil %s",
                type(exc).__name__,
                active_profile().id if active_profile() else "sin filtro",
            )
            return False
        devices = stats.get("devices") or []
        first = devices[0] if devices and isinstance(devices[0], dict) else {}
        name = str(first.get("name") or "")
        vram_gb = float(first.get("vram_total") or 0) / 1024**3
        profile = profile_for_device(name, vram_gb)
        # Dentro de un perfil, la VRAM real decide los flujos con requisito
        # (p. ej. ``agil_edit_nsfw`` en ``l4``: sí en 32 GB, no en una L4 24 GB).
        efectivo = profile.for_vram(vram_gb)
        actual = active_profile()
        if (
            actual is not None
            and actual.id == efectivo.id
            and actual.flows == efectivo.flows
        ):
            return False
        set_active_profile(profile.id, vram_gb=vram_gb)
        logger.info(
            "ComfyUI GPU %s (%.1f GB) -> perfil de pesos %s (%s), %d flujos",
            name or "desconocida",
            vram_gb,
            profile.id,
            profile.label,
            len(efectivo.flows),
        )
        return True

    def supports(self, request: GenerationRequest) -> bool:
        if request.provider != "comfyui":
            return False
        flow = get_flow(str(request.params.get("model") or DEFAULT_COMFYUI_MODEL))
        return flow is not None and flow.media_type is request.media_type

    async def list_loras(self) -> tuple[str, ...]:
        """LoRAs instalados en el box (``/models/loras``), ordenados.

        Cache corta por instancia: el menú de Telegram puede pedirlo en cada
        tap y no debe abrir un túnel ni un HTTP por cada uno. Un fallo de red
        propaga el error tipado del cliente (el caller lo degrada a user-safe).
        """
        self._raise_if_not_configured()
        now = time.monotonic()
        if self._loras_cache is not None and now < self._loras_cache[0]:
            return self._loras_cache[1]
        base_url = await self._transport.ensure()
        client = self._client_factory(base_url)
        try:
            names = await client.model_files("loras")
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()
        self._loras_cache = (now + _LORA_CACHE_TTL, tuple(names))
        return tuple(names)

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
        requested = str(request.params.get("model") or DEFAULT_COMFYUI_MODEL)
        flow = get_flow(requested)
        perfil = active_profile()
        if flow is None and perfil is not None and requested != perfil.default_flow:
            # Flujo guardado por el usuario que no existe en esta tarjeta
            # (p. ej. un *_hq seleccionado en la A100 y ahora corre en T4).
            flow = get_flow(perfil.default_flow)
            if flow is not None:
                logger.info(
                    "Flujo %s no disponible en perfil %s; usando %s",
                    requested,
                    perfil.id,
                    flow.id,
                )
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

    @staticmethod
    def _validate_flow(
        flow: Flow,
        request: GenerationRequest,
        source_image: bytes | None,
    ) -> None:
        """M2 + guards de foto: se validan antes de tocar el transporte."""
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
        # Reverse guard: an edit flow without a photo would run with the baked
        # placeholder and return a silently wrong result.
        if source_image is None and flow.requires_source:
            raise ProviderInputError(
                "La plantilla exige foto de entrada.",
                user_message=_SOURCE_REQUIRED_MSG,
            )

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
        *,
        seed: int | None = None,
    ) -> GenerationResult:
        if not locals_:
            raise ProviderUnavailableError(_NO_DOWNLOAD_MSG, user_message=_NO_DOWNLOAD_MSG)
        meta = {
            "file_paths": locals_,
            "download_allowlist": None,
        }
        if seed is not None:
            meta["seed"] = seed
        return GenerationResult(
            provider="comfyui",
            model_id=request.model_id,
            media_type=flow.media_type,
            file_path=locals_[0],
            meta=meta,
        )

    # -- generate ---------------------------------------------------------
    async def generate(
        self,
        request: GenerationRequest,
        *,
        source_image: bytes | None = None,
    ) -> GenerationResult:
        self._raise_if_not_configured()
        # M1/M2: validar flujo, tipo de media, foto y semilla ANTES de tocar el
        # transporte. La detección de GPU se hace después y, si cambia el
        # perfil, se vuelve a resolver el flujo para usar sus pesos.
        flow = self._resolve_flow(request)  # M1: id validado antes de tocar transporte.
        self._validate_flow(flow, request, source_image)

        seed = request.params.get("seed")
        if seed is None:
            seed = self._seed()
        if not is_valid_comfyui_seed(seed):
            raise ProviderInputError(
                "Semilla inválida.",
                user_message=(
                    "La semilla debe ser un entero entre 0 y "
                    f"{COMFYUI_SEED_LIMIT - 1}."
                ),
            )
        # Túnel + perfil: la detección solo consulta un túnel ya activo.
        base_url = await self._transport.ensure()
        if self._weight_profile == "auto" and await self.refresh_profile():
            flow = self._resolve_flow(request)
            # El fallback del perfil puede cambiar el tipo de flujo (p. ej. un
            # Ágil Edit guardado en una T4 cae a Grok Style T2I): revalidar
            # evita ignorar la foto o exigirla de más.
            self._validate_flow(flow, request, source_image)

        seed_nodes = patchable_seed_nodes(flow)
        if flow.seed_nodes and len(seed_nodes) != len(flow.seed_nodes):
            logger.warning(
                "ComfyUI flow %s declares seed_nodes %s but some have no literal 'seed' input",
                flow.id,
                flow.seed_nodes,
            )
        graph = render(flow, request.prompt, lambda: seed)
        # LoRAs extra del usuario: se apilan sobre la copia del request (la
        # plantilla no se modifica). Un grafo sin cadena de modelo se rechaza
        # antes de encolar en vez de correr sin el LoRA en silencio.
        # El stack se selecciona por el id del flujo RESUELTO, no por el pedido:
        # si el perfil de GPU obligó a caer al flujo default, se aplica el stack
        # de ese flujo (o ninguno), nunca el de un flujo distinto.
        stacks = request.params.get("lora_stacks")
        raw_stack = stacks.get(flow.id) if isinstance(stacks, Mapping) else None
        loras = parse_lora_stack(raw_stack)
        if loras:
            try:
                graph = apply_lora_stack(
                    graph, loras, positive_node=flow.positive_node
                )
            except LoraInjectionError as exc:
                logger.warning(
                    "ComfyUI flow %s cannot take extra LoRAs (%s)",
                    flow.id,
                    type(exc).__name__,
                )
                raise ProviderInputError(
                    "La plantilla no expone una cadena de modelo para el LoRA.",
                    user_message=_LORA_INJECTION_MSG,
                ) from exc
            logger.info(
                "ComfyUI flow %s: %d extra LoRA(s) applied", flow.id, len(loras)
            )
        run_timeout = flow.timeout or (
            _COMFY_VIDEO_TIMEOUT if flow.media_type is MediaType.VIDEO else _COMFY_IMAGE_TIMEOUT
        )

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
        return self._build_result(
            request, flow, locals_, seed=seed if seed_nodes and len(seed_nodes) == len(flow.seed_nodes) else None
        )


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
