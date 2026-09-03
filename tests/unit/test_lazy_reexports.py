"""Probe de lazy re-exports PEP 562 (item 6, D2 — cierra R7).

Tras convertir ``providers/__init__.py`` y ``repositories/__init__.py`` a lazy,
importar la capa telegram (que antes arrastraba por efecto secundario los 4
providers concretos + ssh_client + los 3 JSON repos) NO debe cargar esos
submódulos, y la API pública del paquete-raíz debe seguir funcionando.

Corre en un SUBPROCESO limpio (``sys.executable -c``) con el entorno depurado
(se quitan las ``SETTINGS_ENV_VARS``), para que el resultado no dependa de lo
que pytest ya importó en el proceso padre. 0 red / 0 ``unittest.mock``.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

# Mismas variables que tests/conftest.py (no se importa para no acoplar al
# módulo de pytest); el hijo no debe ver env que un Settings() leería.
SETTINGS_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "REPLICATE_API_TOKEN",
    "XAI_API_KEY",
    "KIE_API_KEY",
    "COMFYUI_HOST",
    "COMFYUI_PORT",
    "ALLOWED_TELEGRAM_IDS",
    "VARIABLES_ADMIN_IDS",
    "REFINE_CONFIRM_TIMEOUT",
    "GROK_DATA_DIR",
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_SRC = _REPO_ROOT / "src"

# Submódulos concretos que el eager previo de los __init__ cargaba al importar
# ``grokbot.telegram.handlers`` (medición pre-D2). Con lazy NO deben estar.
_CONCRETE_PROVIDERS = (
    "grokbot.providers.xai_provider",
    "grokbot.providers.replicate_provider",
    "grokbot.providers.kie_provider",
    "grokbot.providers.comfyui",
    "grokbot.providers.comfyui.provider",
    "grokbot.providers.comfyui.ssh_client",
)
_CONCRETE_REPOS = (
    "grokbot.repositories.json_session_repo",
    "grokbot.repositories.json_variables_repo",
    "grokbot.repositories.generation_refs_repo",
)


def _run_clean_probe(script_body: str) -> subprocess.CompletedProcess:
    """Correr ``script_body`` en un python limpio con ``PYTHONPATH=src``."""
    env = os.environ.copy()
    for var in SETTINGS_ENV_VARS:
        env.pop(var, None)
    env["PYTHONPATH"] = str(_SRC)
    script = (
        "import sys\n"
        "import grokbot.telegram.handlers\n"
        + script_body
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _assert_ok(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0, (
        f"probe falló (rc={proc.returncode})\n--- stdout ---\n{proc.stdout}"
        f"\n--- stderr ---\n{proc.stderr}"
    )


def test_providers_lazy_no_arrastra_concretos_ni_rompe_api():
    body = (
        "\n".join(f"assert {m!r} not in sys.modules, f'cargado: {m}'" for m in _CONCRETE_PROVIDERS)
        + "\n"
        + (
            "from grokbot.providers import ("
            "XaiProvider, ComfyUIProvider, SshClient, ProviderRegistry, "
            "ProviderResolution, ProviderError)\n"
            "print('providers api ok')\n"
        )
    )
    proc = _run_clean_probe(body)
    _assert_ok(proc)


def test_repositories_lazy_no_arrastra_json_repos_ni_rompe_api():
    body = (
        "\n".join(f"assert {m!r} not in sys.modules, f'cargado: {m}'" for m in _CONCRETE_REPOS)
        + "\n"
        + (
            "from grokbot.repositories import ("
            "JsonSessionRepository, JsonVariablesRepository, "
            "JsonGenerationRefsRepository, write_json_atomic)\n"
            "print('repositories api ok')\n"
        )
    )
    proc = _run_clean_probe(body)
    _assert_ok(proc)
