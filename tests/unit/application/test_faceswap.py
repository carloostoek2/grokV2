"""Tests de application/faceswap.py — source-faces + swap use cases (R4 Item 2).

0 red / 0 mock: fakes in-memory del conftest (FakeSessionRepo,
FakeSourceFacesRepo, make_registry). IDs anonimizados (R8).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from grokbot.application.faceswap import (
    SourceFaceError,
    SourceFaceMissingError,
    SourceFacesUseCase,
    SwapFaceUseCase,
)
from grokbot.domain.user_config import AWAITING_SOURCE, IDLE_STATE

from conftest import (
    USER_ID,
    FakeImageProvider,
    FakeSessionRepo,
    FakeSourceFacesRepo,
    make_registry,
)


def _source_uc(*, sessions=None, sources=None) -> SourceFacesUseCase:
    return SourceFacesUseCase(
        sessions=sessions or FakeSessionRepo(),
        sources=sources or FakeSourceFacesRepo(),
    )


def _swap_uc(*, sessions=None, sources=None, registry=None) -> SwapFaceUseCase:
    return SwapFaceUseCase(
        sessions=sessions or FakeSessionRepo(),
        sources=sources or FakeSourceFacesRepo(),
        registry=registry if registry is not None else make_registry(),
    )


def _set_model(sessions: FakeSessionRepo, model: str) -> None:
    cfg = sessions.get_config(USER_ID)
    sessions.save_config(USER_ID, replace(cfg, model=model))


# --- SourceFacesUseCase --------------------------------------------------------

def test_begin_awaiting_source_sets_state():
    sessions = FakeSessionRepo()
    uc = _source_uc(sessions=sessions)

    uc.begin_awaiting_source(USER_ID)

    assert sessions.get_config(USER_ID).state == AWAITING_SOURCE


def test_begin_awaiting_source_idempotent_no_extra_save():
    sessions = FakeSessionRepo()
    uc = _source_uc(sessions=sessions)

    uc.begin_awaiting_source(USER_ID)
    saved_after_first = list(sessions.saved)
    uc.begin_awaiting_source(USER_ID)

    assert sessions.get_config(USER_ID).state == AWAITING_SOURCE
    assert sessions.saved == saved_after_first


def test_save_source_writes_bytes_and_sets_path_idle():
    sessions = FakeSessionRepo()
    sources = FakeSourceFacesRepo()
    uc = _source_uc(sessions=sessions, sources=sources)
    uc.begin_awaiting_source(USER_ID)

    path = uc.save_source(USER_ID, b"face-bytes")

    assert path == f"/sources/{USER_ID}.jpg"
    assert sources.read(USER_ID) == b"face-bytes"
    cfg = sessions.get_config(USER_ID)
    assert cfg.source_path == path
    assert cfg.state == IDLE_STATE


def test_clear_source_resets_path_and_idle():
    sessions = FakeSessionRepo()
    uc = _source_uc(sessions=sessions)
    uc.save_source(USER_ID, b"face-bytes")

    uc.clear_source(USER_ID)

    cfg = sessions.get_config(USER_ID)
    assert cfg.source_path is None
    assert cfg.state == IDLE_STATE


def test_source_available_false_when_file_missing():
    """A4: source_path seteado pero el archivo no existe → no disponible."""
    sessions = FakeSessionRepo()
    sources = FakeSourceFacesRepo()  # vacío: sin bytes
    cfg = sessions.get_config(USER_ID)
    sessions.save_config(USER_ID, replace(cfg, source_path="/sources/x.jpg"))
    uc = _source_uc(sessions=sessions, sources=sources)

    assert uc.source_available(USER_ID) is False


def test_source_available_true_when_path_and_file_present():
    sessions = FakeSessionRepo()
    sources = FakeSourceFacesRepo()
    uc = _source_uc(sessions=sessions, sources=sources)
    uc.save_source(USER_ID, b"face-bytes")

    assert uc.source_available(USER_ID) is True


# --- SwapFaceUseCase -----------------------------------------------------------

@pytest.mark.asyncio
async def test_swap_ok_uses_registry_faceswap():
    """cfg faceswap + source en repo + replicate fake → swap_face con los bytes."""
    sessions = FakeSessionRepo()
    sources = FakeSourceFacesRepo()
    _set_model(sessions, "faceswap")
    SourceFacesUseCase(sessions=sessions, sources=sources).save_source(USER_ID, b"face-bytes")

    replicate = FakeImageProvider(name="replicate")
    uc = _swap_uc(sessions=sessions, sources=sources, registry=make_registry(replicate=replicate))

    result = await uc.swap(USER_ID, input_image=b"target-bytes")

    assert replicate.swap_face_calls == [(b"face-bytes", b"target-bytes")]
    assert result.provider == "replicate"
    assert result.model_id == "fake-faceswap"


@pytest.mark.asyncio
async def test_swap_raises_missing_when_no_source():
    sessions = FakeSessionRepo()
    _set_model(sessions, "faceswap")
    uc = _swap_uc(sessions=sessions, sources=FakeSourceFacesRepo())

    with pytest.raises(SourceFaceMissingError) as exc:
        await uc.swap(USER_ID, input_image=b"target-bytes")

    assert "cambiar_source" in exc.value.user_message


@pytest.mark.asyncio
async def test_swap_raises_when_model_not_faceswap():
    sessions = FakeSessionRepo()  # model default grok, NO faceswap
    sources = FakeSourceFacesRepo(seed={USER_ID: b"face-bytes"})
    cfg = sessions.get_config(USER_ID)
    sessions.save_config(USER_ID, replace(cfg, source_path="/sources/x.jpg"))
    uc = _swap_uc(sessions=sessions, sources=sources)

    with pytest.raises(SourceFaceError) as exc:
        await uc.swap(USER_ID, input_image=b"target-bytes")

    assert "Face Swap" in exc.value.user_message
