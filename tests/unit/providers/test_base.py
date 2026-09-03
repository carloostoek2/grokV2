"""Unit tests for grokbot.providers.base (contract, errors, helpers)."""

from __future__ import annotations

import pytest

from grokbot.providers.base import (
    DEFAULT_IMAGE_ASPECT_RATIO,
    I2V_MAX_IMAGE_BYTES,
    IMAGE_MAX_POLL_SEC,
    POLL_MAX_RETRIES,
    POLL_RETRY_BACKOFF_SEC,
    VIDEO_MAX_POLL_SEC,
    VIDEO_POLL_INTERVAL_SEC,
    ProviderAuthenticationError,
    ProviderContentError,
    ProviderError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    bytes_to_data_uri,
    detect_image_mime,
    validate_image_for_i2v,
)

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP_HEADER = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8


def test_default_image_aspect_ratio():
    assert DEFAULT_IMAGE_ASPECT_RATIO == "9:16"


def test_poll_and_size_constants():
    assert POLL_MAX_RETRIES == 3
    assert POLL_RETRY_BACKOFF_SEC == (2, 4, 8)
    assert VIDEO_POLL_INTERVAL_SEC == 5
    assert IMAGE_MAX_POLL_SEC == 120
    assert VIDEO_MAX_POLL_SEC == 600
    assert I2V_MAX_IMAGE_BYTES == 5 * 1024 * 1024


def test_detect_image_mime_jpeg_png_webp_and_fallback():
    assert detect_image_mime(JPEG_HEADER) == ("image/jpeg", "jpg")
    assert detect_image_mime(PNG_HEADER) == ("image/png", "png")
    assert detect_image_mime(WEBP_HEADER) == ("image/webp", "webp")
    assert detect_image_mime(b"\x00\x01\x02unknown") == ("image/jpeg", "jpg")


def test_bytes_to_data_uri_with_and_without_mime():
    png = PNG_HEADER
    uri = bytes_to_data_uri(png)
    assert uri.startswith("data:image/png;base64,")
    import base64

    assert base64.b64decode(uri.split(",", 1)[1]) == png
    explicit = bytes_to_data_uri(JPEG_HEADER, mime="image/jpeg")
    assert explicit.startswith("data:image/jpeg;base64,")


def test_validate_image_for_i2v_size_limit():
    assert validate_image_for_i2v(b"\x00" * 1024) is None
    too_big = b"\x00" * (I2V_MAX_IMAGE_BYTES + 1)
    msg = validate_image_for_i2v(too_big)
    assert msg is not None
    assert "demasiado grande" in msg
    assert "5 MB" in msg
    # Exactly at the cap is allowed (grok uses strict >).
    assert validate_image_for_i2v(b"\x00" * I2V_MAX_IMAGE_BYTES) is None


# --- Error hierarchy (D3) ---
def test_provider_error_is_exception():
    err = ProviderError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"


def test_provider_error_user_message_defaults_to_message():
    err = ProviderError("technical detail")
    assert err.user_message == "technical detail"
    err2 = ProviderError("tech", user_message="User friendly")
    assert err2.user_message == "User friendly"
    assert str(err2) == "tech"


RETRYABLE_MATRIX = [
    (ProviderError, True),
    (ProviderNotConfiguredError, False),
    (ProviderInputError, False),
    (ProviderContentError, False),
    (ProviderAuthenticationError, False),
    (ProviderGenerationError, False),
    (ProviderRateLimitError, True),
    (ProviderUnavailableError, True),
    (ProviderTimeoutError, False),
]


@pytest.mark.parametrize("exc_cls,expected", RETRYABLE_MATRIX)
def test_error_retryable_flags(exc_cls, expected):
    assert exc_cls().retryable is expected
    assert issubclass(exc_cls, ProviderError)


def test_subclass_can_raise_without_message():
    with pytest.raises(ProviderNotConfiguredError):
        raise ProviderNotConfiguredError("no key")
