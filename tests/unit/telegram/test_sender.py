"""Tests del ResultSender (item 5, R4/R6/R10): fan-out y refs post-envío.

Verifica URL única/multi-URL (kie), local ComfyUI single/álbum, PNG local
que supera el tope de sendPhoto enviado como documento sin recomprimir, rechazo
de la API que limpia el status, video local/
remoto, video local rechazado degradado user-safe sin path (C9c), video remoto
que supera el tope único (``media.MAX_MEDIA_BYTES``) o que la API rechaza →
fallback de texto con la URL de recuperación (R10, privado), allowlist
propagada al downloader, caption con prompt truncado a 1024 y errores de
descarga user-safe sobre el status. 0 red y 0 ``unittest.mock`` (fakes del
conftest implementan los Protocols).
"""

from __future__ import annotations

import pytest

from conftest import (
    CHAT_ID,
    FakeMediaDownloader,
    FakeTelegramGateway,
    flat_callback_data,
    make_result,
)
from aiogram.exceptions import TelegramBadRequest
from grokbot.application.events import ItemResult
from grokbot.domain.generation import GenerationRequest, GenerationResult, MediaType
from grokbot.telegram.deps import ComfyChainMemory
from grokbot.telegram import sender as sender_mod
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.downloader import DownloadError
from grokbot.telegram.formatters import SENSITIVE_DOWNLOAD_WARNING
from grokbot.telegram.media import MAX_PHOTO_BYTES
from grokbot.telegram.sender import ResultSender

URL_1 = "https://files.x.ai/one.png"
URL_2 = "https://files.x.ai/two.png"
XAI_URL = "https://files.x.ai/video.mp4"

# Owner anonimizado (R8): el ref se persiste top-level, fuera del regen opaco.
OWNER_UID = 222222222


def _make_sender(gateway, downloader, refs_repo) -> ResultSender:
    return ResultSender(gateway=gateway, downloader=downloader, refs=refs_repo)


def _item(
    *,
    provider: str = "xai",
    urls: list[str] | None = None,
    remote_url: str | None = None,
    file_paths: list[str] | None = None,
    meta: dict | None = None,
    regen: dict | None = None,
    media_type: MediaType = MediaType.IMAGE,
    prompt: str = "un retrato",
) -> ItemResult:
    m: dict = dict(meta or {})
    if urls is not None:
        m["urls"] = urls
    if file_paths is not None:
        m["file_paths"] = file_paths
    result = make_result(
        provider=provider,
        media_type=media_type,
        remote_url=remote_url,
        file_path=file_paths[0] if file_paths else None,
        meta=m,
    )
    return ItemResult(result=result, prompt=prompt, regen_context=regen)


# --------------------------------------------------------------------------- #
# URL única
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_single_url_photo_regen_kb_and_ref_save(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    regen = {"provider": "xai", "mode": "text", "prompt": "un retrato"}
    item = _item(urls=[URL_1], remote_url=URL_1, meta={"download_allowlist": "xai"}, regen=regen)

    status = await ui.send_text("Generando imagen...")
    sent = await sender.send_image(
        ui, item, "Imagen", status_id=status.message_id, delete_status=True
    )

    assert sent is not None
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1
    assert photos[0]["caption"] == "<b>Imagen:</b> …"
    assert flat_callback_data(photos[0]["reply_markup"]) == ["regen"]
    # ref persistido POST-envío con message_id real.
    ref = refs_repo.get(CHAT_ID, sent.primary.message_id)
    assert ref is not None
    assert ref["provider"] == "xai"
    assert ref["kind"] == "image"
    assert ref["regen"] == regen
    # status borrado tras enviar.
    assert gateway.calls_by_method("delete_message")
    # allowlist propagada al downloader.
    assert downloader.calls[-1]["allowlist"] == "xai"


@pytest.mark.asyncio
async def test_download_allowlist_none_passes_through(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(urls=[URL_1], remote_url=URL_1, meta={})
    await sender.send_image(ui, item, "Imagen")
    assert downloader.calls[-1]["allowlist"] is None


# --------------------------------------------------------------------------- #
# Multi-URL kie → N photos separadas con variante i/N
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_multi_url_kie_photos_variant_and_ref_per_index(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    meta = {"provider": "kie", "task_id": "task-1", "download_allowlist": "kie"}
    item = _item(provider="kie", urls=[URL_1, URL_2], remote_url=URL_1, meta=meta)

    sent = await sender.send_image(ui, item, "Prompt")
    assert sent is not None
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 2
    assert photos[0]["caption"] == "<b>Prompt (1/2):</b> …"
    assert photos[1]["caption"] == "<b>Prompt (2/2):</b> …"

    # ref por índice de cada foto.
    first_ref = refs_repo.get(CHAT_ID, sent.sent[0].message_id)
    second_ref = refs_repo.get(CHAT_ID, sent.sent[1].message_id)
    assert first_ref["kie_task_id"] == "task-1"
    assert first_ref["kie_index"] == 0
    assert second_ref["kie_task_id"] == "task-1"
    assert second_ref["kie_index"] == 1
    # No es álbum: media_group no se usó.
    assert gateway.calls_by_method("send_media_group") == []


# --------------------------------------------------------------------------- #
# ComfyUI local: single y álbum
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_comfyui_local_single_photo(tmp_path, gateway, downloader, refs_repo):
    img = tmp_path / "out.png"
    img.write_bytes(b"png-bytes")
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    regen = {"provider": "comfyui", "mode": "edit"}
    item = _item(
        provider="comfyui",
        file_paths=[str(img)],
        meta={"comfyui_remotes": ["/workspace/out.png"]},
        regen=regen,
    )

    sent = await sender.send_image(ui, item, "Edit")
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1
    assert photos[0]["filename"] == "comfyui.png"
    assert photos[0]["caption"] == "<b>Edit:</b> …"
    assert flat_callback_data(photos[0]["reply_markup"]) == ["regen"]
    ref = refs_repo.get(CHAT_ID, sent.primary.message_id)
    assert ref is not None and ref["provider"] == "comfyui"
    assert ref["regen"] == regen
    # No descarga nada (ruta local).
    assert downloader.calls == []


@pytest.mark.asyncio
async def test_comfyui_known_flow_adds_chain_buttons_and_remembers_path(tmp_path, gateway, downloader, refs_repo):
    img = tmp_path / "out.png"
    img.write_bytes(b"png-bytes")
    ui = ChatUI(gateway, CHAT_ID)
    memory = ComfyChainMemory()
    sender = ResultSender(
        gateway=gateway, downloader=downloader, refs=refs_repo, chain_memory=memory,
    )
    request = GenerationRequest(
        provider="comfyui",
        model_id="grok_style",
        media_type=MediaType.IMAGE,
        prompt="un gato",
        params={"model": "grok_style"},
    )
    item = ItemResult(
        result=make_result(
            provider="comfyui",
            media_type=MediaType.IMAGE,
            file_path=str(img),
            meta={"file_paths": [str(img)]},
        ),
        prompt="un gato",
        regen_context={"provider": "comfyui", "mode": "text"},
        request=request,
    )
    await sender.send_image(ui, item, "Prompt", owner_uid=7)
    photos = gateway.calls_by_method("send_photo")
    assert flat_callback_data(photos[0]["reply_markup"]) == [
        "regen", "pipe:edit", "pipe:detail", "pipe:retake",
    ]
    remembered = memory.last(7)
    assert remembered is not None
    assert remembered["path"] == str(img)
    assert remembered["flow_id"] == "grok_style"


@pytest.mark.asyncio
async def test_comfyui_local_album_ref_in_sent0(tmp_path, gateway, downloader, refs_repo):
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    p1.write_bytes(b"a")
    p2.write_bytes(b"b")
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    regen = {"provider": "comfyui", "mode": "edit"}
    item = _item(
        provider="comfyui",
        file_paths=[str(p1), str(p2)],
        meta={"comfyui_remotes": ["/workspace/a.png", "/workspace/b.png"]},
        regen=regen,
    )

    sent = await sender.send_image(ui, item, "Edit")
    assert sent is not None and sent.is_album is True
    groups = gateway.calls_by_method("send_media_group")
    assert len(groups) == 1
    media = groups[0]["media"]
    assert len(media) == 2
    # caption SOLO en la primera del álbum.
    assert media[0].caption is not None
    assert media[1].caption is None
    # ref en sent[0].
    assert sent.sent[0].message_id == groups[0]["sent_group"][0].message_id
    ref = refs_repo.get(CHAT_ID, sent.sent[0].message_id)
    assert ref is not None and ref["provider"] == "comfyui"
    assert gateway.calls_by_method("send_photo") == []


@pytest.mark.asyncio
async def test_comfyui_local_unreadable_reports_on_status(tmp_path, gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    missing = tmp_path / "missing.png"
    item = _item(provider="comfyui", file_paths=[str(missing)], meta={})
    status = await ui.send_text("Generando...")
    sent = await sender.send_image(ui, item, "Edit", status_id=status.message_id)
    assert sent is None
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "No se pudo leer la imagen generada."
    assert edits[-1]["reply_markup"] is None

_ERR_IMAGE_REJECTED = "No se pudo enviar la imagen por Telegram. Intenta de nuevo."


def test_max_photo_bytes_is_under_send_photo_cap():
    """9.5 MiB: bajo el tope de 10 MiB de sendPhoto, encima de un PNG de ~10 MiB."""
    assert MAX_PHOTO_BYTES == (19 * 1024 * 1024) // 2
    assert MAX_PHOTO_BYTES < 10 * 1024 * 1024


def _local_png(tmp_path, name: str, payload: bytes):
    img = tmp_path / name
    img.write_bytes(payload)
    return img


class _RejectingPhotoGateway(FakeTelegramGateway):
    async def send_photo(
        self, chat_id, photo, *, filename="generated.png", caption=None,
        parse_mode="HTML", reply_markup=None, reply_to_message_id=None,
    ):
        raise TelegramBadRequest(method="sendPhoto", message="photo is too big")


class _RejectingDocumentGateway(FakeTelegramGateway):
    async def send_document(
        self, chat_id, document, *, filename="comfyui.png", caption=None,
        parse_mode="HTML", reply_markup=None, reply_to_message_id=None,
    ):
        raise TelegramBadRequest(method="sendDocument", message="file is too big")


class _RejectingMediaGroupGateway(FakeTelegramGateway):
    async def send_media_group(self, chat_id, media, *, reply_to_message_id=None):
        raise TelegramBadRequest(method="sendMediaGroup", message="photo is too big")


@pytest.mark.asyncio
async def test_comfyui_local_over_photo_limit_sends_png_as_document(
    tmp_path, gateway, downloader, refs_repo, monkeypatch
):
    """Cualquier PNG local que no cabe como foto va como archivo, intacto."""
    monkeypatch.setattr(sender_mod, "MAX_PHOTO_BYTES", 8)
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 16
    img = _local_png(tmp_path, "out.png", payload)
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    regen = {"provider": "comfyui", "mode": "text"}
    item = _item(
        provider="comfyui",
        file_paths=[str(img)],
        meta={},
        regen=regen,
    )
    status = await ui.send_text("Generando imagen con Face Detail…")
    sent = await sender.send_image(ui, item, "Edit", status_id=status.message_id)

    assert sent is not None
    assert gateway.calls_by_method("send_photo") == []
    docs = gateway.calls_by_method("send_document")
    assert len(docs) == 1
    assert docs[0]["document"] == payload
    assert docs[0]["filename"] == "comfyui.png"
    assert docs[0]["filename"].endswith(".png")
    assert docs[0]["caption"] == "<b>Edit:</b> …"
    assert flat_callback_data(docs[0]["reply_markup"]) == ["regen"]
    ref = refs_repo.get(CHAT_ID, sent.primary.message_id)
    assert ref is not None and ref["provider"] == "comfyui"
    assert ref["regen"] == regen
    assert gateway.calls_by_method("delete_message")
    assert not any(
        c["text"] == _ERR_IMAGE_REJECTED
        for c in gateway.calls_by_method("edit_message_text")
    )


@pytest.mark.asyncio
async def test_comfyui_local_at_photo_limit_stays_photo(
    tmp_path, gateway, downloader, refs_repo, monkeypatch
):
    monkeypatch.setattr(sender_mod, "MAX_PHOTO_BYTES", 8)
    img = _local_png(tmp_path, "fit.png", b"12345678")
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(provider="comfyui", file_paths=[str(img)], meta={})
    sent = await sender.send_image(ui, item, "Edit")
    assert sent is not None
    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1
    assert photos[0]["photo"] == b"12345678"
    assert gateway.calls_by_method("send_document") == []


@pytest.mark.asyncio
async def test_comfyui_over_photo_limit_is_not_tied_to_one_flow(
    tmp_path, gateway, downloader, refs_repo, monkeypatch
):
    """El corte es el tamaño, no el id de flujo (p. ej. no solo face_detail)."""
    monkeypatch.setattr(sender_mod, "MAX_PHOTO_BYTES", 4)
    img = _local_png(tmp_path, "big.png", b"\x89PNG" + b"yyyy")
    ui = ChatUI(gateway, CHAT_ID)
    memory = ComfyChainMemory()
    sender = ResultSender(
        gateway=gateway, downloader=downloader, refs=refs_repo, chain_memory=memory,
    )
    request = GenerationRequest(
        provider="comfyui",
        model_id="grok_style",
        media_type=MediaType.IMAGE,
        prompt="un gato",
        params={"model": "grok_style"},
    )
    item = ItemResult(
        result=make_result(
            provider="comfyui",
            media_type=MediaType.IMAGE,
            file_path=str(img),
            meta={"file_paths": [str(img)]},
        ),
        prompt="un gato",
        regen_context={"provider": "comfyui", "mode": "text"},
        request=request,
    )
    await sender.send_image(ui, item, "Prompt", owner_uid=7)
    assert gateway.calls_by_method("send_photo") == []
    docs = gateway.calls_by_method("send_document")
    assert len(docs) == 1
    assert docs[0]["document"] == b"\x89PNG" + b"yyyy"
    assert flat_callback_data(docs[0]["reply_markup"]) == [
        "regen", "pipe:edit", "pipe:detail", "pipe:retake",
    ]
    remembered = memory.last(7)
    assert remembered is not None and remembered["flow_id"] == "grok_style"


@pytest.mark.asyncio
async def test_comfyui_local_photo_rejected_clears_generating_status(
    tmp_path, downloader, refs_repo
):
    gateway = _RejectingPhotoGateway()
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    img = _local_png(tmp_path, "small.png", b"png")
    item = _item(provider="comfyui", file_paths=[str(img)], meta={})
    status = await ui.send_text("Generando imagen con Face Detail…")
    sent = await sender.send_image(ui, item, "Edit", status_id=status.message_id)
    assert sent is None
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == _ERR_IMAGE_REJECTED
    assert edits[-1]["reply_markup"] is None
    assert "Generando" not in edits[-1]["text"]
    assert not any(str(img) in c["text"] for c in edits)
    assert gateway.calls_by_method("delete_message") == []


@pytest.mark.asyncio
async def test_comfyui_local_document_rejected_clears_status(
    tmp_path, downloader, refs_repo, monkeypatch
):
    monkeypatch.setattr(sender_mod, "MAX_PHOTO_BYTES", 2)
    gateway = _RejectingDocumentGateway()
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    img = _local_png(tmp_path, "big.png", b"png-bytes")
    item = _item(provider="comfyui", file_paths=[str(img)], meta={})
    status = await ui.send_text("Generando imagen…")
    sent = await sender.send_image(ui, item, "Edit", status_id=status.message_id)
    assert sent is None
    assert gateway.calls_by_method("send_photo") == []
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == _ERR_IMAGE_REJECTED
    assert "Generando" not in edits[-1]["text"]


@pytest.mark.asyncio
async def test_comfyui_local_photo_rejected_without_status_sends_text(
    tmp_path, downloader, refs_repo
):
    gateway = _RejectingPhotoGateway()
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    img = _local_png(tmp_path, "small.png", b"png")
    item = _item(provider="comfyui", file_paths=[str(img)], meta={})
    sent = await sender.send_image(ui, item, "Edit", status_id=None)
    assert sent is None
    texts = [c["text"] for c in gateway.calls_by_method("send_message")]
    assert texts[-1] == _ERR_IMAGE_REJECTED


@pytest.mark.asyncio
async def test_comfyui_local_over_document_cap_does_not_upload(
    tmp_path, gateway, downloader, refs_repo, monkeypatch
):
    monkeypatch.setattr(sender_mod, "MAX_MEDIA_BYTES", 10)
    monkeypatch.setattr(sender_mod, "MAX_PHOTO_BYTES", 4)
    img = _local_png(tmp_path, "huge.png", b"x" * 11)
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(provider="comfyui", file_paths=[str(img)], meta={})
    status = await ui.send_text("Generando…")
    sent = await sender.send_image(ui, item, "Edit", status_id=status.message_id)
    assert sent is None
    assert gateway.calls_by_method("send_photo") == []
    assert gateway.calls_by_method("send_document") == []
    assert gateway.calls_by_method("edit_message_text")[-1]["text"] == _ERR_IMAGE_REJECTED


@pytest.mark.asyncio
async def test_comfyui_album_over_photo_limit_sends_documents_not_media_group(
    tmp_path, gateway, downloader, refs_repo, monkeypatch
):
    monkeypatch.setattr(sender_mod, "MAX_PHOTO_BYTES", 4)
    small = _local_png(tmp_path, "a.png", b"ab")
    big = _local_png(tmp_path, "b.png", b"\x89PNG-big")
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(
        provider="comfyui",
        file_paths=[str(small), str(big)],
        meta={},
        regen={"provider": "comfyui", "mode": "edit"},
    )
    sent = await sender.send_image(ui, item, "Edit")
    assert sent is not None and sent.is_album is True
    assert gateway.calls_by_method("send_media_group") == []
    photos = gateway.calls_by_method("send_photo")
    docs = gateway.calls_by_method("send_document")
    assert len(photos) == 1 and photos[0]["photo"] == b"ab"
    assert photos[0]["filename"] == "comfyui_0.png"
    assert photos[0]["caption"] is not None
    assert len(docs) == 1 and docs[0]["document"] == b"\x89PNG-big"
    assert docs[0]["filename"] == "comfyui_1.png"
    assert docs[0]["caption"] is None
    ref = refs_repo.get(CHAT_ID, sent.sent[0].message_id)
    assert ref is not None and ref["provider"] == "comfyui"


@pytest.mark.asyncio
async def test_comfyui_album_rejected_clears_status(tmp_path, downloader, refs_repo):
    gateway = _RejectingMediaGroupGateway()
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    p1 = _local_png(tmp_path, "a.png", b"a")
    p2 = _local_png(tmp_path, "b.png", b"b")
    item = _item(provider="comfyui", file_paths=[str(p1), str(p2)], meta={})
    status = await ui.send_text("Generando…")
    sent = await sender.send_image(ui, item, "Edit", status_id=status.message_id)
    assert sent is None
    assert gateway.calls_by_method("edit_message_text")[-1]["text"] == _ERR_IMAGE_REJECTED



# --------------------------------------------------------------------------- #
# Video
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_video_remote_download_and_send(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(
        media_type=MediaType.VIDEO,
        remote_url=XAI_URL,
        meta={"urls": [XAI_URL], "download_allowlist": "xai"},
    )
    status = await ui.send_text("Generando video...")
    sent = await sender.send_video(ui, item, "Prompt", status_id=status.message_id)
    videos = gateway.calls_by_method("send_video")
    assert len(videos) == 1
    assert sent is not None
    assert videos[0]["caption"] == "<b>Prompt:</b> …"
    assert downloader.calls[-1]["allowlist"] == "xai"
    assert gateway.calls_by_method("delete_message")


@pytest.mark.asyncio
async def test_video_local_comfyui_saves_ref(tmp_path, gateway, downloader, refs_repo):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"mp4-bytes")
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    regen = {"provider": "comfyui", "mode": "edit", "kind": "video"}
    item = _item(
        provider="comfyui",
        media_type=MediaType.VIDEO,
        file_paths=[str(clip)],
        meta={"comfyui_remotes": ["/workspace/clip.mp4"]},
        regen=regen,
    )
    sent = await sender.send_video(ui, item, "Edit")
    videos = gateway.calls_by_method("send_video")
    assert len(videos) == 1
    assert videos[0]["filename"] == "generated.mp4"
    ref = refs_repo.get(CHAT_ID, sent.primary.message_id)
    assert ref is not None and ref["kind"] == "video"
    assert downloader.calls == []


@pytest.mark.asyncio
async def test_video_local_send_rejected_degrades_user_safe(tmp_path, refs_repo):
    """C9c: la API rechaza un video LOCAL → degrada user-safe sin crash ni path."""
    gateway = _RejectingVideoGateway()
    downloader = FakeMediaDownloader()
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"mp4-bytes")
    item = _item(
        provider="comfyui",
        media_type=MediaType.VIDEO,
        file_paths=[str(clip)],
        meta={"comfyui_remotes": ["/workspace/clip.mp4"]},
        regen={"provider": "comfyui", "mode": "edit"},
    )
    status = await ui.send_text("Generando video...")
    sent = await sender.send_video(ui, item, "Edit", status_id=status.message_id)
    assert sent is None
    assert gateway.calls_by_method("send_video") == [], "el envío fue rechazado"
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == (
        "No se pudo enviar el video por Telegram. "
        "Prueba con otro modelo o una duración/resolución menor."
    )
    assert edits[-1]["reply_markup"] is None
    assert not any("clip.mp4" in c["text"] for c in edits), "no filtra el path local"


@pytest.mark.asyncio
async def test_video_over_limit_fallback_text_no_send(
    gateway, downloader, refs_repo, monkeypatch
):
    monkeypatch.setattr(sender_mod, "MAX_MEDIA_BYTES", 1000)
    downloader.payload = b"x" * 2001
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(
        media_type=MediaType.VIDEO,
        remote_url=XAI_URL,
        meta={"urls": [XAI_URL], "download_allowlist": "xai"},
    )
    status = await ui.send_text("Generando video...")
    sent = await sender.send_video(ui, item, "Prompt", status_id=status.message_id)
    assert sent is None
    assert gateway.calls_by_method("send_video") == []
    edits = gateway.calls_by_method("edit_message_text")
    text = edits[-1]["text"]
    assert "El video es demasiado grande para Telegram" in text
    assert "Descárgalo aquí:" in text
    assert SENSITIVE_DOWNLOAD_WARNING in text
    # La URL de contenido no se fuga fuera del fallback de descarga explícito.
    assert edits[-1]["reply_markup"] is None


@pytest.mark.asyncio
async def test_video_no_url_reports_on_status(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    # Resultado sin remote_url ni file_path (make_result fijaría un default).
    no_url = ItemResult(
        result=GenerationResult(
            provider="xai",
            model_id="fake-video",
            media_type=MediaType.VIDEO,
            remote_url=None,
            meta={"urls": []},
        ),
        prompt="un perro corriendo",
    )
    status = await ui.send_text("Generando video...")
    sent = await sender.send_video(ui, no_url, "Prompt", status_id=status.message_id)
    assert sent is None
    assert gateway.calls_by_method("send_video") == []
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"].startswith("Error: el modelo no devolvió URL de video")


class _RejectingVideoGateway(FakeTelegramGateway):
    """Gateway cuyo ``send_video`` es rechazado por la Bot API.

    Reproduce el error que el ``AiogramGateway`` real relanza en ``send_video``
    (O2 arch): el sender debe degradar a texto con la URL + warning, nunca
    crashar ni filtrar el video.
    """

    async def send_video(
        self, chat_id, video, *, filename="generated.mp4", caption=None,
        parse_mode="HTML", reply_markup=None, reply_to_message_id=None,
    ):
        raise TelegramBadRequest(method="sendVideo", message="video file is too big")


@pytest.mark.asyncio
async def test_video_remote_send_rejected_falls_back_text(refs_repo):
    """O2: la API rechaza el ``send_video`` → fallback de texto user-safe."""
    gateway = _RejectingVideoGateway()
    downloader = FakeMediaDownloader(payload=b"v" * 100)
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(
        media_type=MediaType.VIDEO,
        remote_url=XAI_URL,
        meta={"urls": [XAI_URL], "download_allowlist": "xai"},
    )
    status = await ui.send_text("Generando video...")
    sent = await sender.send_video(ui, item, "Prompt", status_id=status.message_id)
    assert sent is None
    assert gateway.calls_by_method("send_video") == [], "el envío fue rechazado"
    edits = gateway.calls_by_method("edit_message_text")
    text = edits[-1]["text"]
    assert "No se pudo enviar el video por Telegram." in text
    assert "Descárgalo aquí:" in text
    assert XAI_URL in text  # URL en el fallback de descarga explícito
    assert SENSITIVE_DOWNLOAD_WARNING in text
    assert edits[-1]["reply_markup"] is None


@pytest.mark.asyncio
async def test_video_remote_send_rejected_no_status_sends_text(refs_repo):
    """O2: sin status_id el fallback de ``send_video`` rechazado es un mensaje."""
    gateway = _RejectingVideoGateway()
    downloader = FakeMediaDownloader(payload=b"v" * 100)
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(
        media_type=MediaType.VIDEO,
        remote_url=XAI_URL,
        meta={"urls": [XAI_URL]},
    )
    sent = await sender.send_video(ui, item, "Prompt", status_id=None)
    assert sent is None
    texts = [c["text"] for c in gateway.calls_by_method("send_message")]
    assert texts[-1].startswith("No se pudo enviar el video por Telegram.")


# --------------------------------------------------------------------------- #
# Caption con prompt truncado + errores user-safe
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_caption_prompt_truncated_to_1024(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    big_prompt = "x" * 5000
    item = _item(urls=[URL_1], remote_url=URL_1, meta={}, prompt=big_prompt)
    await sender.send_image(
        ui, item, "Imagen", caption_model=None, caption_prompt=True
    )
    caption = gateway.calls_by_method("send_photo")[0]["caption"]
    assert len(caption) <= 1024
    assert "\n<b>Prompt:</b> " in caption
    assert caption.endswith("…")


# --------------------------------------------------------------------------- #
# Owner del ref (R8): se persiste top-level para scopear el botón Regenerar
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ref_persists_explicit_owner_uid(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    regen = {"provider": "xai", "mode": "text", "prompt": "un retrato"}
    item = _item(urls=[URL_1], remote_url=URL_1, meta={"download_allowlist": "xai"}, regen=regen)
    sent = await sender.send_image(ui, item, "Imagen", owner_uid=OWNER_UID)
    ref = refs_repo.get(CHAT_ID, sent.primary.message_id)
    assert ref is not None and ref["owner_uid"] == OWNER_UID
    # owner_uid NUNCA dentro del regen opaco.
    assert "owner_uid" not in ref["regen"]
    assert "user_id" not in ref["regen"]


@pytest.mark.asyncio
async def test_ref_owner_falls_back_to_regen_context_user_id(gateway, downloader, refs_repo):
    """R8: si el caller no hilo owner_uid, cae al user_id que estampa generate_image."""
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    regen = {"provider": "xai", "mode": "text", "prompt": "un retrato", "user_id": OWNER_UID}
    item = _item(urls=[URL_1], remote_url=URL_1, meta={"download_allowlist": "xai"}, regen=regen)
    sent = await sender.send_image(ui, item, "Imagen")
    ref = refs_repo.get(CHAT_ID, sent.primary.message_id)
    assert ref is not None and ref["owner_uid"] == OWNER_UID


@pytest.mark.asyncio
async def test_video_local_ref_persists_owner_uid(tmp_path, gateway, downloader, refs_repo):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"mp4-bytes")
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    regen = {"provider": "comfyui", "mode": "edit", "user_id": OWNER_UID}
    item = _item(
        provider="comfyui",
        media_type=MediaType.VIDEO,
        file_paths=[str(clip)],
        meta={"comfyui_remotes": ["/workspace/clip.mp4"]},
        regen=regen,
    )
    sent = await sender.send_video(ui, item, "Edit", owner_uid=OWNER_UID)
    ref = refs_repo.get(CHAT_ID, sent.primary.message_id)
    assert ref is not None and ref["owner_uid"] == OWNER_UID


@pytest.mark.asyncio
async def test_downloader_error_edits_status_user_safe(gateway, downloader, refs_repo):
    downloader.error = DownloadError("No se pudo descargar el archivo (URL no permitida).")
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(urls=[URL_1], remote_url=URL_1, meta={"download_allowlist": "xai"})
    status = await ui.send_text("Generando...")
    sent = await sender.send_image(ui, item, "Imagen", status_id=status.message_id)
    assert sent is None
    assert gateway.calls_by_method("send_photo") == []
    edits = gateway.calls_by_method("edit_message_text")
    assert edits[-1]["text"] == "No se pudo descargar el archivo (URL no permitida)."
    assert edits[-1]["reply_markup"] is None


# --------------------------------------------------------------------------- #
# Reply al mensaje invocador + tiempo real en el caption
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_send_image_single_url_replies_to_invoker(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(urls=[URL_1], remote_url=URL_1, meta={"download_allowlist": "xai"})

    await sender.send_image(ui, item, "Imagen", reply_to=42)

    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 1
    assert photos[0]["reply_to_message_id"] == 42


@pytest.mark.asyncio
async def test_send_image_multi_url_replies_to_invoker(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(provider="kie", urls=[URL_1, URL_2], remote_url=URL_1, meta={})

    await sender.send_image(ui, item, "Prompt", reply_to=42)

    photos = gateway.calls_by_method("send_photo")
    assert len(photos) == 2
    assert all(p["reply_to_message_id"] == 42 for p in photos)


@pytest.mark.asyncio
async def test_send_image_local_single_and_album_reply(tmp_path, gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    img = tmp_path / "out.png"
    img.write_bytes(b"png-bytes")

    # local single → photo con reply.
    single = _item(provider="comfyui", file_paths=[str(img)], meta={"comfyui_remotes": ["/w.png"]})
    await sender.send_image(ui, single, "Edit", reply_to=42)
    photos = gateway.calls_by_method("send_photo")
    assert photos[-1]["reply_to_message_id"] == 42

    # álbum local → media_group con reply (en el group, no por ítem).
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    p1.write_bytes(b"a")
    p2.write_bytes(b"b")
    album = _item(provider="comfyui", file_paths=[str(p1), str(p2)], meta={})
    await sender.send_image(ui, album, "Edit", reply_to=42)
    groups = gateway.calls_by_method("send_media_group")
    assert groups[-1]["reply_to_message_id"] == 42


@pytest.mark.asyncio
async def test_send_image_default_no_reply(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(urls=[URL_1], remote_url=URL_1, meta={"download_allowlist": "xai"})

    await sender.send_image(ui, item, "Imagen")

    photos = gateway.calls_by_method("send_photo")
    assert photos[0]["reply_to_message_id"] is None


@pytest.mark.asyncio
async def test_send_video_replies_to_invoker(gateway, downloader, refs_repo):
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(
        media_type=MediaType.VIDEO,
        remote_url=XAI_URL,
        meta={"urls": [XAI_URL], "download_allowlist": "xai"},
    )

    await sender.send_video(ui, item, "Prompt", reply_to=42)

    videos = gateway.calls_by_method("send_video")
    assert len(videos) == 1
    assert videos[0]["reply_to_message_id"] == 42


@pytest.mark.asyncio
async def test_caption_shows_real_elapsed_when_present(gateway, downloader, refs_repo):
    """Parte A del lado sender: con ``meta["elapsed_sec"]`` el caption muestra el tiempo."""
    ui = ChatUI(gateway, CHAT_ID)
    sender = _make_sender(gateway, downloader, refs_repo)
    item = _item(urls=[URL_1], remote_url=URL_1, meta={"elapsed_sec": 45})

    await sender.send_image(ui, item, "Imagen")

    photos = gateway.calls_by_method("send_photo")
    assert photos[0]["caption"] == "<b>Imagen:</b> 45s"
