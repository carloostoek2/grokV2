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
        self.model_files_calls: list[str] = []

    async def model_files(self, folder: str) -> tuple[str, ...]:
        self.model_files_calls.append(folder)
        return ("a.safetensors", "b.safetensors")

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
async def test_edit_flow_without_source_photo_raises(tmp_path):
    """Reverse guard: without a photo an edit flow would run with its baked
    placeholder (example.png) and return a silently wrong result."""
    prov = _provider(FakeTransport("box"), tmp_path)

    with pytest.raises(ProviderInputError) as exc:
        await prov.generate(_req(params={"model": "grok_edit"}))

    assert "foto" in exc.value.user_message


@pytest.mark.asyncio
async def test_t2i_flow_without_source_photo_is_allowed(tmp_path):
    """The guard only targets photo-requiring flows; plain txt2img stays valid."""
    prov = _provider(FakeTransport("box"), tmp_path)

    result = await prov.generate(_req(params={"model": "grok_style"}))

    assert result.media_type is MediaType.IMAGE


@pytest.mark.asyncio
async def test_missing_template_combo_raises(tmp_path):
    prov = _provider(FakeTransport("box"), tmp_path)

    with pytest.raises(ProviderInputError):
        await prov.generate(_req(params={"model": "qwen", "lora": "none"}))


@pytest.mark.asyncio
async def test_m2_video_request_with_image_flow_raises(tmp_path):
    prov = _provider(FakeTransport("box"), tmp_path)
    # grok_style es un flujo de imagen: un request VIDEO es un seam del caller.
    req = _req(media_type=MediaType.VIDEO, params={"model": "grok_style"})

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

    # Solo los flujos con template son soportados, y el media_type debe coincidir.
    assert prov.supports(_req(media_type=MediaType.IMAGE))  # default grok_style
    assert prov.supports(_req(media_type=MediaType.IMAGE, params={"model": "grok_style"}))
    assert not prov.supports(_req(media_type=MediaType.IMAGE, params={"model": "qwen"}))
    assert not prov.supports(_req(media_type=MediaType.VIDEO, params={"model": "grok_style"}))  # flujo de imagen
    assert prov.supports(_req(media_type=MediaType.VIDEO, params={"model": "wan_i2v"}))  # flujo de video
    assert not prov.supports(_req(media_type=MediaType.IMAGE, params={"model": "wan_i2v"}))  # video != imagen
    assert not prov.supports(
        GenerationRequest(provider="xai", model_id="x", media_type=MediaType.IMAGE, prompt="x")
    )

@pytest.mark.parametrize("seed", [0, 123456, (1 << 63) - 1])
async def test_fixed_seed_reaches_all_seed_nodes_and_result(tmp_path, seed):
    calls = []
    prov = ComfyUIProvider("box", transport=FakeTransport(), workflow_source="embed",
        client_factory=lambda url: calls.append(FakeClient(url)) or calls[-1], tmpdir=tmp_path)
    result = await prov.generate(_req(params={"model": "grok_style", "seed": seed}))
    assert calls[0].graph["1"]["inputs"]["seed"] == seed
    assert calls[0].graph["17"]["inputs"]["seed"] == seed
    assert result.meta["seed"] == seed


@pytest.mark.parametrize("seed", ["7", True, -1, 1 << 63])
async def test_invalid_seed_is_rejected_before_touching_transport(tmp_path, seed):
    transport = FakeTransport()
    calls = []
    prov = ComfyUIProvider(
        "box", transport=transport, workflow_source="embed",
        client_factory=lambda url: calls.append(FakeClient(url)) or calls[-1],
        tmpdir=tmp_path,
    )

    with pytest.raises(ProviderInputError):
        await prov.generate(_req(params={"model": "grok_style", "seed": seed}))

    assert calls == []
    assert transport.ensure_calls == 0


async def test_fallback_seed_is_drawn_once_and_shared_by_all_seed_nodes(tmp_path):
    values = iter([111, 222])
    calls = []
    prov = ComfyUIProvider(
        "box", transport=FakeTransport(), workflow_source="embed",
        client_factory=lambda url: calls.append(FakeClient(url)) or calls[-1],
        tmpdir=tmp_path, seed_factory=lambda: next(values),
    )

    result = await prov.generate(_req(params={"model": "grok_style"}))

    # Un solo sorteo: si render volviera a pedir una semilla por nodo, el
    # segundo nodo recibiría 222 y el meta no coincidiría con el grafo.
    assert calls[0].graph["1"]["inputs"]["seed"] == 111
    assert calls[0].graph["17"]["inputs"]["seed"] == 111
    assert result.meta["seed"] == 111

async def test_incomplete_seed_mapping_never_claims_reproducibility(tmp_path):
    import json
    from grokbot.providers.comfyui.workflows.resolver import configure_workflow_source

    calls = []
    prov = ComfyUIProvider("box", transport=FakeTransport(), workflow_source="embed",
        client_factory=lambda url: calls.append(FakeClient(url)) or calls[-1], tmpdir=tmp_path)
    graph = {
        "_meta": {"id": "grok_style", "media_type": "image", "positive_node": "4", "seed_nodes": ["1", "17"], "save_nodes": ["19"]},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "1": {"class_type": "KSampler", "inputs": {"seed": 0}},
        "17": {"class_type": "KSamplerAdvanced", "inputs": {"noise_seed": 7}},
    }
    configure_workflow_source(source="remote", fetch=lambda _: json.dumps(graph))
    result = await prov.generate(_req(params={"model": "grok_style", "seed": 42}))
    assert "seed" not in result.meta
    assert calls[0].graph["1"]["inputs"]["seed"] == 42
    assert calls[0].graph["17"]["inputs"]["noise_seed"] == 7


@pytest.mark.asyncio
async def test_list_loras_reads_the_box_and_caches_within_ttl(tmp_path):
    transport = FakeTransport("box")
    calls: list[FakeClient] = []
    prov = ComfyUIProvider(
        "box", 22, transport=transport,
        client_factory=lambda url: calls.append(FakeClient(url)) or calls[-1],
        tmpdir=tmp_path,
    )

    assert await prov.list_loras() == ("a.safetensors", "b.safetensors")
    assert await prov.list_loras() == ("a.safetensors", "b.safetensors")

    assert len(calls) == 1
    assert calls[0].model_files_calls == ["loras"]
    assert calls[0].closed is True


@pytest.mark.asyncio
async def test_list_loras_without_a_box_raises_not_configured(tmp_path):
    prov = _provider(FakeTransport(host=""), tmp_path)

    with pytest.raises(ProviderNotConfiguredError):
        await prov.list_loras()


@pytest.mark.asyncio
async def test_generate_applies_extra_loras_on_top_of_the_flow(tmp_path):
    transport = FakeTransport("box")
    calls: list[FakeClient] = []
    prov = ComfyUIProvider(
        "box", 22, transport=transport,
        client_factory=lambda url: calls.append(FakeClient(url)) or calls[-1],
        tmpdir=tmp_path, seed_factory=lambda: 42,
    )

    await prov.generate(
        _req(
            params={
                "model": "grok_style",
                "lora_stacks": {
                    "grok_style": [
                        {"name": "style.safetensors", "strength": 0.7}
                    ]
                },
            }
        )
    )

    graph = calls[-1].graph
    assert graph["900000"]["class_type"] == "LoraLoaderModelOnly"
    assert graph["900000"]["inputs"]["lora_name"] == "style.safetensors"
    assert graph["900000"]["inputs"]["strength_model"] == 0.7
    # Base y refine consumen el stack; la LoRA horneada sigue en el grafo.
    assert graph["1"]["inputs"]["model"] == ["900000", 0]
    assert graph["17"]["inputs"]["model"] == ["900000", 0]
    assert graph["100"]["inputs"]["lora_name"] == "grokstyle_krea2_v2.safetensors"


@pytest.mark.asyncio
async def test_generate_ignores_an_invalid_lora_stack(tmp_path):
    transport = FakeTransport("box")
    calls: list[FakeClient] = []
    prov = ComfyUIProvider(
        "box", 22, transport=transport,
        client_factory=lambda url: calls.append(FakeClient(url)) or calls[-1],
        tmpdir=tmp_path, seed_factory=lambda: 42,
    )

    await prov.generate(
        _req(
            params={
                "model": "grok_style",
                "lora_stacks": {"grok_style": [{"name": "", "strength": 99}]},
            }
        )
    )

    assert "900000" not in calls[-1].graph
    assert calls[-1].graph["1"]["inputs"]["model"] == ["100", 0]


@pytest.mark.asyncio
async def test_generate_uses_the_resolved_flow_stack_not_the_requested_one(tmp_path):
    """Si el perfil cae al flujo default, se aplica SU stack (o ninguno)."""
    from grokbot.providers.comfyui.workflows.resolver import set_active_profile

    transport = FakeTransport("box")
    calls: list[FakeClient] = []
    prov = ComfyUIProvider(
        "box", 22, transport=transport,
        client_factory=lambda url: calls.append(FakeClient(url)) or calls[-1],
        tmpdir=tmp_path, seed_factory=lambda: 42,
    )
    set_active_profile("t4")  # agil_edit_hq (a100) no existe: cae a grok_style
    try:
        await prov.generate(
            _req(
                params={
                    "model": "agil_edit_hq",
                    "lora_stacks": {
                        "agil_edit_hq": [
                            {"name": "hq_only.safetensors", "strength": 1.0}
                        ],
                        "grok_style": [
                            {"name": "style.safetensors", "strength": 0.7}
                        ],
                    },
                }
            )
        )
    finally:
        set_active_profile(None)

    graph = calls[-1].graph
    # El flujo resuelto es grok_style: se aplica SU stack, nunca el de HQ.
    assert graph["900000"]["inputs"]["lora_name"] == "style.safetensors"
    assert all(
        "hq_only.safetensors" not in str(node)
        for node in graph.values()
    )
