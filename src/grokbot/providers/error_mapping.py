"""Shared provider error classification → typed ProviderError + Spanish UX.

Providers (Replicate, xAI, Kie, …) funnel HTTP bodies / prediction.error /
exception strings through :func:`classify_provider_error` so image and video
surface the same clear Spanish messages instead of a generic
“Error… Intenta más tarde.” for known failure modes (moderation, billing,
rate limits, …).

Never log prompts, tokens, or secrets — only kind + provider + truncated detail.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from grokbot.providers.base import (
    ProviderAuthenticationError,
    ProviderContentError,
    ProviderError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)

_DETAIL_LIMIT = 200

# ---------------------------------------------------------------------------
# Public Spanish copy (single source of truth for providers)
# ---------------------------------------------------------------------------
USER_MSG_MODERATION = (
    "El contenido fue bloqueado por las políticas de moderación. "
    "Prueba con otro prompt o imagen."
)
# Alias kept for call sites that previously imported xAI’s constant.
CONTENT_MODERATION_MSG = USER_MSG_MODERATION

USER_MSG_BILLING = (
    "No hay créditos suficientes o se alcanzó el límite de gasto. "
    "Revisa la cuenta del proveedor."
)
USER_MSG_RATE_LIMIT = "Demasiadas solicitudes. Intenta de nuevo en unos segundos."
USER_MSG_TIMEOUT = "Tiempo de espera agotado. Intenta de nuevo."
USER_MSG_NOT_FOUND = "El modelo o recurso no se encontró. Revisa la configuración."
USER_MSG_INVALID_INPUT = "La solicitud no es válida. Revisa el prompt o la imagen."
USER_MSG_GENERIC = "Error en la generación. Intenta de nuevo más tarde."


class ErrorKind(str, Enum):
    """Coarse failure category shared across providers."""

    MODERATION = "moderation"
    BILLING = "billing"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MappedError:
    """Structured classification result (safe to log ``detail``)."""

    kind: ErrorKind
    user_message: str
    detail: str


# Keyword / code heuristics (matched against a lowercased haystack).
_MODERATION_MARKERS = (
    "sensitive",
    "e005",
    "flagged",
    "nsfw",
    "moderation",
    "content policy",
    "content_policy",
    "respect_moderation",
    "safety system",
    "safety filter",
    "blocked by",
    "violat",  # violate / violation
)
_BILLING_MARKERS = (
    "credit",
    "crédito",
    "credito",
    "spending limit",
    "insufficient",
    "billing",
    "payment required",
    "payment_required",
    "out of credits",
    "quota exceeded",
    "quota_exceeded",
    "balance",
    "top up",
    "top-up",
)
_RATE_MARKERS = ("rate limit", "rate_limit", "too many requests", "throttl")
_TIMEOUT_MARKERS = ("timeout", "timed out", "time-out", "deadline exceeded", "deadline_exceeded")
_NOT_FOUND_MARKERS = (
    "not found",
    "does not exist",
    "unknown model",
    "model not found",
    "no such model",
)
_INVALID_MARKERS = (
    "invalid",
    "bad request",
    "validation",
    "malformed",
    "unsupported",
    "must be",
    "required field",
)


def truncate_detail(text: str, limit: int = _DETAIL_LIMIT) -> str:
    """Collapse whitespace and truncate for WARNING logs (never prompts/secrets)."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "…"


def _prediction_error_from_exc(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    prediction = getattr(exc, "prediction", None)
    if prediction is None:
        return None
    err = getattr(prediction, "error", None)
    if err is None:
        return None
    return str(err)


def _haystack(
    *,
    message: str | None,
    body: str | None,
    prediction_error: str | None,
    exc: BaseException | None,
) -> str:
    parts: list[str] = []
    for chunk in (prediction_error, message, body):
        if chunk:
            parts.append(str(chunk))
    pred_from_exc = _prediction_error_from_exc(exc)
    if pred_from_exc:
        parts.append(pred_from_exc)
    if exc is not None:
        parts.append(f"{type(exc).__name__}: {exc}")
    return " ".join(parts).lower()


def _contains_any(haystack: str, markers: tuple[str, ...]) -> bool:
    return any(marker in haystack for marker in markers)


def classify_provider_error(
    *,
    message: str | None = None,
    body: str | None = None,
    prediction_error: str | None = None,
    status_code: int | None = None,
    exc: BaseException | None = None,
) -> MappedError:
    """Heuristic map of provider failure signals → kind + Spanish user_message.

    Status codes are consulted alongside text (402 → billing, 429 → rate_limit,
    404 → not_found). Text markers cover Replicate ``E005``/sensitive, xAI
    credits/moderation, and Kie failMsg strings.
    """
    text = _haystack(
        message=message,
        body=body,
        prediction_error=prediction_error,
        exc=exc,
    )
    detail_src = prediction_error or message or body or (
        f"{type(exc).__name__}: {exc}" if exc is not None else ""
    )
    if status_code is not None and not detail_src:
        detail_src = f"HTTP {status_code}"
    elif status_code is not None:
        detail_src = f"HTTP {status_code}; {detail_src}"
    detail = truncate_detail(detail_src)

    # --- Specific kinds (order: moderation > billing > rate > timeout > …) ---
    if _contains_any(text, _MODERATION_MARKERS):
        return MappedError(ErrorKind.MODERATION, USER_MSG_MODERATION, detail)

    if status_code == 402 or _contains_any(text, _BILLING_MARKERS):
        return MappedError(ErrorKind.BILLING, USER_MSG_BILLING, detail)

    if status_code == 429 or _contains_any(text, _RATE_MARKERS):
        return MappedError(ErrorKind.RATE_LIMIT, USER_MSG_RATE_LIMIT, detail)

    if _contains_any(text, _TIMEOUT_MARKERS):
        return MappedError(ErrorKind.TIMEOUT, USER_MSG_TIMEOUT, detail)

    if status_code == 404 or _contains_any(text, _NOT_FOUND_MARKERS):
        return MappedError(ErrorKind.NOT_FOUND, USER_MSG_NOT_FOUND, detail)

    if _contains_any(text, _INVALID_MARKERS) or (
        status_code is not None and 400 <= status_code < 500 and status_code not in (401, 403)
    ):
        return MappedError(ErrorKind.INVALID_INPUT, USER_MSG_INVALID_INPUT, detail)

    return MappedError(ErrorKind.UNKNOWN, USER_MSG_GENERIC, detail)


def to_provider_error(
    mapped: MappedError,
    *,
    technical: str | None = None,
    status_code: int | None = None,
    has_prediction: bool = False,
    fallback_user_message: str | None = None,
    unavailable_if_unknown: bool = False,
) -> ProviderError:
    """Convert a :class:`MappedError` into the existing typed ProviderError tree.

    ``fallback_user_message`` overrides the UX string only for
    :attr:`ErrorKind.UNKNOWN` (e.g. “Error en la generación de video…”).
    """
    tech = technical or mapped.detail or mapped.kind.value
    um = mapped.user_message
    if mapped.kind is ErrorKind.UNKNOWN and fallback_user_message:
        um = fallback_user_message

    if mapped.kind is ErrorKind.MODERATION:
        return ProviderContentError(tech, user_message=um)
    if mapped.kind is ErrorKind.BILLING:
        return ProviderAuthenticationError(tech, user_message=um)
    if mapped.kind is ErrorKind.RATE_LIMIT:
        return ProviderRateLimitError(tech, user_message=um)
    if mapped.kind is ErrorKind.TIMEOUT:
        return ProviderTimeoutError(tech, user_message=um)
    if mapped.kind is ErrorKind.NOT_FOUND:
        return ProviderInputError(tech, user_message=um)
    if mapped.kind is ErrorKind.INVALID_INPUT:
        return ProviderInputError(tech, user_message=um)

    # UNKNOWN — preserve structural cues from the transport layer.
    if status_code == 429:
        return ProviderRateLimitError(tech, user_message=um)
    if status_code in (401, 403):
        return ProviderAuthenticationError(tech, user_message=um)
    if has_prediction:
        return ProviderGenerationError(tech, user_message=um)
    if status_code is not None and status_code >= 500:
        return ProviderUnavailableError(tech, user_message=um)
    if status_code is not None and 400 <= status_code < 500:
        return ProviderInputError(tech, user_message=um)
    if unavailable_if_unknown:
        return ProviderUnavailableError(tech, user_message=um)
    return ProviderGenerationError(tech, user_message=um)


def log_mapped_error(mapped: MappedError, *, provider: str) -> None:
    """WARNING with kind + provider + truncated detail (no prompts/secrets)."""
    logger.warning(
        "provider_error kind=%s provider=%s detail=%s",
        mapped.kind.value,
        provider,
        mapped.detail,
    )


def map_and_raise(
    *,
    provider: str,
    message: str | None = None,
    body: str | None = None,
    prediction_error: str | None = None,
    status_code: int | None = None,
    exc: BaseException | None = None,
    technical: str | None = None,
    has_prediction: bool = False,
    fallback_user_message: str | None = None,
    unavailable_if_unknown: bool = False,
) -> None:
    """Classify, log at WARNING, and raise. Never returns."""
    mapped = classify_provider_error(
        message=message,
        body=body,
        prediction_error=prediction_error,
        status_code=status_code,
        exc=exc,
    )
    log_mapped_error(mapped, provider=provider)
    err = to_provider_error(
        mapped,
        technical=technical,
        status_code=status_code,
        has_prediction=has_prediction,
        fallback_user_message=fallback_user_message,
        unavailable_if_unknown=unavailable_if_unknown,
    )
    if exc is not None:
        raise err from exc
    raise err
