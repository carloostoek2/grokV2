"""Tests del use case integrate (R4 Item 3, Task 2): referencia + seam de 2 imágenes.

Cubre el contrato de ``IntegrateRefsUseCase`` (set/read/load_for_edit con prereqs
user-safe byte-parity de grok) y el dispatch de ``GenerateImageUseCase.run`` a
``edit_with_reference`` (xAI) sin ampliar ``ImageProvider.generate``. 0 red /
0 ``unittest.mock``: fakes in-memory de los conftests. IDs/prompts anonimizados.
"""

from __future__ import annotations

import dataclasses

import pytest

from grokbot.application.events import ItemFailed, ItemResult
from grokbot.application.generate_image import GenerateImageUseCase
from grokbot.application.integrate_refs import (
    NO_REFERENCE_MSG,
    REQUIRES_XAI_MSG,
    IntegrateReferenceError,
    IntegrateRefsUseCase,
)
from grokbot.domain.user_config import UserConfig

# Fakes comunes.
from conftest import (
    USER_ID,
    FakeImageProvider,
    FakeIntegrateRefsRepo,
    FakeSessionRepo,
    make_registry,
)

_REF_BYTES = b"ref-jpg-bytes"


def _refs(seed: dict[int, bytes] | None = None) -> FakeIntegrateRefsRepo:
    return FakeIntegrateRefsRepo(seed=seed)


def _uc(sessions: FakeSessionRepo, refs: FakeIntegrateRefsRepo) -> IntegrateRefsUseCase:
    return IntegrateRefsUseCase(sessions=sessions, refs=refs)


def _xai_cfg() -> UserConfig:
    """UserConfig efectivo grok + xAI (grok_imagine_provider='xai')."""
    return dataclasses.replace(
        UserConfig.defaults(), model="grok", grok_imagine_provider="xai"
    )


async def _run(uc, **kw):
    return [ev async for ev in uc.run(**kw)]


# --------------------------------------------------------------------------- #
# IntegrateRefsUseCase
# --------------------------------------------------------------------------- #
async def test_set_reference_writes_bytes_and_persists_path():
    sessions = FakeSessionRepo()
    refs = _refs()
    uc = _uc(sessions, refs)

    path = uc.set_reference(USER_ID, _REF_BYTES)

    assert path == f"/integrate_refs/{USER_ID}.jpg"
    assert refs.read(USER_ID) == _REF_BYTES
    assert (USER_ID, _REF_BYTES) in refs.saved
    cfg = sessions.get_config(USER_ID)
    assert cfg.integrate_ref_path == path
    assert sessions.saved and sessions.saved[-1][0] == USER_ID


async def test_reference_available_false_when_file_missing():
    sessions = FakeSessionRepo()
    refs = _refs()
    # Path seteado en la config pero sin bytes en el repo → trata como ausente.
    cfg = dataclasses.replace(UserConfig.defaults(), integrate_ref_path=f"/integrate_refs/{USER_ID}.jpg")
    sessions.save_config(USER_ID, cfg)
    uc = _uc(sessions, refs)

    assert uc.reference_available(USER_ID) is False
    assert uc.read_reference(USER_ID) is None


async def test_load_for_edit_requires_xai_provider():
    sessions = FakeSessionRepo()  # cfg default grok/kie
    refs = _refs(seed={USER_ID: _REF_BYTES})
    uc = _uc(sessions, refs)
    cfg = sessions.get_config(USER_ID)

    with pytest.raises(IntegrateReferenceError) as exc:
        uc.load_for_edit(USER_ID, cfg)
    assert exc.value.user_message == REQUIRES_XAI_MSG


async def test_load_for_edit_missing_reference():
    sessions = FakeSessionRepo(seed={USER_ID: _xai_cfg()})
    refs = _refs()  # repo vacío
    uc = _uc(sessions, refs)
    cfg = sessions.get_config(USER_ID)

    with pytest.raises(IntegrateReferenceError) as exc:
        uc.load_for_edit(USER_ID, cfg)
    assert exc.value.user_message == NO_REFERENCE_MSG


async def test_load_for_edit_ok():
    # A8: cfg.integrate_ref_path seteado (lo persiste set_reference) + repo con bytes.
    cfg = dataclasses.replace(_xai_cfg(), integrate_ref_path=f"/integrate_refs/{USER_ID}.jpg")
    sessions = FakeSessionRepo(seed={USER_ID: cfg})
    refs = _refs(seed={USER_ID: _REF_BYTES})
    uc = _uc(sessions, refs)

    assert uc.load_for_edit(USER_ID, sessions.get_config(USER_ID)) == _REF_BYTES


# --------------------------------------------------------------------------- #
# GenerateImageUseCase.run — seam edit_with_reference (xAI)
# --------------------------------------------------------------------------- #
async def test_generate_with_reference_routes_to_edit_with_reference():
    prov = FakeImageProvider(name="xai")
    reg = make_registry(xai=prov)
    sessions = FakeSessionRepo(seed={USER_ID: _xai_cfg()})
    uc = GenerateImageUseCase(sessions=sessions, registry=reg)

    events = await _run(
        uc, user_id=USER_ID, prompt="hazla sonreir",
        source_image=b"src", reference_image=b"ref",
    )

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemResult)
    assert len(prov.edit_with_reference_calls) == 1
    request, source, reference = prov.edit_with_reference_calls[0]
    assert source == b"src"
    assert reference == b"ref"
    assert prov.calls == [], "generate NO se llama en el flujo integrate"
    assert ev.regen_context["integrate_mode"] is True
    assert ev.regen_context["mode"] == "edit"
    # A10/R8: el ctx del ref NUNCA lleva path/file_id de la referencia.
    assert "integrate_ref_path" not in ev.regen_context


async def test_generate_with_reference_non_xai_fails():
    sessions = FakeSessionRepo()  # cfg default grok/kie
    reg = make_registry()
    uc = GenerateImageUseCase(sessions=sessions, registry=reg)

    events = await _run(
        uc, user_id=USER_ID, prompt="hazla sonreir",
        source_image=b"src", reference_image=b"ref",
    )

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemFailed)
    assert ev.terminal is True
    assert ev.reason == REQUIRES_XAI_MSG
    assert reg.provider("kie").calls == []
    assert reg.provider("xai").edit_with_reference_calls == []


async def test_generate_without_reference_still_uses_generate():
    prov = FakeImageProvider(name="xai")
    reg = make_registry(xai=prov)
    sessions = FakeSessionRepo(seed={USER_ID: _xai_cfg()})
    uc = GenerateImageUseCase(sessions=sessions, registry=reg)

    events = await _run(
        uc, user_id=USER_ID, prompt="una imagen normal", source_image=b"src",
    )

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ItemResult)
    assert len(prov.calls) == 1
    assert prov.edit_with_reference_calls == []
    assert "integrate_mode" not in (ev.regen_context or {})
