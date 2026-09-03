"""Integration tests for JsonVariablesRepository (item 3, task 4).

Fixtures are anonymized with the same structural shape as grok's real
variables_lists.json / variables_packages files.
"""

from __future__ import annotations

import json

import pytest

from grokbot.domain.variables import DEFAULT_TEMPLATE
from grokbot.repositories.json_variables_repo import DEFAULT_LISTS, JsonVariablesRepository


def package_active_doc() -> dict:
    """A hand-created (package-active) file shape that must NOT be seeded."""
    return {
        "lists": {
            "bodies": ["cuerpo A", "cuerpo B"],
            "hands": ["mano X"],
            "angles": ["ángulo 1"],
        },
        "template": "{bodies}, {hands}, {angles}",
        "blacklist": [["cuerpo A", "mano X", "ángulo 1"]],
        "_package": "anon",
        "_extra_top": "keep",
    }


def write(path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_new_file_seeds_defaults(tmp_path):
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    lists = repo.get_lists()
    assert set(lists) == {"poses", "angles", "actions"}
    for name in ("poses", "angles", "actions"):
        assert lists[name] == DEFAULT_LISTS[name]
    assert repo.get_template() == DEFAULT_TEMPLATE
    assert repo.get_blacklist() == set()
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {"lists", "template", "blacklist"}


def test_empty_and_corrupt_file_seed_without_raise(tmp_path):
    path = tmp_path / "variables_lists.json"
    path.write_text("{}", encoding="utf-8")
    repo = JsonVariablesRepository(path)
    assert set(repo.get_lists()) == {"poses", "angles", "actions"}
    path.write_text("{invalid", encoding="utf-8")
    repo2 = JsonVariablesRepository(path)
    assert set(repo2.get_lists()) == {"poses", "angles", "actions"}


def test_package_active_file_not_seeded_and_preserved(tmp_path):
    path = tmp_path / "variables_lists.json"
    write(path, package_active_doc())
    repo = JsonVariablesRepository(path)
    lists = repo.get_lists()
    assert set(lists) == {"bodies", "hands", "angles"}
    assert "poses" not in lists
    # a mutation preserves _package and unknown top-level keys
    assert repo.add_item("bodies", "cuerpo C")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["_package"] == "anon"
    assert raw["_extra_top"] == "keep"
    assert raw["lists"]["bodies"] == ["cuerpo A", "cuerpo B", "cuerpo C"]
    assert raw["template"] == "{bodies}, {hands}, {angles}"
    assert raw["blacklist"] == [["cuerpo A", "mano X", "ángulo 1"]]


def test_set_template_preserves_package_active_keys(tmp_path):
    path = tmp_path / "variables_lists.json"
    write(path, package_active_doc())
    repo = JsonVariablesRepository(path)
    assert repo.set_template("{bodies}, {hands}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["_package"] == "anon"
    assert raw["_extra_top"] == "keep"
    assert raw["template"] == "{bodies}, {hands}"


def test_crud_add_update_delete(tmp_path):
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    assert set(repo.get_lists()) == {"poses", "angles", "actions"}  # seed
    # add_item
    assert repo.add_item("poses", DEFAULT_LISTS["poses"][0]) is False  # duplicate
    assert repo.add_item("poses", "   ") is False  # blank
    assert repo.add_item("zzz", "x") is False  # unknown name
    assert repo.add_item("poses", "new pose") is True
    assert "new pose" in repo.get_list("poses")
    # update_item
    assert repo.update_item("poses", 999, "x") is False  # out of range
    assert repo.update_item("poses", 0, DEFAULT_LISTS["poses"][0]) is True  # same text no-op
    assert repo.update_item("poses", 1, DEFAULT_LISTS["poses"][0]) is False  # duplicate
    assert repo.update_item("poses", 0, "updated pose") is True
    assert repo.get_list("poses")[0] == "updated pose"
    # delete_item
    assert repo.delete_item("poses", 999) is False  # out of range
    assert repo.delete_item("poses", 0) is True
    assert "updated pose" not in repo.get_list("poses")


def test_get_list_unknown_raises(tmp_path):
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    repo.get_lists()  # seed
    with pytest.raises(ValueError):
        repo.get_list("zzz")


def test_set_template_validation(tmp_path):
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    assert repo.set_template("") is False
    assert repo.set_template("   ") is False
    assert repo.set_template("{pose} only") is True
    assert repo.get_template() == "{pose} only"


def test_blacklist(tmp_path):
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    assert repo.blacklist_add(("a", "b")) is True
    assert repo.blacklist_add(("a", "b")) is False  # duplicate
    assert repo.get_blacklist() == {("a", "b")}
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["blacklist"] == [["a", "b"]]
    repo.blacklist_clear()
    assert repo.get_blacklist() == set()
    assert json.loads(path.read_text(encoding="utf-8"))["blacklist"] == []


def test_is_valid_list_name(tmp_path):
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    assert repo.is_valid_list_name("poses") is True  # constant
    assert repo.is_valid_list_name("zzz") is False
    write(path, package_active_doc())
    repo2 = JsonVariablesRepository(path)
    assert repo2.is_valid_list_name("bodies") is True  # self-describing package field
    assert repo2.is_valid_list_name("poses") is True  # constant always valid
    assert repo2.is_valid_list_name("zzz") is False


def test_packages_save_list_load_activate_delete(tmp_path):
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    # save two packages: one via the "fields" alias, one canonical
    ok, err = repo.save_package(
        "anon",
        {"lists": {"bodies": ["cuerpo A"], "hands": ["mano X"]}, "template": "{bodies}, {hands}"},
    )
    assert ok and err is None
    ok, err = repo.save_package("Mi Paquete!", {"fields": {"cuerpos": ["A"]}, "template": "{cuerpos}"})
    assert ok and err is None
    pkg_file = path.parent / "variables_packages" / "mi_paquete.json"
    assert pkg_file.exists()
    raw_pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    assert "fields" not in raw_pkg
    assert raw_pkg["lists"] == {"cuerpos": ["A"]}
    assert repo.list_packages() == ["anon", "mi_paquete"]
    assert repo.package_exists("anon") is True
    assert repo.package_exists("missing") is False
    loaded = repo.load_package("anon")
    assert loaded["lists"]["bodies"] == ["cuerpo A"]
    assert loaded["template"] == "{bodies}, {hands}"
    # activate
    assert repo.activate_package("anon") is True
    active = json.loads(path.read_text(encoding="utf-8"))
    assert active["_package"] == "anon"
    assert active["blacklist"] == []
    assert active["lists"] == {"bodies": ["cuerpo A"], "hands": ["mano X"]}
    assert active["template"] == "{bodies}, {hands}"
    # delete active -> False; delete missing -> False
    assert repo.delete_package("anon") is False
    assert repo.delete_package("otro") is False
    # invalid payload -> (False, error)
    ok, err = repo.save_package("nuevo", {"template": "solo"})
    assert ok is False
    assert isinstance(err, str)


def test_delete_package_non_active_removes_file(tmp_path):
    """D3: deleting an existing, non-active package succeeds and removes the file."""
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    assert repo.save_package("victima", {"lists": {"cuerpos": ["A"]}, "template": "{cuerpos}"}) == (True, None)
    pkg = path.parent / "variables_packages" / "victima.json"
    assert pkg.exists()
    assert repo.delete_package("victima") is True
    assert not pkg.exists()
    assert repo.delete_package("victima") is False  # now missing


def test_activate_package_missing_returns_false(tmp_path):
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    assert repo.activate_package("missing") is False


def test_dump_flags_variables_and_package_ensure_ascii_false(tmp_path):
    path = tmp_path / "variables_lists.json"
    repo = JsonVariablesRepository(path)
    repo.get_lists()  # seed
    assert repo.add_item("angles", "ángulo 1")
    text = path.read_text(encoding="utf-8")
    assert "ángulo" in text  # literal accent, NOT á (ensure_ascii=False, D9)
    assert "\\u00e1" not in text
    # package file also keeps literal accents
    ok, _ = repo.save_package("pkg", {"lists": {"a": ["á"]}, "template": "x"})
    assert ok
    pkg_text = (path.parent / "variables_packages" / "pkg.json").read_text(encoding="utf-8")
    assert "á" in pkg_text
    assert "\\u00e1" not in pkg_text
