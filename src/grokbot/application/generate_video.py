"""Caso de uso de generación de video (single-attempt) — sin retry (item 4).

Paridad grok: el video es single-attempt (el provider hace submit+poll interno
y la cancelación es por cancelación del task asyncio que lo corre). Valida
media_type ANTES de generar (M2): un request VIDEO contra un provider/modelo de
imagen es un seam de caller y termina sin llamar a ``generate``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from grokbot.application.events import ItemFailed, ItemResult
from grokbot.domain.generation import GenerationRequest, ImageSource, MediaType
from grokbot.domain.user_config import UserConfig
from grokbot.providers.base import ProviderError
from grokbot.providers.registry import ProviderRegistry, ProviderResolution
from grokbot.repositories.base import SessionRepository

_GENERIC_ERROR_MSG = "Error en la generación. Intenta de nuevo más tarde."


def build_video_request(
    cfg: UserConfig,
    res: ProviderResolution,
    prompt: str,
    source: ImageSource | None,
) -> GenerationRequest:
    """Arma el :class:`GenerationRequest` de video desde la config del usuario.

    El ``video_model`` viaja en ``model_id`` (el ``GenerationRequest`` de domain
    no tiene campo ``video_model``; los providers lo leen de ``model_id``). Para
    ComfyUI se pasan model/lora en ``params`` para el guard ``supports()``.
    """
    params = {"model": cfg.comfyui.model, "lora": cfg.comfyui.lora} if res.name == "comfyui" else {}
    return GenerationRequest(
        provider=res.name,
        model_id=res.model_id,
        media_type=MediaType.VIDEO,
        prompt=prompt,
        source=source,
        aspect_ratio=cfg.video.aspect_ratio,
        video_duration=cfg.video.duration,
        video_resolution=cfg.video.resolution,
        video_mode=cfg.video.mode,
        params=params,
    )


class GenerateVideoUseCase:
    """Generación single de video (t2v/i2v) con single attempt (paridad grok)."""

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
        index: int | None = None,
        total: int | None = None,
    ) -> AsyncIterator[ItemResult | ItemFailed]:
        cfg = self._sessions.get_config(user_id)

        try:
            res = self._registry.resolve_video(cfg)
        except ProviderError as err:
            yield ItemFailed(
                reason=str(err.user_message),
                prompt=prompt,
                terminal=True,
                index=index,
                total=total,
            )
            return

        request = build_video_request(cfg, res, prompt, source)

        # Guard M2: comfyui con modelo de imagen en un request VIDEO se rechaza.
        if res.provider.supports(request) is False:
            yield ItemFailed(
                reason="El modelo ComfyUI configurado no genera video.",
                prompt=prompt,
                terminal=True,
                index=index,
                total=total,
            )
            return

        try:
            result = await res.provider.generate(request, source_image=source_image)
        except ProviderError as err:
            yield ItemFailed(
                reason=str(err.user_message),
                prompt=prompt,
                terminal=not err.retryable,
                index=index,
                total=total,
            )
            return
        except Exception:
            yield ItemFailed(
                reason=_GENERIC_ERROR_MSG,
                prompt=prompt,
                terminal=True,
                index=index,
                total=total,
            )
            return

        yield ItemResult(result=result, prompt=prompt, index=index, total=total)
