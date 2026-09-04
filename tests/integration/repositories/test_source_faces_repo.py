"""Integration tests for DiskSourceFacesRepository (real FS via tmp_path).

Verifica la escritura atómica (tmp + os.replace): tras ``save`` no quedan
``*.tmp`` residuales y el archivo final tiene exactamente los bytes.
"""

from __future__ import annotations

from pathlib import Path

from grokbot.repositories.disk_source_faces import DiskSourceFacesRepository

USER_ID = 111111111
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _repo(tmp_path: Path) -> DiskSourceFacesRepository:
    return DiskSourceFacesRepository(tmp_path / "sources")


def test_save_creates_file_and_returns_path(tmp_path):
    repo = _repo(tmp_path)

    path = repo.save(USER_ID, JPEG)

    assert Path(path) == tmp_path / "sources" / f"{USER_ID}.jpg"
    assert Path(path).read_bytes() == JPEG
    assert repo.exists(USER_ID) is True


def test_read_returns_bytes_and_none_when_absent(tmp_path):
    repo = _repo(tmp_path)

    assert repo.read(USER_ID) is None
    repo.save(USER_ID, JPEG)
    assert repo.read(USER_ID) == JPEG


def test_exists_false_after_file_deleted(tmp_path):
    repo = _repo(tmp_path)
    path = repo.save(USER_ID, JPEG)

    Path(path).unlink()

    assert repo.exists(USER_ID) is False
    assert repo.read(USER_ID) is None


def test_save_is_atomic_no_tmp_residual(tmp_path):
    repo = _repo(tmp_path)
    repo.save(USER_ID, JPEG)

    sources_dir = tmp_path / "sources"
    assert list(sources_dir.glob("*.tmp")) == []
    assert list(sources_dir.glob(f"{USER_ID}.jpg")) == [sources_dir / f"{USER_ID}.jpg"]


def test_save_overwrites_existing_source(tmp_path):
    repo = _repo(tmp_path)
    first = repo.save(USER_ID, JPEG)
    second_payload = JPEG + b"\x00" * 8

    second = repo.save(USER_ID, second_payload)

    assert second == first  # mismo path
    assert Path(second).read_bytes() == second_payload
