"""Async ComfyUI HTTP/WS client (native API) for the Vast box.

Implements the canonical flow from ``docs/comfyui/API_COMFYUI.md`` over
ComfyUI's native API:

1. ``POST /prompt`` with an API-format workflow + ``client_id``.
2. Listen on ``/ws?clientId=<same client_id>`` until ``executing`` with
   ``node is None`` (matching our ``prompt_id``), ignoring binary previews.
   If the WebSocket is unavailable, fall back to polling ``/history``.
3. Confirm ``/history/<prompt_id>`` reports ``status_str == "success"``.
4. Download outputs via ``/view`` (PNG/JPEG/WebP or MP4 bytes).

The aiohttp session and the WebSocket source are injectable so tests drive REST
with ``aioresponses`` and script the WS with a stub. Every failure surfaces
through the typed provider error hierarchy in ``providers/base.py``; messages
are always user-safe (R6), details only go to logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from grokbot.providers.base import (
    ProviderError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderUnavailableError,
)
from grokbot.providers.error_mapping import (
    USER_MSG_GENERIC,
    classify_provider_error,
    log_mapped_error,
    to_provider_error,
)

logger = logging.getLogger(__name__)

_WS_HEARTBEAT_SEC = 30
_HTTP_CONNECT_TIMEOUT_SEC = 15
_DEFAULT_HTTP_TIMEOUT_SEC = 120.0
_DEFAULT_POLL_INTERVAL_SEC = 2.0

# User-safe copy (R6): details/logs carry the technical error, never the user msg.
_NOT_AVAILABLE_MSG = (
    "ComfyUI no está disponible en este momento. Contacta al administrador del bot."
)
_GENERIC_ERR_MSG = USER_MSG_GENERIC

# WebSocket message types we react to (the rest are ignored).
_WS_EXEC_ERROR = "execution_error"
_WS_EXEC_INTERRUPTED = "execution_interrupted"
_WS_EXECUTING = "executing"


class ComfyApiClient:
    """Small async client over ComfyUI's REST + WebSocket surfaces."""

    def __init__(
        self,
        base_url: str,
        *,
        session: aiohttp.ClientSession | None = None,
        ws_connect: (
            Callable[[str], Awaitable[Any]] | None
        ) = None,
        http_timeout: float = _DEFAULT_HTTP_TIMEOUT_SEC,
        poll_interval: float = _DEFAULT_POLL_INTERVAL_SEC,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._ws_connect = ws_connect
        self._http_timeout = float(http_timeout)
        self._poll_interval = float(poll_interval)

    # ------------------------------------------------------------------ #
    # Session plumbing
    # ------------------------------------------------------------------ #
    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def _acquire_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=self._http_timeout,
                    connect=_HTTP_CONNECT_TIMEOUT_SEC,
                )
            )
            self._owns_session = True
        return self._session

    async def aclose(self) -> None:
        """Release the aiohttp session when this client owns it."""
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    @staticmethod
    def _raise_unavailable(message: str = _NOT_AVAILABLE_MSG) -> None:
        raise ProviderUnavailableError(message, user_message=message)

    # ------------------------------------------------------------------ #
    # Read endpoints
    # ------------------------------------------------------------------ #
    async def health(self) -> bool:
        """True when the ComfyUI API answers ``/system_stats`` (short probe)."""
        try:
            session = await self._acquire_session()
            async with session.get(self._url("/system_stats")) as resp:
                return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def object_info(self) -> dict:
        """Full node registry (schemas + model dropdowns) from ``/object_info``."""
        session = await self._acquire_session()
        try:
            async with session.get(self._url("/object_info")) as resp:
                if resp.status != 200:
                    self._raise_unavailable()
                return await resp.json()
        except aiohttp.ClientError as exc:
            logger.warning("comfyui /object_info unreachable: %s", type(exc).__name__)
            self._raise_unavailable()
        except asyncio.TimeoutError:
            logger.warning("comfyui /object_info timed out.")
            self._raise_unavailable()

    async def history(self, prompt_id: str) -> dict:
        """Execution history for one ``prompt_id`` (``outputs`` + ``status``)."""
        session = await self._acquire_session()
        try:
            async with session.get(self._url(f"/history/{prompt_id}")) as resp:
                if resp.status != 200:
                    self._raise_unavailable()
                return await resp.json()
        except aiohttp.ClientError as exc:
            logger.warning("comfyui /history unreachable: %s", type(exc).__name__)
            self._raise_unavailable()
        except asyncio.TimeoutError:
            logger.warning("comfyui /history timed out.")
            self._raise_unavailable()

    async def view(self, *, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        """Download one output/media file by its history metadata."""
        params: dict[str, str] = {"filename": filename}
        if subfolder:
            params["subfolder"] = subfolder
        if type_ and type_ != "output":
            params["type"] = type_
        session = await self._acquire_session()
        try:
            async with session.get(self._url("/view"), params=params) as resp:
                if resp.status != 200:
                    self._raise_unavailable()
                return await resp.read()
        except aiohttp.ClientError as exc:
            logger.warning("comfyui /view download failed: %s", type(exc).__name__)
            self._raise_unavailable()
        except asyncio.TimeoutError:
            logger.warning("comfyui /view download timed out.")
            self._raise_unavailable()

    # ------------------------------------------------------------------ #
    # Enqueue + finish detection
    # ------------------------------------------------------------------ #
    async def enqueue(self, workflow: dict, client_id: str) -> str:
        """Validate + enqueue an API-format workflow; return its ``prompt_id``.

        A 4xx (validation ``node_errors``) is a terminal caller defect; a
        transport failure is a transient ``ProviderUnavailableError``.
        """
        session = await self._acquire_session()
        try:
            async with session.post(
                self._url("/prompt"), json={"prompt": workflow, "client_id": client_id}
            ) as resp:
                status = resp.status
                body = await resp.json()
        except aiohttp.ClientError as exc:
            logger.warning("comfyui /prompt unreachable: %s", type(exc).__name__)
            self._raise_unavailable()
        except asyncio.TimeoutError:
            logger.warning("comfyui /prompt timed out.")
            self._raise_unavailable()
        if status != 200:
            # node_errors is diagnostics only; the user gets generic copy (R6).
            if isinstance(body, dict) and body.get("node_errors"):
                logger.warning(
                    "comfyui workflow validation failed (node_errors=%s)",
                    ",".join(body["node_errors"].keys()),
                )
            raise ProviderInputError(
                "ComfyUI rechazó el workflow.",
                user_message=_GENERIC_ERR_MSG,
            )
        prompt_id = body.get("prompt_id") if isinstance(body, dict) else None
        if not prompt_id:
            raise ProviderGenerationError(
                "ComfyUI no devolvió prompt_id.",
                user_message=_GENERIC_ERR_MSG,
            )
        return str(prompt_id)

    async def run_workflow(self, workflow: dict, *, timeout: float) -> str:
        """Enqueue ``workflow``, wait for completion and return ``prompt_id``.

        End detection is WS-first with a ``/history`` polling fallback. Raises
        the typed error hierarchy on failure/timeout; on success the caller
        reads outputs via :meth:`history` and downloads via :meth:`view`.
        """
        client_id = str(uuid.uuid4())
        prompt_id = await self.enqueue(workflow, client_id)
        await self._wait_done(prompt_id, client_id, timeout=float(timeout))
        return prompt_id

    # ------------------------------------------------------------------ #
    # Upload helper (source images for i2i / i2v)
    # ------------------------------------------------------------------ #
    async def upload_image(self, filename: str, data: bytes) -> str:
        """Upload an input image; returns the ``name`` a ``LoadImage`` can use."""
        form = aiohttp.FormData()
        form.add_field("image", data, filename=filename)
        form.add_field("type", "input")
        form.add_field("overwrite", "false")
        session = await self._acquire_session()
        try:
            async with session.post(
                self._url("/upload/image"), data=form
            ) as resp:
                if resp.status != 200:
                    self._raise_unavailable()
                body = await resp.json()
        except aiohttp.ClientError as exc:
            logger.warning("comfyui /upload/image failed: %s", type(exc).__name__)
            self._raise_unavailable()
        except asyncio.TimeoutError:
            logger.warning("comfyui /upload/image timed out.")
            self._raise_unavailable()
        name = body.get("name") if isinstance(body, dict) else None
        if not name:
            raise ProviderGenerationError(
                "ComfyUI no devolvió el nombre del archivo subido.",
                user_message=_GENERIC_ERR_MSG,
            )
        return str(name)

    # ------------------------------------------------------------------ #
    # Finish detection internals
    # ------------------------------------------------------------------ #
    async def _wait_done(self, prompt_id: str, client_id: str, *, timeout: float) -> None:
        """Wait until ``prompt_id`` finishes; WS first, polling as fallback.

        A single deadline bounds the whole wait: if the WS never reports the end
        (or is unreachable), we fall back to polling ``/history`` with whatever
        budget is left — never more than ``timeout`` in total.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        ws_used = False
        try:
            remaining = max(0.0, deadline - loop.time())
            await asyncio.wait_for(self._consume_ws(prompt_id, client_id), timeout=remaining)
            ws_used = True
        except ProviderError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(
                "comfyui WS no terminó (%s); verificando por /history.", type(exc).__name__
            )
        await self._confirm_success(prompt_id, deadline=deadline, ws_used=ws_used)

    async def _ws_url(self, client_id: str) -> str:
        return self._url(f"/ws?clientId={client_id}")

    async def _connect_ws(self, url: str):
        """Return a WS context; default opens aiohttp, tests inject a factory."""
        if self._ws_connect is not None:
            return await self._ws_connect(url)
        session = await self._acquire_session()
        return await session.ws_connect(url, heartbeat=_WS_HEARTBEAT_SEC)

    async def _consume_ws(self, prompt_id: str, client_id: str) -> None:
        """Consume WS events until our prompt finishes (node None) or errors."""
        ws = await self._connect_ws(await self._ws_url(client_id))
        async with ws:
            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.BINARY:
                    continue  # binary frames are preview images — discard
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    if msg.type is aiohttp.WSMsgType.ERROR:
                        logger.warning("comfyui WS error frame: %s", type(msg).__name__)
                    continue
                try:
                    payload = json.loads(msg.data)
                except (TypeError, ValueError):
                    continue
                kind = payload.get("type")
                data = payload.get("data") or {}
                if kind == _WS_EXEC_ERROR:
                    raise ProviderGenerationError(
                        f"Ejecución ComfyUI falló (nodo {data.get('node_type') or data.get('node_id') or '?'}).",
                        user_message=_GENERIC_ERR_MSG,
                    )
                if kind == _WS_EXEC_INTERRUPTED:
                    raise ProviderGenerationError(
                        "Ejecución ComfyUI interrumpida.",
                        user_message=_GENERIC_ERR_MSG,
                    )
                if (
                    kind == _WS_EXECUTING
                    and data.get("prompt_id") == prompt_id
                    and data.get("node") is None
                ):
                    return  # our prompt finished

    async def _confirm_success(self, prompt_id: str, *, deadline: float, ws_used: bool) -> None:
        """Poll ``/history`` until the run is committed; verify status success.

        When the WS was used this is a short double-check (the server commits
        the history entry a moment after the WS ``node None``); when the WS was
        unavailable or silent it is the primary finish detector.
        """
        loop = asyncio.get_running_loop()
        missing_waits = 0  # history record not committed yet (WS fast-path)
        stale_waits = 0  # record present but status not terminal yet (WS fast-path)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ProviderUnavailableError(
                    "La generación excedió el tiempo de espera.",
                    user_message="La GPU tardó demasiado. Intenta de nuevo.",
                )
            hist = await self.history(prompt_id)
            rec = (hist or {}).get(prompt_id)
            if not rec:
                if ws_used:
                    missing_waits += 1
                    if missing_waits >= 3:
                        raise ProviderGenerationError(
                            "ComfyUI no registró la ejecución.",
                            user_message=_GENERIC_ERR_MSG,
                        )
                await asyncio.sleep(self._poll_interval)
                continue
            status = (rec or {}).get("status") or {}
            status_str = status.get("status_str")
            if status.get("completed") or status_str == "success":
                return
            if status_str == "error":
                messages = status.get("messages") or []
                detail = "; ".join(str(m) for m in messages[:3]) if messages else "error"
                mapped = classify_provider_error(message=detail)
                log_mapped_error(mapped, provider="comfyui")
                raise to_provider_error(
                    mapped,
                    technical=f"ComfyUI run failed prompt_id={prompt_id}: {mapped.detail}",
                    fallback_user_message=_GENERIC_ERR_MSG,
                )
            if ws_used:
                # WS signalled completion but the record is not terminal yet.
                stale_waits += 1
                if stale_waits >= 3:
                    raise ProviderGenerationError(
                        "Estado inconsistente tras el fin de la ejecución.",
                        user_message=_GENERIC_ERR_MSG,
                    )
                await asyncio.sleep(self._poll_interval)
                continue
            await asyncio.sleep(self._poll_interval)
