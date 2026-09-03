"""Prompt-template value objects and pure helpers for the /variables batch engine.

Rendering is done with regex substitution (never ``str.format``), so templates
that embed literal JSON braces render intact. When the template references an
unknown placeholder or a format expression, rendering falls back to a plain
join of the supplied values.
"""

from __future__ import annotations

import random
import re
from collections.abc import Mapping
from dataclasses import dataclass

LIST_NAMES = ("poses", "angles", "actions")
DEFAULT_TEMPLATE = "{pose}, {angle}, {action}"

# Matches named template placeholders like {pose}, {angle}, {action}.
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Matches str.format expressions (attribute/index/conversion/format-spec) such as
# {pose.foo}, {pose!r}, {pose:>10}. These are invalid as plain placeholders and
# make the template fall back to a join (JSON object braces never match).
_FORMAT_EXPR_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*(?:[.!:])")


def normalize_items(raw: object) -> list[str]:
    """Coerce a stored list value into a clean list of non-empty strings."""
    if not isinstance(raw, list):
        return []
    items: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
    return items


def pluralize(word: str) -> str:
    """Regular plural for the list-resolution helper (``body`` -> ``bodies``)."""
    if word.endswith("y"):
        return word[:-1] + "ies"
    return word + "s"


def list_for_placeholder(placeholder: str, lists: Mapping[str, object]) -> str | None:
    """Resolve a template placeholder to a stored list name.

    Accepts a bare name or a braced placeholder (``{body}``/``body``). Resolution
    order mirrors grok ``_list_for_placeholder``: exact match, regular plural,
    then shortest prefix. Returns None when no list provides the field.
    """
    name = placeholder.strip()
    if name.startswith("{") and name.endswith("}"):
        name = name[1:-1].strip()
    if name in lists:
        return name
    plural = pluralize(name)
    if plural in lists:
        return plural
    matches = [key for key in lists if key.startswith(name)]
    return min(matches, key=len) if matches else None


def clean_gaps(text: str) -> str:
    """Collapse separator artifacts left by placeholders that rendered empty."""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r",\s*,", ",", text)
    text = re.sub(r",\s*$", "", text)
    text = re.sub(r"^\s*,\s*", "", text)
    return text.strip()


@dataclass(frozen=True)
class PromptTemplate:
    """A self-describing prompt template with named ``{placeholder}`` fields."""

    template: str

    @classmethod
    def default(cls) -> "PromptTemplate":
        return cls(DEFAULT_TEMPLATE)

    def fields(self) -> list[str]:
        """Placeholder field names in order of appearance."""
        return _PLACEHOLDER_RE.findall(self.template)

    def has_format_expr(self) -> bool:
        return _FORMAT_EXPR_RE.search(self.template) is not None

    def missing_fields(self, values: Mapping[str, str]) -> list[str]:
        """Placeholder fields with no value supplied."""
        return [field for field in self.fields() if field not in values]

    def render(self, values: Mapping[str, str]) -> str:
        """Fill the template with ``{field}`` values.

        Falls back to a join when the template has no value for a placeholder or
        contains a str.format expression. Literal JSON braces render intact.
        """
        if self.has_format_expr():
            return ", ".join(values.values())
        placeholders = self.fields()
        if any(field not in values for field in placeholders):
            return ", ".join(values.values())
        return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], self.template)

    def render_inline(self, fields: list[str]) -> str:
        """Render with inline ``/var`` fields filling placeholders positionally.

        The first field fills the FIRST placeholder; fields beyond the placeholder
        count are ignored; leftover empty-placeholder separator artifacts are
        cleaned via :func:`clean_gaps`. Templates without placeholders join fields.
        """
        clean = [f.strip() for f in fields if isinstance(f, str) and f.strip()]
        names = self.fields()
        if not names or self.has_format_expr():
            return ", ".join(clean)
        values = {name: "" for name in names}
        for i, name in enumerate(names):
            if i < len(clean):
                values[name] = clean[i]
        rendered = _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], self.template)
        return clean_gaps(rendered)

    def render_positional(self, values: list[str]) -> str:
        """Render filling placeholders left-to-right from ``values``."""
        if self.has_format_expr():
            return ", ".join(values)
        it = iter(values)
        return _PLACEHOLDER_RE.sub(lambda _m: next(it, ""), self.template)


def combo_key(template: str, values: Mapping[str, str]) -> tuple[str, ...]:
    """Ordered tuple of the values that actually render into the template.

    Only the fields the template references contribute, so the key identifies a
    combination by its prompt content, independent of other lists.
    """
    return tuple(values.get(field, "") for field in PromptTemplate(template).fields())


def combo_label(values: Mapping[str, str]) -> str:
    """Human-readable label for a combo (all values joined)."""
    return ", ".join(values.values())


# --- Batch constants (D11, item 4). ---
# Transcribed from grok bot.py:77 / variables_store.py (parity ranges 340-353,
# 374-417). Range/derangement limits used by the batch strategies live here so
# the application layer never redefines them.
VARIABLES_MAX = 10
MAX_COMBO_ATTEMPTS = 30
MULTIPOSE_BATCH_SIZE = 5


def build_shuffled_prompt(template: str, values: Mapping[str, str]) -> str:
    """Render ``template`` with the contributing values in a different order.

    Pure mirror of grok ``variables_store.build_prompt_shuffled`` (340-353): the
    values that actually contribute are shuffled and, when the shuffle reproduces
    the canonical order, reversed — guaranteeing a derangement for two or more
    contributing fields (with two fields this is a plain swap).
    """
    pt = PromptTemplate(template)
    ordered = [values.get(field, "") for field in pt.fields()]
    ordered = [value for value in ordered if value]
    if len(ordered) >= 2:
        canonical = list(ordered)
        random.shuffle(ordered)
        if ordered == canonical:
            ordered.reverse()
    return pt.render_positional(ordered)
