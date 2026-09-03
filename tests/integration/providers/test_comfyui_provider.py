"""ComfyUIProvider tests with an injected fake ssh client (no SSH/network)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from grokbot.domain.generation import GenerationRequest, MediaType
from grokbot.providers.base import (
    ProviderGenerationError,
    ProviderInputError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
)
from grokbot.providers.comfyui.provider import ComfyUIProvider

PNG_1x1 = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeSsh:
    """Stand-in SshClient: records run_remote/pull and serves scripted outputs."""

    def __init__(self, host="box", remotes=("/workspace/out.png",), rc=0):
        self.host = host
        self.is_configured = bool(host)
        self.remotes = list(remotes)
        self.rc = rc
        self.run_calls = []
        self.pull_calls = []

    async def run_remote(self, cmd: str, payload: str, *, timeout: int):
        self.run_calls.append({"cmd": cmd, "payload": payload, "timeout": timeout})
        return list(self.remotes), self.rc

    async def pull(self, remote_path: str) -> str:
        self.pull_calls.append(remote_path)
        return f"local-{Path(remote_path).name}"


def _req(media_type=MediaType.IMAGE, **overrides):
    base = {
        "provider": "comfyui",
        "model_id": "comfyui",
        "media_type": media_type,
        "prompt": "hola",
    }
    base.update(overrides)
    return GenerationRequest(**base)


def _provider(fake: FakeSsh) -> ComfyUIProvider:
    return ComfyUIProvider(fake.host or "", 22, ssh=fake)


@pytest.mark.asyncio
async def test_t2i_generate_file_path_and_meta():
    fake = FakeSsh(remotes=("/workspace/out.png",))
    prov = _provider(fake)

    result = await prov.generate(_req())

    assert result.provider == "comfyui"
    assert result.media_type is MediaType.IMAGE
    assert result.file_path == "local-out.png"
    assert result.meta["file_paths"] == ["local-out.png"]
    assert result.meta["comfyui_remotes"] == ["/workspace/out.png"]
    assert result.meta["download_allowlist"] is None

    call = fake.run_calls[0]
    assert "MODEL='krea2' LORA='none' python3 /workspace/gen_comfy.py" == call["cmd"]
    assert call["payload"] == "hola"  # t2i sends the raw prompt on stdin
    assert call["timeout"] == 600
    assert fake.pull_calls == ["/workspace/out.png"]


@pytest.mark.asyncio
async def test_img2img_generate_sends_json_with_image_b64():
    fake = FakeSsh(remotes=("/workspace/edit.png",))
    prov = _provider(fake)
    req = _req(params={"model": "krea2", "lora": "krea_edit"})

    await prov.generate(req, source_image=PNG_1x1)

    call = fake.run_calls[0]
    assert "MODEL='krea2' LORA='krea_edit' python3 /workspace/gen_comfy.py" == call["cmd"]
    payload = json.loads(call["payload"])
    assert payload["prompt"] == "hola"
    assert payload["image_b64"] == base64.b64encode(PNG_1x1).decode("ascii")
    assert "prompts" not in payload


@pytest.mark.asyncio
async def test_img2img_with_prompts_param():
    fake = FakeSsh(remotes=("/workspace/multi.png",))
    prov = _provider(fake)
    req = _req(params={"model": "qwen", "lora": "multipose_batch", "prompts": ["rama a", "rama b"]})

    await prov.generate(req, source_image=PNG_1x1)

    payload = json.loads(fake.run_calls[0]["payload"])
    assert payload["prompts"] == ["rama a", "rama b"]


@pytest.mark.parametrize(
    "params,needle",
    [
        ({"model": "wan_i2v"}, "video necesita una foto"),
        ({"model": "minimax_i2v"}, "video necesita una foto"),
        ({"model": "krea2", "lora": "krea_edit"}, "edición de identidad"),
        ({"model": "qwen", "lora": "multipose_batch"}, "Multi-pose"),
        ({"model": "qwen_aio"}, "Qwen AIO"),
    ],
)
@pytest.mark.asyncio
async def test_t2i_preconditions_without_photo_raise(params, needle):
    fake = FakeSsh()
    prov = _provider(fake)

    with pytest.raises(ProviderInputError) as exc:
        await prov.generate(_req(params=params))

    assert needle in exc.value.user_message
    assert fake.run_calls == []  # validated before hitting the box


@pytest.mark.asyncio
async def test_video_generate_detected_by_model_and_longer_timeout():
    fake = FakeSsh(remotes=("/workspace/vid.mp4",))
    prov = _provider(fake)
    req = _req(media_type=MediaType.VIDEO, params={"model": "wan_i2v"})

    assert prov.supports(req)
    result = await prov.generate(req, source_image=PNG_1x1)

    assert result.media_type is MediaType.VIDEO
    assert result.file_path == "local-vid.mp4"
    assert fake.run_calls[0]["timeout"] == 1500


@pytest.mark.asyncio
async def test_multi_output_meta_lists():
    fake = FakeSsh(remotes=("/workspace/a.png", "/workspace/b.png"))
    prov = _provider(fake)

    result = await prov.generate(_req())

    assert result.file_path == "local-a.png"
    assert result.meta["file_paths"] == ["local-a.png", "local-b.png"]
    assert result.meta["comfyui_remotes"] == ["/workspace/a.png", "/workspace/b.png"]


@pytest.mark.asyncio
async def test_no_remotes_raises_unavailable():
    fake = FakeSsh(remotes=())
    prov = _provider(fake)

    with pytest.raises(ProviderUnavailableError):
        await prov.generate(_req())


@pytest.mark.asyncio
async def test_empty_host_raises_not_configured():
    fake = FakeSsh(host="")
    prov = _provider(fake)

    with pytest.raises(ProviderNotConfiguredError):
        await prov.generate(_req())
    with pytest.raises(ProviderNotConfiguredError):
        await prov.refine(_req(), ["/workspace/a.png"])
    assert not prov.available


@pytest.mark.asyncio
async def test_refine_builds_refine_only_command():
    fake = FakeSsh(remotes=("/workspace/refined.png",))
    prov = _provider(fake)
    req = _req(params={"model": "krea2_raw", "lora": "none"})

    result = await prov.refine(req, ["/workspace/base.png", "/workspace/base2.png"])

    assert result.file_path == "local-refined.png"
    call = fake.run_calls[0]
    assert "REFINE_ONLY='1'" in call["cmd"]
    assert "REFINE_INPUT='/workspace/base.png,/workspace/base2.png'" in call["cmd"]
    assert "MODEL='krea2_raw'" in call["cmd"]
    assert call["payload"] == "hola"
    assert call["timeout"] == 1200 * 2 + 300


@pytest.mark.asyncio
async def test_refine_invalid_remote_path_raises():
    fake = FakeSsh()
    prov = _provider(fake)

    # A non-"/workspace" prefix and a shell meta-character are both rejected
    # (the charset guard prevents shell injection when the path is embedded).
    with pytest.raises(ProviderInputError):
        await prov.refine(_req(), ["bad-no-slash.png"])
    with pytest.raises(ProviderInputError):
        await prov.refine(_req(), ["/workspace/a'; rm -rf /tmp"])
    assert fake.run_calls == []


@pytest.mark.asyncio
async def test_refine_returncodes_map_to_typed_errors():
    fake = FakeSsh(remotes=())
    fake.rc = 2
    prov = _provider(fake)
    with pytest.raises(ProviderInputError):
        await prov.refine(_req(), ["/workspace/a.png"])

    fake.rc = 3
    with pytest.raises(ProviderGenerationError):
        await prov.refine(_req(), ["/workspace/a.png"])


@pytest.mark.parametrize(
    "params",
    [
        {"model": "krea2' ; touch /tmp/pwned", "lora": "none"},
        {"model": "krea2", "lora": "none'; rm -rf /tmp"},
        {"model": "modelo_inexistente", "lora": "none"},
        {"model": "krea2", "lora": "$(id)"},
    ],
)
@pytest.mark.asyncio
async def test_m1_invalid_model_or_lora_raises_before_command(params):
    fake = FakeSsh()
    prov = _provider(fake)

    with pytest.raises(ProviderInputError):
        await prov.generate(_req(params=params))

    assert fake.run_calls == []  # never composed/ran a shell command


@pytest.mark.asyncio
async def test_m1_refine_validates_model_and_lora():
    fake = FakeSsh()
    prov = _provider(fake)

    with pytest.raises(ProviderInputError):
        await prov.refine(_req(params={"model": "krea2'; id", "lora": "none"}), ["/workspace/a.png"])

    assert fake.run_calls == []


@pytest.mark.asyncio
async def test_m1_valid_model_lora_command_is_plain():
    fake = FakeSsh(remotes=("/workspace/ok.png",))
    prov = _provider(fake)
    await prov.generate(_req(params={"model": "krea2_raw", "lora": "krea_snapshot"}))

    cmd = fake.run_calls[0]["cmd"]
    model_part = cmd.split("MODEL='")[1].split("'", 1)[0]
    lora_part = cmd.split("LORA='")[1].split("'", 1)[0]
    # Only the validated identifiers reach the shell command (no raw params).
    assert model_part == "krea2_raw"
    assert lora_part == "krea_snapshot"


@pytest.mark.asyncio
async def test_m2_video_request_with_image_model_raises():
    fake = FakeSsh()
    prov = _provider(fake)
    req = _req(media_type=MediaType.VIDEO, params={"model": "krea2"})

    with pytest.raises(ProviderInputError) as exc:
        await prov.generate(req, source_image=PNG_1x1)

    assert "no genera video" in exc.value.user_message
    assert fake.run_calls == []


@pytest.mark.asyncio
async def test_supports_routing():
    fake = FakeSsh()
    prov = _provider(fake)

    assert prov.supports(_req(media_type=MediaType.IMAGE, params={"model": "krea2"}))
    assert prov.supports(_req(media_type=MediaType.VIDEO, params={"model": "minimax_i2v"}))
    assert not prov.supports(_req(media_type=MediaType.VIDEO, params={"model": "krea2"}))
    assert not prov.supports(
        GenerationRequest(provider="xai", model_id="grok-imagine-image", media_type=MediaType.IMAGE, prompt="x")
    )
