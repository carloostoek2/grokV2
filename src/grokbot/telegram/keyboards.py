"""Builders puros de teclados inline (item 5).

Devuelven :class:`InlineKeyboardMarkup` con los callback_data de grok
(bot.py 429-452/685-713, config_flow.py 66-219/241-303, variables_flow.py
190-251/680-727). No tienen estado ni I/O: reciben la config actual como
argumento y producen el markup. Los handlers eligen qué builder llamar según la
pantalla; los tests fijan los callback_data exactos.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from grokbot.domain.catalog import GROK_IMAGINE_VARIANTS, MODELS, resolve_grok_config
from grokbot.domain.user_config import (
    VALID_VIDEO_ASPECT_RATIOS,
    VALID_VIDEO_DURATIONS,
    VALID_VIDEO_MODES,
    VALID_VIDEO_RESOLUTIONS,
    ComfyUIConfig,
    UserConfig,
    VideoConfig,
    kie_video_aspect_ratios,
)
from grokbot.telegram.formatters import (
    COMFYUI_CONFIG_MODEL_LABELS,
    VIDEO_MODEL_LABELS,
    VIDEO_MODE_LABELS,
    comfyui_config_lora_label,
    prov_label,
)

# ---------------------------------------------------------------------------
# Generación (confirm / cancel / regen / refine) — bot.py 429-452 / 685-713
# ---------------------------------------------------------------------------

def confirmation_keyboard() -> InlineKeyboardMarkup:
    """Keyboard de confirmación de prompt (confirm:yes/no)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Confirmar", callback_data="confirm:yes"),
         InlineKeyboardButton(text="Cancelar", callback_data="confirm:no")],
    ])


def cancel_job_keyboard(job_id: str | None = None) -> InlineKeyboardMarkup:
    """Keyboard de cancelación con ``cancel_job:<job_id>`` (o ``cancel_job``)."""
    data = f"cancel_job:{job_id}" if job_id else "cancel_job"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Cancelar", callback_data=data)],
        ]
    )


def image_regenerate_keyboard(*, show_cancel: bool = False) -> InlineKeyboardMarkup:
    """Keyboard bajo imágenes generadas (Regenerar [+ Cancelar])."""
    row = [InlineKeyboardButton(text="Regenerar", callback_data="regen")]
    if show_cancel:
        row.append(InlineKeyboardButton(text="Cancelar", callback_data="cancel_job"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def refine_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    """Keyboard de confirmación de refine (refine:<token>:yes|no)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✨ Refinar", callback_data=f"refine:{token}:yes"),
                InlineKeyboardButton(text="⏭ Continuar", callback_data=f"refine:{token}:no"),
            ],
        ]
    )


def refining_keyboard() -> InlineKeyboardMarkup:
    """Placeholder mientras refina (el choice no se puede re-tap)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Refinando…", callback_data="refine_noop")],
        ]
    )


# ---------------------------------------------------------------------------
# /config — config_flow.py 66-219 / 241-303
# ---------------------------------------------------------------------------

def config_model_keyboard(cfg: UserConfig) -> InlineKeyboardMarkup:
    """Pantalla de modelo (5 modelos) con marca de selección."""
    buttons = []
    for key, m in MODELS.items():
        if key == "grok":
            res = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
            suffix = f"{prov_label(res['provider'])} • {GROK_IMAGINE_VARIANTS[res['variant']]['label']}"
            label = f"{'✅ ' if cfg.model == key else ''}Grok Imagine ({suffix})"
        else:
            label = f"{'✅ ' if cfg.model == key else ''}{m['name']}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"cfg:model:{key}")])
    buttons.append([InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def config_provider_keyboard(cfg: UserConfig) -> InlineKeyboardMarkup:
    """Pantalla de proveedor de Grok Imagine (kie/xai/replicate)."""
    res = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
    current = res["provider"]
    buttons = []
    for prov in ("kie", "xai", "replicate"):
        prefix = "✅ " if prov == current else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix}{prov_label(prov)}",
                callback_data=f"cfg:provider:{prov}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="← Modelo", callback_data="cfg:back:model")])
    buttons.append([InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def config_variant_keyboard(cfg: UserConfig) -> InlineKeyboardMarkup:
    """Pantalla de nivel de calidad (standard/quality)."""
    res = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
    buttons = []
    for var, spec in GROK_IMAGINE_VARIANTS.items():
        prefix = "✅ " if var == res["variant"] else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix}{spec['label']}",
                callback_data=f"cfg:variant:{var}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="← Proveedor", callback_data="cfg:back:provider")])
    buttons.append([InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# LoRAs válidos por modelo ComfyUI (config_flow.py:153-170).
_COMFYUI_LORAS_BY_MODEL = {
    "qwen": ("none", "lightning", "qwen_snofs", "multiangle", "multiangle_batch", "multipose_batch"),
    "qwen_aio": ("none",),
    "krea2": (
        "none", "krea_nsfw", "krea_snapshot", "krea_both", "krea_reddit", "krea_snofs",
        "krea_edit", "krea_edit_nsfw", "krea_edit_snapshot", "krea_edit_both",
    ),
    "krea2_raw": (
        "none", "krea_nsfw", "krea_snapshot", "krea_both", "krea_reddit", "krea_snofs",
        "krea_edit", "krea_edit_nsfw", "krea_edit_snapshot", "krea_edit_both",
    ),
    "krea2_moody": (
        "none", "krea_nsfw", "krea_snapshot", "krea_both", "krea_reddit", "krea_snofs",
        "krea_edit", "krea_edit_nsfw", "krea_edit_snapshot", "krea_edit_both",
    ),
    "wan_i2v": ("none", "lightx2v", "dr34ml4y"),
    "minimax_i2v": ("none",),
}


def config_comfyui_keyboard(cfg: ComfyUIConfig) -> InlineKeyboardMarkup:
    """Pantalla ComfyUI: modelo + LoRAs válidas + toggle refine."""
    rows = []
    for k in ("qwen", "qwen_aio", "krea2", "krea2_raw", "krea2_moody", "wan_i2v", "minimax_i2v"):
        mark = "✅ " if k == cfg.model else "• "
        rows.append([
            InlineKeyboardButton(
                text=f"{mark}{COMFYUI_CONFIG_MODEL_LABELS.get(k, k)}",
                callback_data=f"cfg:comfyui:model:{k}",
            )
        ])
    for k in _COMFYUI_LORAS_BY_MODEL.get(cfg.model, ("none",)):
        mark = "✅ " if k == cfg.lora else "• "
        rows.append([
            InlineKeyboardButton(
                text=f"{mark}{comfyui_config_lora_label(cfg.model, k)}",
                callback_data=f"cfg:comfyui:lora:{k}",
            )
        ])
    refine_on = cfg.refine == "1"
    rows.append([
        InlineKeyboardButton(
            text=("✨ Refinar: ON" if refine_on else "✨ Refinar: OFF"),
            callback_data=f"cfg:comfyui:refine:{'0' if refine_on else '1'}",
        )
    ])
    rows.append([InlineKeyboardButton(text="← Modelo", callback_data="cfg:back:model")])
    rows.append([InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def config_video_keyboard(cfg: VideoConfig, *, is_kie: bool) -> InlineKeyboardMarkup:
    """Pantalla de video: modelo, duración, aspect, resolución (+ modo si Kie)."""
    def _btn(label: str, data: str, *, selected: bool) -> InlineKeyboardButton:
        prefix = "✅ " if selected else ""
        return InlineKeyboardButton(text=f"{prefix}{label}", callback_data=data)

    aspects = list(
        kie_video_aspect_ratios(cfg.model) if is_kie else VALID_VIDEO_ASPECT_RATIOS
    )
    aspect_rows = [aspects[i : i + 4] for i in range(0, len(aspects), 4)]

    buttons = [
        [
            _btn(VIDEO_MODEL_LABELS.get("grok-imagine-video", "Base"), "cfg:video:model:grok-imagine-video", selected=cfg.model == "grok-imagine-video"),
            _btn(VIDEO_MODEL_LABELS.get("grok-imagine-video-1.5", "1.5"), "cfg:video:model:grok-imagine-video-1.5", selected=cfg.model == "grok-imagine-video-1.5"),
        ],
        [_btn(f"{v}s", f"cfg:video:duration:{v}", selected=cfg.duration == v) for v in VALID_VIDEO_DURATIONS],
    ]
    for row in aspect_rows:
        buttons.append([_btn(v, f"cfg:video:aspect:{v}", selected=cfg.aspect_ratio == v) for v in row])
    buttons.append([
        _btn("480p", "cfg:video:resolution:480p", selected=cfg.resolution == "480p"),
        _btn("720p", "cfg:video:resolution:720p", selected=cfg.resolution == "720p"),
    ])
    if is_kie:
        buttons.append([
            _btn(VIDEO_MODE_LABELS.get(v, v), f"cfg:video:mode:{v}", selected=cfg.mode == v)
            for v in VALID_VIDEO_MODES
        ])
    buttons.append([InlineKeyboardButton(text="← Proveedor", callback_data="cfg:back:provider")])
    buttons.append([InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def simple_close_keyboard() -> InlineKeyboardMarkup:
    """Teclado de cierre para modelos simples (config_flow.py:380-386)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Modelo", callback_data="cfg:back:model")],
            [InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")],
        ]
    )


# ---------------------------------------------------------------------------
# /listas — variables_flow.py 190-251 / 680-727
# ---------------------------------------------------------------------------

def variables_menu_keyboard(lists: dict[str, list[str]], labels: dict[str, str]) -> InlineKeyboardMarkup:
    """Menú principal del panel: una fila por lista con su count + template/paquetes."""
    buttons = []
    for name, items in lists.items():
        label = labels.get(name, name)
        buttons.append([
            InlineKeyboardButton(
                text=f"{label} ({len(items)})",
                callback_data=f"var:open:{name}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="✏️ Plantilla", callback_data="var:tmpl"),
        InlineKeyboardButton(text="📦 Paquetes", callback_data="var:packs"),
    ])
    buttons.append([InlineKeyboardButton(text="❌ Cerrar", callback_data="var:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def variables_list_keyboard(name: str) -> InlineKeyboardMarkup:
    """Pantalla de una lista: añadir/editar/eliminar/menú/cerrar."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Añadir", callback_data=f"var:add:{name}"),
            InlineKeyboardButton(text="✏️ Editar", callback_data=f"var:edit:{name}"),
        ],
        [
            InlineKeyboardButton(text="🗑 Eliminar", callback_data=f"var:del:{name}"),
            InlineKeyboardButton(text="← Menú", callback_data="var:back"),
        ],
        [InlineKeyboardButton(text="❌ Cerrar", callback_data="var:close")],
    ])


def _truncate(text: str, limit: int) -> str:
    """Trunca a ``limit`` reemplazando saltos por espacios (variables_flow 159-163)."""
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def variables_picker_keyboard(name: str, items: list[str], action: str) -> InlineKeyboardMarkup:
    """Picker de ítems para editar/eliminar (hasta PICKER_MAX_ITEMS)."""
    rows = []
    for i, item in enumerate(items[:90]):
        rows.append([
            InlineKeyboardButton(
                text=f"{i + 1}. {_truncate(item, 40)}",
                callback_data=f"var:item:{action}:{name}:{i}",
            )
        ])
    rows.append([InlineKeyboardButton(text="← Volver", callback_data=f"var:open:{name}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def variables_cancel_keyboard() -> InlineKeyboardMarkup:
    """Cancelar una entrada de texto del panel (var:cancel)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Cancelar", callback_data="var:cancel")]
    ])


def packages_keyboard(packages: list[str]) -> InlineKeyboardMarkup:
    """Pantalla de paquetes: ver/crear + volver al menú."""
    buttons = []
    for slug in packages:
        buttons.append([
            InlineKeyboardButton(text=f"📦 {slug}", callback_data=f"var:pack:view:{slug}")
        ])
    buttons.append([InlineKeyboardButton(text="➕ Crear paquete", callback_data="var:pack:new")])
    buttons.append([InlineKeyboardButton(text="← Menú", callback_data="var:pack:back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def package_view_keyboard(slug: str, active_slug: str | None) -> InlineKeyboardMarkup:
    """Pantalla de un paquete: activar (si no activo), eliminar, volver."""
    rows = []
    if active_slug != slug:
        rows.append([
            InlineKeyboardButton(text="✔ Activar", callback_data=f"var:pack:activate:{slug}"),
        ])
    rows.append([
        InlineKeyboardButton(text="🗑 Eliminar", callback_data=f"var:pack:del:{slug}"),
        InlineKeyboardButton(text="← Paquetes", callback_data="var:packs"),
    ])
    rows.append([InlineKeyboardButton(text="← Menú", callback_data="var:pack:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
