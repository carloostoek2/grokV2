"""Unit tests for shared provider error_mapping."""

from __future__ import annotations

import logging

import pytest

from grokbot.providers.base import (
    ProviderAuthenticationError,
    ProviderContentError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from grokbot.providers.error_mapping import (
    USER_MSG_BILLING,
    USER_MSG_GENERIC,
    USER_MSG_MODERATION,
    USER_MSG_RATE_LIMIT,
    USER_MSG_TIMEOUT,
    ErrorKind,
    classify_provider_error,
    log_mapped_error,
    to_provider_error,
)
from grokbot.providers.replicate_provider import _wrap_run_error


def test_classify_replicate_sensitive_e005():
    msg = (
        "ModelError: The input or output was flagged as sensitive "
        "content by a content safety classifier (E005). "
        "Flagged categories: sexual"
    )
    mapped = classify_provider_error(prediction_error=msg)
    assert mapped.kind is ErrorKind.MODERATION
    assert mapped.user_message == USER_MSG_MODERATION
    assert "try later" not in mapped.user_message.lower()
    assert "más tarde" not in mapped.user_message
    assert "E005" in mapped.detail or "e005" in mapped.detail.lower() or "sensitive" in mapped.detail.lower()


def test_classify_xai_credits_403():
    mapped = classify_provider_error(
        status_code=403,
        body='{"error":{"message":"Insufficient credits. Spending limit reached."}}',
    )
    assert mapped.kind is ErrorKind.BILLING
    assert mapped.user_message == USER_MSG_BILLING
    err = to_provider_error(mapped, status_code=403, technical="xAI HTTP 403")
    assert isinstance(err, ProviderAuthenticationError)
    assert err.retryable is False
    assert err.user_message == USER_MSG_BILLING


def test_classify_429_rate_limit():
    mapped = classify_provider_error(status_code=429, message="rate limited")
    assert mapped.kind is ErrorKind.RATE_LIMIT
    assert mapped.user_message == USER_MSG_RATE_LIMIT
    err = to_provider_error(mapped, status_code=429)
    assert isinstance(err, ProviderRateLimitError)
    assert err.retryable is True


def test_classify_timeout():
    mapped = classify_provider_error(message="Polling timed out after 600s")
    assert mapped.kind is ErrorKind.TIMEOUT
    assert mapped.user_message == USER_MSG_TIMEOUT
    err = to_provider_error(mapped)
    assert isinstance(err, ProviderTimeoutError)
    assert err.retryable is False


def test_classify_unknown_falls_back_generic():
    mapped = classify_provider_error(message="something weird happened upstream")
    assert mapped.kind is ErrorKind.UNKNOWN
    assert mapped.user_message == USER_MSG_GENERIC
    err = to_provider_error(
        mapped,
        fallback_user_message="Error en la generación de video. Intenta de nuevo más tarde.",
    )
    assert isinstance(err, ProviderGenerationError)
    assert err.user_message == "Error en la generación de video. Intenta de nuevo más tarde."


def test_classify_not_found_404():
    mapped = classify_provider_error(status_code=404, body="model not found")
    assert mapped.kind is ErrorKind.NOT_FOUND
    err = to_provider_error(mapped, status_code=404)
    assert isinstance(err, ProviderInputError)


def test_wrap_run_error_moderation_from_prediction():
    class _Pred:
        error = (
            "The input or output was flagged as sensitive "
            "content by a content safety classifier (E005). "
            "Flagged categories: sexual"
        )

    class _ModelError(Exception):
        def __init__(self):
            super().__init__(str(_Pred.error))
            self.prediction = _Pred()

    err = _wrap_run_error(
        _ModelError(),
        fallback_user_message="Error en la generación de video. Intenta de nuevo más tarde.",
    )
    assert isinstance(err, ProviderContentError)
    assert err.user_message == USER_MSG_MODERATION
    assert err.retryable is False


def test_wrap_run_error_prediction_vs_network():
    class _Pred(Exception):
        prediction = object()

    class _Limited(Exception):
        status = 429

    user = "safe fallback"
    gen = _wrap_run_error(_Pred("bad"), user_message=user)
    assert isinstance(gen, ProviderGenerationError)
    assert gen.retryable is False
    assert gen.user_message == user  # UNKNOWN → fallback
    limited = _wrap_run_error(_Limited("slow"), user_message=user)
    assert isinstance(limited, ProviderRateLimitError)
    assert limited.user_message == USER_MSG_RATE_LIMIT  # known kind wins
    net = _wrap_run_error(RuntimeError("boom"), user_message=user)
    assert isinstance(net, ProviderUnavailableError)
    assert net.retryable is True
    assert net.user_message == user


def test_log_mapped_error_warning(caplog):
    mapped = classify_provider_error(
        prediction_error="flagged as sensitive (E005)",
    )
    with caplog.at_level(logging.WARNING, logger="grokbot.providers.error_mapping"):
        log_mapped_error(mapped, provider="replicate")
    assert any("kind=moderation" in r.message for r in caplog.records)
    assert any("provider=replicate" in r.message for r in caplog.records)
    # Must not look like a prompt dump
    for r in caplog.records:
        assert "sk-" not in r.message
