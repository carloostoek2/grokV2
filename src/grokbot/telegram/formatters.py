"""Copy puro de UI de la capa telegram (item 5).

Funciones/labels sin negocio: formateo de captions, mensajes de status,
resúmenes y pantallas de config/listas. Los strings de alto valor se
transcriben byte a byte de grok bot.py / config_flow.py / variables_flow.py
(los rangos citados en PLAN.md) — NO se "corrigen" tildes ni typos originales
("Enviame", "Tambien", ...).

Nunca formatean prompts/payloads pagos fuera de la pantalla correspondiente
(R6/R8); ``escape`` se usa antes de interpolar prompts en HTML.
"""

from __future__ import annotations

import html

from grokbot.domain.catalog import (
    DEFAULT_MODEL,
    GROK_IMAGINE_VARIANTS,
    MODELS,
    resolve_grok_config,
)
from grokbot.domain.user_config import UserConfig, video_provider_for_config

# Telegram text limits (bot.py:73-75).
TELEGRAM_MAX_CAPTION_LEN = 1024
TELEGRAM_MAX_TEXT_LEN = 4096

# Listas editables de /variables (variables_flow.py:49-53).
LIST_LABELS = {
    "poses": "Poses",
    "angles": "Ángulos",
    "actions": "Acciones",
}

# Labels de video (bot.py:124-132).
VIDEO_MODEL_LABELS = {
    "grok-imagine-video": "Base",
    "grok-imagine-video-1.5": "1.5 (reciente)",
}
VIDEO_MODE_LABELS = {
    "fun": "Fun",
    "normal": "Normal",
    "spicy": "Spicy",
}

# Captions de modelos ComfyUI (bot.py:315-323).
COMFYUI_CAPTION_MODEL_LABELS = {
    "qwen": "Qwen-Image-Edit 2511",
    "qwen_aio": "Qwen AIO v23 (Rápido)",
    "krea2": "Krea 2",
    "krea2_raw": "Krea 2 RAW",
    "krea2_moody": "Moody (Krea 2 Mix)",
    "wan_i2v": "Wan 2.2",
    "minimax_i2v": "MiniMax H3",
}
# Captions de LoRAs ComfyUI (bot.py:324-342).
COMFYUI_CAPTION_LORA_LABELS = {
    "none": "Sin LoRA",
    "lightning": "Lightning 4 pasos",
    "multiangle": "Multi-ángulo (auto)",
    "multiangle_batch": "Multi-ángulo ×5 (auto)",
    "multipose_batch": "Multi-pose ×5 (variables)",
    "krea_nsfw": "NSFW V4",
    "krea_snapshot": "Realistic Snapshot",
    "krea_both": "NSFW V4 + Realistic Snapshot",
    "krea_reddit": "Reddit (textura + phone)",
    "krea_snofs": "SNOFS v1.3D",
    "qwen_snofs": "SNOFS v1.3",
    "krea_edit": "✏️ Editar (Identity)",
    "krea_edit_nsfw": "✏️ Editar + NSFW",
    "krea_edit_snapshot": "✏️ Editar + Snapshot",
    "krea_edit_both": "✏️ Editar + NSFW + Snapshot",
    "lightx2v": "lightx2v (rápido)",
    "dr34ml4y": "DR34ML4Y (All-In-One NSFW)",
}

# Labels del panel /config ComfyUI (config_flow.py:125-152).
COMFYUI_CONFIG_MODEL_LABELS = {
    "qwen": "Qwen-Image-Edit 2511",
    "qwen_aio": "Qwen AIO v23 (Rápido)",
    "krea2": "Krea 2 (Turbo)",
    "krea2_raw": "Krea 2 (RAW)",
    "krea2_moody": "Moody (Krea 2 Mix)",
    "wan_i2v": "Wan 2.2 (video)",
    "minimax_i2v": "MiniMax H3 (video)",
}
COMFYUI_CONFIG_LORA_LABELS = {
    "none": "Sin LoRA",
    "lightning": "Lightning 4 pasos",
    "krea_nsfw": "Krea2 NSFW V4",
    "krea_snapshot": "Realistic Snapshot",
    "krea_both": "NSFW V4 + Realistic Snapshot",
    "krea_reddit": "Reddit (textura + phone)",
    "krea_snofs": "SNOFS v1.3D",
    "qwen_snofs": "SNOFS v1.3",
    "krea_edit": "✏️ Editar (Identity Edit)",
    "krea_edit_nsfw": "✏️ Editar + NSFW V4",
    "krea_edit_snapshot": "✏️ Editar + Snapshot",
    "krea_edit_both": "✏️ Editar + NSFW + Snapshot",
    "lightx2v": "Rápido (lightx2v)",
    "dr34ml4y": "DR34ML4Y (All-In-One NSFW)",
    "multiangle": "🎲 Multi-ángulo (auto)",
    "multiangle_batch": "🎲 Multi-ángulo ×5 (auto)",
    "multipose_batch": "🎲 Multi-pose ×5 (variables)",
}

# Bandeja de warning para enlaces temporales (bot.py:264-266).
SENSITIVE_DOWNLOAD_WARNING = (
    "\n\n⚠️ Enlace temporal con tu contenido generado; no lo compartas públicamente."
)

# Mensaje de jobs llenos (bot.py:439-441).
JOBS_FULL_MSG = "Ya hay 3 procesos en curso. Espera a que termine uno o cancélalo."


def escape(text: str) -> str:
    """Escape HTML (``html.escape``; espejo ``_escape_prompt`` de grok)."""
    return html.escape(text)


def prov_label(prov: str) -> str:
    """Label corto de proveedor (bot.py:247-248)."""
    return {"xai": "xAI", "replicate": "Replicate", "kie": "Kie.ai"}.get(prov, prov)


def comfyui_config_lora_label(model: str, key: str) -> str:
    """Label de LoRA en el panel /config, con overrides de wan_i2v (config_flow 173-181)."""
    if model == "wan_i2v":
        if key == "none":
            return "Full (calidad, 40 pasos)"
        if key == "lightx2v":
            return "Rápido (lightx2v, 4×)"
        if key == "dr34ml4y":
            return "DR34ML4Y (posiciones NSFW, 24 pasos)"
    return COMFYUI_CONFIG_LORA_LABELS.get(key, key)


def caption_lora_label(key: str) -> str:
    """Label de LoRA en captions de resultados (bot.py:324-342)."""
    return COMFYUI_CAPTION_LORA_LABELS.get(key, key)


def format_elapsed(sec: int | float | None) -> str:
    """Tiempo de ejecución legible: '45s', '3m 05s'. '…' cuando no se midió."""
    if sec is None:
        return "…"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    return f"{m}m {s:02d}s"


def append_prompt_to_caption(caption: str, prompt: str) -> str:
    """Append el prompt al caption con truncado por binary search (bot.py:356-377).

    El truncado aplica sobre el prompt ESCAPADO para no partir entidades HTML y
    garantiza que el caption combinado quede bajo el límite de Telegram.
    """
    if not prompt:
        return caption
    label = "\n<b>Prompt:</b> "
    escaped = escape(prompt)
    if len(caption) + len(label) + len(escaped) <= TELEGRAM_MAX_CAPTION_LEN:
        return f"{caption}{label}{escaped}"
    budget = TELEGRAM_MAX_CAPTION_LEN - len(caption) - len(label) - 1
    if budget <= 0:
        return caption
    lo, hi = 0, len(prompt)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if len(escape(prompt[:mid])) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return f"{caption}{label}{escape(prompt[:best])}…"


def format_model_caption(
    model: dict, elapsed_sec: int | None, prompt: str | None = None
) -> str:
    """Caption con Modelo / LoRA (ComfyUI) / tiempo + prompt (bot.py:380-400)."""
    cm = model.get("comfyui_model")
    if cm:
        mlabel = COMFYUI_CAPTION_MODEL_LABELS.get(cm, cm) or "?"
        cl = model.get("comfyui_lora")
        llabel = COMFYUI_CAPTION_LORA_LABELS.get(cl, cl) or "Sin LoRA"
        body = (
            f"<b>Modelo:</b> {escape(mlabel)}\n"
            f"<b>LoRA:</b> {escape(llabel)}\n"
            f"<b>Tiempo:</b> {format_elapsed(elapsed_sec)}"
        )
    else:
        mlabel = model.get("name", "?")
        body = (
            f"<b>Modelo:</b> {escape(mlabel)}\n"
            f"<b>Tiempo:</b> {format_elapsed(elapsed_sec)}"
        )
    return append_prompt_to_caption(body, prompt)


def format_result_caption(
    prefix: str,
    elapsed_sec: int | None,
    variant: str | None = None,
    model: dict | None = None,
    prompt: str | None = None,
) -> str:
    """Caption de un resultado generado (bot.py:403-413)."""
    if model is not None:
        return format_model_caption(model, elapsed_sec, prompt)
    header = f"<b>{prefix} ({variant}):</b> " if variant else f"<b>{prefix}:</b> "
    return append_prompt_to_caption(f"{header}{format_elapsed(elapsed_sec)}", prompt)


def model_display(cfg: UserConfig) -> dict:
    """Dict de modelo para MOSTRAR (espejo ``get_model`` grok bot.py:877-922).

    Solo lectura; nunca se usa para resolver providers en wire. Devuelve el
    ``name`` correcto según la config granular (Grok Imagine), video efectivo y
    ComfyUI model/lora.
    """
    base = dict(MODELS.get(cfg.model, MODELS[DEFAULT_MODEL]))
    if cfg.model == "grok":
        res = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
        m = dict(base)
        m["provider"] = res["provider"]
        m["id"] = res["id"]
        m["name"] = (
            f"Grok Imagine ({prov_label(res['provider'])} • "
            f"{GROK_IMAGINE_VARIANTS[res['variant']]['label']})"
        )
        m["desc"] = (
            f"xAI Grok Imagine — {prov_label(res['provider'])} • "
            f"{GROK_IMAGINE_VARIANTS[res['variant']]['label']}: "
            f"{GROK_IMAGINE_VARIANTS[res['variant']]['desc']}"
        )
        m["imagine_provider"] = res["provider"]
        m["imagine_variant"] = res["variant"]
        return m
    if cfg.model == "grok_video":
        m = dict(base)
        res = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
        video_prov = video_provider_for_config(cfg)
        prov = video_prov if video_prov is not None else "xai"
        prov_name = prov_label(prov)
        if res["provider"] == "replicate":
            m["name"] = f"Grok Imagine Video ({prov_name}; imágenes: Replicate)"
            m["desc"] = "Generación de video con xAI; imágenes vía Replicate"
        else:
            m["name"] = f"Grok Imagine Video ({prov_name})"
            m["desc"] = f"Generación de video con Grok Imagine — {prov_name}"
        m["provider"] = prov
        m["imagine_provider"] = res["provider"]
        m["imagine_variant"] = res["variant"]
        return m
    if cfg.model == "comfyui":
        m = dict(base)
        m["comfyui_model"] = cfg.comfyui.model
        m["comfyui_lora"] = cfg.comfyui.lora
        m["comfyui_refine"] = cfg.comfyui.refine
        m["name"] = f"ComfyUI ({cfg.comfyui.model} • lora {cfg.comfyui.lora})"
        m["desc"] = f"ComfyUI en la GPU — modelo {cfg.comfyui.model}, LoRA {cfg.comfyui.lora}"
        return m
    return base


def validate_prompt(prompt: str, *, max_len: int = TELEGRAM_MAX_TEXT_LEN) -> str | None:
    """Valida el prompt de texto (bot.py:307-312); None si válido."""
    if len(prompt) < 3:
        return "El prompt es muy corto. Dame algo mas descriptivo."
    if len(prompt) > max_len:
        return f"El prompt es demasiado largo (máximo {max_len} caracteres)."
    return None


def video_start_message(model_id: str, prompt: str) -> str:
    """Mensaje inicial de generación de video (bot.py:227-232, HTML)."""
    return (
        f"Generando video con <b>{escape(model_id)}</b>...\n\n"
        f"<i>{escape(prompt)}</i>"
    )


def video_status_message(model_id: str, detail: str, prompt: str) -> str:
    """Línea de status de video con detalle (bot.py:219-224, HTML)."""
    return (
        f"Generando video con <b>{escape(model_id)}</b>... {detail}\n\n"
        f"<i>{escape(prompt)}</i>"
    )


def retry_status_text(label: str, attempt: int, max_attempts: int) -> str:
    """Status de reintento: ``label (intento N/M)`` (espejo grok 4858)."""
    return f"{label} (intento {attempt}/{max_attempts})"


def format_variables_batch_summary(completed: int, failed: int, count: int) -> str:
    """Resumen terminal del batch de variables (grok bot.py:2111-2116)."""
    if failed == 0:
        return f"✅ Listo: {completed}/{count} imágenes generadas."
    err_label = "error" if failed == 1 else "errores"
    icon = "✅" if completed else "⚠️"
    return f"{icon} Listo: {completed}/{count} imágenes generadas ({failed} {err_label})."


def format_failed_item_message(
    label: str, index: int, count: int, prompt: str
) -> str:
    """Notificación de ítem fallido con el prompt intentado (grok bot.py:2128-2133)."""
    return (
        f"{label} {index}/{count} falló con el siguiente prompt:\n\n"
        f"{escape(prompt)}"
    )


def format_multipose_summary(combos: tuple[str, ...]) -> str:
    """Resumen de poses usadas en multipose (grok bot.py:2242-2244)."""
    lines = ["<b>🎲 Multi-pose ×5</b> — poses usadas:"]
    lines += [f"  {i + 1}. {escape(label)}" for i, label in enumerate(combos)]
    return "\n".join(lines)
