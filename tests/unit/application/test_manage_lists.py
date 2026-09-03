"""Tests de ManageListsUseCase — listas derivadas de get_lists() (O1/O2/R7).

Dos fixtures: (a) FakeVariablesRepo legacy (poses/angles/actions) para CRUD /
validate / paquetes / blacklist; (b) JsonVariablesRepository real sobre tmp_path
con un paquete activo sin ``poses`` para verificar que una constante ausente del
archivo activo NO se ofrece ni contamina (sin KeyError).
"""

from __future__ import annotations

import json

from grokbot.application.manage_lists import ManageListsUseCase
from grokbot.repositories.json_variables_repo import JsonVariablesRepository

from conftest import FakeVariablesRepo


def _uc(variables):
    return ManageListsUseCase(variables=variables)


# --- (a) FakeVariablesRepo legacy ---------------------------------------------

def test_list_names_and_get_list():
    repo = FakeVariablesRepo(
        lists={"poses": ["de pie", "sentada"], "angles": ["frontal"], "actions": ["mirando"]}
    )
    uc = _uc(repo)
    assert uc.list_names() == ("poses", "angles", "actions")
    assert uc.get_list("poses") == ["de pie", "sentada"]
    assert uc.get_list("missing") is None  # nunca KeyError


def test_validate_rules():
    uc = _uc(FakeVariablesRepo(lists={"poses": ["de pie"]}))
    assert uc.validate_item("ok") is None
    assert uc.validate_item("x") == "El texto es demasiado corto (mínimo 2 caracteres)."
    assert uc.validate_item("") == "El texto es demasiado corto (mínimo 2 caracteres)."
    assert uc.validate_template("{pose} {angle} {action}") is None
    assert uc.validate_template("ab") == "La plantilla es demasiado corta (mínimo 3 caracteres)."


def test_add_update_delete_items():
    repo = FakeVariablesRepo(lists={"poses": ["de pie"]})
    uc = _uc(repo)

    assert uc.add_item("poses", "  sentada  ") is True
    assert uc.get_list("poses") == ["de pie", "sentada"]

    assert uc.add_item("poses", "sentada") is False  # duplicado → repo False
    assert uc.add_item("poses", "x") is False  # demasiado corto → no llama repo
    assert uc.add_item("no-such", "algo") is False

    assert uc.update_item("poses", 0, "saltando") is True
    assert uc.get_list("poses")[0] == "saltando"

    assert uc.delete_item("poses", 1) is True
    assert uc.get_list("poses") == ["saltando"]
    assert uc.delete_item("poses", 99) is False


def test_set_template_and_validate_gate():
    repo = FakeVariablesRepo(lists={"poses": ["de pie"]}, template="{pose}, {angle}, {action}")
    uc = _uc(repo)

    assert uc.set_template("  {pose} {action}  ") is True
    assert uc.template() == "{pose} {action}"

    assert uc.set_template("ab") is False  # validate gate antes del repo
    assert uc.template() == "{pose} {action}"


def test_packages_and_blacklist_fake_repo():
    repo = FakeVariablesRepo(lists={"poses": ["de pie"]})
    uc = _uc(repo)
    payload = {"lists": {"bodies": ["a", "b"], "hands": ["c"]}, "template": "{body} {hands}"}

    assert uc.save_package("mi pack", payload) == (True, None)
    assert uc.list_packages() == ["mi_pack"]
    assert uc.package_content("mi pack") == payload
    assert uc.active_package() is None

    assert uc.activate_package("mi pack") is True
    assert uc.active_package() == "mi_pack"
    assert set(uc.list_names()) == {"bodies", "hands"}

    # No se puede borrar el paquete activo; activar otro / borrar el inactivo sí.
    assert uc.delete_package("mi pack") is False
    assert uc.save_package("otro", payload) == (True, None)
    assert uc.activate_package("otro") is True
    assert uc.delete_package("mi_pack") is True
    assert uc.list_packages() == ["otro"]

    # blacklist
    repo2 = FakeVariablesRepo(lists={"poses": ["a"]}, blacklist={("a", "b")})
    uc2 = _uc(repo2)
    assert uc2.get_blacklist() == {("a", "b")}
    uc2.blacklist_clear()
    assert uc2.get_blacklist() == set()


# --- (b) JsonVariablesRepository real + paquete activo (O1/O2) ----------------

def test_package_active_without_constant_list(tmp_path):
    repo = JsonVariablesRepository(
        tmp_path / "variables_lists.json",
        packages_dir=tmp_path / "packages",
    )
    payload = {
        "lists": {
            "bodies": ["guerrera", "exploradora"],
            "hands": ["sosteniendo espada", "manos vacías"],
            "angles": ["frontal", "perfil"],
        },
        "template": "{body} {hands} {angle}",
    }
    assert repo.save_package("mi-paquete", payload) == (True, None)
    assert repo.activate_package("mi-paquete") is True

    uc = _uc(repo)

    # La fuente de verdad es el archivo activo: bodies/hands/angles.
    assert set(uc.list_names()) == {"bodies", "hands", "angles"}
    assert uc.get_list("poses") is None  # constante ausente → None (sin KeyError)
    assert uc.get_list("bodies") == ["guerrera", "exploradora"]
    assert uc.template() == "{body} {hands} {angle}"


def test_add_constant_absent_does_not_contaminate_active_file(tmp_path):
    repo = JsonVariablesRepository(
        tmp_path / "variables_lists.json",
        packages_dir=tmp_path / "packages",
    )
    payload = {
        "lists": {
            "bodies": ["guerrera", "exploradora"],
            "hands": ["sosteniendo espada", "manos vacías"],
            "angles": ["frontal", "perfil"],
        },
        "template": "{body} {hands} {angle}",
    }
    repo.save_package("mi-paquete", payload)
    repo.activate_package("mi-paquete")
    uc = _uc(repo)

    assert uc.add_item("poses", "de pie") is False  # O1: no se ofrece
    assert uc.update_item("poses", 0, "x") is False
    assert uc.delete_item("poses", 0) is False

    # O2: el archivo activo NO gana el campo `poses`.
    active = json.loads((tmp_path / "variables_lists.json").read_text(encoding="utf-8"))
    assert "poses" not in active["lists"]
    assert set(active["lists"]) == {"bodies", "hands", "angles"}

    # El CRUD sobre listas reales del paquete funciona y persiste.
    assert uc.add_item("bodies", "ninja") is True
    active2 = json.loads((tmp_path / "variables_lists.json").read_text(encoding="utf-8"))
    assert "ninja" in active2["lists"]["bodies"]
