"""SSH local-forward tunnel to ComfyUI's native HTTP/WS API on the Vast box.

The bot no longer runs scripts or scp on the box (that was the ``gen_comfy.py``
path). It opens ONE long-lived ``ssh -N -L`` local-forward to
``127.0.0.1:<remote_port>`` (the ComfyUI HTTP endpoint *inside* the box) and the
:class:`ComfyApiClient` talks to ``http://127.0.0.1:<local_port>``.

The spawner is injectable so tests never touch a real ``ssh`` binary; the
spawned process is expected to expose the ``asyncio.subprocess`` surface
(``returncode``, ``wait()``, ``terminate()``, ``kill()``, ``stderr.readline()``).
The tunnel is supervised by the provider: before each generation it probes
``/system_stats`` through the client and calls :meth:`restart` when the tunnel
dropped (ssh exit, network blip).
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from grokbot.providers.base import (
    ProviderNotConfiguredError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)

_COMFY_NOT_CONFIGURED_MSG = (
    "ComfyUI no está disponible en este momento. Contacta al administrador del bot."
)
_TUNNEL_DOWN_MSG = (
    "No se pudo establecer la conexión con la GPU remota. Intenta de nuevo."
)

_DEFAULT_SSH_BIN = "ssh"
_SSH_GRACE_SEC = 3.0  # window to detect an immediate ssh exit (bad key/auth/port)
_DRAIN_LIMIT = 40


@dataclass(frozen=True)
class TunnelConfig:
    """SSH endpoint + ports that define the local-forward tunnel."""

    host: str
    ssh_port: int = 22
    # ComfyUI HTTP port INSIDE the box (127.0.0.1:<remote_port>).
    remote_port: int = 18188
    # Local port to bind; 0 = pick a free ephemeral port at start.
    local_port: int = 0
    ssh_bin: str = _DEFAULT_SSH_BIN


class ProcessLike(Protocol):
    """Minimal ``asyncio.subprocess.Process`` surface the tunnel consumes."""

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    @property
    def stderr(self): ...


async def _default_spawner(argv: list[str]) -> ProcessLike:
    """Spawn the real ``ssh`` binary with stderr piped (drained to a tail log)."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )


class SshLocalForward:
    """Manage one persistent local-forward tunnel to the box's ComfyUI."""

    def __init__(
        self,
        config: TunnelConfig,
        *,
        spawner=None,
        grace: float = _SSH_GRACE_SEC,
    ) -> None:
        self._cfg = config
        self._spawner = spawner if spawner is not None else _default_spawner
        self._grace = grace
        self._proc: ProcessLike | None = None
        self._bound: int | None = None
        self._drain_task: asyncio.Task | None = None
        self._tail: deque[str] = deque(maxlen=_DRAIN_LIMIT)

    # -- state ------------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return bool(self._cfg.host)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def base_url(self) -> str | None:
        """``http://127.0.0.1:<local_port>`` when the tunnel is up, else None."""
        if not self.running or self._bound is None:
            return None
        return f"http://127.0.0.1:{self._bound}"

    @property
    def bound_port(self) -> int | None:
        return self._bound

    @property
    def stderr_tail(self) -> list[str]:
        """Last drained stderr lines (for diagnostics/logging; never user-facing)."""
        return list(self._tail)

    # -- argv -------------------------------------------------------------
    def _resolve_local_port(self) -> int:
        if self._cfg.local_port:
            return int(self._cfg.local_port)
        # Ask the OS for a free port, release it, and let ssh bind it right after.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _argv(self, local_port: int) -> list[str]:
        return [
            self._cfg.ssh_bin,
            "-p", str(self._cfg.ssh_port),
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=25",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=4",
            "-o", "StrictHostKeyChecking=accept-new",
            "-N",
            "-L",
            f"127.0.0.1:{local_port}:127.0.0.1:{self._cfg.remote_port}",
            f"root@{self._cfg.host}",
        ]

    # -- lifecycle --------------------------------------------------------
    async def ensure(self) -> str:
        """Return the tunnel ``base_url``, starting (or restarting) if needed."""
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                _COMFY_NOT_CONFIGURED_MSG, user_message=_COMFY_NOT_CONFIGURED_MSG
            )
        if not self.running:
            await self._start()
        url = self.base_url
        if url is None:
            raise ProviderUnavailableError(_TUNNEL_DOWN_MSG, user_message=_TUNNEL_DOWN_MSG)
        return url

    async def restart(self) -> str:
        """Tear down and re-open the tunnel (provider probes health first)."""
        await self.close()
        return await self.ensure()

    async def close(self) -> None:
        """Stop the tunnel and release the local port."""
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        self._bound = None
        if self._drain_task is not None:
            self._drain_task.cancel()
            self._drain_task = None
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    # -- internals --------------------------------------------------------
    async def _start(self) -> None:
        # An earlier process may have exited on its own; discard it before respawn.
        if self._proc is not None and self._drain_task is not None:
            self._drain_task.cancel()
            self._drain_task = None
        local = self._resolve_local_port()
        proc = await self._spawner(self._argv(local))
        if proc.returncode is not None:
            logger.warning("comfyui tunnel exited immediately (rc=%s).", proc.returncode)
            raise ProviderUnavailableError(_TUNNEL_DOWN_MSG, user_message=_TUNNEL_DOWN_MSG)
        self._proc = proc
        self._bound = local
        self._drain_task = asyncio.create_task(self._drain_stderr(proc))
        if self._grace:
            await asyncio.sleep(self._grace)
        if proc.returncode is not None:
            # Died during the grace window (auth refused, forward failure...).
            await self.close()
            raise ProviderUnavailableError(_TUNNEL_DOWN_MSG, user_message=_TUNNEL_DOWN_MSG)

    async def _drain_stderr(self, proc: ProcessLike) -> None:
        """Read stderr line by line into a bounded tail (log-only)."""
        try:
            stderr = getattr(proc, "stderr", None)
            if stderr is None:
                return
            while True:
                line = await stderr.readline()
                if not line:
                    break
                self._tail.append(line.decode(errors="replace").rstrip())
        except Exception:  # noqa: BLE001 — stream closed during stop
            pass
