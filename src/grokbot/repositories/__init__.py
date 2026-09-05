"""Paquete de persistencia — contratos de repositorio + backends JSON.

Re-exporta la API pública de repositorios. Los backends concretos se agregan
tarea por tarea (``JsonSessionRepository`` en task 3,
``JsonVariablesRepository``/``DEFAULT_LISTS`` en task 4,
``JsonGenerationRefsRepository`` en task 5). Desde el ítem 6 (R7), el paquete
re-exporta de forma lazy vía PEP 562 (``__getattr__`` de módulo): importar
``grokbot.repositories`` (o cualquier capa que lo importe) NO carga los backends
JSON hasta que se accede a un nombre público. ``__all__`` es idéntico al eager
anterior — la API pública no cambia, solo se aplaza la carga.
"""

from __future__ import annotations

import importlib

__all__ = [
    "SessionRepository",
    "VariablesRepository",
    "GenerationRefsRepository",
    "SourceFacesRepository",
    "IntegrateRefsRepository",
    "write_json_atomic",
    "JsonSessionRepository",
    "JsonVariablesRepository",
    "DEFAULT_LISTS",
    "JsonGenerationRefsRepository",
    "DiskSourceFacesRepository",
    "DiskIntegrateRefsRepository",
]

# Mapa nombre público → submódulo que lo define (carga lazy bajo demanda).
_SOURCES = {
    "grokbot.repositories.base": [
        "SessionRepository",
        "VariablesRepository",
        "GenerationRefsRepository",
        "SourceFacesRepository",
        "IntegrateRefsRepository",
        "write_json_atomic",
    ],
    "grokbot.repositories.json_session_repo": ["JsonSessionRepository"],
    "grokbot.repositories.json_variables_repo": ["JsonVariablesRepository", "DEFAULT_LISTS"],
    "grokbot.repositories.generation_refs_repo": ["JsonGenerationRefsRepository"],
    "grokbot.repositories.disk_source_faces": ["DiskSourceFacesRepository"],
    "grokbot.repositories.disk_integrate_refs": ["DiskIntegrateRefsRepository"],
}
_NAMES = {name: mod for mod, names in _SOURCES.items() for name in names}


def __getattr__(name: str):
    """Resuelve un nombre público del paquete importando su submódulo (PEP 562).

    Para cualquier otro nombre (submódulos como ``base``, dunder, etc.) se
    levanta ``AttributeError`` para que el import system resuelva los submódulos
    con normalidad y las herramientas no rompan.
    """
    module = _NAMES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__():
    return sorted(set(globals()) | set(__all__))
