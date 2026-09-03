"""SshClient tests with an injected fake runner (no real SSH/SCP)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from grokbot.providers.comfyui.ssh_client import SshClient

CMD = "MODEL='krea2' LORA='none' python3 /workspace/gen_comfy.py"


class FakeRunner:
    """Async runner that records calls and serves a scripted response queue."""

    def __init__(self):
        self.calls = []
        self._queue: list[tuple[int | None, bytes | str]] = []

    def add(self, returncode: int | None, stdout: bytes | str = b""):
        self._queue.append((returncode, stdout))

    async def __call__(self, argv, *, input_bytes: bytes, timeout: int):
        self.calls.append({"argv": argv, "input_bytes": input_bytes, "timeout": timeout})
        rc, out = self._queue.pop(0) if self._queue else (0, b"")
        if argv and argv[0] == "scp" and rc == 0:
            local = Path(argv[-1])
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(out if isinstance(out, bytes) else out.encode())
        return SimpleNamespace(returncode=rc, stdout=out)


@pytest.mark.asyncio
async def test_run_remote_builds_command_and_parses_workspace_lines(tmp_path):
    runner = FakeRunner()
    ssh = SshClient("box", 22, tmpdir=tmp_path, runner=runner)
    runner.add(0, stdout=b"noise\n/workspace/out.png\n/workspace/out2.png\n")

    lines, rc = await ssh.run_remote(CMD, "a cat", timeout=600)

    assert rc == 0
    assert lines == ["/workspace/out.png", "/workspace/out2.png"]
    rec = runner.calls[0]
    argv = rec["argv"]
    assert argv[0] == "ssh"
    assert argv[1:3] == ["-p", "22"]
    opts = set(argv)
    assert "BatchMode=yes" in opts
    assert "ControlMaster=auto" in opts
    assert "ControlPersist=120" in opts
    assert "ServerAliveInterval=30" in opts
    assert any(a.startswith("ControlPath=") and "sshctl_box_22" in a for a in argv)
    assert f"root@box" in opts
    assert argv[-1] == CMD
    assert rec["input_bytes"] == b"a cat"
    assert rec["timeout"] == 600


@pytest.mark.asyncio
async def test_run_remote_payload_json_roundtrip(tmp_path):
    runner = FakeRunner()
    ssh = SshClient("box", tmpdir=tmp_path, runner=runner)
    payload = '{"prompt": "x", "image_b64": "eA=="}'
    runner.add(0, stdout=b"/workspace/o.png\n")

    lines, rc = await ssh.run_remote(CMD, payload, timeout=600)

    assert rc == 0 and lines == ["/workspace/o.png"]
    assert runner.calls[0]["input_bytes"] == payload.encode()


@pytest.mark.asyncio
async def test_run_remote_timeout_returns_none_rc(tmp_path):
    runner = FakeRunner()
    ssh = SshClient("box", tmpdir=tmp_path, runner=runner)
    runner.add(None, stdout=b"")  # runner returned no returncode (timeout)

    lines, rc = await ssh.run_remote(CMD, "slow", timeout=600)

    assert lines == [] and rc is None


@pytest.mark.asyncio
async def test_run_remote_keep_other_stdout_lines_out(tmp_path):
    runner = FakeRunner()
    ssh = SshClient("box", tmpdir=tmp_path, runner=runner)
    runner.add(3, stdout=b"/tmp/something\n/workspace/only.png\n")

    lines, rc = await ssh.run_remote(CMD, "x", timeout=600)

    assert lines == ["/workspace/only.png"]
    assert rc == 3


@pytest.mark.asyncio
async def test_not_configured_host_short_circuits(tmp_path):
    runner = FakeRunner()
    ssh = SshClient("", tmpdir=tmp_path, runner=runner)

    lines, rc = await ssh.run_remote(CMD, "x", timeout=600)
    assert lines == [] and rc is None
    assert runner.calls == []


@pytest.mark.asyncio
async def test_pull_returns_local_path(tmp_path):
    runner = FakeRunner()
    ssh = SshClient("box", 2222, tmpdir=tmp_path, runner=runner)
    runner.add(0, stdout=b"filedata")

    local = await ssh.pull("/workspace/out.mp4")

    assert Path(local).exists()
    assert local.startswith(str(tmp_path))
    assert local.endswith(".mp4")
    rec = runner.calls[0]
    assert rec["argv"][0] == "scp"
    assert rec["argv"][1:3] == ["-P", "2222"]
    assert f"root@box:/workspace/out.mp4" in rec["argv"]
    assert rec["argv"][-1] == local
    assert rec["timeout"] == 120


@pytest.mark.asyncio
async def test_pull_failure_returns_empty(tmp_path):
    runner = FakeRunner()
    ssh = SshClient("box", tmpdir=tmp_path, runner=runner)
    runner.add(1, stdout=b"")

    assert await ssh.pull("/workspace/o.png") == ""


@pytest.mark.asyncio
async def test_pull_not_configured_returns_empty(tmp_path):
    runner = FakeRunner()
    ssh = SshClient("", tmpdir=tmp_path, runner=runner)

    assert await ssh.pull("/workspace/o.png") == ""
    assert runner.calls == []
