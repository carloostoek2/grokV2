"""Caso de uso de generación de imágenes (single) — retry/exhausted tipado (item 4).

Reimplementa el bucle de reintentos de grok ``generate_image`` (bot.py
3753-3802): se reintenta SOLO cuando ``ProviderError.retryable is True`` (M3),
con backoff ``POLL_RETRY_BACKOFF_SEC[min(attempt, 2)]``; los errores terminales
NUNCA se reintentan. Emite eventos tipados que item 5 traduce.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from grokbot.application._retry import GENERATE_MAX_RETRIES, _retry_backoff
from grokbot.application.events import ItemFailed, ItemResult, RetryScheduled
from grokbot.domain.generation import GenerationRequest, ImageSource, KieTaskRef, MediaType
from grokbot.domain.user_config import UserConfig, is_comfy_video_model
from grokbot.providers.base import ProviderError
from grokbot.providers.registry import ProviderRegistry, ProviderResolution
from grokbot.repositories.base import SessionRepository

# Mensajes user-safe (R6); el copy final lo arma item 5.
_COMFY_VIDEO_NOT_IMAGE_MSG = "El modelo ComfyUI configurado genera video, no imágenes."
_UNSUPPORTED_IMAGE_MSG = "El modelo configurado no puede generar esta imagen."
_GENERIC_ERROR_MSG = "Error en la generación. Intenta de nuevo más tarde."

# --- Helpers puros -----------------------------------------------------------

def _comfy_params(cfg: UserConfig, prompts: list[str] | None) -> dict:
    """Parámetros ComfyUI (model/lora/refine) + ``prompts`` multipose si aplica."""
    params = {
        "model": cfg.comfyui.model,
        "lora": cfg.comfyui.lora,
        "refine": cfg.comfyui.refine,
    }
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
) -> dict:
    """Contexto de regeneración (item 5 lo persiste opaco; sin URLs/payloads).

    Espejo de grok ``_build_image_regen_context`` (bot.py:780-810): guarda el
    modo, el modelo activo, provider y (para grok) el proveedor/variante de
    Grok Imagine, más el ref de archivo/Kie cuando aplica.
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
    return ctx


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
    ) -> AsyncIterator[ItemResult | ItemFailed | RetryScheduled]:
        cfg = self._sessions.get_config(user_id)

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
        for attempt in range(GENERATE_MAX_RETRIES + 1):
            try:
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

            ctx = _build_image_regen_context(
                cfg=cfg,
                user_id=user_id,
                res=res,
                prompt=prompt,
                source_image=source_image,
                source=source,
                source_file_id=source_file_id,
            )
            yield ItemResult(
                result=result,
                prompt=prompt,
                index=index,
                total=total,
                regen_context=ctx,
            )
            return
