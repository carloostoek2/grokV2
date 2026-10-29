"""Familias en el repo JSON: persistencia, activa y paquetes (retrocompatible)."""

from __future__ import annotations

import json

from grokbot.domain.families import Family
from grokbot.repositories.json_variables_repo import JsonVariablesRepository


def _repo(tmp_path) -> JsonVariablesRepository:
    repo = JsonVariablesRepository(tmp_path / "variables_lists.json")
    repo.get_lists()  # seed defaults
    return repo


def _family() -> Family:
    return Family(
        id="mirror",
        label="Mirror / Selfie",
        description="Autofotos",
        pools={"poses": ("de pie",)},
        avoid={"actions": ("caminando",)},
    )


def test_family_roundtrip_and_persistence(tmp_path):
    repo = _repo(tmp_path)
    assert repo.save_family(_family()) is True

    other = JsonVariablesRepository(tmp_path / "variables_lists.json")
    family = other.get_family("mirror")
    assert family is not None
    assert family.label == "Mirror / Selfie"
    assert family.pools == {"poses": ("de pie",)}
    assert family.avoid == {"actions": ("caminando",)}


def test_active_family_and_delete_guard(tmp_path):
    repo = _repo(tmp_path)
    repo.save_family(_family())

    assert repo.set_active_family("unknown") is False
    assert repo.set_active_family("mirror") is True
    assert repo.active_family_name() == "mirror"
    assert repo.delete_family("mirror") is False

    assert repo.set_active_family(None) is True
    assert repo.active_family_name() is None
    assert repo.delete_family("mirror") is True
    assert repo.get_family("mirror") is None


def test_old_file_without_families_is_tolerated(tmp_path):
    path = tmp_path / "variables_lists.json"
    path.write_text(
        json.dumps({"lists": {"poses": ["de pie"]}, "template": "{pose}"}),
        encoding="utf-8",
    )
    repo = JsonVariablesRepository(path)
    assert repo.get_families() == {}
    assert repo.active_family_name() is None


def test_package_roundtrip_with_families(tmp_path):
    repo = _repo(tmp_path)
    payload = {
        "lists": {"poses": ["de pie", "sentada"]},
        "template": "{pose}",
        "families": {
            "standing": {
                "label": "Standing",
                "pools": {"poses": ["de pie"]},
            }
        },
        "family": "standing",
    }
    ok, err = repo.save_package("standing_pack", payload)
    assert (ok, err) == (True, None)

    loaded = repo.load_package("standing_pack")
    assert loaded is not None
    assert loaded["families"]["standing"]["pools"]["poses"] == ["de pie"]

    assert repo.activate_package("standing_pack") is True
    assert repo.get_family("standing") is not None
    assert repo.active_family_name() == "standing"


def test_package_rejects_family_with_unknown_value(tmp_path):
    repo = _repo(tmp_path)
    payload = {
        "lists": {"poses": ["de pie"]},
        "template": "{pose}",
        "families": {"bad": {"label": "Bad", "pools": {"poses": ["volando"]}}},
    }
    ok, err = repo.save_package("bad_pack", payload)
    assert ok is False
    assert "no existe en la lista 'poses'" in str(err)


def test_package_rejects_non_dict_families(tmp_path):
    repo = _repo(tmp_path)
    ok, err = repo.save_package(
        "p",
        {"lists": {"poses": ["de pie"]}, "template": "{pose}", "families": []},
    )
    assert ok is False
    assert err == "'families' debe ser un objeto {id: {...}}."


def test_package_rejects_duplicate_family_ids_after_slug(tmp_path):
    repo = _repo(tmp_path)
    ok, err = repo.save_package(
        "p",
        {
            "lists": {"poses": ["de pie"]},
            "template": "{pose}",
            "families": {
                "Mirror / Selfie": {"label": "A", "pools": {"poses": ["de pie"]}},
                "mirror_selfie": {"label": "B", "pools": {"poses": ["de pie"]}},
            },
        },
    )
    assert ok is False
    assert "duplicados tras normalizar" in str(err)


def test_activate_package_preserves_unknown_top_level_keys(tmp_path):
    path = tmp_path / "variables_lists.json"
    path.write_text(
        json.dumps(
            {
                "lists": {"poses": ["de pie"]},
                "template": "{pose}",
                "blacklist": [],
                "_extra_top": {"keep": True},
            }
        ),
        encoding="utf-8",
    )
    repo = JsonVariablesRepository(path)
    repo.save_package(
        "p",
        {"lists": {"poses": ["sentada"]}, "template": "{pose}"},
    )
    assert repo.activate_package("p") is True

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["_extra_top"] == {"keep": True}
    assert raw["lists"] == {"poses": ["sentada"]}


def test_package_without_families_keeps_definitions_but_clears_active(tmp_path):
    repo = _repo(tmp_path)
    repo.save_package(
        "with_fam",
        {
            "lists": {"poses": ["de pie"]},
            "template": "{pose}",
            "families": {"f": {"label": "F", "pools": {"poses": ["de pie"]}}},
            "family": "f",
        },
    )
    repo.activate_package("with_fam")
    assert repo.get_family("f") is not None

    repo.save_package("plain", {"lists": {"poses": ["de pie"]}, "template": "{pose}"})
    repo.activate_package("plain")
    # Switching to a legacy package must not destroy hand-authored families.
    assert repo.get_family("f") is not None
    assert repo.active_family_name() is None


def test_package_family_ids_are_slugified(tmp_path):
    repo = _repo(tmp_path)
    payload = {
        "lists": {"poses": ["de pie"]},
        "template": "{pose}",
        "families": {
            "Mirror / Selfie": {"label": "Mirror", "pools": {"poses": ["de pie"]}}
        },
        "family": "Mirror / Selfie",
    }
    ok, err = repo.save_package("pack", payload)
    assert (ok, err) == (True, None)

    loaded = repo.load_package("pack")
    assert "mirror_selfie" in loaded["families"]
    assert loaded["family"] == "mirror_selfie"
