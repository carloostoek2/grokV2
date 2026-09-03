"""Infrastructure persistence package — repository contracts + JSON backends.

Re-exports the public repository API. Concrete backends are added task by task
(``JsonSessionRepository`` in task 3, ``JsonVariablesRepository``/``DEFAULT_LISTS``
in task 4, ``JsonGenerationRefsRepository`` in task 5).
"""

from __future__ import annotations

from grokbot.repositories.base import (
    GenerationRefsRepository,
    SessionRepository,
    VariablesRepository,
    write_json_atomic,
)
from grokbot.repositories.generation_refs_repo import JsonGenerationRefsRepository
from grokbot.repositories.json_session_repo import JsonSessionRepository
from grokbot.repositories.json_variables_repo import DEFAULT_LISTS, JsonVariablesRepository

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
