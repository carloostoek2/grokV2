"""Tests de refine ComfyUI 2-stage (item 5, R1): 4 decisiones x 2 shapes.

Usa ``ResolveRefineUseCase`` real con un ``FakeComfyuiProvider`` (de
application/conftest) — 0 red, 0 ``unittest.mock``. Las decisiones se resuelven
por el propio use case (``decide``/``cancel_for_job``/timeout inyectado), igual
que en producción vía el hook del JobManager.
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import (
    CHAT_ID,
    USER_ID,
    FakeComfyuiProvider,
    FakeTelegramGateway,
    flat_callback_data,
    make_result,
)
from grokbot.application.events import ItemResult
from grokbot.application.refine_flow import RefineDecision, ResolveRefineUseCase
from grokbot.domain.generation import GenerationRequest, MediaType
from grokbot.domain.user_config import ComfyUIConfig
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.refine_chat import run_refine_flow
from grokbot.telegram.sender import ResultSender

REMOTE_1 = "/workspace/base_0.png"
REMOTE_2 = "/workspace/base_1.png"
DUMMY_PROMPT = "retrato de una persona de pie"


class _RefineBoom(Exception):
    user_message = "Fallo el refino"


def _comfy_request(model: str = "krea2") -> GenerationRequest:
    return GenerationRequest(
        provider="comfyui",
        model_id=model,
        media_type=MediaType.IMAGE,
        prompt=DUMMY_PROMPT,
        params={"model": model, "lora": "none", "refine": "1"},
    )


def _item(tmp_path, *, n: int = 1, prompt: str = DUMMY_PROMPT) -> ItemResult:
    """Item ComfyUI refinable con ``n`` imágenes locales (single o álbum)."""
    paths = []
    remotes = []
    for i in range(n):
        p = tmp_path / f"base_{i}.png"
        p.write_bytes(b"img")
        paths.append(str(p))
        remotes.append(f"/workspace/base_{i}.png")
    return ItemResult(
        result=make_result(
            provider="comfyui",
            model_id="krea2",
            media_type=MediaType.IMAGE,
            file_path=paths[0],
            meta={"file_paths": paths, "comfyui_remotes": remotes},
        ),
        prompt=prompt,
        regen_context={"provider": "comfyui", "mode": "edit"},
        request=_comfy_request(),
    )


def _refined_result(tmp_path, *, n: int = 1) -> "object":
    paths = []
    for i in range(n):
        p = tmp_path / f"refined_{i}.png"
        p.write_bytes(b"refined")
        paths.append(str(p))
    return make_result(
        provider="comfyui",
        model_id="krea2",
        media_type=MediaType.IMAGE,
        file_path=paths[0],
        meta={"file_paths": paths, "comfyui_remotes": [f"/workspace/refined_{i}.png" for i in range(n)]},
    )


def _provider(tmp_path, *, refined_n: int = 1, outcome=None) -> FakeComfyuiProvider:
    return FakeComfyuiProvider(
        refine_outcomes=[outcome if outcome is not None else _refined_result(tmp_path, n=refined_n)]
    )


def _schedule_decision(refine_uc: ResolveRefineUseCase, token: str, choice: str) -> None:
    async def _decide() -> None:
        await asyncio.sleep(0)
        refine_uc.decide(token, USER_ID, choice)

    asyncio.create_task(_decide())


def _schedule_cancel(refine_uc: ResolveRefineUseCase, job_id: str) -> None:
    async def _cancel() -> None:
        await asyncio.sleep(0)
        refine_uc.cancel_for_job(USER_ID, job_id)

    asyncio.create_task(_cancel())


# --------------------------------------------------------------------------- #
# offer (policy de refine_flow, smoke)
# --------------------------------------------------------------------------- #
def test_offer_true_false_by_remotes_and_model():
    provider = FakeComfyuiProvider()
    uc = ResolveRefineUseCase(provider=provider)
    cfg = ComfyUIConfig(model="krea2", lora="none", refine="1")
    result_ok = make_result(meta={"comfyui_remotes": [REMOTE_1]})
    assert uc.offer(cfg, result_ok) is True
    assert uc.offer(cfg, make_result(meta={})) is False
    cfg_off = ComfyUIConfig(model="krea2", lora="none", refine="0")
    assert uc.offer(cfg_off, result_ok) is False
    cfg_aio = ComfyUIConfig(model="qwen_aio", lora="none", refine="1")
    assert uc.offer(cfg_aio, result_ok) is False


# --------------------------------------------------------------------------- #
# single: yes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_single_yes_base_kb_refining_refine_and_delete_base(tmp_path, gateway, refs_repo):
    downloader = object()
    sender = ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)  # type: ignore[arg-type]
    provider = _provider(tmp_path)
    refine_uc = ResolveRefineUseCase(provider=provider, timeout=10.0)
    token = refine_uc.register(user_id=USER_ID, job_id=None)
    item = _item(tmp_path)
    ui = ChatUI(gateway, CHAT_ID)
    status = await ui.send_text("Editando imagen...")

    _schedule_decision(refine_uc, token, "yes")
    decision = await run_refine_flow(
        ui,
        item=item,
        sender=sender,
        refine_uc=refine_uc,
        prefix="Edit",
        status_id=status.message_id,
        delete_status=True,
        user_id=USER_ID,
        token=token,
    )

    assert decision == RefineDecision.yes
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 2  # base + refinada
    base_kb = flat_callback_data(photos[0]["reply_markup"])
    assert base_kb == [f"refine:{token}:yes", f"refine:{token}:no"]

    # base marcada "Refinando…" y status idem (sin botón cancel: sin job).
    refining_markup = [
        c for c in gateway.calls_by_method("edit_message_reply_markup")
        if c["message_id"] == photos[0]["sent"].message_id
    ]
    assert flat_callback_data(refining_markup[-1]["reply_markup"]) == ["refine_noop"]
    status_edits = gateway.calls_by_method("edit_message_text")
    assert any(c["text"] == "Refinando…" for c in status_edits)

    # refine llamado con el request del ItemResult y los remotes.
    assert len(provider.refine_calls) == 1
    req, remote_paths = provider.refine_calls[0]
    assert req == item.request
    assert remote_paths == [REMOTE_1]

    # base borrada tras entregar la refinada.
    deleted = [c["message_id"] for c in gateway.calls_by_method("delete_message")]
    assert photos[0]["sent"].message_id in deleted
    # ref de la refinada guardada.
    ref = refs_repo.get(CHAT_ID, photos[1]["sent"].message_id)
    assert ref is not None and ref["provider"] == "comfyui"


@pytest.mark.asyncio
async def test_single_yes_refined_shows_elapsed_and_reply_to(tmp_path, gateway, refs_repo):
    """La refinada cronometra su propio 2º stage y base/refinada responden al ancla.

    ``run_refine_flow`` mide el refine y estampa ``elapsed_sec`` en el meta del
    resultado refinado (paridad grok bot.py:4407-4411), así su caption ya no da
    ``…``; y propaga ``reply_to`` (message_id del invocador) a la base y refinada.
    """
    downloader = object()
    sender = ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)  # type: ignore[arg-type]
    provider = _provider(tmp_path)
    refine_uc = ResolveRefineUseCase(provider=provider, timeout=10.0)
    token = refine_uc.register(user_id=USER_ID, job_id=None)
    item = _item(tmp_path)
    ui = ChatUI(gateway, CHAT_ID)
    status = await ui.send_text("Editando imagen...")

    _schedule_decision(refine_uc, token, "yes")
    decision = await run_refine_flow(
        ui,
        item=item,
        sender=sender,
        refine_uc=refine_uc,
        prefix="Edit",
        status_id=status.message_id,
        delete_status=True,
        user_id=USER_ID,
        token=token,
        reply_to=42,
    )

    assert decision == RefineDecision.yes
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 2  # base + refinada
    # base y refinada responden al mensaje que invocó el flujo.
    assert photos[0]["reply_to_message_id"] == 42
    assert photos[1]["reply_to_message_id"] == 42
    # la refinada muestra el tiempo REAL del 2º stage (no "…").
    refined_caption = photos[1]["caption"]
    assert "<b>Tiempo:</b> " in refined_caption
    assert "<b>Tiempo:</b> …" not in refined_caption


# --------------------------------------------------------------------------- #
# single: no / timeout → base final con regen kb
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_single_no_finalizes_base_with_regen(tmp_path, gateway, refs_repo):
    downloader = object()
    sender = ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)  # type: ignore[arg-type]
    provider = _provider(tmp_path)
    refine_uc = ResolveRefineUseCase(provider=provider, timeout=10.0)
    token = refine_uc.register(user_id=USER_ID, job_id=None)
    item = _item(tmp_path)
    ui = ChatUI(gateway, CHAT_ID)
    status = await ui.send_text("Editando imagen...")

    _schedule_decision(refine_uc, token, "no")
    decision = await run_refine_flow(
        ui, item=item, sender=sender, refine_uc=refine_uc,
        prefix="Edit", status_id=status.message_id, delete_status=True,
        user_id=USER_ID, token=token,
    )

    assert decision == RefineDecision.no
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1  # solo la base
    regen_markup = [
        c for c in gateway.calls_by_method("edit_message_reply_markup")
        if c["message_id"] == photos[0]["sent"].message_id
    ]
    assert flat_callback_data(regen_markup[-1]["reply_markup"]) == ["regen"]
    assert provider.refine_calls == []
    # delete_status=True → el status del flujo se borra.
    assert status.message_id in [c["message_id"] for c in gateway.calls_by_method("delete_message")]


@pytest.mark.asyncio
async def test_single_timeout_finalizes_base(tmp_path, gateway, refs_repo):
    downloader = object()
    sender = ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)  # type: ignore[arg-type]
    provider = _provider(tmp_path)
    refine_uc = ResolveRefineUseCase(provider=provider, timeout=0.01)
    token = refine_uc.register(user_id=USER_ID, job_id=None)
    item = _item(tmp_path)
    ui = ChatUI(gateway, CHAT_ID)
    status = await ui.send_text("Editando imagen...")

    decision = await run_refine_flow(
        ui, item=item, sender=sender, refine_uc=refine_uc,
        prefix="Edit", status_id=status.message_id, delete_status=True,
        user_id=USER_ID, token=token,
    )

    assert decision == RefineDecision.timeout
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1
    regen_markup = [
        c for c in gateway.calls_by_method("edit_message_reply_markup")
        if c["message_id"] == photos[0]["sent"].message_id
    ]
    assert flat_callback_data(regen_markup[-1]["reply_markup"]) == ["regen"]
    assert provider.refine_calls == []


# --------------------------------------------------------------------------- #
# single: cancelled → base queda sin refinada, sin botón de confirm
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_single_cancelled_keeps_base_without_kb(tmp_path, gateway, refs_repo):
    downloader = object()
    sender = ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)  # type: ignore[arg-type]
    provider = _provider(tmp_path)
    refine_uc = ResolveRefineUseCase(provider=provider, timeout=10.0)
    job_id = "job-refine"
    token = refine_uc.register(user_id=USER_ID, job_id=job_id)
    item = _item(tmp_path)
    ui = ChatUI(gateway, CHAT_ID)
    status = await ui.send_text("Editando imagen...")

    _schedule_cancel(refine_uc, job_id)
    decision = await run_refine_flow(
        ui, item=item, sender=sender, refine_uc=refine_uc,
        prefix="Edit", status_id=status.message_id, delete_status=True,
        user_id=USER_ID, token=token, job_id=job_id,
    )

    assert decision == RefineDecision.cancelled
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1  # sin refinada
    kb_edits = [
        c for c in gateway.calls_by_method("edit_message_reply_markup")
        if c["message_id"] == photos[0]["sent"].message_id
    ]
    assert flat_callback_data(kb_edits[-1]["reply_markup"]) == []  # kb vacío
    assert provider.refine_calls == []


# --------------------------------------------------------------------------- #
# álbum: yes → confirm aparte, borra confirm, envía álbum refinado
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_album_yes_confirm_separate_and_delete(tmp_path, gateway, refs_repo):
    downloader = object()
    sender = ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)  # type: ignore[arg-type]
    provider = _provider(tmp_path, refined_n=2)
    refine_uc = ResolveRefineUseCase(provider=provider, timeout=10.0)
    token = refine_uc.register(user_id=USER_ID, job_id=None)
    item = _item(tmp_path, n=2)  # álbum base
    ui = ChatUI(gateway, CHAT_ID)

    _schedule_decision(refine_uc, token, "yes")
    decision = await run_refine_flow(
        ui, item=item, sender=sender, refine_uc=refine_uc,
        prefix="Multi-pose ×2", status_id=None, delete_status=False,
        user_id=USER_ID, token=token,
    )

    assert decision == RefineDecision.yes
    groups = gateway.calls_by_method("send_media_group")
    assert len(groups) == 2  # base + refinada
    # Confirm en mensaje de texto aparte con el keyboard de refine.
    texts = gateway.calls_by_method("send_message")
    confirms = [c for c in texts if c["text"] == "¿Refinar las imágenes generadas?"]
    assert len(confirms) == 1
    assert flat_callback_data(confirms[0]["reply_markup"]) == [
        f"refine:{token}:yes",
        f"refine:{token}:no",
    ]
    confirm_id = confirms[0]["sent"].message_id
    # confirm editado a "Refinando…" y borrado al final.
    assert any(c["message_id"] == confirm_id and c["text"] == "Refinando…"
               for c in gateway.calls_by_method("edit_message_text"))
    assert confirm_id in [c["message_id"] for c in gateway.calls_by_method("delete_message")]
    assert len(provider.refine_calls) == 1
    # ref del álbum base en sent[0].
    base_group = groups[0]["sent_group"]
    ref = refs_repo.get(CHAT_ID, base_group[0].message_id)
    assert ref is not None and ref["provider"] == "comfyui"


# --------------------------------------------------------------------------- #
# error del refine → base final + status con error user-safe
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_refine_error_restores_base_and_status(tmp_path, gateway, refs_repo):
    downloader = object()
    sender = ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)  # type: ignore[arg-type]
    provider = FakeComfyuiProvider(refine_outcomes=[_RefineBoom()])
    refine_uc = ResolveRefineUseCase(provider=provider, timeout=10.0)
    token = refine_uc.register(user_id=USER_ID, job_id=None)
    item = _item(tmp_path)
    ui = ChatUI(gateway, CHAT_ID)
    status = await ui.send_text("Editando imagen...")

    _schedule_decision(refine_uc, token, "yes")
    decision = await run_refine_flow(
        ui, item=item, sender=sender, refine_uc=refine_uc,
        prefix="Edit", status_id=status.message_id, delete_status=True,
        user_id=USER_ID, token=token,
    )

    assert decision == RefineDecision.yes
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1  # solo base; sin refinada
    edits = gateway.calls_by_method("edit_message_text")
    assert any(c["text"] == "Fallo el refino" for c in edits)
    regen_markup = [
        c for c in gateway.calls_by_method("edit_message_reply_markup")
        if c["message_id"] == photos[0]["sent"].message_id
    ]
    assert flat_callback_data(regen_markup[-1]["reply_markup"]) == ["regen"]


# --------------------------------------------------------------------------- #
# álbum: no / timeout → confirm editado a "Imagen final.", base final
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("choice", ["no", "timeout"])
@pytest.mark.asyncio
async def test_album_no_or_timeout_finalizes_base(tmp_path, gateway, refs_repo, choice):
    downloader = object()
    sender = ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)  # type: ignore[arg-type]
    provider = _provider(tmp_path)
    timeout = 0.01 if choice == "timeout" else 10.0
    refine_uc = ResolveRefineUseCase(provider=provider, timeout=timeout)
    token = refine_uc.register(user_id=USER_ID, job_id=None)
    item = _item(tmp_path, n=2)  # álbum base
    ui = ChatUI(gateway, CHAT_ID)

    if choice == "no":
        _schedule_decision(refine_uc, token, "no")
    decision = await run_refine_flow(
        ui, item=item, sender=sender, refine_uc=refine_uc,
        prefix="Multi-pose ×2", status_id=None, delete_status=False,
        user_id=USER_ID, token=token,
    )

    assert decision == (RefineDecision.no if choice == "no" else RefineDecision.timeout)
    groups = gateway.calls_by_method("send_media_group")
    assert len(groups) == 1  # solo base; sin álbum refinado
    confirms = [
        c for c in gateway.calls_by_method("send_message")
        if c["text"] == "¿Refinar las imágenes generadas?"
    ]
    assert len(confirms) == 1
    confirm_id = confirms[0]["sent"].message_id
    edits = [
        c for c in gateway.calls_by_method("edit_message_text")
        if c["message_id"] == confirm_id
    ]
    assert edits and edits[-1]["text"] == "Imagen final."
    assert provider.refine_calls == []


# --------------------------------------------------------------------------- #
# álbum: cancelled → confirm borrado, base queda, sin refinada
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_album_cancelled_keeps_base_without_refined(tmp_path, gateway, refs_repo):
    downloader = object()
    sender = ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)  # type: ignore[arg-type]
    provider = _provider(tmp_path)
    refine_uc = ResolveRefineUseCase(provider=provider, timeout=10.0)
    job_id = "job-album-cancel"
    token = refine_uc.register(user_id=USER_ID, job_id=job_id)
    item = _item(tmp_path, n=2)
    ui = ChatUI(gateway, CHAT_ID)

    _schedule_cancel(refine_uc, job_id)
    decision = await run_refine_flow(
        ui, item=item, sender=sender, refine_uc=refine_uc,
        prefix="Multi-pose ×2", status_id=None, delete_status=False,
        user_id=USER_ID, token=token, job_id=job_id,
    )

    assert decision == RefineDecision.cancelled
    groups = gateway.calls_by_method("send_media_group")
    assert len(groups) == 1  # base queda; sin refinada
    confirms = [
        c for c in gateway.calls_by_method("send_message")
        if c["text"] == "¿Refinar las imágenes generadas?"
    ]
    assert len(confirms) == 1
    confirm_id = confirms[0]["sent"].message_id
    assert confirm_id in [
        c["message_id"] for c in gateway.calls_by_method("delete_message")
    ], "el confirm de álbum cancelado se borra"
    assert provider.refine_calls == []
