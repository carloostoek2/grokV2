"""Tests de PendingPrompts (confirmación efímera, A6/C4) — ancla original.

``source_message_id`` permite que el resultado de una generación confirmada
responda al mensaje ORIGINAL del usuario (no al de confirmación del bot).
"""

from __future__ import annotations

from grokbot.telegram.deps import PendingPrompts


def test_pop_entry_returns_source_message_id_and_cleans():
    store = PendingPrompts()
    store.set(7, "retrato", chat_id=1, message_id=10, source_message_id=3)

    assert store.owns(1, 10, 7) is True
    entry = store.pop_entry(7)

    assert entry == ("retrato", 3)
    # el pendiente y su owner se limpiaron (misma semántica que pop).
    assert store.get(7) is None
    assert store.owner_of(1, 10) is None


def test_pop_entry_without_source_returns_none():
    store = PendingPrompts()
    store.set(7, "retrato", chat_id=1, message_id=10)

    assert store.pop_entry(7) == ("retrato", None)


def test_pop_entry_unknown_user_returns_none():
    store = PendingPrompts()
    assert store.pop_entry(7) is None
