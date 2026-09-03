"""Tests del ResultSender (item 5, R4/R6): fan-out y refs post-envío.

Verifica URL única/multi-URL (kie), local ComfyUI single/álbum, video local/
remoto, fallback de video > tope, allowlist propagada al downloader, caption con
prompt truncado a 1024 y errores de descarga user-safe sobre el status. 0 red y
0 ``unittest.mock`` (fakes del conftest implementan los Protocols).
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
from grokbot.application.events import ItemResult
from grokbot.domain.generation import GenerationResult, MediaType
from grokbot.telegram import sender as sender_mod
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.downloader import DownloadError
from grokbot.telegram.formatters import SENSITIVE_DOWNLOAD_WARNING
from grokbot.telegram.sender import ResultSender

URL_1 = "https://files.x.ai/one.png"
URL_2 = "https://files.x.ai/two.png"
XAI_URL = "https://files.x.ai/video.mp4"


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
async def test_video_over_limit_fallback_text_no_send(
    gateway, downloader, refs_repo, monkeypatch
):
    monkeypatch.setattr(sender_mod, "TELEGRAM_MAX_VIDEO_BYTES", 1000)
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
