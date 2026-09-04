"""Binary per-user face-swap source faces on disk.

Implements :class:`SourceFacesRepository` storing each user's source face as a
single JPEG file under ``data/sources/{user_id}.jpg``. ``save`` writes via a
temp file + ``os.replace`` so a crash can never leave a truncated file where a
valid source used to be (same atomicity contract as ``write_json_atomic``).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class DiskSourceFacesRepository:
    """Binary source faces on disk under ``sources_dir`` (tmp + os.replace)."""

    def __init__(self, sources_dir: Path) -> None:
        self._sources_dir = sources_dir

    def _path(self, user_id: int) -> Path:
        return self._sources_dir / f"{user_id}.jpg"

    def save(self, user_id: int, data: bytes) -> str:
        path = self._path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return str(path)

    def read(self, user_id: int) -> bytes | None:
        try:
            return self._path(user_id).read_bytes()
        except FileNotFoundError:
            return None

    def exists(self, user_id: int) -> bool:
        return self._path(user_id).is_file()
