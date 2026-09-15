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
    NANO_BANANA_VARIANTS,
    resolve_grok_config,
    resolve_nano_banana_config,
)
from grokbot.domain.user_config import (
    COMFYUI_FLOW_LABELS,
    UserConfig,
    video_provider_for_config,
)

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

# Labels de ComfyUI viven en ``domain.user_config.COMFYUI_FLOW_LABELS``
# (id de flujo → nombre). Cada flujo = un workflow API-format con su modelo/LoRA
# horneados; no hay modelo/LoRA separados ni LoRAs que mostrar (lora/refine
# quedaron dormidos tras el slice HTTP/WS).

# Bandeja de warning para enlaces temporales (bot.py:264-266). Se muestra junto
# a la URL de recuperación de un video no enviable por Telegram (R10, privado).
SENSITIVE_DOWNLOAD_WARNING = (
    "\n\n⚠️ Enlace temporal con tu contenido generado; no lo compartas públicamente."
)

def escape(text: str) -> str:
    """Escape HTML (``html.escape``; espejo ``_escape_prompt`` de grok)."""
    return html.escape(text)


def prov_label(prov: str) -> str:
    """Label corto de proveedor (bot.py:247-248)."""
    return {"xai": "xAI", "replicate": "Replicate", "kie": "Kie.ai"}.get(prov, prov)


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
    """Caption de modelo + tiempo + prompt (bot.py:380-400).

    ComfyUI muestra el **flujo** (``comfyui_model`` = id de flujo → nombre de
    ``domain.COMFYUI_FLOW_LABELS``); sin fila de LoRA (el LoRA vive en el
    workflow, no es configurable).
    """
    cm = model.get("comfyui_model")
    if cm:
        mlabel = COMFYUI_FLOW_LABELS.get(cm, cm) or "?"
        body = (
            f"<b>Modelo:</b> {escape(mlabel)}\n"
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
    if cfg.model == "nano_banana":
        res = resolve_nano_banana_config(cfg.nano_banana_provider, cfg.nano_banana_variant)
        m = dict(base)
        m["provider"] = res["provider"]
        m["id"] = res["id"]
        m["name"] = (
            f"Nano Banana ({prov_label(res['provider'])} • "
            f"{NANO_BANANA_VARIANTS[res['variant']]['label']})"
        )
        m["desc"] = (
            f"Google Nano Banana — {prov_label(res['provider'])} • "
            f"{NANO_BANANA_VARIANTS[res['variant']]['label']}: "
            f"{NANO_BANANA_VARIANTS[res['variant']]['desc']}"
        )
        m["nano_banana_provider"] = res["provider"]
        m["nano_banana_variant"] = res["variant"]
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
        flow = cfg.comfyui.model
        flow_name = COMFYUI_FLOW_LABELS.get(flow, flow)
        m["comfyui_model"] = flow
        m["name"] = flow_name
        m["desc"] = f"Imagen en tu GPU (ComfyUI) — flujo {flow_name}."
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


# --- Face swap progress (parity grok bot.py:3274-3298 / 3326-3354) ------------
FACESWAP_PROGRESS_WIDTH = 10


def faceswap_progress_bar(completed: int, total: int, *, width: int = FACESWAP_PROGRESS_WIDTH) -> str:
    if total <= 0:
        return f"[{'?' * width}] 0/0 (0%)"
    completed = max(0, min(completed, total))
    filled = round(width * completed / total)
    empty = width - filled
    pct = completed * 100 // total
    return f"[{'█' * filled}{'░' * empty}] {completed}/{total} ({pct}%)"


def faceswap_progress_message(completed: int, total: int, *, current: int | None = None) -> str:
    bar = faceswap_progress_bar(completed, total)
    if current is not None and 1 <= current <= total:
        return f"Face swap\n{bar}\nImagen {current}/{total} en Replicate..."
    return f"Face swap\n{bar}"


def format_faceswap_batch_status(processed: int, total: int, failures: list[str], *, cancelled: bool = False) -> str:
    bar = faceswap_progress_bar(processed, total)
    if cancelled:
        summary = f"⏹ Cancelado. Completadas {processed}/{total} imagenes."
        if failures:
            shown = failures[:3]
            detail = "; ".join(shown)
            if len(failures) > 3:
                detail += f"; y {len(failures) - 3} mas"
            return f"{bar}\n{summary}\nFallos: {detail}"
        return f"{bar}\n{summary}"
    if failures:
        summary = f"Completadas {processed}/{total} imagenes."
        if processed == 0:
            summary = f"No se pudo procesar ninguna de las {total} imagenes."
        shown = failures[:3]
        detail = "; ".join(shown)
        if len(failures) > 3:
            detail += f"; y {len(failures) - 3} mas"
        return f"{bar}\n{summary}\nFallos: {detail}"
    if total == 1:
        return f"{bar}\nProcesada 1 imagen."
    return f"{bar}\nProcesadas {processed}/{total} imagenes."


# --- /estado (grok cmd_estado bot.py:1467-1513) ------------------------------
def kie_map_duration(duration: int) -> int:
    """Clamp de duración efectiva de video en Kie.ai (bot.py 1455-1457)."""
    return max(6, min(duration, 30))


def video_duration_display(configured: int, provider: str | None) -> str:
    """Duración configurada → display (Kie.ai muestra el clamp efectivo)."""
    if provider == "kie":
        effective = kie_map_duration(configured)
        if effective != configured:
            return f"{configured}s → {effective}s (Kie.ai)"
        return f"{configured}s"
    return f"{configured}s"


_ESTADO_PROV_LABELS = {"xai": "xAI (oficial)", "replicate": "Replicate", "kie": "Kie.ai"}


def estado_card(cfg: UserConfig, *, integrate_ref: bool = False) -> str:
    """Tarjeta de configuración de /estado (transcripción de grok, sin jobs).

    A5: la rama faceswap muestra Source/Estado desde ``cfg.source_path``/
    ``cfg.state`` (``IDLE`` o ``AWAITING_SOURCE`` mientras se configura la cara
    fuente con /cambiar_source). A6: texto plano, se envía con ``parse_mode=None``.
    """
    model = model_display(cfg)
    lines = ["Estado\n", f"Modelo: {model['name']}\n"]
    if cfg.model == "faceswap":
        has_source = bool(cfg.source_path)
        lines.append(f"Source: {'Configurado' if has_source else 'No configurado'}\n")
        lines.append(f"Estado: {cfg.state}")
    elif model.get("key") == "grok":
        prov = model.get("imagine_provider") or model.get("provider", "?")
        var = model.get("imagine_variant", "?")
        var_label = GROK_IMAGINE_VARIANTS.get(var, {}).get("label", var)
        lines.append(f"API / Backend: {_ESTADO_PROV_LABELS.get(prov, prov)} • {var_label}\n")
        lines.append(f"Referencia integrate (/s): {'Configurada' if integrate_ref else 'No configurada'}\n")
        lines.append("Listo para generar/editar imagenes.")
    elif model.get("key") == "grok_video":
        prov = video_provider_for_config(cfg)
        lines.append(f"API / Backend: {_ESTADO_PROV_LABELS.get(prov, prov)}\n")
        if model.get("imagine_provider") == "replicate":
            lines.append("(Imágenes: Replicate; video vía xAI)\n")
        model_label = VIDEO_MODEL_LABELS.get(cfg.video.model, cfg.video.model)
        dur = video_duration_display(cfg.video.duration, prov)
        lines.append(f"Video: {model_label}, {dur}, {cfg.video.aspect_ratio}, {cfg.video.resolution}\n")
        lines.append("Listo para generar videos (texto o imagen a video).")
        lines.append("Usa /config (o /video) para configurar modelo, duración, aspecto y resolución.")
    else:
        lines.append("Listo para generar/editar imagenes.")
    return "\n".join(lines)
