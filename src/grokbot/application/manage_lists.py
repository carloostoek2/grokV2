"""Gestión de listas de variables — ManageListsUseCase (item 4).

API de retorno directo (ítem 5 formatea el copy). Las listas editables se derivan
de ``get_lists()`` (O1/O2): una constante ``LIST_NAMES`` ausente del archivo
activo (p.ej. un paquete que no trae ``poses``) NO se ofrece y operarla devuelve
no-editable sin tocar el repo — nunca KeyError ni contaminación del archivo.
"""

from __future__ import annotations

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
