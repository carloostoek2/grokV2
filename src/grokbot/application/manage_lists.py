"""Gestión de listas de variables — ManageListsUseCase (item 4).

API de retorno directo (ítem 5 formatea el copy). Las listas editables se derivan
de ``get_lists()`` (O1/O2): una constante ``LIST_NAMES`` ausente del archivo
activo (p.ej. un paquete que no trae ``poses``) NO se ofrece y operarla devuelve
no-editable sin tocar el repo — nunca KeyError ni contaminación del archivo.
"""

from __future__ import annotations

from grokbot.domain.families import (
    Family,
    family_payload_error,
    normalize_family,
    validate_family,
)
from grokbot.repositories.base import VariablesRepository

_ITEM_TOO_SHORT_MSG = "El texto es demasiado corto (mínimo 2 caracteres)."
_TEMPLATE_TOO_SHORT_MSG = "La plantilla es demasiado corta (mínimo 3 caracteres)."


class ManageListsUseCase:
    """CRUD de listas/template/paquetes de variables (delega a VariablesRepository)."""

    def __init__(self, *, variables: VariablesRepository) -> None:
        self._variables = variables

    # -- lectura ------------------------------------------------------------

    def list_names(self) -> tuple[str, ...]:
        """Nombres de listas editables: las del archivo activo (orden de archivo)."""
        return tuple(self._variables.get_lists())

    def get_list(self, name: str) -> list[str] | None:
        """Lista activa, o None si el nombre está ausente del archivo activo."""
        lists = self._variables.get_lists()
        if name not in lists:
            return None
        return list(lists[name])

    def template(self) -> str:
        return self._variables.get_template()

    def get_blacklist(self) -> set[tuple[str, ...]]:
        """Blacklist actual (lectura para la UI de ítem 5)."""
        return self._variables.get_blacklist()

    # -- validación ---------------------------------------------------------

    def validate_item(self, text: str) -> str | None:
        """Regla de copy de ítem: mínimo 2 caracteres (stripped). None si válido."""
        if not isinstance(text, str) or len(text.strip()) < 2:
            return _ITEM_TOO_SHORT_MSG
        return None

    def validate_template(self, text: str) -> str | None:
        """Regla de copy de plantilla: mínimo 3 caracteres. None si válido."""
        if not isinstance(text, str) or len(text.strip()) < 3:
            return _TEMPLATE_TOO_SHORT_MSG
        return None

    # -- CRUD ---------------------------------------------------------------

    def add_item(self, name: str, item: str) -> bool:
        """Agregar ítem a la lista activa; False si la lista no está activa."""
        if name not in self.list_names():
            return False
        if self.validate_item(item) is not None:
            return False
        return self._variables.add_item(name, item.strip())

    def update_item(self, name: str, index: int, item: str) -> bool:
        if name not in self.list_names():
            return False
        if self.validate_item(item) is not None:
            return False
        return self._variables.update_item(name, index, item.strip())

    def delete_item(self, name: str, index: int) -> bool:
        if name not in self.list_names():
            return False
        return self._variables.delete_item(name, index)

    def set_template(self, template: str) -> bool:
        if self.validate_template(template) is not None:
            return False
        return self._variables.set_template(template.strip())

    def blacklist_clear(self) -> None:
        return self._variables.blacklist_clear()

    # -- familias -----------------------------------------------------------

    def list_families(self) -> dict[str, Family]:
        """Familias definidas en el archivo activo, por id."""
        return self._variables.get_families()

    def get_family(self, family_id: str) -> Family | None:
        return self._variables.get_family(family_id)

    def active_family_name(self) -> str | None:
        return self._variables.active_family_name()

    def active_family(self) -> Family | None:
        """Familia activa resuelta, o None si no hay o ya no existe."""
        family_id = self._variables.active_family_name()
        return self._variables.get_family(family_id) if family_id else None

    def save_family(self, family_id: str, payload: dict) -> tuple[bool, str | None]:
        """Create/replace a family, validating its shape and active-list values."""
        shape_err = family_payload_error(payload)
        if shape_err:
            return False, shape_err
        family = normalize_family(family_id, payload)
        if family is None:
            return False, "Los datos de la familia no son válidos."
        err = validate_family(family, self._variables.get_lists())
        if err:
            return False, err
        if not self._variables.save_family(family):
            return False, "No se pudo guardar la familia."
        return True, None

    def delete_family(self, family_id: str) -> bool:
        return self._variables.delete_family(family_id)

    def activate_family(self, family_id: str | None) -> tuple[bool, str | None]:
        """Activate a family, re-validating it against the current lists.

        A family kept across a package switch may reference fields/values the
        new dataset does not have; activating it silently would ignore the
        restriction, so it fails closed with Spanish copy.
        """
        if family_id is None:
            return self._variables.set_active_family(None), None
        family = self._variables.get_family(family_id)
        if family is None:
            return False, "La familia ya no existe."
        err = validate_family(family, self._variables.get_lists())
        if err:
            return False, f"La familia no encaja con las listas activas: {err}"
        return self._variables.set_active_family(family_id), None

    # -- paquetes (delegan al repo) ----------------------------------------

    def list_packages(self) -> list[str]:
        return self._variables.list_packages()

    def package_content(self, name: str) -> dict | None:
        return self._variables.load_package(name)

    def save_package(self, name: str, payload: dict) -> tuple[bool, str | None]:
        return self._variables.save_package(name, payload)

    def activate_package(self, name: str) -> bool:
        return self._variables.activate_package(name)

    def delete_package(self, name: str) -> bool:
        return self._variables.delete_package(name)

    def active_package(self) -> str | None:
        return self._variables.active_package_name()
