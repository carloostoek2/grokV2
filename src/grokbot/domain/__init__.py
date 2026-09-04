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
    Job,
    JobStatus,
)
from grokbot.domain.user_config import (
    KIE_15_VIDEO_ASPECT_RATIOS,
    KIE_BASE_VIDEO_ASPECT_RATIOS,
    COMFY_VIDEO_MODELS,
    ComfyUIConfig,
    UserConfig,
    VideoConfig,
    is_comfy_video_model,
    kie_aspect_ratio_fallback,
    kie_video_aspect_ratios,
    video_provider_for_config,
)
from grokbot.domain.variables import (
    MAX_COMBO_ATTEMPTS,
    MULTIPOSE_BATCH_SIZE,
    VARIABLES_MAX,
    PromptTemplate,
    build_shuffled_prompt,
    combo_key,
    combo_label,
    normalize_items,
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
    "UserConfig",
    "VideoConfig",
    "ComfyUIConfig",
    "COMFY_VIDEO_MODELS",
    "is_comfy_video_model",
    "video_provider_for_config",
    "KIE_BASE_VIDEO_ASPECT_RATIOS",
    "KIE_15_VIDEO_ASPECT_RATIOS",
    "kie_video_aspect_ratios",
    "kie_aspect_ratio_fallback",
    "VARIABLES_MAX",
    "MAX_COMBO_ATTEMPTS",
    "MULTIPOSE_BATCH_SIZE",
    "PromptTemplate",
    "build_shuffled_prompt",
    "normalize_items",
    "combo_key",
    "combo_label",
]
