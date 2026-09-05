"""ComfyApiClient tests: HTTP via an injected fake session, WS via a stub.

The fake session avoids aioresponses, which is incompatible with the installed
aiohttp 3.14 (``ClientResponse.__init__`` now requires ``stream_writer``).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import urlparse

import aiohttp
import pytest

from grokbot.providers.base import (
    ProviderGenerationError,
    ProviderInputError,
    ProviderUnavailableError,
)
from grokbot.providers.comfyui.client import ComfyApiClient

BASE = "http://127.0.0.1:8765"


# --------------------------------------------------------------------------- #
# Fake HTTP session / responses (duck-typing the aiohttp surface we use)
# --------------------------------------------------------------------------- #
class FakeResp:
    def __init__(self, *, status: int = 200, payload=None, body: bytes = b""):
        self.status = status
        self._payload = payload
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self._body.decode())

    async def read(self):
        return self._body


class FakeSession:
    """Scripted session keyed by (method, path); the last response repeats."""

    def __init__(self):
        self.routes: dict[tuple[str, str], list[FakeResp]] = {}
        self.calls: list[tuple[str, str, dict]] = []

    def add(self, method: str, path: str, *, status: int = 200, payload=None, body: bytes = b""):
        self.routes.setdefault((method.lower(), path), []).append(
            FakeResp(status=status, payload=payload, body=body)
        )

    def _resolve(self, method: str, url: str, **kwargs):
        path = urlparse(url).path
        self.calls.append((method.lower(), url, kwargs))
        queue = self.routes.get((method.lower(), path))
        if not queue:
            raise AssertionError(f"no fake route for {method.upper()} {path} (calls={self.calls})")
        return queue[0] if len(queue) == 1 else queue.pop(0)

    def get(self, url, **kwargs):
        return self._resolve("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._resolve("post", url, **kwargs)

    async def close(self):
        return None


def _history_success(prompt_id: str) -> dict:
    return {
        prompt_id: {
            "status": {"status_str": "success", "completed": True},
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            },
        }
    }


# --------------------------------------------------------------------------- #
# WS stub
# --------------------------------------------------------------------------- #
def _msg(kind: str, data: dict):
    return SimpleNamespace(
        type=aiohttp.WSMsgType.TEXT, data=json.dumps({"type": kind, "data": data})
    )


class FakeWS:
    def __init__(self, messages: list):
        self._messages = iter(messages)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration


class BlockingWS(FakeWS):
    """WS that never emits another event (forces the overall deadline)."""

    def __init__(self):
        super().__init__([])

    async def __anext__(self):
        await asyncio.sleep(3600)
        raise StopAsyncIteration


def _make_client(*, session, ws_connect=None) -> ComfyApiClient:
    return ComfyApiClient(BASE, session=session, ws_connect=ws_connect, poll_interval=0.01)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_health_true_and_false():
    for status in (200, 500):
        session = FakeSession()
        session.add("get", "/system_stats", status=status, payload={"system": {}})
        client = _make_client(session=session)
        assert await client.health() is (status == 200)


@pytest.mark.asyncio
async def test_enqueue_returns_prompt_id():
    session = FakeSession()
    session.add("post", "/prompt", payload={"prompt_id": "p1"})
    client = _make_client(session=session)

    assert await client.enqueue({"1": {}}, "cid") == "p1"


@pytest.mark.asyncio
async def test_enqueue_validation_error_raises_input_error():
    session = FakeSession()
    session.add(
        "post", "/prompt",
        status=400,
        payload={"error": "x", "node_errors": {"5": {"class_type": "SaveImage"}}},
    )
    client = _make_client(session=session)

    with pytest.raises(ProviderInputError) as err:
        await client.enqueue({"1": {}}, "cid")
    assert err.value.retryable is False


@pytest.mark.asyncio
async def test_run_workflow_ws_success():
    async def ws_factory(url):
        assert "/ws" in url
        return FakeWS([
            _msg("executing", {"prompt_id": "p1", "node": 5}),
            _msg("executing", {"prompt_id": "p1", "node": None}),
        ])

    session = FakeSession()
    session.add("post", "/prompt", payload={"prompt_id": "p1"})
    session.add("get", "/history/p1", payload=_history_success("p1"))
    client = _make_client(session=session, ws_connect=ws_factory)

    assert await client.run_workflow({"1": {}}, timeout=30) == "p1"


@pytest.mark.asyncio
async def test_run_workflow_ws_ignores_binary_previews():
    async def ws_factory(url):
        return FakeWS([
            SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=b"\x01\x02"),
            _msg("executing", {"prompt_id": "p1", "node": None}),
        ])

    session = FakeSession()
    session.add("post", "/prompt", payload={"prompt_id": "p1"})
    session.add("get", "/history/p1", payload=_history_success("p1"))
    client = _make_client(session=session, ws_connect=ws_factory)

    assert await client.run_workflow({"1": {}}, timeout=30) == "p1"


@pytest.mark.asyncio
async def test_run_workflow_ws_execution_error_raises():
    async def ws_factory(url):
        return FakeWS([
            _msg("execution_error", {"prompt_id": "p1", "node_type": "KSampler", "node_id": 7})
        ])

    session = FakeSession()
    session.add("post", "/prompt", payload={"prompt_id": "p1"})
    client = _make_client(session=session, ws_connect=ws_factory)

    with pytest.raises(ProviderGenerationError) as err:
        await client.run_workflow({"1": {}}, timeout=30)
    assert err.value.retryable is False


@pytest.mark.asyncio
async def test_run_workflow_ws_down_falls_back_to_history():
    async def ws_factory(url):
        raise aiohttp.ClientConnectionError("no ws")

    session = FakeSession()
    session.add("post", "/prompt", payload={"prompt_id": "p1"})
    session.add("get", "/history/p1", payload={})  # first: not committed yet
    session.add("get", "/history/p1", payload=_history_success("p1"))
    client = _make_client(session=session, ws_connect=ws_factory)

    assert await client.run_workflow({"1": {}}, timeout=30) == "p1"


@pytest.mark.asyncio
async def test_history_returns_entry():
    session = FakeSession()
    session.add("get", "/history/p1", payload=_history_success("p1"))
    client = _make_client(session=session)

    hist = await client.history("p1")
    assert hist["p1"]["status"]["status_str"] == "success"


@pytest.mark.asyncio
async def test_view_returns_bytes_and_sends_params():
    session = FakeSession()
    session.add("get", "/view", body=b"\x00\x01\x02")
    client = _make_client(session=session)

    assert await client.view(filename="clip.mp4", subfolder="", type_="output") == b"\x00\x01\x02"
    assert session.calls[0][2]["params"] == {"filename": "clip.mp4"}


@pytest.mark.asyncio
async def test_upload_image_posts_multipart_and_returns_name():
    session = FakeSession()
    session.add("post", "/upload/image", payload={"name": "src.png"})
    client = _make_client(session=session)

    assert await client.upload_image("src.png", b"imgbytes") == "src.png"
    assert session.calls[0][1].endswith("/upload/image")


@pytest.mark.asyncio
async def test_generation_deadline_raises_unavailable():
    async def ws_factory(url):
        return BlockingWS()

    session = FakeSession()
    session.add("post", "/prompt", payload={"prompt_id": "p1"})
    session.add("get", "/history/p1", payload={})
    client = _make_client(session=session, ws_connect=ws_factory)

    with pytest.raises(ProviderUnavailableError):
        await client.run_workflow({"1": {}}, timeout=0.05)
