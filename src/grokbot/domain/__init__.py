"""Pure domain package — stdlib only (dataclasses, enums, re), no I/O.

Re-exports the public domain API consumed by items 2-6.
"""

from __future__ import annotations

from grokbot.domain.catalog import (
    DEFAULT_GROK_IMAGINE_PROVIDER,
    DEFAULT_GROK_IMAGINE_VARIANT,
    DEFAULT_MODEL,
    GROK_IMAGINE_VARIANTS,
    MODELS,
    model_spec,
    resolve_grok_config,
    resolve_model_id,
)
from grokbot.domain.generation import (
    GenerationRequest,
    GenerationResult,
    ImageSource,
    KieTaskRef,
    LocalPathRef,
    MediaType,
    TelegramFileRef,
    UrlRef,
)
from grokbot.domain.job import (
    MAX_ACTIVE_JOBS_PER_USER,
    Job,
    JobStatus,
)

__all__ = [
    "MODELS",
    "GROK_IMAGINE_VARIANTS",
    "DEFAULT_MODEL",
    "DEFAULT_GROK_IMAGINE_PROVIDER",
    "DEFAULT_GROK_IMAGINE_VARIANT",
    "model_spec",
    "resolve_grok_config",
    "resolve_model_id",
    "MediaType",
    "GenerationRequest",
    "GenerationResult",
    "ImageSource",
    "TelegramFileRef",
    "LocalPathRef",
    "UrlRef",
    "KieTaskRef",
    "Job",
    "JobStatus",
    "MAX_ACTIVE_JOBS_PER_USER",
]
