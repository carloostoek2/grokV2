"""Flujos de configuración `/config` (handlers, item 5) — FSM por pantalla.

Parity grok ``config_flow.py``: FSM de modelo → proveedor (grok/grok_video) →
variante/opciones, con ``ConfigResult`` de :class:`UpdateUserConfigUseCase`
traduciendo cada tap (inválido / no-op "ya activo" / cambio). Guards:

* solo chat privado (``chat_is_private``, copy config_flow 22-50);
* stale callback: ``config_message_id`` del FSM vs ``callback.message.message_id``
  (R10; un botón de una pantalla vieja queda inerte).

``cmd_model``/``cmd_imaginess``/``cmd_video`` son accesos directos (el último
activa el modelo antes de mostrar su pantalla). 0 ``message.answer``: todo
outbound por :class:`ChatUI`/``answer_callback``.
"""

from __future__ import annotations

from functools import partial

from aiogram import Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from grokbot.domain.catalog import (
    GROK_IMAGINE_VARIANTS,
    MODELS,
    resolve_grok_config,
)
from grokbot.domain.user_config import (
    VALID_VIDEO_ASPECT_RATIOS,
    VALID_VIDEO_DURATIONS,
    VALID_VIDEO_MODELS,
    VALID_VIDEO_MODES,
    VALID_VIDEO_RESOLUTIONS,
    kie_video_aspect_ratios,
    video_provider_for_config,
)
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.formatters import (
    COMFYUI_CONFIG_MODEL_LABELS,
    VIDEO_MODEL_LABELS,
    VIDEO_MODE_LABELS,
    comfyui_config_lora_label,
    prov_label,
)
from grokbot.telegram.fsm_states import ConfigStates
from grokbot.telegram.handlers._common import answer_callback
from grokbot.telegram.keyboards import (
    config_comfyui_keyboard,
    config_model_keyboard,
    config_provider_keyboard,
    config_variant_keyboard,
    config_video_keyboard,
    simple_close_keyboard,
)

_STALE_TEXT = "Esta pantalla ya no está activa. Usa /config para empezar de nuevo."
_DESYNC_TEXT = "Sesión de configuración desactualizada. Usa /config para empezar de nuevo."
_DENY_PRIVATE_TEXT = "La configuración solo está disponible en chats privados."


# --------------------------------------------------------------------------- #
# Copy de pantallas (transcrito de config_flow.py 222-386)
# --------------------------------------------------------------------------- #
def _provider_screen_text(cfg, model_key: str) -> str:
    res = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
    model_label = "Grok Imagine" if model_key == "grok" else "Grok Imagine Video"
    return (
        f"Configuración de <b>{model_label}</b> — proveedor.\n\n"
        f"Actual: <b>{prov_label(res['provider'])}</b>\n\n"
        "Elige el proveedor de API. El cambio se guarda inmediatamente.\n\n"
        "<i>Si el proveedor activo ya está seleccionado, tócalo de nuevo para continuar.</i>"
    )


def _variant_screen_text(cfg, *, updated: bool = False) -> str:
    res = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)
    spec = GROK_IMAGINE_VARIANTS[res["variant"]]
    prefix = "✅ Configuración de Grok Imagine actualizada y guardada.\n\n" if updated else ""
    return (
        f"{prefix}"
        "Configuración de <b>Grok Imagine</b> — nivel de calidad.\n\n"
        f"Actual: <b>{prov_label(res['provider'])} • {spec['label']}</b>\n"
        f"<i>{spec['desc']}</i>\n\n"
        "Elige nivel de calidad. El cambio se guarda inmediatamente."
    )


def _kie_map_duration(duration: int) -> int:
    return max(6, min(duration, 30))


def _video_duration_display(configured: int, provider: str | None) -> str:
    if provider == "kie":
        effective = _kie_map_duration(configured)
        if effective != configured:
            return f"{configured}s → {effective}s (Kie.ai)"
        return f"{configured}s"
    return f"{configured}s"


def _video_summary(cfg) -> str:
    prov = video_provider_for_config(cfg)
    model_label = VIDEO_MODEL_LABELS.get(cfg.video.model, cfg.video.model)
    dur = _video_duration_display(cfg.video.duration, prov)
    summary = f"<b>{model_label}</b> • {dur} • {cfg.video.aspect_ratio} • {cfg.video.resolution}"
    if prov == "kie":
        mode_label = VIDEO_MODE_LABELS.get(cfg.video.mode, cfg.video.mode)
        summary += f" • {mode_label}"
    return summary


def _video_screen_text(cfg, *, updated: bool = False, aspect_reset_msg: str | None = None) -> str:
    prov = video_provider_for_config(cfg)
    summary = _video_summary(cfg)
    prefix = "✅ Configuración de video actualizada y guardada.\n\n" if updated else ""
    text = (
        f"{prefix}"
        "Configuración de <b>Grok Imagine Video</b> (persistente).\n\n"
        f"Actual: {summary}\n\n"
        "Elige modelo, duración, relación de aspecto y resolución. El cambio se guarda inmediatamente."
    )
    if prov == "kie":
        text += (
            "\n\n<i>Kie.ai: duración mínima 6s (3/5s se ajustan a 6s). "
            "Modelo 1.5 solo soporta imagen a video en Kie.ai. "
            "Modo Spicy solo funciona al animar imágenes generadas por el bot (reply a imagen del bot).</i>"
        )
    if aspect_reset_msg:
        text += f"\n\n<i>{aspect_reset_msg}</i>"
    return text


def _comfyui_screen_text(cfg, *, updated: bool = False) -> str:
    cc = cfg.comfyui
    header = "Configuración actualizada ✅\n" if updated else "Configuración de ComfyUI (GPU propia):\n"
    model_label = COMFYUI_CONFIG_MODEL_LABELS.get(cc.model, cc.model)
    lora_label = comfyui_config_lora_label(cc.model, cc.lora)
    refine_label = "ON ✨" if cc.refine == "1" else "OFF"
    hint = (
        "\nVideo: envía una foto + prompt (o responde a una foto) para generar el video."
        if cc.model == "wan_i2v"
        else ""
    )
    return (
        f"{header}"
        f"<b>Modelo:</b> {model_label}\n"
        f"<b>LoRA:</b> {lora_label}\n"
        f"<b>Refinar:</b> {refine_label}\n\n"
        f"Elige el modelo y el LoRA:{hint}"
    )


def _simple_model_text(cfg) -> str:
    model = MODELS.get(cfg.model, MODELS["grok"])
    lines = [
        f"Modelo cambiado a <b>{model['name']}</b>.\n",
        f"<i>{model['desc']}</i>\n",
    ]
    if cfg.model == "faceswap":
        lines.append("Usa /cambiar_source para configurar tu cara fuente.\n")
        lines.append("Luego Envía fotos (incluso albumes) para hacer face swap.")
    elif cfg.model == "seedream":
        lines.append("Enviame un prompt para generar una imagen.")
        lines.append("O Envía una foto con caption para editarla.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #
def _is_private(message_or_cb) -> bool:
    from grokbot.telegram.middlewares import chat_is_private

    chat = message_or_cb.chat
    return chat_is_private(chat)


async def _reject_non_private_message(message: types.Message, deps: BotDeps) -> bool:
    if _is_private(message):
        return False
    ui = ChatUI.for_message(deps.gateway, message)
    await ui.send_text(_DENY_PRIVATE_TEXT)
    return True


async def _reject_non_private_callback(callback: types.CallbackQuery, deps: BotDeps) -> bool:
    if callback.message is None or _is_private(callback.message):
        return False
    await answer_callback(deps.gateway, callback, _DENY_PRIVATE_TEXT, show_alert=True)
    return True


async def _reject_stale_callback(
    callback: types.CallbackQuery,
    state: FSMContext,
    deps: BotDeps,
    *,
    allowed_states: tuple,
    required_config_models: tuple[str, ...] | None = None,
) -> bool:
    data = await state.get_data()
    stored_id = data.get("config_message_id")
    if stored_id is not None and callback.message is not None and callback.message.message_id != stored_id:
        await answer_callback(deps.gateway, callback, _STALE_TEXT, show_alert=True)
        return True
    current = await state.get_state()
    if current not in {st.state for st in allowed_states}:
        await answer_callback(deps.gateway, callback, _STALE_TEXT, show_alert=True)
        return True
    if required_config_models is not None:
        if data.get("config_model") not in required_config_models:
            await answer_callback(deps.gateway, callback, _DESYNC_TEXT, show_alert=True)
            return True
    return False


# --------------------------------------------------------------------------- #
# Showers (editan el mensaje del panel y guardan id en el FSM)
# --------------------------------------------------------------------------- #
async def _show_model_screen(target: types.Message, state: FSMContext, deps: BotDeps) -> None:
    await state.set_state(ConfigStates.select_model)
    await state.update_data(config_model=None, config_message_id=target.message_id)
    ui = ChatUI.for_message(deps.gateway, target)
    await ui.edit_text(
        target.message_id,
        "Selecciona el modelo:",
        reply_markup=config_model_keyboard(deps.sessions.get_config(target.from_user.id)),
    )


async def _show_provider_screen(target: types.Message, state: FSMContext, deps: BotDeps, model_key: str) -> None:
    await state.set_state(ConfigStates.select_provider)
    await state.update_data(config_model=model_key, config_message_id=target.message_id)
    ui = ChatUI.for_message(deps.gateway, target)
    cfg = deps.sessions.get_config(target.from_user.id)
    await ui.edit_text(
        target.message_id,
        _provider_screen_text(cfg, model_key),
        reply_markup=config_provider_keyboard(cfg),
    )


async def _show_configure_screen(
    target: types.Message,
    state: FSMContext,
    deps: BotDeps,
    model_key: str,
    *,
    updated: bool = False,
    aspect_reset_msg: str | None = None,
) -> None:
    await state.set_state(ConfigStates.configure)
    await state.update_data(config_model=model_key, config_message_id=target.message_id)
    ui = ChatUI.for_message(deps.gateway, target)
    cfg = deps.sessions.get_config(target.from_user.id)

    if model_key == "grok":
        await ui.edit_text(
            target.message_id,
            _variant_screen_text(cfg, updated=updated),
            reply_markup=config_variant_keyboard(cfg),
        )
        return
    if model_key == "grok_video":
        await ui.edit_text(
            target.message_id,
            _video_screen_text(cfg, updated=updated, aspect_reset_msg=aspect_reset_msg),
            reply_markup=config_video_keyboard(cfg.video, is_kie=video_provider_for_config(cfg) == "kie"),
        )
        return
    if model_key == "comfyui":
        await ui.edit_text(
            target.message_id,
            _comfyui_screen_text(cfg, updated=updated),
            reply_markup=config_comfyui_keyboard(cfg.comfyui),
        )
        return
    await ui.edit_text(
        target.message_id,
        _simple_model_text(cfg),
        reply_markup=simple_close_keyboard(),
    )


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #
async def cmd_config(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_message(message, deps):
        return
    await state.set_state(ConfigStates.select_model)
    await state.update_data(config_model=None)
    ui = ChatUI.for_message(deps.gateway, message)
    sent = await ui.send_text(
        "Selecciona el modelo:",
        reply_markup=config_model_keyboard(deps.sessions.get_config(message.from_user.id)),
    )
    await state.update_data(config_message_id=sent.message_id)


async def cmd_model(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    await cmd_config(message, state, deps)


async def cmd_imaginess(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    """/imagine e /imaginess: activa Grok Imagine y muestra variante/proveedor."""
    if await _reject_non_private_message(message, deps):
        return
    uid = message.from_user.id
    deps.update_config.set_model(uid, "grok")
    cfg = deps.sessions.get_config(uid)
    ui = ChatUI.for_message(deps.gateway, message)
    await state.update_data(config_model="grok")
    await state.set_state(ConfigStates.configure)
    sent = await ui.send_text(
        _variant_screen_text(cfg),
        reply_markup=config_variant_keyboard(cfg),
    )
    await state.update_data(config_message_id=sent.message_id)


async def cmd_video(message: types.Message, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_message(message, deps):
        return
    uid = message.from_user.id
    deps.update_config.set_model(uid, "grok_video")
    cfg = deps.sessions.get_config(uid)
    ui = ChatUI.for_message(deps.gateway, message)
    await state.set_state(ConfigStates.configure)
    await state.update_data(config_model="grok_video")
    sent = await ui.send_text(
        _video_screen_text(cfg),
        reply_markup=config_video_keyboard(cfg.video, is_kie=video_provider_for_config(cfg) == "kie"),
    )
    await state.update_data(config_message_id=sent.message_id)


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
async def handle_cfg_model(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(ConfigStates.select_model,)):
        return
    model_key = callback.data.split(":", 2)[2]
    if model_key not in MODELS:
        await answer_callback(deps.gateway, callback, "Modelo no disponible.", show_alert=True)
        return
    uid = callback.from_user.id
    cfg = deps.sessions.get_config(uid)
    same_model = cfg.model == model_key
    if not same_model:
        deps.update_config.set_model(uid, model_key)
    if model_key in ("grok", "grok_video"):
        await _show_provider_screen(callback.message, state, deps, model_key)
        await answer_callback(deps.gateway, callback, f"Modelo: {MODELS[model_key]['name']}")
        return
    await _show_configure_screen(callback.message, state, deps, model_key)
    if same_model:
        await answer_callback(deps.gateway, callback, "Ya estás usando ese modelo.")
        return
    await answer_callback(deps.gateway, callback, f"Modelo: {MODELS[model_key]['name']}")


async def handle_cfg_provider(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_stale_callback(
        callback, state, deps,
        allowed_states=(ConfigStates.select_provider,),
        required_config_models=("grok", "grok_video"),
    ):
        return
    prov = callback.data.split(":", 2)[2]
    if prov not in ("xai", "replicate", "kie"):
        await answer_callback(deps.gateway, callback, "Proveedor no disponible.", show_alert=True)
        return
    data = await state.get_data()
    model_key = data.get("config_model")
    uid = callback.from_user.id
    cfg = deps.sessions.get_config(uid)
    prior = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)["provider"]
    if prov == prior:
        await _show_configure_screen(callback.message, state, deps, model_key)
        await answer_callback(deps.gateway, callback, "Ya está activo ese proveedor.")
        return
    result = deps.update_config.set_grok_imagine_provider(uid, prov)
    aspect_reset_msg = None
    if result.ok and result.aspect_reset_to:
        aspect_reset_msg = (
            f"Relación de aspecto de video ajustada a {result.aspect_reset_to} (compatible con Kie.ai)."
        )
    if model_key == "grok":
        deps.update_config.set_model(uid, "grok")
    else:
        deps.update_config.set_model(uid, "grok_video")
    await _show_configure_screen(
        callback.message, state, deps, model_key, aspect_reset_msg=aspect_reset_msg,
    )
    new_cfg = deps.sessions.get_config(uid)
    resolved = resolve_grok_config(new_cfg.grok_imagine_provider, new_cfg.grok_imagine_variant)
    await answer_callback(deps.gateway, callback, f"Proveedor: {prov_label(resolved['provider'])}")


async def handle_cfg_variant(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_stale_callback(
        callback, state, deps,
        allowed_states=(ConfigStates.configure,),
        required_config_models=("grok",),
    ):
        return
    var = callback.data.split(":", 2)[2]
    if var not in GROK_IMAGINE_VARIANTS:
        await answer_callback(deps.gateway, callback, "Opción inválida.", show_alert=True)
        return
    uid = callback.from_user.id
    cfg = deps.sessions.get_config(uid)
    prior = resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)["variant"]
    if var == prior:
        await answer_callback(deps.gateway, callback, "Ya está activa esa configuración.")
        return
    deps.update_config.set_grok_imagine_variant(uid, var)
    deps.update_config.set_model(uid, "grok")
    await _show_configure_screen(callback.message, state, deps, "grok", updated=True)
    new_cfg = deps.sessions.get_config(uid)
    resolved = resolve_grok_config(new_cfg.grok_imagine_provider, new_cfg.grok_imagine_variant)
    await answer_callback(
        deps.gateway, callback,
        f"Grok Imagine: {prov_label(resolved['provider'])} • {GROK_IMAGINE_VARIANTS[resolved['variant']]['label']}",
    )


_VIDEO_NOOP_TEXT = {
    "model": "Ya está activo ese modelo.",
    "duration": "Ya está activa esa duración.",
    "aspect": "Ya está activa esa relación de aspecto.",
    "resolution": "Ya está activa esa resolución.",
    "mode": "Ya está activo ese modo.",
}


async def handle_cfg_video(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_stale_callback(
        callback, state, deps,
        allowed_states=(ConfigStates.configure,),
        required_config_models=("grok_video",),
    ):
        return
    parts = callback.data.split(":")
    if len(parts) < 4 or parts[0] != "cfg" or parts[1] != "video":
        await answer_callback(deps.gateway, callback, "Opción inválida.", show_alert=True)
        return
    field = parts[2]
    value = ":".join(parts[3:])
    uid = callback.from_user.id
    cfg = deps.sessions.get_config(uid)
    prov = video_provider_for_config(cfg)
    prior = cfg.video
    gateway = deps.gateway

    if field == "model":
        if value not in VALID_VIDEO_MODELS:
            await answer_callback(gateway, callback, "Modelo no disponible.", show_alert=True)
            return
        if value == prior.model:
            await answer_callback(gateway, callback, "Ya está activo ese modelo.")
            return
        result = deps.update_config.set_video(uid, model=value)
        aspect_reset_msg = (
            f"Relación de aspecto ajustada a {result.aspect_reset_to} (compatible con Kie.ai)."
            if result.ok and result.aspect_reset_to
            else None
        )
    elif field == "duration":
        try:
            duration = int(value)
        except ValueError:
            await answer_callback(gateway, callback, "Duración inválida.", show_alert=True)
            return
        if duration not in VALID_VIDEO_DURATIONS:
            await answer_callback(gateway, callback, "Duración no disponible.", show_alert=True)
            return
        if duration == prior.duration:
            await answer_callback(gateway, callback, "Ya está activa esa duración.")
            return
        deps.update_config.set_video(uid, duration=duration)
        aspect_reset_msg = None
    elif field == "aspect":
        allowed = (
            kie_video_aspect_ratios(prior.model) if prov == "kie" else VALID_VIDEO_ASPECT_RATIOS
        )
        if value not in allowed:
            await answer_callback(gateway, callback, "Relación de aspecto no disponible.", show_alert=True)
            return
        if value == prior.aspect_ratio:
            await answer_callback(gateway, callback, "Ya está activa esa relación de aspecto.")
            return
        deps.update_config.set_video(uid, aspect_ratio=value)
        aspect_reset_msg = None
    elif field == "resolution":
        if value not in VALID_VIDEO_RESOLUTIONS:
            await answer_callback(gateway, callback, "Resolución no disponible.", show_alert=True)
            return
        if value == prior.resolution:
            await answer_callback(gateway, callback, "Ya está activa esa resolución.")
            return
        deps.update_config.set_video(uid, resolution=value)
        aspect_reset_msg = None
    elif field == "mode":
        if prov != "kie":
            await answer_callback(gateway, callback, "Modo de video solo disponible con Kie.ai.", show_alert=True)
            return
        if value not in VALID_VIDEO_MODES:
            await answer_callback(gateway, callback, "Modo no disponible.", show_alert=True)
            return
        if value == prior.mode:
            await answer_callback(gateway, callback, "Ya está activo ese modo.")
            return
        deps.update_config.set_video(uid, mode=value)
        aspect_reset_msg = None
    else:
        await answer_callback(gateway, callback, "Opción inválida.", show_alert=True)
        return

    await _show_configure_screen(
        callback.message, state, deps, "grok_video", updated=True, aspect_reset_msg=aspect_reset_msg,
    )
    new_cfg = deps.sessions.get_config(uid)
    dur_label = _video_duration_display(new_cfg.video.duration, video_provider_for_config(new_cfg))
    model_label = VIDEO_MODEL_LABELS.get(new_cfg.video.model, new_cfg.video.model)
    answer = f"Video: {model_label} • {dur_label} • {new_cfg.video.aspect_ratio} • {new_cfg.video.resolution}"
    if video_provider_for_config(new_cfg) == "kie":
        mode_label = VIDEO_MODE_LABELS.get(new_cfg.video.mode, new_cfg.video.mode)
        answer += f" • {mode_label}"
    await answer_callback(gateway, callback, answer)


async def handle_cfg_comfyui(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_stale_callback(
        callback, state, deps,
        allowed_states=(ConfigStates.configure,),
        required_config_models=("comfyui",),
    ):
        return
    parts = callback.data.split(":")
    if len(parts) != 4 or parts[2] not in ("model", "lora", "refine"):
        await answer_callback(deps.gateway, callback, "Opción inválida.", show_alert=True)
        return
    kind, value = parts[2], parts[3]
    gateway = deps.gateway
    uid = callback.from_user.id
    cfg = deps.sessions.get_config(uid)
    current = getattr(cfg.comfyui, kind)
    if value == current:
        await answer_callback(gateway, callback, "Ya está activa esa opción.")
        return
    result = deps.update_config.set_comfyui(uid, **{kind: value})
    if not result.ok:
        msg = {
            "model": "Modelo no disponible.",
            "lora": "LoRA no disponible.",
            "refine": "Opción inválida.",
        }[kind]
        await answer_callback(gateway, callback, msg, show_alert=True)
        return
    deps.update_config.set_model(uid, "comfyui")
    await _show_configure_screen(callback.message, state, deps, "comfyui", updated=True)
    new_cfg = deps.sessions.get_config(uid)
    if kind == "refine":
        label = "ON ✨" if value == "1" else "OFF"
    elif kind == "lora":
        label = value
    else:
        label = new_cfg.comfyui.model
    await answer_callback(gateway, callback, f"ComfyUI {kind}: {label}")


async def handle_cfg_back_model(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_stale_callback(
        callback, state, deps,
        allowed_states=(ConfigStates.select_provider, ConfigStates.configure),
    ):
        return
    await _show_model_screen(callback.message, state, deps)
    await answer_callback(deps.gateway, callback)


async def handle_cfg_back_provider(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_stale_callback(callback, state, deps, allowed_states=(ConfigStates.configure,)):
        return
    data = await state.get_data()
    uid = callback.from_user.id
    model_key = data.get("config_model")
    if model_key not in ("grok", "grok_video"):
        model_key = deps.sessions.get_config(uid).model
    if model_key not in ("grok", "grok_video"):
        await answer_callback(deps.gateway, callback, _DESYNC_TEXT, show_alert=True)
        return
    await _show_provider_screen(callback.message, state, deps, model_key)
    await answer_callback(deps.gateway, callback)


async def handle_cfg_close(callback: types.CallbackQuery, state: FSMContext, deps: BotDeps) -> None:
    if await _reject_non_private_callback(callback, deps):
        return
    if await _reject_stale_callback(
        callback, state, deps,
        allowed_states=(ConfigStates.select_model, ConfigStates.select_provider, ConfigStates.configure),
    ):
        return
    await state.clear()
    ui = ChatUI.for_message(deps.gateway, callback.message)
    await ui.edit_text(callback.message.message_id, "Configuración cerrada.", reply_markup=None)
    await answer_callback(deps.gateway, callback)


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
def register_config(dp: Dispatcher, deps: BotDeps) -> None:
    dp.message.register(partial(cmd_config, deps=deps), Command("config", "model"))
    dp.message.register(partial(cmd_imaginess, deps=deps), Command("imagine", "imaginess"))
    dp.message.register(partial(cmd_video, deps=deps), Command("video"))
    dp.callback_query.register(partial(handle_cfg_back_model, deps=deps), lambda c: c.data == "cfg:back:model")
    dp.callback_query.register(partial(handle_cfg_back_provider, deps=deps), lambda c: c.data == "cfg:back:provider")
    dp.callback_query.register(partial(handle_cfg_close, deps=deps), lambda c: c.data == "cfg:close")
    dp.callback_query.register(partial(handle_cfg_model, deps=deps), lambda c: c.data and c.data.startswith("cfg:model:"))
    dp.callback_query.register(partial(handle_cfg_provider, deps=deps), lambda c: c.data and c.data.startswith("cfg:provider:"))
    dp.callback_query.register(partial(handle_cfg_comfyui, deps=deps), lambda c: c.data and c.data.startswith("cfg:comfyui:"))
    dp.callback_query.register(partial(handle_cfg_variant, deps=deps), lambda c: c.data and c.data.startswith("cfg:variant:"))
    dp.callback_query.register(partial(handle_cfg_video, deps=deps), lambda c: c.data and c.data.startswith("cfg:video:"))


__all__ = [
    "register_config",
    "cmd_config",
    "cmd_imaginess",
    "cmd_video",
    "_variant_screen_text",
    "_video_screen_text",
    "_comfyui_screen_text",
]
