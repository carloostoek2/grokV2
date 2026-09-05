"""SshLocalForward tests with an injected fake ssh spawner (no real SSH)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from grokbot.providers.base import (
    ProviderNotConfiguredError,
    ProviderUnavailableError,
)
from grokbot.providers.comfyui.transport import SshLocalForward, TunnelConfig


class AsyncLines:
    """Async byte-stream whose ``readline`` reaches EOF then stays empty."""

    def __init__(self, lines: list[bytes] | None = None):
        self._lines = list(lines or [])
        self._i = 0

    async def readline(self) -> bytes:
        if self._i < len(self._lines):
            line = self._lines[self._i]
            self._i += 1
            return line
        return b""


class FakeProc:
    """Stands in for ``asyncio.subprocess.Process``."""

    def __init__(self, returncode: int | None = None, stderr: list[bytes] | None = None):
        self.returncode = returncode
        self.stderr = AsyncLines(stderr)
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeSpawner:
    """Async spawner that records argv and returns a scripted process."""

    def __init__(self, proc: FakeProc | None = None):
        self.proc = proc
        self.calls: list[list[str]] = []

    async def __call__(self, argv):
        self.calls.append(list(argv))
        return self.proc


def _make_tunnel(host: str = "box", **overrides) -> SshLocalForward:
    cfg = TunnelConfig(host=host, ssh_port=2222, remote_port=18188, local_port=19000)
    cfg = replace(cfg, **overrides)
    return SshLocalForward(cfg, grace=0.0)


def test_argv_builds_local_forward_command():
    fwd = _make_tunnel()
    argv = fwd._argv(19000)

    assert argv[0] == "ssh"
    assert argv[1:3] == ["-p", "2222"]
    assert "BatchMode=yes" in argv
    assert "ExitOnForwardFailure=yes" in argv
    assert "-N" in argv
    assert "-L" in argv
    assert "127.0.0.1:19000:127.0.0.1:18188" in argv
    assert "root@box" in argv
    # The tunnel only forwards; it never runs a remote command.
    assert not any(a.startswith("root@box:") for a in argv)


@pytest.mark.asyncio
async def test_ensure_starts_and_returns_base_url():
    proc = FakeProc(returncode=None, stderr=[b"debug1: ok\n"])
    spawner = FakeSpawner(proc)
    fwd = _make_tunnel()
    fwd._spawner = spawner

    url = await fwd.ensure()

    assert url == "http://127.0.0.1:19000"
    assert fwd.running is True
    assert fwd.bound_port == 19000
    assert len(spawner.calls) == 1
    await asyncio.sleep(0)  # let the stderr drain task consume its line
    assert fwd.stderr_tail == ["debug1: ok"]
    await fwd.close()
    assert fwd.running is False


@pytest.mark.asyncio
async def test_ensure_not_configured_raises():
    fwd = _make_tunnel(host="")

    with pytest.raises(ProviderNotConfiguredError):
        await fwd.ensure()


@pytest.mark.asyncio
async def test_ensure_immediate_exit_raises_unavailable():
    proc = FakeProc(returncode=255, stderr=[b"Permission denied\n"])
    fwd = _make_tunnel()
    fwd._spawner = FakeSpawner(proc)

    with pytest.raises(ProviderUnavailableError):
        await fwd.ensure()
    assert fwd.running is False


@pytest.mark.asyncio
async def test_restart_tears_down_and_reopens():
    proc = FakeProc(returncode=None)
    spawner = FakeSpawner(proc)
    fwd = _make_tunnel()
    fwd._spawner = spawner

    assert await fwd.ensure() == "http://127.0.0.1:19000"
    first_pid = id(fwd._proc)
    assert await fwd.restart() == "http://127.0.0.1:19000"
    assert id(fwd._proc) == first_pid  # same fake object reused by spawner
    assert len(spawner.calls) == 2
    await fwd.close()


@pytest.mark.asyncio
async def test_close_terminates_running_process():
    proc = FakeProc(returncode=None)
    fwd = _make_tunnel()
    fwd._spawner = FakeSpawner(proc)

    await fwd.ensure()
    await fwd.close()

    assert proc.terminated is True
    assert fwd.running is False
    assert fwd.bound_port is None


def test_resolve_local_port_uses_configured_value():
    assert _make_tunnel(local_port=4321)._resolve_local_port() == 4321


def test_resolve_local_port_ephemeral_is_free_port():
    fwd = SshLocalForward(
        TunnelConfig(host="box", local_port=0), grace=0.0
    )
    port = fwd._resolve_local_port()
    assert isinstance(port, int) and 1024 <= port <= 65535
