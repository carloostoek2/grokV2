"""Composition root y entrypoint del bot grokbot (item 6, Fase 6 SPEC).

Cablea las capas 1–5 en un bot arrancable con paridad de arranque de
``grok/bot.py``: ``load_dotenv`` al boot (39-46) → ``Settings`` fail-fast →
4 providers siempre instanciados (D6) → ``ProviderRegistry`` → repos JSON
(paths derivados) → use cases + ``JobManager(refine_hook=…)`` (D4) → ``BotDeps``
→ ``Dispatcher(MemoryStorage)`` con allowlist attachada a message y
callback_query (paridad grok 976-977) → ``register_all`` → polling
(``dp.start_polling``, paridad 5492-5494). Sin webhook (D9).

Invariante C1: importar este módulo NO construye nada (0 side-effects): no crea
``data/``, no instancia ``Settings``/``Bot``/providers ni lee el entorno. Todo
vive dentro de funciones; ``load_dotenv()`` ocurre dentro de ``run()``.

Sistemas sensibles (C3): los mensajes de arranque y de error jamás incluyen
tokens/IDs/payloads. Los errores de ``Settings`` se formatean por NOMBRES de
campo (``shared/errors.py``) y el error-handler global de updates loguea solo
``type(exception).__name__``.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from pydantic import ValidationError

from grokbot.application.generate_image import GenerateImageUseCase
from grokbot.application.generate_video import GenerateVideoUseCase
from grokbot.application.job_manager import JobManager
from grokbot.application.manage_config import UpdateUserConfigUseCase
from grokbot.application.manage_lists import ManageListsUseCase
from grokbot.application.refine_flow import ResolveRefineUseCase
from grokbot.application.run_variable_batch import RunVariableBatchUseCase
from grokbot.providers import (
    ComfyUIProvider,
    KieProvider,
    ProviderRegistry,
    ReplicateProvider,
    XaiProvider,
)
from grokbot.repositories import (
    JsonGenerationRefsRepository,
    JsonSessionRepository,
    JsonVariablesRepository,
)
from grokbot.settings import Settings, get_settings
from grokbot.shared.errors import settings_error_user_message
from grokbot.shared.logging import configure_logging
from grokbot.telegram.adapters.aiogram_gateway import AiogramGateway
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.downloader import AiohttpMediaDownloader
from grokbot.telegram.handlers import register_all
from grokbot.telegram.middlewares import AllowlistMiddleware
from grokbot.telegram.ports import MediaDownloader, TelegramGateway

__all__ = [
    "Settings",
    "get_settings",
    "build_deps",
    "assemble_dispatcher",
    "run_polling",
    "run",
]


def _build_providers(
    settings: Settings,
) -> tuple[XaiProvider, ReplicateProvider, KieProvider, ComfyUIProvider]:
    """Instanciar SIEMPRE los 4 providers (D6).

    Un token/host vacío NO produce ``None``: el provider se construye igual y
    queda con ``available is False`` (el registry degrada a
    ``ProviderNotConfiguredError`` al resolver). Así ``ResolveRefineUseCase`` y
    el registry reciben siempre objetos tipados, nunca ``None``.
    """
    return (
        XaiProvider(settings.xai_api_key),
        ReplicateProvider(settings.replicate_api_token),
        KieProvider(settings.kie_api_key),
        ComfyUIProvider(settings.comfyui_host, settings.comfyui_port),
    )


def _build_registry(settings: Settings) -> ProviderRegistry:
    """ProviderRegistry con los 4 providers reales (helper testeable)."""
    xai, replicate, kie, comfyui = _build_providers(settings)
    return ProviderRegistry(xai=xai, replicate=replicate, kie=kie, comfyui=comfyui)


def build_deps(
    settings: Settings,
    *,
    gateway: TelegramGateway | None = None,
    downloader: MediaDownloader | None = None,
) -> BotDeps:
    """Composición de deps con repos JSON reales y seams inyectables (D3).

    El grafo sigue el orden del impacto: providers → registry → repos (paths
    derivados de Settings) → refine_uc → JobManager(refine_hook) → use cases →
    BotDeps. ``gateway``/``downloader`` admiten fakes para smoke offline (en
    producción se construyen el ``AiogramGateway`` real y el
    ``AiohttpMediaDownloader``).

    D5 (fail-fast): un ``telegram_bot_token`` vacío lanza ``ValueError`` user-safe
    aquí mismo — ``Settings()`` acepta ``""`` y ``Bot("")`` no es un error claro
    (aiogram lo rechaza recién al validar el token, sin contexto).
    """
    if not settings.telegram_bot_token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN no puede estar vacío. Configúralo en el entorno o en el archivo .env."
        )

    xai, replicate, kie, comfyui = _build_providers(settings)
    registry = ProviderRegistry(xai=xai, replicate=replicate, kie=kie, comfyui=comfyui)

    sessions = JsonSessionRepository(settings.sessions_file)
    variables = JsonVariablesRepository(
        settings.variables_file, packages_dir=settings.packages_dir
    )
    refs = JsonGenerationRefsRepository(settings.generation_refs_file)

    refine_uc = ResolveRefineUseCase(
        provider=registry.provider("comfyui"), timeout=settings.refine_confirm_timeout
    )
    # D4: cancel/finish de un job resuelve a `cancelled` las confirmaciones de
    # refine pendientes de ese user/job (paridad grok 716-748).
    job_manager = JobManager(refine_hook=refine_uc.cancel_for_job)

    generate_image = GenerateImageUseCase(sessions=sessions, registry=registry)
    generate_video = GenerateVideoUseCase(sessions=sessions, registry=registry)
    run_batch = RunVariableBatchUseCase(
        sessions=sessions,
        registry=registry,
        variables=variables,
        job_manager=job_manager,
        generate_image=generate_image,
    )
    update_config = UpdateUserConfigUseCase(sessions=sessions)
    manage_lists = ManageListsUseCase(variables=variables)

    gateway = gateway if gateway is not None else AiogramGateway(settings.telegram_bot_token)
    downloader = downloader if downloader is not None else AiohttpMediaDownloader()

    return BotDeps(
        gateway=gateway,
        downloader=downloader,
        refs=refs,
        sessions=sessions,
        variables=variables,
        job_manager=job_manager,
        refine_uc=refine_uc,
        generate_image=generate_image,
        generate_video=generate_video,
        run_batch=run_batch,
        update_config=update_config,
        manage_lists=manage_lists,
        allowed_telegram_ids=settings.allowed_telegram_ids,
        variables_admin_ids=settings.variables_admin_ids,
    )


def assemble_dispatcher(deps: BotDeps) -> Dispatcher:
    """Armar el :class:`Dispatcher` con allowlist attachada y todos los handlers.

    Attacha :class:`AllowlistMiddleware` a ``dp.message`` y ``dp.callback_query``
    SIEMPRE (paridad grok 976-977), aunque ``allowed_ids is None`` (el middleware
    con ``None`` deja pasar a todos — bot abierto por defecto). Registra el
    error-handler global que loguea SOLO el tipo de la excepción (C3, sin update
    ni ``str(exception)``, que podría filtrar payloads).
    """
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AllowlistMiddleware(deps.gateway, deps.allowed_telegram_ids))  # grok 976
    dp.callback_query.middleware(  # grok 977
        AllowlistMiddleware(deps.gateway, deps.allowed_telegram_ids)
    )

    register_all(dp, deps)

    @dp.errors.register
    async def _on_update_error(event) -> None:
        """Error de update: loguea solo ``type(exception).__name__`` (C3)."""
        logger = logging.getLogger(__name__)
        logger.error("Error procesando update (tipo=%s).", type(event.exception).__name__)

    return dp


async def run_polling(dp: Dispatcher, deps: BotDeps) -> None:
    """Arrancar el polling (paridad grok 5492-5494); sin webhook (D9).

    ``deps.gateway`` es el MISMO objeto ``AiogramGateway(Bot)`` que se pasa a
    ``start_polling`` (paridad grok: el bot del gateway es el bot del polling).
    El shutdown limpio sale de los defaults de aiogram (``handle_signals=True``,
    ``close_bot_session=True``): al parar, aiogram cierra la sesión del bot y
    las tareas de handlers/futures de refine mueren con el loop (C7).
    """
    await dp.start_polling(deps.gateway)


def run() -> int:
    """Entrypoint CLI (console script ``grokbot = grokbot.main:run``).

    Orden de boot: ``load_dotenv`` → ``configure_logging`` → ``get_settings``
    (fail-fast pydantic) → ``mkdir(data_dir)`` (D7: fail-fast de permisos) →
    ``build_deps`` (D5) → ``assemble_dispatcher`` → ``asyncio.run(run_polling)``.

    Los errores de arranque salen con código no-cero y mensaje user-safe: los de
    ``Settings`` listan SOLO nombres de campo; el branch genérico jamás imprime
    ``str(exc)`` (podría filtrar secretos).

    Nota D7/C5: el ``data_dir`` default es relativo al cwd (``./data``); correr
    el bot desde la raíz del repo o setear ``GROK_DATA_DIR`` absoluto en deploy.
    """
    load_dotenv()
    configure_logging()
    logger = logging.getLogger(__name__)
    try:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)  # D7: fail-fast permisos
        deps = build_deps(settings)  # D5 puede lanzar ValueError (token vacío)
        dp = assemble_dispatcher(deps)
    except ValidationError as exc:  # subclase de ValueError → va ANTES
        msg = settings_error_user_message(exc)  # shared/errors: SOLO nombres de campo
        logger.error("%s", msg)
        print(msg, file=sys.stderr)
        return 2
    except ValueError as exc:  # D5: el mensaje ya es user-safe
        msg = str(exc)
        logger.error("%s", msg)
        print(msg, file=sys.stderr)
        return 2
    except Exception as exc:  # genérico: jamás str(exc) (podría filtrar secretos)
        logger.error("Error al iniciar el bot (tipo=%s).", type(exc).__name__)
        print("Error al iniciar el bot. Revisa los logs.", file=sys.stderr)
        return 1

    logger.info(
        "grokbot listo. data_dir=%s allowlist=%s. Modo: polling.",
        settings.data_dir.resolve(),
        "activa" if settings.allowed_telegram_ids else "abierta",
    )
    try:
        asyncio.run(run_polling(dp, deps))
    except KeyboardInterrupt:
        logger.info("Interrupción recibida; cerrando.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
