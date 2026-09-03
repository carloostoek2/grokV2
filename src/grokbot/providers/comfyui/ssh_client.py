"""Isolated SSH/SCP transport for the ComfyUI Vast box.

Encapsulates every subprocess boundary of the ComfyUI provider (semantic
reference: grok bot.py ``_comfyui_ssh_opts``/``_comfyui_ssh_base``/
``_comfyui_run_remote``/``_comfyui_pull``/``_comfyui_tmpdir``). The runner is
injectable so tests never touch a real shell. ``tmpdir`` is configurable and
defaults to a repo-local ``tmp/comfyui`` (outside system ``/tmp``, mirroring
bot.py — the box's tmpfs quota used to break the scp pull-back).

No tokens are involved: the box is reached with key-based auth (``BatchMode``).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

COMFY_PULL_TIMEOUT = 120  # scp of a single generated file
_REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class _RunResult:
    """Stand-in for ``subprocess.CompletedProcess`` on timeout (no returncode)."""

    returncode: int | None
    stdout: bytes


async def _default_runner(
    argv: list[str], *, input_bytes: bytes, timeout: int
):
    """Run ``argv`` in a thread with stdin ``input_bytes``; never raises on
    timeout — returns a result with ``returncode is None`` instead."""
    try:
        return await asyncio.to_thread(
            subprocess.run,
            argv,
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _RunResult(returncode=None, stdout=b"")


class SshClient:
    """Run remote commands and pull files over SSH, one shared connection.

    ``runner`` is an async callable
    ``(argv: list[str], *, input_bytes: bytes, timeout: int) -> CompletedProcess-like``
    (``.returncode: int | None``, ``.stdout: bytes | str``). The default runner
    shells out to the real ``ssh``/``scp`` binaries in a worker thread.
    """

    def __init__(
        self,
        host: str,
        port: int = 22,
        *,
        tmpdir: str | Path | None = None,
        runner=None,
    ):
        self._host = host or ""
        self._port = int(port or 22)
        self._tmpdir = (
            Path(tmpdir) if tmpdir is not None else _REPO_ROOT / "tmp" / "comfyui"
        )
        self._runner = runner or _default_runner

    # -- state ------------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return bool(self._host)

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def tmpdir(self) -> str:
        return str(self._tmpdir)

    # -- internals --------------------------------------------------------
    def _ensure_tmpdir(self) -> None:
        self._tmpdir.mkdir(parents=True, exist_ok=True)

    def _ssh_opts(self) -> list[str]:
        """Shared ssh/scp options reusing ONE connection across a generation
        (ControlMaster), keyed under tmpdir (mirrors grok bot.py)."""
        ctl = self._tmpdir / f"sshctl_{self._host or 'box'}_{self._port or 22}"
        return [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=25",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={ctl}",
            "-o", "ControlPersist=120",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=4",
        ]

    @staticmethod
    def _decode_stdout(stdout: bytes | str | None) -> str:
        if stdout is None:
            return ""
        if isinstance(stdout, bytes):
            return stdout.decode(errors="replace")
        return str(stdout)

    # -- public API -------------------------------------------------------
    async def run_remote(
        self, cmd: str, payload: str, *, timeout: int = 600
    ) -> tuple[list[str], int | None]:
        """Run ``cmd`` on the box with ``payload`` on stdin.

        Returns ``(output_paths, returncode)`` where ``output_paths`` are the
        stdout lines starting with ``/workspace``. ``returncode`` is ``None``
        when the run never completed (host unset, ssh unavailable or timeout).
        """
        if not self.is_configured:
            return [], None
        self._ensure_tmpdir()
        argv = ["ssh", "-p", str(self._port), *self._ssh_opts(), f"root@{self._host}", cmd]
        proc = await self._runner(argv, input_bytes=payload.encode("utf-8"), timeout=timeout)
        if proc.returncode is None:
            return [], None
        out = self._decode_stdout(getattr(proc, "stdout", None))
        lines = [ln for ln in out.splitlines() if ln.startswith("/workspace")]
        return lines, proc.returncode

    async def pull(self, remote_path: str) -> str:
        """scp ``remote_path`` to a local temp path under ``tmpdir``.

        Returns the local path or ``""`` when host is unset / scp fails.
        """
        if not self.is_configured:
            return ""
        self._ensure_tmpdir()
        ext = os.path.splitext(remote_path)[1] or ".png"
        local = str(
            self._tmpdir / f"comfyui_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
        )
        argv = ["scp", "-P", str(self._port), *self._ssh_opts(), f"root@{self._host}:{remote_path}", local]
        proc = await self._runner(argv, input_bytes=b"", timeout=COMFY_PULL_TIMEOUT)
        if proc.returncode is None or proc.returncode != 0 or not os.path.exists(local):
            return ""
        return local
