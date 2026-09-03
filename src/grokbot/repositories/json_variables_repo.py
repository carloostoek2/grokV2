"""JSON variables repository — lists/template/blacklist + packages (D3).

Behavior parity with grok ``variables_store.py`` (HEAD 81832a5). The active file
is self-describing: whichever fields it carries (poses/angles/actions for the
legacy model, or package fields like bodies/hands/angles) are the ones combined.
Default lists are seeded ONLY when the file is empty/missing/corrupt or its
``lists`` dict is empty, so an active package is never polluted. Writes use
``ensure_ascii=False`` (D9) and mutate the loaded raw document (preserving
unknown top-level keys such as ``_extra_top``).
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from grokbot.domain.variables import DEFAULT_TEMPLATE, LIST_NAMES, normalize_items
from grokbot.repositories.base import write_json_atomic

_ACTIVE_PACKAGE_KEY = "_package"

# Seed content for a brand-new/empty active file (transcribed verbatim from
# grok variables_store.py:38-70 as seed data — NOT domain constants).
DEFAULT_LISTS: dict[str, list[str]] = {
    "poses": [
        "standing with weight shifted to one leg, free hand resting on hip",
        "combat-ready stance with knees slightly bent and torso angled forward",
        "leaning one shoulder against the wall",
        "classic model contrapposto with one leg slightly forward",
        "twisted torso looking back over the shoulder",
        "crouched low with one knee almost touching the ground",
        "legs crossed at the ankles while standing tall",
        "dynamic mid-stride pose as if just stopping",
        "sitting on the steps with knees together",
        "kneeling on one knee with the sword resting across the thigh",
    ],
    "actions": [
        "wearing a black combat dress with a high collar",
        "wearing a flowing white gown with a side slit",
        "dressed in a sleek futuristic bodysuit with glowing accents",
        "wearing a casual oversized hoodie and ripped jeans",
        "in a red evening dress with matching gloves",
    ],
    "angles": [
        "eye-level full body frontal",
        "low angle looking slightly upward",
        "high angle looking downward",
        "three-quarter view from the left side",
        "profile side view from the right",
        "slightly elevated three-quarter rear angle",
        "close full-body shot from below the waist upward",
        "over-the-shoulder perspective from behind",
        "eye-level medium shot",
        "wide shot from a slight distance",
    ],
}


class JsonVariablesRepository:
    """``variables_lists.json`` backend implementing the :class:`VariablesRepository` contract."""

    def __init__(self, path: Path, *, packages_dir: Path | None = None) -> None:
        self._path = path
        self._packages_dir = packages_dir or (path.parent / "variables_packages")
        self._lock = threading.Lock()

    # -- public API: lists/template ----------------------------------------

    def get_lists(self) -> dict[str, list[str]]:
        """Return every list stored in the active file as {name: [items]}."""
        data = self._data()
        lists = data.get("lists", {})
        return {name: normalize_items(items) for name, items in lists.items()}

    def get_list(self, name: str) -> list[str]:
        if not self.is_valid_list_name(name):
            raise ValueError(f"Unknown list: {name!r}")
        return self.get_lists()[name]

    def is_valid_list_name(self, name: str) -> bool:
        """D8: constant legacy names or any self-describing list in the active file."""
        return name in LIST_NAMES or name in self.get_lists()

    def get_template(self) -> str:
        return self._data().get("template", DEFAULT_TEMPLATE)

    def set_template(self, template: str) -> bool:
        """Persist a new prompt template. Returns False when invalid (empty)."""
        if not isinstance(template, str) or not template.strip():
            return False
        with self._lock:
            data = self._load()
            self._ensure_full(data)
            data["template"] = template.strip()
            self._save(data)
        return True

    # -- public API: CRUD --------------------------------------------------

    def add_item(self, name: str, item: str) -> bool:
        """Append an item to a list. Returns False when invalid or a duplicate."""
        if not self.is_valid_list_name(name):
            return False
        if not isinstance(item, str) or not item.strip():
            return False
        clean = item.strip()
        with self._lock:
            data = self._load()
            self._ensure_full(data)
            items = normalize_items(data["lists"].get(name, []))
            if clean in items:
                return False
            items.append(clean)
            data["lists"][name] = items
            self._save(data)
        return True

    def update_item(self, name: str, index: int, item: str) -> bool:
        """Replace the item at ``index``. Returns False on invalid args/duplicates."""
        if not self.is_valid_list_name(name):
            return False
        if not isinstance(item, str) or not item.strip():
            return False
        clean = item.strip()
        with self._lock:
            data = self._load()
            self._ensure_full(data)
            items = normalize_items(data["lists"].get(name, []))
            if not 0 <= index < len(items):
                return False
            if items[index] == clean:
                return True  # no-op edit: nothing to persist
            if clean in items:
                return False
            items[index] = clean
            data["lists"][name] = items
            self._save(data)
        return True

    def delete_item(self, name: str, index: int) -> bool:
        """Remove the item at ``index``. Returns False when out of range."""
        if not self.is_valid_list_name(name):
            return False
        with self._lock:
            data = self._load()
            self._ensure_full(data)
            items = normalize_items(data["lists"].get(name, []))
            if not 0 <= index < len(items):
                return False
            del items[index]
            data["lists"][name] = items
            self._save(data)
        return True

    # -- public API: blacklist ---------------------------------------------

    def get_blacklist(self) -> set[tuple[str, ...]]:
        """Persistent set of combo keys marked as exhausted (never retry)."""
        data = self._data()
        raw = data.get("blacklist", [])
        out: set[tuple[str, ...]] = set()
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, list):
                    out.add(tuple(item))
        return out

    def blacklist_add(self, key: tuple[str, ...]) -> bool:
        """Persist a combo key to the blacklist. False if invalid or already there."""
        if not isinstance(key, tuple):
            return False
        entry = list(key)
        with self._lock:
            data = self._load()
            self._ensure_full(data)
            blacklist = data["blacklist"]
            if entry in blacklist:
                return False
            blacklist.append(entry)
            self._save(data)
        return True

    def blacklist_clear(self) -> None:
        """Remove all blacklisted combo keys."""
        with self._lock:
            data = self._load()
            self._ensure_full(data)
            data["blacklist"] = []
            self._save(data)

    # -- public API: packages (D3/D7) --------------------------------------

    def list_packages(self) -> list[str]:
        """Available package slugs, sorted alphabetically (app-friendly list)."""
        if not self._packages_dir.exists():
            return []
        return sorted(p.stem for p in self._packages_dir.glob("*.json"))

    def save_package(self, name: str, payload: dict) -> tuple[bool, str | None]:
        """Create (or overwrite) a package file. Returns (ok, None) or (False, error)."""
        slug = self._slugify(name)
        if not slug:
            return False, "El nombre del paquete no es válido."
        normalized, err = self._normalize_package_payload(payload)
        if err:
            return False, err
        self._packages_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._save_package(slug, normalized)
        return True, None

    def load_package(self, name: str) -> dict | None:
        """Load a package's normalized content, or None when missing/invalid."""
        slug = self._slugify(name)
        path = self._packages_dir / f"{slug}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def package_exists(self, name: str) -> bool:
        return (self._packages_dir / f"{self._slugify(name)}.json").exists()

    def active_package_name(self) -> str | None:
        """Slug of the active package, or None when the file is hand-edited."""
        name = self._data().get(_ACTIVE_PACKAGE_KEY)
        return name if isinstance(name, str) and name else None

    def activate_package(self, name: str) -> bool:
        """Copy a package's content into the active file, clearing the blacklist."""
        payload = self.load_package(name)
        if payload is None:
            return False
        slug = self._slugify(name)
        with self._lock:
            data = {
                "lists": payload["lists"],
                "template": payload["template"],
                "blacklist": [],
                _ACTIVE_PACKAGE_KEY: slug,
            }
            self._save(data)
        return True

    def delete_package(self, name: str) -> bool:
        """Delete a package file. False when missing or when it is active."""
        slug = self._slugify(name)
        if not self.package_exists(slug):
            return False
        if self.active_package_name() == slug:
            return False
        (self._packages_dir / f"{slug}.json").unlink()
        return True

    # -- private helpers ----------------------------------------------------

    def _load(self) -> dict:
        """Tolerant load: corrupt/missing/non-dict -> {} (parity variables_store)."""
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save(self, data: dict) -> None:
        write_json_atomic(self._path, data, ensure_ascii=False)

    def _save_package(self, slug: str, payload: dict) -> None:
        write_json_atomic(self._packages_dir / f"{slug}.json", payload, ensure_ascii=False)

    def _ensure_full(self, data: dict) -> bool:
        """Ensure a loadable structure. Returns True when changed.

        Default lists are seeded ONLY when the file has no lists at all (or its
        lists dict is empty); a package with its own fields is never polluted.
        """
        changed = False
        if not isinstance(data.get("lists"), dict):
            data["lists"] = {}
            changed = True
        if not data["lists"]:
            for name in LIST_NAMES:
                data["lists"][name] = list(DEFAULT_LISTS[name])
            changed = True
        if not isinstance(data.get("template"), str) or not data["template"].strip():
            data["template"] = DEFAULT_TEMPLATE
            changed = True
        if not isinstance(data.get("blacklist"), list):
            data["blacklist"] = []
            changed = True
        return changed

    def _data(self) -> dict:
        """Load the persisted structure, applying defaults when missing/corrupt."""
        with self._lock:
            data = self._load()
            if self._ensure_full(data):
                self._save(data)
        return data

    @staticmethod
    def _slugify(name: str) -> str:
        """Normalize a package name into a filesystem/callback-safe slug."""
        slug = re.sub(r"\W+", "_", name.strip().lower()).strip("_")
        return slug

    def _normalize_package_payload(self, raw: object) -> tuple[dict | None, str | None]:
        """Validate/normalize a raw payload into {"lists": {...}, "template": str}.

        Accepts both the ``lists`` and the ``fields`` top-level key. Returns
        (payload, None) or (None, error) mirroring grok 497-519.
        """
        if not isinstance(raw, dict):
            return None, "El paquete debe ser un objeto JSON."
        raw_lists = raw.get("lists") if isinstance(raw.get("lists"), dict) else None
        if raw_lists is None and isinstance(raw.get("fields"), dict):
            raw_lists = raw["fields"]
        if not raw_lists:
            return None, "Falta 'lists' (o 'fields') con al menos un campo."
        lists: dict[str, list[str]] = {}
        for field, items in raw_lists.items():
            normalized = normalize_items(items)
            if not normalized:
                continue
            lists[str(field)] = normalized
        if not lists:
            return None, "Ningún campo tiene elementos."
        template = raw.get("template")
        if not isinstance(template, str) or not template.strip():
            return None, "Falta 'template' (texto del prompt)."
        return {"lists": lists, "template": template.strip()}, None
