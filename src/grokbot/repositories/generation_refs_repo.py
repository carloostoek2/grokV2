"""JSON generation-refs repository — Kie chaining + regen metadata.

Behavior parity with grok ``sessions.py`` ``save_generation_ref`` /
``get_generation_ref`` (HEAD 81832a5). Records are keyed ``chat_id:message_id``
and pruned on a 14-day TTL on every save/get. ``regen`` is stored opaquely
(never re-modeled) and records of untouched keys are preserved on dump.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from grokbot.repositories.base import write_json_atomic

GENERATION_REF_TTL_SEC = 14 * 24 * 3600


class JsonGenerationRefsRepository:
    """``generation_refs.json`` backend implementing the :class:`GenerationRefsRepository` contract."""

    GENERATION_REF_TTL_SEC = GENERATION_REF_TTL_SEC

    def __init__(self, path: Path) -> None:
        self._path = path

    # -- public API ---------------------------------------------------------

    def save(
        self,
        chat_id: int,
        message_id: int,
        *,
        kie_task_id: str | None = None,
        kie_index: int = 0,
        provider: str = "kie",
        kind: str = "image",
        prompt: str = "",
        regen: dict | None = None,
        now: float | None = None,
    ) -> None:
        """Persist generation metadata for a bot-sent image (no-op without task/regen)."""
        if not kie_task_id and not regen:
            return
        now = now if now is not None else time.time()
        refs = self._prune(self._load(), now)
        rec: dict = {
            "provider": provider,
            "kind": kind,
            "prompt": prompt[:500] if prompt else "",
            "created_at": now,
        }
        if kie_task_id:
            rec["kie_task_id"] = kie_task_id
            rec["kie_index"] = max(0, min(int(kie_index), 5))
        if regen:
            rec["regen"] = regen  # opaque, never re-modeled
        refs[self._key(chat_id, message_id)] = rec
        self._save(refs)

    def get(self, chat_id: int, message_id: int) -> dict | None:
        """Return generation metadata for a bot message, or None if missing/expired."""
        raw = self._load()
        refs = self._prune(raw)
        key = self._key(chat_id, message_id)
        rec = refs.get(key)
        if rec is None:
            return None
        if refs != raw:
            self._save(refs)
        return rec

    # -- private helpers ----------------------------------------------------

    @staticmethod
    def _key(chat_id: int, message_id: int) -> str:
        return f"{chat_id}:{message_id}"

    def _load(self) -> dict:
        """Load the top-level dict; missing file or non-dict top-level -> {}.

        Corrupt JSON propagates ``json.JSONDecodeError`` (parity grok
        ``_load_generation_refs``).
        """
        if not self._path.exists():
            return {}
        with open(self._path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        write_json_atomic(self._path, data)

    @classmethod
    def _prune(cls, refs: dict, now: float | None = None) -> dict:
        """Keep only dict records with a float created_at inside the TTL window."""
        now = now if now is not None else time.time()
        pruned: dict = {}
        for key, rec in refs.items():
            if not isinstance(rec, dict):
                continue
            created = rec.get("created_at")
            try:
                created_f = float(created)
            except (TypeError, ValueError):
                continue
            if now - created_f < GENERATION_REF_TTL_SEC:
                pruned[key] = rec
        return pruned
