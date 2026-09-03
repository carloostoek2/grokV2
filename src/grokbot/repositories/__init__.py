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

__all__ = [
    "SessionRepository",
    "VariablesRepository",
    "GenerationRefsRepository",
    "write_json_atomic",
]
