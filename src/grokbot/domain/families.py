"""Families: coherent value pools for the ``/variables`` batch engine.

A family narrows the lists referenced by the prompt template to the values that
make sense together — a mirror selfie never walks down the street — and can veto
individual values. Pools are hard constraints, not a bias: the batch only samples
from them. Values are matched against the active lists, so a value removed from a
list silently leaves the pool instead of being invented back.

The module is pure stdlib and I/O-free: the repository persists the record and
the application layer validates it against the active lists.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grokbot.domain.variables import normalize_items


@dataclass(frozen=True)
class Family:
    """A named set of allowed/vetoed values per list.

    ``pools`` maps a list name to the values the batch may draw from. ``avoid``
    maps a list name to values vetoed even when no pool is declared. Both are
    stored as tuples so the value object stays immutable.
    """

    id: str
    label: str
    description: str = ""
    pools: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    avoid: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def pool_for(self, field: str) -> tuple[str, ...] | None:
        """Declared pool for ``field``, or None when the family leaves it open."""
        return self.pools.get(field)

    def __hash__(self) -> int:
        """Families hash by id: mapping fields would otherwise make them unhashable."""
        return hash(self.id)


def _normalize_mapping(raw: object) -> dict[str, tuple[str, ...]]:
    """Coerce ``{field: [values]}`` into ordered tuples, dropping empty fields."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for field_name, values in raw.items():
        if not isinstance(field_name, str) or not field_name.strip():
            continue
        clean = normalize_items(values)
        if clean:
            out[field_name.strip()] = tuple(dict.fromkeys(clean))
    return out


def family_payload_error(raw: object) -> str | None:
    """Strict shape check for a family payload sent by the user or a package.

    Tolerance is for *reading* old documents; a save that silently drops an
    empty/typoed pool would invert the hard-constraint contract, so it is
    rejected with Spanish copy instead.
    """
    if not isinstance(raw, dict):
        return "La familia debe ser un objeto JSON."
    for key in ("pools", "preferred", "avoid"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            return f"'{key}' debe ser un objeto {{campo: [valores]}}."
        for field_name, items in value.items():
            if not isinstance(items, list) or not items:
                return f"'{field_name}' debe ser una lista de valores no vacía."
            for item in items:
                if not isinstance(item, str) or not item.strip():
                    return (
                        f"'{field_name}' contiene un valor inválido "
                        "(debe ser texto no vacío)."
                    )
    return None


def normalize_family(family_id: str, raw: object) -> Family | None:
    """Build a family from a stored record. Tolerant: invalid structure -> None.

    Accepts the canonical ``pools`` key and the demo's ``preferred`` alias. A
    missing label falls back to the id so hand-edited files stay readable.
    """
    if not isinstance(family_id, str) or not family_id.strip() or not isinstance(raw, dict):
        return None
    raw_pools = raw.get("pools")
    if not isinstance(raw_pools, dict):
        raw_pools = raw.get("preferred")
    pools = _normalize_mapping(raw_pools)
    avoid = _normalize_mapping(raw.get("avoid"))
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        label = family_id
    description = raw.get("description")
    if not isinstance(description, str):
        description = ""
    return Family(
        id=family_id.strip(),
        label=label.strip(),
        description=description.strip(),
        pools=MappingProxyType(pools),
        avoid=MappingProxyType(avoid),
    )


def family_to_record(family: Family) -> dict:
    """Serialize a family into the JSON record persisted under ``families[id]``."""
    record: dict = {"label": family.label, "description": family.description}
    if family.pools:
        record["pools"] = {name: list(values) for name, values in family.pools.items()}
    if family.avoid:
        record["avoid"] = {name: list(values) for name, values in family.avoid.items()}
    return record


def allowed_values(
    family: Family | None,
    field: str,
    items: Iterable[str],
) -> tuple[str, ...]:
    """Values the batch may draw for ``field`` under ``family``.

    Without a family the base list is returned unchanged. With a family the
    declared pool is intersected with the base list (order preserved), then the
    ``avoid`` values are removed. An empty result means the field is unusable
    under this family; callers must surface it instead of inventing a value.
    """
    base = tuple(items)
    if family is None:
        return base
    pool = family.pool_for(field)
    if pool is None:
        candidates = base
    else:
        allowed = set(base)
        candidates = tuple(value for value in pool if value in allowed)
    vetoed = set(family.avoid.get(field, ()))
    if not vetoed:
        return candidates
    return tuple(value for value in candidates if value not in vetoed)


def effective_lists(
    lists: Mapping[str, Iterable[str]],
    family: Family | None,
) -> dict[str, tuple[str, ...]]:
    """Apply ``family`` to every list, returning the effective vocabularies."""
    return {name: allowed_values(family, name, items) for name, items in lists.items()}


def validate_family(family: Family, lists: Mapping[str, Iterable[str]]) -> str | None:
    """Semantic validation against the active lists. Returns Spanish copy or None."""
    if not family.id.strip():
        return "La familia necesita un identificador."
    if not family.label.strip():
        return "La familia necesita un nombre."
    for field_name, values in family.pools.items():
        if field_name not in lists:
            return f"El campo '{field_name}' no existe en las listas activas."
        base = set(lists[field_name])
        for value in values:
            if value not in base:
                return f"El valor '{value}' no existe en la lista '{field_name}'."
    for field_name, values in family.avoid.items():
        if field_name not in lists:
            return f"El campo '{field_name}' no existe en las listas activas."
        base = set(lists[field_name])
        for value in values:
            if value not in base:
                return f"El valor '{value}' no existe en la lista '{field_name}'."
        overlap = set(values) & set(family.pools.get(field_name, ()))
        if overlap:
            value = sorted(overlap)[0]
            return f"El valor '{value}' no puede estar permitido y vetado a la vez en '{field_name}'."
    return None
