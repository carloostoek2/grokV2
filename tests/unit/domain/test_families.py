"""Unit tests for grokbot.domain.families (coherent value pools)."""

from __future__ import annotations

from grokbot.domain.families import (
    Family,
    allowed_values,
    effective_lists,
    family_payload_error,
    family_to_record,
    normalize_family,
    validate_family,
)

LISTS = {
    "poses": ["standing", "walking", "lying"],
    "actions": ["smiling", "walking casually"],
    "locations": ["mirror", "street"],
}


def test_normalize_family_record():
    family = normalize_family(
        "mirror",
        {
            "label": "Mirror",
            "description": " selfies ",
            "pools": {"poses": ["standing", "standing", ""], "actions": []},
            "avoid": {"actions": ["walking casually"]},
        },
    )
    assert family == Family(
        id="mirror",
        label="Mirror",
        description="selfies",
        pools={"poses": ("standing",)},
        avoid={"actions": ("walking casually",)},
    )


def test_normalize_family_accepts_preferred_alias_and_label_fallback():
    family = normalize_family("mirror", {"preferred": {"poses": ["standing"]}})
    assert family is not None
    assert family.label == "mirror"
    assert family.pools == {"poses": ("standing",)}


def test_family_payload_error_accepts_valid_shape():
    assert family_payload_error({"label": "M", "pools": {"poses": ["a"]}, "avoid": {}}) is None


def test_family_payload_error_rejects_empty_pool():
    assert family_payload_error({"pools": {"poses": []}}) == (
        "'poses' debe ser una lista de valores no vacía."
    )


def test_family_payload_error_rejects_non_dict_pools():
    assert family_payload_error({"pools": ["a"]}) == (
        "'pools' debe ser un objeto {campo: [valores]}."
    )


def test_family_payload_error_rejects_scalar_field():
    assert family_payload_error({"pools": {"poses": "a"}}) == (
        "'poses' debe ser una lista de valores no vacía."
    )


def test_family_payload_error_rejects_non_dict_payload():
    assert family_payload_error([]) == "La familia debe ser un objeto JSON."


def test_family_payload_error_rejects_invalid_items():
    expected = "'poses' contiene un valor inválido (debe ser texto no vacío)."
    for items in ([""], ["   "], [123], [None], ["de pie", 123]):
        assert family_payload_error({"pools": {"poses": items}}) == expected


def test_normalize_family_rejects_invalid_structure():
    assert normalize_family("", {}) is None
    assert normalize_family("mirror", []) is None
    assert normalize_family("mirror", "nope") is None


def test_family_to_record_roundtrip():
    family = Family(id="m", label="M", description="d", pools={"poses": ("a",)}, avoid={"actions": ("b",)})
    assert family_to_record(family) == {
        "label": "M",
        "description": "d",
        "pools": {"poses": ["a"]},
        "avoid": {"actions": ["b"]},
    }
    assert normalize_family("m", family_to_record(family)) == family


def test_allowed_values_without_family_returns_base():
    assert allowed_values(None, "poses", LISTS["poses"]) == tuple(LISTS["poses"])


def test_allowed_values_pool_is_a_hard_constraint():
    family = Family(id="f", label="F", pools={"poses": ("standing",)})
    assert allowed_values(family, "poses", LISTS["poses"]) == ("standing",)
    # A field without a pool keeps the base list.
    assert allowed_values(family, "actions", LISTS["actions"]) == tuple(LISTS["actions"])


def test_allowed_values_pool_intersects_stale_values():
    family = Family(id="f", label="F", pools={"poses": ("standing", "removed")})
    assert allowed_values(family, "poses", LISTS["poses"]) == ("standing",)


def test_allowed_values_avoid_removes_without_pool():
    family = Family(id="f", label="F", avoid={"actions": ("walking casually",)})
    assert allowed_values(family, "actions", LISTS["actions"]) == ("smiling",)


def test_allowed_values_can_be_empty():
    family = Family(id="f", label="F", avoid={"actions": ("smiling", "walking casually")})
    assert allowed_values(family, "actions", LISTS["actions"]) == ()


def test_effective_lists_applies_family_to_every_field():
    family = Family(id="f", label="F", pools={"poses": ("lying",)})
    effective = effective_lists(LISTS, family)
    assert effective["poses"] == ("lying",)
    assert effective["actions"] == tuple(LISTS["actions"])


def test_validate_family_accepts_valid_record():
    family = Family(
        id="mirror",
        label="Mirror",
        pools={"poses": ("standing",)},
        avoid={"actions": ("walking casually",)},
    )
    assert validate_family(family, LISTS) is None


def test_validate_family_rejects_unknown_field():
    family = Family(id="f", label="F", pools={"nope": ("standing",)})
    assert validate_family(family, LISTS) == "El campo 'nope' no existe en las listas activas."


def test_validate_family_rejects_unknown_value():
    family = Family(id="f", label="F", pools={"poses": ("flying",)})
    assert validate_family(family, LISTS) == "El valor 'flying' no existe en la lista 'poses'."


def test_validate_family_rejects_unknown_avoid_value():
    family = Family(id="f", label="F", avoid={"actions": ("shouting",)})
    assert validate_family(family, LISTS) == "El valor 'shouting' no existe en la lista 'actions'."


def test_validate_family_rejects_overlap():
    family = Family(
        id="f",
        label="F",
        pools={"poses": ("standing", "walking")},
        avoid={"poses": ("walking",)},
    )
    assert validate_family(family, LISTS) == (
        "El valor 'walking' no puede estar permitido y vetado a la vez en 'poses'."
    )
