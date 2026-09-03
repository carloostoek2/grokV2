"""Generation providers (xAI, Replicate, Kie.ai, ComfyUI) behind a common contract.

Concrete providers are added by their own modules and re-exported here as they
land (base contract first, then each provider, registry last).
"""

from grokbot.providers.base import (
    DEFAULT_IMAGE_ASPECT_RATIO,
    I2V_MAX_IMAGE_BYTES,
    IMAGE_MAX_POLL_SEC,
    POLL_MAX_RETRIES,
    POLL_RETRY_BACKOFF_SEC,
    VIDEO_MAX_POLL_SEC,
    VIDEO_POLL_INTERVAL_SEC,
    ImageProvider,
    ProviderAuthenticationError,
    ProviderContentError,
    ProviderError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    VideoProvider,
    bytes_to_data_uri,
    detect_image_mime,
    validate_image_for_i2v,
)

__all__ = [
    "DEFAULT_IMAGE_ASPECT_RATIO",
    "I2V_MAX_IMAGE_BYTES",
    "IMAGE_MAX_POLL_SEC",
    "POLL_MAX_RETRIES",
    "POLL_RETRY_BACKOFF_SEC",
    "VIDEO_MAX_POLL_SEC",
    "VIDEO_POLL_INTERVAL_SEC",
    "ImageProvider",
    "VideoProvider",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderInputError",
    "ProviderContentError",
    "ProviderAuthenticationError",
    "ProviderGenerationError",
    "ProviderRateLimitError",
    "ProviderUnavailableError",
    "ProviderTimeoutError",
    "bytes_to_data_uri",
    "detect_image_mime",
    "validate_image_for_i2v",
]
