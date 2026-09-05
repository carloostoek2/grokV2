"""Caso de uso de generación de imágenes (single) — retry/exhausted tipado (item 4).

Reimplementa el bucle de reintentos de grok ``generate_image`` (bot.py
3753-3802): se reintenta SOLO cuando ``ProviderError.retryable is True`` (M3),
con backoff ``POLL_RETRY_BACKOFF_SEC[min(attempt, 2)]``; los errores terminales
NUNCA se reintentan. Emite eventos tipados que item 5 traduce.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from grokbot.application._retry import GENERATE_MAX_RETRIES, _retry_backoff
from grokbot.application.events import ItemFailed, ItemResult, RetryScheduled
from grokbot.application.integrate_refs import REQUIRES_XAI_MSG
from grokbot.domain.generation import GenerationRequest, ImageSource, KieTaskRef, MediaType
from grokbot.domain.user_config import UserConfig, is_comfy_video_model
from grokbot.providers.base import ProviderError, ProviderInputError
from grokbot.providers.registry import ProviderRegistry, ProviderResolution
from grokbot.repositories.base import SessionRepository

# Mensajes user-safe (R6); el copy final lo arma item 5.
_COMFY_VIDEO_NOT_IMAGE_MSG = "El modelo ComfyUI configurado genera video, no imágenes."
_UNSUPPORTED_IMAGE_MSG = "El modelo configurado no puede generar esta imagen."
_GENERIC_ERROR_MSG = "Error en la generación. Intenta de nuevo más tarde."

# --- Helpers puros -----------------------------------------------------------

def _comfy_params(cfg: UserConfig, prompts: list[str] | None) -> dict:
    """Parámetros ComfyUI: el id de flujo (``cfg.comfyui.model``).

    El modelo/LoRA del flujo los define su workflow; lora/refine quedaron
    dormidos. ``prompts`` multipose ya no aplica (sin flujo qwen).
    """
    params = {"model": cfg.comfyui.model}
    if prompts:
        params["prompts"] = list(prompts)
    return params


def build_image_request(
    cfg: UserConfig,
    res: ProviderResolution,
    prompt: str,
    source: ImageSource | None,
    prompts: list[str] | None,
) -> GenerationRequest:
    """Arma el :class:`GenerationRequest` de imagen (payload builders por provider).

    ``source`` se setea solo cuando el caller lo provee (un ref de tipo URL/file
    no se resuelve acá — D2: 0 descargas en use cases).
    """
    params = _comfy_params(cfg, prompts) if res.name == "comfyui" else {}
    return GenerationRequest(
        provider=res.name,
        model_id=res.model_id,
        media_type=MediaType.IMAGE,
        prompt=prompt,
        source=source,
        params=params,
    )


def _build_image_regen_context(
    *,
    cfg: UserConfig,
    user_id: int,
    res: ProviderResolution,
    prompt: str,
    source_image: bytes | None,
    source: ImageSource | None,
    source_file_id: str | None,
    integrate_mode: bool = False,
) -> dict:
    """Contexto de regeneración (item 5 lo persiste opaco; sin URLs/payloads).

    Espejo de grok ``_build_image_regen_context`` (bot.py:780-810): guarda el
    modo, el modelo activo, provider y (para grok) el proveedor/variante de
    Grok Imagine, más el ref de archivo/Kie cuando aplica. Con
    ``integrate_mode=True`` se estampa la marca para que el regen re-descargue la
    ref por ``user_id`` (A10) — NUNCA el path/file_id de la referencia (R6/R8).
    """
    ctx = {
        "mode": "edit" if (source_image is not None or source is not None) else "text",
        "model_key": cfg.model,
        "user_id": user_id,
        "prompt": prompt,
        "provider": res.name,
    }
    if cfg.model == "grok":
        ctx["imagine_provider"] = cfg.grok_imagine_provider
        ctx["imagine_variant"] = cfg.grok_imagine_variant
    if source_file_id:
        ctx["source_file_id"] = source_file_id
    if isinstance(source, KieTaskRef):
        ctx["kie_source_ref"] = {"task_id": source.task_id, "index": source.index}
    if integrate_mode:
        ctx["integrate_mode"] = True
    return ctx


async def _edit_with_reference(provider, request, source_image, reference_image):
    """Duck-type del seam xAI de 2 imágenes (fuera de los Protocols).

    Solo se invoca con provider efectivo ``xai`` (guard en ``run``); los demás
    providers NO soportan la edición contra una segunda imagen de referencia. Un
    provider sin el método es un fallo terminal user-safe (nunca ``repr``).
    """
    edit = getattr(provider, "edit_with_reference", None)
    if edit is None:
        raise ProviderInputError(
            "Provider does not support edit_with_reference.",
            user_message="Error en la generación. Intenta de nuevo más tarde.",
        )
    return await edit(request, source_image=source_image, reference_image=reference_image)


class GenerateImageUseCase:
    """Generación single de imagen (t2i/i2i) con retry de transitorios.

    Providers y repositorios inyectados por constructor (sin imports globales).
    """

    def __init__(self, *, sessions: SessionRepository, registry: ProviderRegistry) -> None:
        self._sessions = sessions
        self._registry = registry

    async def run(
        self,
        *,
        user_id: int,
        prompt: str,
        source_image: bytes | None = None,
        source: ImageSource | None = None,
        source_file_id: str | None = None,
        prompts: list[str] | None = None,
        index: int | None = None,
        total: int | None = None,
        cfg_override: UserConfig | None = None,
        reference_image: bytes | None = None,
    ) -> AsyncIterator[ItemResult | ItemFailed | RetryScheduled]:
        # cfg_override (aditivo D2 del item 5): el caller (regen/edit) puede fijar
        # la config efectiva del request sin re-leer sessions (el usuario pudo
        # cambiar /config entre base y confirm; paridad grok ``_process_single_photo_edit``).
        cfg = cfg_override if cfg_override is not None else self._sessions.get_config(user_id)

        # 1. Resolver el provider de imagen para la config.
        try:
            res = self._registry.resolve_image(cfg)
        except ProviderError as err:
            yield ItemFailed(
                reason=str(err.user_message),
                prompt=prompt,
                terminal=True,
                index=index,
                total=total,
            )
            return

        request = build_image_request(cfg, res, prompt, source, prompts)

        # 1b. Guard integrate: la edición con referencia SOLO va por xAI (el wire
        # de 2 imágenes es ``edit_with_reference``, fuera de ImageProvider). El
        # provider no-xai con reference_image emite el copy REQUIRES_XAI_MSG
        # (el handler ya hizo preflight; esto es el guard defensivo del use case).
        if reference_image is not None and res.name != "xai":
            yield ItemFailed(
                reason=REQUIRES_XAI_MSG,
                prompt=prompt,
                terminal=True,
                index=index,
                total=total,
            )
            return

        # 2. Guard M2: validar media_type ANTES de generar.
        if res.name == "comfyui" and is_comfy_video_model(cfg.comfyui.model):
            yield ItemFailed(
                reason=_COMFY_VIDEO_NOT_IMAGE_MSG,
                prompt=prompt,
                terminal=True,
                index=index,
                total=total,
            )
            return
        if res.provider.supports(request) is False:
            yield ItemFailed(
                reason=_UNSUPPORTED_IMAGE_MSG,
                prompt=prompt,
                terminal=True,
                index=index,
                total=total,
            )
            return

        # 3. Loop de intentos (primer intento + GENERATE_MAX_RETRIES reintentos).
        # El cronómetro arranca ANTES del loop (paridad grok bot.py:3772/3791/3794):
        # el elapsed incluye reintentos + backoff, y se escribe en el meta del
        # resultado exitoso (única salida) para que el sender lo muestre en el caption.
        started = time.monotonic()
        for attempt in range(GENERATE_MAX_RETRIES + 1):
            try:
                if reference_image is not None:
                    result = await _edit_with_reference(
                        res.provider, request, source_image, reference_image
                    )
                else:
                    result = await res.provider.generate(request, source_image=source_image)
            except ProviderError as err:
                if err.retryable and attempt < GENERATE_MAX_RETRIES:
                    yield RetryScheduled(
                        attempt=attempt + 2,
                        max_attempts=GENERATE_MAX_RETRIES + 1,
                        provider=res.name,
                        index=index,
                        total=total,
                    )
                    await asyncio.sleep(_retry_backoff(attempt))
                    continue
                if err.retryable:  # agotado
                    yield ItemFailed(
                        reason=str(err.user_message),
                        prompt=prompt,
                        exhausted=True,
                        index=index,
                        total=total,
                    )
                    return
                # Terminal: nunca se reintenta (M3).
                yield ItemFailed(
                    reason=str(err.user_message),
                    prompt=prompt,
                    terminal=True,
                    index=index,
                    total=total,
                )
                return
            except Exception:
                # R6: nunca exponer ``repr(exc)`` al usuario.
                yield ItemFailed(
                    reason=_GENERIC_ERROR_MSG,
                    prompt=prompt,
                    terminal=True,
                    index=index,
                    total=total,
                )
                return

            # Tiempo real de la generación (incluye reintentos). Mutación in-place
            # del dict mutable de meta: preserva la identidad del GenerationResult
            # que los tests y el sender esperan (nunca dataclasses.replace). Si un
            # resultado llegara con ``meta=None`` (el sender lo tolera con ``or {}``),
            # no se marca y el caption queda con "…".
            if result.meta is not None:
                result.meta["elapsed_sec"] = int(time.monotonic() - started)
            ctx = _build_image_regen_context(
                cfg=cfg,
                user_id=user_id,
                res=res,
                prompt=prompt,
                source_image=source_image,
                source=source,
                source_file_id=source_file_id,
                integrate_mode=reference_image is not None,
            )
            yield ItemResult(
                result=result,
                prompt=prompt,
                index=index,
                total=total,
                regen_context=ctx,
                request=request,
            )
            return
