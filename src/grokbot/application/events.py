"""Eventos tipados de la capa application (D1, item 4).

Vocabulario compartido que los casos de uso emiten como ``AsyncIterator`` y que
el item 5 traduce a mensajes de Telegram. Cada dataclass es frozen y documenta
su uso en item 5 + el rango grok de paridad. Los campos NO llevan HTML/emoji ni
payloads pagos (R6/R8): el copy/format lo decide item 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from grokbot.domain.generation import GenerationResult

if TYPE_CHECKING:
    from grokbot.domain.generation import GenerationRequest


@dataclass(frozen=True)
class BatchStarted:
    """Encabezado de un batch (item 5 muestra el header con botón cancelar).

    ``style`` es ``"variables"`` (random) | ``"var"`` (fijo) | ``"multipose"``.
    ``job_id`` identifica el job activo del batch (item 5 lo usa para el teclado
    Cancelar; aditivo D2 del item 5). Paridad: grok bot.py 2321-2324 (random),
    2730-2733 (var), 2180-2184 (multipose).
    """

    style: str
    total: int
    provider: str | None = None
    model_id: str | None = None
    job_id: str | None = None


@dataclass(frozen=True)
class ItemStarted:
    """Un ítem del batch está por arrancar (item 5 edita el status ``X/Y``).

    Se emite ANTES del poll del provider; el item puede tardar y no emitir más
    eventos hasta el terminal. Paridad: grok bot.py 2333-2340 / 2742-2749.
    """

    index: int
    total: int


@dataclass(frozen=True)
class RetryScheduled:
    """Un intento falló con error retryable y se reintentará tras backoff.

    ``attempt`` es el número de reintento que va a correr (1-based); item 5 lo
    puede mostrar como status. Paridad: grok generate_image 3799-3801.
    """

    attempt: int
    max_attempts: int
    provider: str
    index: int | None = None
    total: int | None = None


@dataclass(frozen=True)
class ItemResult:
    """Terminal exitoso de una generación (single o ítem de batch).

    ``result`` viaja intacto (item 5 decide fan-out por ``meta["file_paths"]`` /
    ``meta["urls"]``, R10). ``combos`` se setea en multipose para el resumen de
    "poses usadas"; ``regen_context`` lo persiste item 5 (opaco, sin payloads).
    ``request`` es el :class:`GenerationRequest` efectivo del ítem (aditivo D2 del
    item 5): refine_chat lo usa para el segundo stage sin re-resolver el provider
    con la config actual. Paridad: grok bot.py 2416-2423 / 2220-2226.
    """

    result: GenerationResult
    prompt: str
    index: int | None = None
    total: int | None = None
    combos: tuple[str, ...] | None = None
    regen_context: dict | None = None
    request: "GenerationRequest | None" = None


@dataclass(frozen=True)
class ItemFailed:
    """Terminal de error de una generación (single o ítem de batch).

    ``exhausted=True`` → último error retryable agotado (solo random dispara
    shuffle+blacklist, item 4). ``terminal=True`` → error no reintentable.
    ``reason`` siempre user-safe (R6). Paridad: grok generate_image 3796-3802.
    """

    reason: str
    prompt: str
    index: int | None = None
    total: int | None = None
    exhausted: bool = False
    terminal: bool = False


@dataclass(frozen=True)
class BatchCancelled:
    """El usuario canceló el batch en un checkpoint entre ítems/eventos.

    ``completed``/``failed`` reflejan lo ya corrido. Item 5 muestra "Cancelado".
    Paridad: grok bot.py 2327-2332 / 2400-2405 / 2736-2741.
    """

    completed: int
    failed: int
    total: int


@dataclass(frozen=True)
class BatchSummary:
    """Resumen terminal del batch (item 5 formatea 2458-2461 / 2820-2823).

    ``combos`` solo en multipose (resumen "poses usadas", grok 2240-2246).
    """

    completed: int
    failed: int
    total: int
    combos: tuple[str, ...] | None = None


@dataclass(frozen=True)
class EmptyList:
    """Una lista necesaria está vacía y no se pudo construir el prompt.

    Item 5 mapea ``name`` → ``LIST_LABELS`` para el label HTML. ``style`` es
    aditivo (C15): el precheck de lista vacía de multipose se emite ANTES del
    ``BatchStarted``, así el evento transporta el estilo efectivo para que el
    presenter elija el target del copy ("el modo Multi-pose" vs "/variables")
    sin depender del parámetro del handler. Paridad: grok bot.py 2301-2310 /
    2341-2347 (variables) y 2168-2172 (multipose).
    """

    name: str
    style: str | None = None


@dataclass(frozen=True)
class BatchRejected:
    """El modelo configurado no aplica para el batch (video/faceswap...).

    ``reason`` es user-safe. Paridad: grok ``_variables_model_or_reject``
    (bot.py 2083-2108).
    """

    reason: str


@dataclass(frozen=True)
class JobsFull:
    """El usuario ya alcanzó MAX_ACTIVE_JOBS_PER_USER (no arranca el batch).

    Item 5 muestra el mensaje de "procesos en curso". Paridad: grok bot.py
    2312-2315 (JOBS_FULL_MSG).
    """

    active: int
    max_active: int
