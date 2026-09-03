"""Conftest del smoke e2e offline de telegram (item 5, Task 4).

Carga ``tests/unit/telegram/conftest.py`` con un loader ``importlib`` (módulo
alias cacheado ``_unit_tg_conftest``) y re-exporta TODO su contenido público,
para que ``test_routing.py`` arme un ``Dispatcher`` con ``MemoryStorage`` +
``Bot("42:TEST")`` vía ``make_dispatcher`` y alimente updates con
``dp.feed_update`` — 0 red / 0 token / 0 ``unittest.mock``.

El re-export total (superset) evita que, al correr ``tests/unit/telegram`` y
``tests/e2e/telegram`` juntos, el ``import conftest`` de los tests unit resuelva
a este módulo y pierda nombres (mismo patrón que el conftest unit, que re-exporta
todo el conftest de application).
"""

from __future__ import annotations

import importlib.util
import pathlib

_UNIT_CONFTEST = pathlib.Path(__file__).resolve().parents[2] / "unit" / "telegram" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_unit_tg_conftest", _UNIT_CONFTEST)
_unit_tg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_unit_tg)  # type: ignore[union-attr]

# Re-export total del conftest unit (superset; evita romper el `import conftest`
# de los tests unit cuando pytest corre ambos directorios juntos).
for _name, _value in vars(_unit_tg).items():
    if not _name.startswith("__"):
        globals()[_name] = _value
del _name, _value
