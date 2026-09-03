"""Unit tests for grokbot.domain.variables (R2/R10)."""

from __future__ import annotations

import json

from grokbot.domain.variables import (
    DEFAULT_TEMPLATE,
    PromptTemplate,
    clean_gaps,
    combo_key,
    combo_label,
    list_for_placeholder,
    normalize_items,
    pluralize,
)

# Copied from grok tests/test_variables_store.py:159-166 (reference behavior).
JSON_TEMPLATE = (
    '{\n'
    '  "subject": "2B",\n'
    '  "pose": "{pose}",\n'
    '  "camera": {"angle": "{angle}"},\n'
    '  "note": "keep braces literal {}"\n'
    '}'
)


def test_normalize_items_cleans_and_drops_invalid():
    assert normalize_items(["a", "  b ", "", None, 3, ["x"]]) == ["a", "b"]
    assert normalize_items(None) == []
    assert normalize_items("nope") == []


def test_pluralize():
    assert pluralize("body") == "bodies"
    assert pluralize("pose") == "poses"


def test_fields_order():
    assert PromptTemplate("{pose}, {angle}, {action}").fields() == ["pose", "angle", "action"]


def test_render_json_template_with_literal_braces():
    tpl = PromptTemplate(JSON_TEMPLATE)
    prompt = tpl.render({"pose": "de pie", "angle": "frontal", "action": "mirando"})
    assert '"pose": "de pie"' in prompt
    assert '"camera": {"angle": "frontal"}' in prompt
    assert prompt.startswith("{")
    assert prompt.endswith("}")
    assert '"keep braces literal {}"' in prompt
    data = json.loads(prompt)
    assert data["pose"] == "de pie"
    assert data["camera"]["angle"] == "frontal"


def test_render_fallback_unknown_placeholder():
    prompt = PromptTemplate("{unknown} {pose}").render({"pose": "a", "angle": "b", "action": "c"})
    assert prompt == "a, b, c"


def test_render_fallback_format_expr():
    assert PromptTemplate("{pose.foo}").render({"pose": "a", "angle": "b"}) == "a, b"


def test_render_inline_clean_gaps_and_join():
    tpl = PromptTemplate(DEFAULT_TEMPLATE)
    assert tpl.render_inline(["de pie"]) == "de pie"
    assert tpl.render_inline(["de pie", "frontal", "extra"]) == "de pie, frontal, extra"
    assert tpl.render_inline(["de pie", "", "extra"]) == "de pie, extra"
    # Template with no placeholders joins the clean fields.
    assert PromptTemplate("foto fija").render_inline(["de pie", "frontal"]) == "de pie, frontal"


def test_render_positional():
    assert PromptTemplate("{pose} {angle}").render_positional(["A", "B"]) == "A B"


def test_combo_key_uses_only_template_fields():
    values = {"pose": "de pie", "angle": "de frente", "action": "mirando"}
    assert combo_key("{pose} {angle}", values) == ("de pie", "de frente")


def test_combo_label():
    assert combo_label({"pose": "de pie", "angle": "frontal"}) == "de pie, frontal"


def test_list_for_placeholder():
    lists = {"bodies": ["x", "y"]}
    assert list_for_placeholder("{bodies}", lists) == "bodies"
    assert list_for_placeholder("{body}", lists) == "bodies"  # regular plural
    assert list_for_placeholder("bodies", lists) == "bodies"
    assert list_for_placeholder("{nose}", lists) is None


def test_clean_gaps():
    # Mirrors grok _clean_placeholder_gaps: ", ," collapses to "," (no space).
    assert clean_gaps("de pie, , ") == "de pie"
    assert clean_gaps("a, ,b") == "a,b"
    assert clean_gaps(", ,a, ,b, ") == "a,b"
    assert clean_gaps("de pie, frontal, ") == "de pie, frontal"
    assert clean_gaps("  , , ") == ""
