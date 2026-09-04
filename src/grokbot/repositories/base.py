"""Persistence repository contracts and atomic JSON write helper.

Defines the three repository Protocols consumed by the application layer
(items 4-6) plus :func:`write_json_atomic`, the single atomic writer used by
every concrete JSON backend. Behavior parity targets grok (sessions.py /
variables_store.py); concrete repos are in this package and never import this
module's siblings (no cycles).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from grokbot.domain.user_config import UserConfig


@runtime_checkable
class SessionRepository(Protocol):
    """Per-user persisted session config (``sessions.json``).

    ``get_config`` persists the default record for a brand-new user (parity
    grok ``get_session``/``_get_or_create_full``). ``video_hourly_timestamps``
    is repo-owned quota data and never appears in ``UserConfig.to_record``.
    """

    def get_config(self, user_id: int) -> UserConfig: ...

    def save_config(self, user_id: int, config: UserConfig) -> None: ...

    def record_video_hourly_usage(self, user_id: int, *, now: float | None = None) -> None: ...

    def count_video_hourly_usage(self, user_id: int, *, now: float | None = None) -> int: ...

    def count_global_video_hourly_usage(self, *, now: float | None = None) -> int: ...


@runtime_checkable
class VariablesRepository(Protocol):
    """Prompt-variable lists + template + blacklist + packages.

    CRUD semantics mirror ``variables_store.py``; the active file is
    self-describing (whichever fields it carries are the ones combined), so a
    package file with its own fields is never polluted with system defaults.
    Packages (D3) live in this repository, keyed by ``packages_dir``.
    """

    def get_lists(self) -> dict[str, list[str]]: ...

    def get_list(self, name: str) -> list[str]: ...

    def is_valid_list_name(self, name: str) -> bool: ...

    def get_template(self) -> str: ...

    def set_template(self, template: str) -> bool: ...

    def add_item(self, name: str, item: str) -> bool: ...

    def update_item(self, name: str, index: int, item: str) -> bool: ...

    def delete_item(self, name: str, index: int) -> bool: ...

    def get_blacklist(self) -> set[tuple[str, ...]]: ...

    def blacklist_add(self, key: tuple[str, ...]) -> bool: ...

    def blacklist_clear(self) -> None: ...

    # packages (D3)
    def list_packages(self) -> list[str]: ...

    def save_package(self, name: str, payload: dict) -> tuple[bool, str | None]: ...

    def load_package(self, name: str) -> dict | None: ...

    def package_exists(self, name: str) -> bool: ...

    def active_package_name(self) -> str | None: ...

    def activate_package(self, name: str) -> bool: ...

    def delete_package(self, name: str) -> bool: ...


@runtime_checkable
class GenerationRefsRepository(Protocol):
    """Kie/regen generation metadata per bot message (``generation_refs.json``).

    ``regen`` is stored opaquely (never re-modeled); the TTL prune runs on every
    save and get. ``save`` is a no-op when neither ``kie_task_id`` nor ``regen``
    is supplied (parity grok ``save_generation_ref``). ``owner_uid`` (R8) se
    persiste como campo top-level del record (nunca dentro de ``regen``) para
    scopear el botón Regenerar al dueño de la generación.
    """

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
        owner_uid: int | None = None,
        now: float | None = None,
    ) -> None: ...

    def get(self, chat_id: int, message_id: int) -> dict | None: ...


def write_json_atomic(path: Path, data: dict, *, ensure_ascii: bool = True, indent: int = 2) -> None:
    """Atomically write ``data`` as JSON to ``path`` (parent created, tmp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
