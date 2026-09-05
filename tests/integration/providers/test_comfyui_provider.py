"""ComfyUIProvider tests with injected fake transport/client (no SSH/network).

The provider composes templates + ComfyApiClient; this file drives it with a
fake tunnel and a fake client, asserting the GenerationResult convention and
that results NEVER carry ``comfyui_remotes`` (refine stays dormant).
"""

from __future__ import annotations

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


class FakeTransport:
    """Stand-in SshLocalForward: reports configured and returns a base_url."""

    def __init__(self, host="box"):
        self.host = host
        self.is_configured = bool(host)
        self.ensure_calls = 0

    async def ensure(self) -> str:
        self.ensure_calls += 1
        return "http://127.0.0.1:19000"


class FakeClient:
    """Stand-in ComfyApiClient serving a scripted run/history/view."""

    def __init__(self, url: str, *, images=("out.png",), blob: bytes = PNG_1x1):
        self.url = url
        self._images = [{"filename": f, "subfolder": "", "type": "output"} for f in images]
        self._blob = blob
        self.closed = False
        self.graph: dict | None = None
        self.timeout: float | None = None
        self.prompt_id = "p1"

    async def run_workflow(self, workflow: dict, *, timeout: float) -> str:
        self.graph = workflow
        self.timeout = timeout
        return self.prompt_id

    async def history(self, prompt_id: str) -> dict:
        return {
            prompt_id: {
                "status": {"status_str": "success", "completed": True},
                "outputs": {"19": {"images": self._images}},
            }
        }

    async def view(self, *, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        return self._blob

    async def aclose(self) -> None:
        self.closed = True


def _req(media_type=MediaType.IMAGE, **overrides):
    base = {
        "provider": "comfyui",
        "model_id": "comfyui",
        "media_type": media_type,
        "prompt": "hola",
    }
    base.update(overrides)
    return GenerationRequest(**base)


def _provider(transport: FakeTransport, tmp_path, images=("out.png",)) -> ComfyUIProvider:
    return ComfyUIProvider(
        transport.host,
        22,
        transport=transport,
        client_factory=lambda url: FakeClient(url, images=images),
        tmpdir=tmp_path,
        seed_factory=lambda: 42,
    )


@pytest.mark.asyncio
async def test_t2i_generate_patches_graph_and_returns_local_file(tmp_path):
    transport = FakeTransport("box")
    calls: list[FakeClient] = []
    prov = ComfyUIProvider(
        "box", 22, transport=transport,
        client_factory=lambda url: calls.append(FakeClient(url)) or calls[-1],
        tmpdir=tmp_path, seed_factory=lambda: 42,
    )

    result = await prov.generate(_req())

    assert result.provider == "comfyui"
    assert result.media_type is MediaType.IMAGE
    assert result.file_path.startswith(str(tmp_path))
    assert Path(result.file_path).read_bytes() == PNG_1x1
    assert result.meta["file_paths"] == [result.file_path]
    # Refine must stay dormant: the legacy trigger key is never emitted.
    assert "comfyui_remotes" not in result.meta

    client = calls[-1]
    assert client.graph["4"]["inputs"]["text"] == "hola"
    assert isinstance(client.graph["1"]["inputs"]["seed"], int)
    assert isinstance(client.graph["17"]["inputs"]["seed"], int)
    assert client.timeout == 600
    assert client.closed is True
    assert transport.ensure_calls == 1


@pytest.mark.asyncio
async def test_generate_no_outputs_raises_unavailable(tmp_path):
    prov = _provider(FakeTransport("box"), tmp_path, images=())

    with pytest.raises(ProviderUnavailableError):
        await prov.generate(_req())


@pytest.mark.asyncio
async def test_empty_host_raises_not_configured(tmp_path):
    transport = FakeTransport(host="")
    prov = _provider(transport, tmp_path)

    assert not prov.available
    with pytest.raises(ProviderNotConfiguredError):
        await prov.generate(_req())


@pytest.mark.asyncio
async def test_source_photo_without_template_variant_raises(tmp_path):
    prov = _provider(FakeTransport("box"), tmp_path)

    with pytest.raises(ProviderInputError) as exc:
        await prov.generate(_req(), source_image=PNG_1x1)

    assert "foto" in exc.value.user_message


@pytest.mark.asyncio
async def test_missing_template_combo_raises(tmp_path):
    prov = _provider(FakeTransport("box"), tmp_path)

    with pytest.raises(ProviderInputError):
        await prov.generate(_req(params={"model": "qwen", "lora": "none"}))


@pytest.mark.asyncio
async def test_m2_video_request_with_image_model_raises(tmp_path):
    prov = _provider(FakeTransport("box"), tmp_path)
    req = _req(media_type=MediaType.VIDEO, params={"model": "krea2", "lora": "none"})

    with pytest.raises(ProviderInputError) as exc:
        await prov.generate(req, source_image=PNG_1x1)

    assert "no genera video" in exc.value.user_message


@pytest.mark.parametrize(
    "params",
    [
        {"model": "krea2' ; touch /tmp/pwned", "lora": "none"},
        {"model": "krea2", "lora": "none'; rm -rf /tmp"},
        {"model": "modelo_inexistente", "lora": "none"},
    ],
)
@pytest.mark.asyncio
async def test_m1_invalid_model_or_lora_raises_before_transport(params, tmp_path):
    transport = FakeTransport("box")
    prov = _provider(transport, tmp_path)

    with pytest.raises(ProviderInputError):
        await prov.generate(_req(params=params))

    assert transport.ensure_calls == 0  # never reached the tunnel/client


@pytest.mark.asyncio
async def test_supports_routing(tmp_path):
    prov = _provider(FakeTransport("box"), tmp_path)

    # Only template-backed combos are supported (v1: krea2/none image).
    assert prov.supports(_req(media_type=MediaType.IMAGE, params={"model": "krea2", "lora": "none"}))
    assert not prov.supports(_req(media_type=MediaType.IMAGE, params={"model": "qwen", "lora": "none"}))
    assert not prov.supports(_req(media_type=MediaType.VIDEO, params={"model": "krea2", "lora": "none"}))
    assert not prov.supports(_req(media_type=MediaType.VIDEO, params={"model": "wan_i2v", "lora": "none"}))
    assert not prov.supports(
        GenerationRequest(provider="xai", model_id="x", media_type=MediaType.IMAGE, prompt="x")
    )
