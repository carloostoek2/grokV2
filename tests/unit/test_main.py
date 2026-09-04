"""Tests del composition root y entrypoint (item 6, Task 2).

Cubren el wiring de ``main.build_deps``/``assemble_dispatcher`` con repos JSON
reales en ``tmp_path`` y fakes de borde inyectados (gateway/downloader): 0 red,
0 token real, 0 ``unittest.mock``. Fixtures anonimizados (USER_ID/CHAT_ID
``111111111``); ``GROK_DATA_DIR`` siempre a ``tmp_path`` cuando se construyen
repos reales.

Carga los fakes del conftest unit de telegram con el MISMO loader importlib de
módulo alias cacheado (nunca ``from conftest import ...``, que puede resolver al
conftest raíz equivocado) y re-exporta lo necesario.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pathlib

import pydantic
import pytest
from aiogram import Bot
from aiogram.filters import Command

import grokbot.main as main
from grokbot.settings import Settings, get_settings
from grokbot.shared.errors import settings_error_user_message

# --- Cargar fakes del conftest unit de telegram (módulo alias cacheado) ---------
_TG_CONFTEST = pathlib.Path(__file__).resolve().parent / "telegram" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_tg_conftest", _TG_CONFTEST)
_tg_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tg_conftest)  # type: ignore[union-attr]

FakeTelegramGateway = _tg_conftest.FakeTelegramGateway
FakeMediaDownloader = _tg_conftest.FakeMediaDownloader
USER_ID = _tg_conftest.USER_ID
CHAT_ID = _tg_conftest.CHAT_ID
text_message = _tg_conftest.text_message
message_update = _tg_conftest.message_update
callback_query = _tg_conftest.callback_query

# --- Imports de clases concretas para asserts -----------------------------------
from grokbot.telegram.adapters.aiogram_gateway import AiogramGateway  # noqa: E402
from grokbot.telegram.downloader import AiohttpMediaDownloader  # noqa: E402
from grokbot.repositories.json_session_repo import JsonSessionRepository  # noqa: E402
from grokbot.repositories.json_variables_repo import JsonVariablesRepository  # noqa: E402
from grokbot.repositories.generation_refs_repo import JsonGenerationRefsRepository  # noqa: E402
from grokbot.application.refine_flow import RefineDecision  # noqa: E402
from grokbot.domain.user_config import UserConfig  # noqa: E402
from grokbot.providers import ProviderNotConfiguredError  # noqa: E402

# Token dummy en formato que aiogram acepta (solo formato; jamás se usa en red).
_DUMMY_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
_BOT = Bot("42:TEST")


def _set_base_env(monkeypatch, tmp_path) -> None:
    """Enviroment mínimo con las 3 required y ``GROK_DATA_DIR=tmp_path``."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _DUMMY_TOKEN)
    monkeypatch.setenv("REPLICATE_API_TOKEN", "rep-dummy")
    monkeypatch.setenv("XAI_API_KEY", "xai-dummy")
    monkeypatch.setenv("GROK_DATA_DIR", str(tmp_path))


def _sent_texts(gateway) -> list[str]:
    return [c["text"] for c in gateway.calls_by_method("send_message")]


# -- 1. Import probe (C1) ---------------------------------------------------------
def test_import_grokbot_main_sin_env_cero_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    importlib.reload(main)
    assert get_settings.cache_info().currsize == 0, "import no debe instanciar Settings"
    assert not (tmp_path / "data").exists(), "import no debe crear data/"
    assert not (tmp_path / "sessions.json").exists()


# -- 2. build_deps wiring real (D3) ------------------------------------------------
def test_build_deps_wiring_real(tmp_path, monkeypatch):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "111111111")
    monkeypatch.setenv("VARIABLES_ADMIN_IDS", "999")
    monkeypatch.setenv("REFINE_CONFIRM_TIMEOUT", "123")  # D3: timeout se cablea de Settings
    settings = get_settings()

    deps = main.build_deps(settings)

    # Gateway/downloader reales por defecto.
    assert isinstance(deps.gateway, AiogramGateway)
    assert deps.gateway.token == _DUMMY_TOKEN
    assert isinstance(deps.downloader, AiohttpMediaDownloader)

    # Repos JSON reales con paths derivados de Settings (tmp_path).
    assert isinstance(deps.sessions, JsonSessionRepository)
    assert deps.sessions._path == settings.sessions_file
    assert isinstance(deps.variables, JsonVariablesRepository)
    assert deps.variables._path == settings.variables_file
    assert deps.variables._packages_dir == settings.packages_dir
    assert isinstance(deps.refs, JsonGenerationRefsRepository)
    assert deps.refs._path == settings.generation_refs_file

    # El timeout de refine_uc se cablea desde Settings (D3: refinado confirmación).
    assert deps.refine_uc._timeout == 123.0

    # IDs allowlist/admin.
    assert deps.allowed_telegram_ids == {111111111}
    assert deps.variables_admin_ids == {999}

    # La construcción no crea archivos JSON (la creación es lazy en 1er acceso).
    assert not (tmp_path / "sessions.json").exists()
    assert not (tmp_path / "variables_lists.json").exists()
    assert not (tmp_path / "generation_refs.json").exists()


# -- 3. Refine hook cableado (D4) ---------------------------------------------------
async def test_refine_hook_cancel_resuelve_pendiente(tmp_path, monkeypatch):
    _set_base_env(monkeypatch, tmp_path)
    settings = get_settings()
    deps = main.build_deps(settings)

    uid = USER_ID
    job = deps.job_manager.start(uid, "batch")
    assert job is not None
    token = deps.refine_uc.register(user_id=uid, job_id=job.job_id)

    assert deps.job_manager.cancel(uid, job.job_id) is True
    assert await deps.refine_uc.await_decision(token) is RefineDecision.cancelled


# -- 4. Disponibilidad / degrade (D6) ------------------------------------------------
def test_providers_siempre_instanciados_y_degradan_por_available(tmp_path, monkeypatch):
    # Solo required: KIE/COMFYUI ausentes → available=False pero NUNCA None.
    _set_base_env(monkeypatch, tmp_path)
    settings = get_settings()
    registry = main._build_registry(settings)

    assert registry.is_available("xai") is True
    assert registry.is_available("replicate") is True
    assert registry.is_available("kie") is False
    assert registry.is_available("comfyui") is False

    kie = registry.provider("kie")
    comfyui = registry.provider("comfyui")
    assert kie is not None and kie.available is False
    assert comfyui is not None and comfyui.available is False

    with pytest.raises(ProviderNotConfiguredError):
        registry.resolve_image(UserConfig(model="comfyui"))


# -- 5. Fail-fast D5 ------------------------------------------------------------------
def test_build_deps_token_vacio_lanza_value_error_user_safe(tmp_path, monkeypatch):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")  # pasa pydantic, falla en build_deps
    settings = get_settings()

    with pytest.raises(ValueError) as excinfo:
        main.build_deps(settings)
    assert "TELEGRAM_BOT_TOKEN" in str(excinfo.value)
    assert "vacío" in str(excinfo.value)


# -- 6. Helper de error sin valores (D8) ---------------------------------------------
def test_settings_error_user_message_no_filtra_valores(monkeypatch):
    secreto = "xai-secret-value-abc123"
    monkeypatch.setenv("XAI_API_KEY", secreto)
    # Las otras 2 required ausentes → ValidationError al instanciar Settings().
    with pytest.raises(pydantic.ValidationError) as excinfo:
        Settings()

    msg = settings_error_user_message(excinfo.value)
    assert "telegram_bot_token" in msg
    assert "replicate_api_token" in msg
    assert secreto not in msg


# -- 7. Assembly smoke allowlist (C2) ---------------------------------------------------
async def test_assembly_smoke_allowlist_deny_y_allow(tmp_path, monkeypatch):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "111111111")
    settings = get_settings()
    gateway = FakeTelegramGateway()
    deps = main.build_deps(settings, gateway=gateway, downloader=FakeMediaDownloader())
    dp = main.assemble_dispatcher(deps)

    # Usuario fuera de la allowlist: deny sin correr el handler.
    await dp.feed_update(
        _BOT,
        message_update(text_message("/start", user_id=222222222, chat_id=222222222)),
    )
    texts = _sent_texts(gateway)
    assert any(t == "No tienes permiso para usar este bot." for t in texts)
    assert not any("Modelo actual:" in t for t in texts), "handler no debe correr"

    gateway.reset()

    # Usuario permitido: el handler /start corre (session repo real en tmp_path).
    await dp.feed_update(_BOT, message_update(text_message("/start")))
    texts = _sent_texts(gateway)
    assert any("Modelo actual:" in t for t in texts)


# -- 8. Error-handler global de updates (O2 arch: C3 sin secrets) -------------------
async def test_error_handler_global_loguea_solo_tipo_sin_payload(
    tmp_path, monkeypatch, caplog
):
    _set_base_env(monkeypatch, tmp_path)
    settings = get_settings()
    gateway = FakeTelegramGateway()
    deps = main.build_deps(settings, gateway=gateway, downloader=FakeMediaDownloader())
    dp = main.assemble_dispatcher(deps)

    # Handler sintético que lanza: deriva la excepción al @dp.errors.register que
    # registra assemble_dispatcher (el error-handler real; no se mockea). El payload
    # del raise nunca debe filtrarse al log (C3).
    async def _boom_handler(message) -> None:
        raise RuntimeError("SECRET-PAYLOAD-O2")

    dp.message.register(_boom_handler, Command("boom"))

    with caplog.at_level(logging.ERROR, logger="grokbot.main"):
        await dp.feed_update(_BOT, message_update(text_message("/boom")))

    assert any(
        r.name == "grokbot.main"
        and r.getMessage() == "Error procesando update (tipo=RuntimeError)."
        for r in caplog.records
    ), f"no se logueó el tipo de error: {caplog.text}"
    assert "SECRET-PAYLOAD-O2" not in caplog.text
    # C3: tampoco se filtran textos del update (p. ej. el comando) en el record.
    assert not any("/boom" in r.getMessage() for r in caplog.records)


# -- 9. run() captura errores fatales del polling (O1/C6) --------------------------
def test_run_polling_error_fatal_loguea_tipo_y_sale_1(tmp_path, monkeypatch, caplog):
    _set_base_env(monkeypatch, tmp_path)
    # No tocar el entorno/raíz reales del proceso de test.
    monkeypatch.setattr(main, "load_dotenv", lambda **_: None)
    monkeypatch.setattr(main, "configure_logging", lambda **_: None)

    async def _boom_polling(dp, deps) -> None:
        raise RuntimeError("SECRET-POLL-O1")

    monkeypatch.setattr(main, "run_polling", _boom_polling)

    with caplog.at_level(logging.ERROR, logger="grokbot.main"):
        code = main.run()

    assert code == 1
    assert any(
        r.name == "grokbot.main"
        and r.getMessage() == "Error fatal en el polling (tipo=RuntimeError)."
        for r in caplog.records
    ), f"no se logueó el tipo: {caplog.text}"
    assert "SECRET-POLL-O1" not in caplog.text, "C3: no filtra el payload del error"


# -- 10. Gate global de SOLO chat privado (R9) -------------------------------------
async def test_assembly_private_only_denies_group_message(tmp_path, monkeypatch):
    """R9: assemble_dispatcher adjunta el gate de chat privado; un grupo no corre handlers."""
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "111111111")  # el dueño pasaría la allowlist
    settings = get_settings()
    gateway = FakeTelegramGateway()
    deps = main.build_deps(settings, gateway=gateway, downloader=FakeMediaDownloader())
    dp = main.assemble_dispatcher(deps)

    # El dueño (allowlist OK) en un GRUPO → el gate de chat privado lo bloquea.
    await dp.feed_update(
        _BOT,
        message_update(text_message("/start", user_id=111111111, chat_id=222222222,
                                    chat_type="group")),
    )
    texts = _sent_texts(gateway)
    assert any(t == "Este bot solo funciona en chats privados." for t in texts)
    assert not any("Modelo actual:" in t for t in texts), "handler no debe correr en grupo"


# -- 11. run(): aviso de allowlist abierta en el boot (R9) --------------------------
def test_run_avisa_allowlist_abierta_en_boot(tmp_path, monkeypatch, caplog):
    """Sin ALLOWED_TELEGRAM_IDS el boot avisa (postura abierta de un solo owner)."""
    _set_base_env(monkeypatch, tmp_path)  # sin ALLOWED_TELEGRAM_IDS → abierta
    monkeypatch.setattr(main, "load_dotenv", lambda **_: None)
    monkeypatch.setattr(main, "configure_logging", lambda **_: None)

    async def _ok_polling(dp, deps) -> None:
        return None

    monkeypatch.setattr(main, "run_polling", _ok_polling)

    with caplog.at_level(logging.WARNING, logger="grokbot.main"):
        code = main.run()

    assert code == 0
    warns = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("ALLOWED_TELEGRAM_IDS" in w for w in warns), f"no se avisó: {warns}"


def test_run_no_avisa_con_allowlist_definida(tmp_path, monkeypatch, caplog):
    """Con la allowlist del owner definida, el boot no avisa."""
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "111111111")
    monkeypatch.setattr(main, "load_dotenv", lambda **_: None)
    monkeypatch.setattr(main, "configure_logging", lambda **_: None)

    async def _ok_polling(dp, deps) -> None:
        return None

    monkeypatch.setattr(main, "run_polling", _ok_polling)

    with caplog.at_level(logging.WARNING, logger="grokbot.main"):
        code = main.run()

    assert code == 0
    assert all("ALLOWED_TELEGRAM_IDS" not in r.getMessage() for r in caplog.records)
