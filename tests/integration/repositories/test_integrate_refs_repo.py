"""Integration tests for DiskIntegrateRefsRepository (real FS via tmp_path).

Verifica la escritura atómica (tmp + os.replace): tras ``save`` no quedan
``*.tmp`` residuales y el archivo final tiene exactamente los bytes (mismo
contrato de atomicidad que ``write_json_atomic`` / ``DiskSourceFacesRepository``).
"""

from __future__ import annotations

from pathlib import Path

from grokbot.repositories.disk_integrate_refs import DiskIntegrateRefsRepository

USER_ID = 111111111
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _repo(tmp_path: Path) -> DiskIntegrateRefsRepository:
    return DiskIntegrateRefsRepository(tmp_path / "integrate_refs")


def test_save_creates_file_and_returns_path(tmp_path):
    repo = _repo(tmp_path)

    path = repo.save(USER_ID, JPEG)

    assert Path(path) == tmp_path / "integrate_refs" / f"{USER_ID}.jpg"
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

    refs_dir = tmp_path / "integrate_refs"
    assert list(refs_dir.glob("*.tmp")) == []
    assert list(refs_dir.glob(f"{USER_ID}.jpg")) == [refs_dir / f"{USER_ID}.jpg"]


def test_save_overwrites_existing_reference(tmp_path):
    repo = _repo(tmp_path)
    first = repo.save(USER_ID, JPEG)
    second_payload = JPEG + b"\x00" * 8

    second = repo.save(USER_ID, second_payload)

    assert second == first  # mismo path
    assert Path(second).read_bytes() == second_payload
