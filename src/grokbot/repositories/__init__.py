"""Infrastructure persistence package — repository contracts + JSON backends.

Re-exports the public repository API. Concrete backends are added task by task
(``JsonSessionRepository`` in task 3, ``JsonVariablesRepository``/``DEFAULT_LISTS``
in task 4, ``JsonGenerationRefsRepository`` in task 5). Since item 6 (R7), the
package re-exports lazily via PEP 562 (module ``__getattr__``): importing
``grokbot.repositories`` or any layer that imports it does NOT load the JSON
backends until a public name is actually accessed. ``__all__`` es idéntico al
eager anterior — la API pública no cambia, solo se aplaza la carga.
"""

from __future__ import annotations

import importlib

__all__ = [
    "SessionRepository",
    "VariablesRepository",
    "GenerationRefsRepository",
    "write_json_atomic",
    "JsonSessionRepository",
    "JsonVariablesRepository",
    "DEFAULT_LISTS",
    "JsonGenerationRefsRepository",
]

# Mapa nombre público → submódulo que lo define (carga lazy bajo demanda).
_SOURCES = {
    "grokbot.repositories.base": [
        "SessionRepository",
        "VariablesRepository",
        "GenerationRefsRepository",
        "write_json_atomic",
    ],
    "grokbot.repositories.json_session_repo": ["JsonSessionRepository"],
    "grokbot.repositories.json_variables_repo": ["JsonVariablesRepository", "DEFAULT_LISTS"],
    "grokbot.repositories.generation_refs_repo": ["JsonGenerationRefsRepository"],
}
_NAMES = {name: mod for mod, names in _SOURCES.items() for name in names}


def __getattr__(name: str):
    """Resolver un nombre público del paquete importando su submódulo (PEP 562).

    Para cualquier otro nombre (submódulos como ``base``, dunder, etc.) se
    levanta ``AttributeError`` para que el import system resuelva los submódulos
    con normalidad y los proxies de herramienta no rompan.
    """
    module = _NAMES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__():
    return sorted(set(globals()) | set(__all__))
