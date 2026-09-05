"""Flujos de `/variables` y `/var` (handlers, item 5).

Parity grok ``cmd_variables_help``/``cmd_var_help`` (bot.py 2048-2080 y
2598-2628) más sus variantes photo/reply y los runners ``_run_variables_batch`` /
``_run_var_batch`` (2256-2468 / 2691-2830). Un solo batch engine
(:class:`RunVariableBatchUseCase`) con dos estrategias puras:

* ``/variables N`` → :class:`RandomComboStrategy` (draws random de listas).
* ``/var [N] texto`` → :class:`FixedPromptStrategy` con el prompt inyectado en la
  plantilla (``PromptTemplate.render_inline``, paridad ``build_prompt_inline``).

Los Command de este módulo se registran ANTES que los flujos genéricos de
foto/texto (register_all), así consumen captions/replies con el comando. El batch
siempre descarga la imagen fuente por ``gateway.get_file_bytes`` (el use case no
acepta ``KieTaskRef``); reply a foto del bot incluido. 0 JSON/red directa (R9).
"""

from __future__ import annotations

from functools import partial

from aiogram import Dispatcher, types
from aiogram.filters import Command

from grokbot.domain.variables import VARIABLES_MAX, PromptTemplate
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.formatters import model_display, validate_prompt
from grokbot.telegram.handlers._common import (
    SOURCE_MEDIA_UNAVAILABLE_MSG,
    fetch_source_bytes,
    is_album,
    largest_photo,
    make_sender,
    parse_var_count_and_text,
    parse_var_prompt,
    parse_variables_count,
)
from grokbot.telegram.stream_presenter import present_batch

_VARIABLES_USAGE = (
    "Para usar <b>/variables</b>:\n"
    "• Envía una foto con el caption <b>/variables N</b>, o responde a una foto, "
    "para generar N ediciones (N = 1-" + str(VARIABLES_MAX) + ") combinando "
    "aleatoriamente poses, ángulos y acciones.\n"
    "• Envía <b>/variables N</b> como mensaje de texto para generar N imágenes "
    "directamente desde la combinación de listas.\n"
    "• ¿Prefieres un texto fijo? Usa <b>/var texto</b> para inyectarlo "
    "directamente en la plantilla, sin listas.\n\n"
    "Gestiona las listas con <b>/listas</b>."
)


def _var_usage() -> str:
    return (
        "Para usar <b>/var</b>:\n"
        "• Envía <b>/var texto</b> (o <b>/var N texto</b>, N = 1-"
        f"{VARIABLES_MAX}) como mensaje para generar una imagen (o N) con ese "
        "texto como prompt.\n"
        "• Envía una foto con el caption <b>/var texto</b>, o responde a una foto "
        "con <b>/var texto</b>, para editarla con ese texto.\n\n"
        "El texto se inyecta en la <b>plantilla</b> configurada en <b>/listas</b>: "
        "separa con comas los valores para cada placeholder (p. ej. con la "
        "plantilla <code>{pose}, {angle}, {action}</code>, <b>/var de pie, "
        "frontal, elegante</b> genera con «de pie, frontal, elegante»). Si "
        "escribes un solo valor, va al primer placeholder. Con <b>N</b>, todas "
        "las imágenes usan el mismo prompt."
    )


# --------------------------------------------------------------------------- #
# Runners de batch (edit/text) — comparten use case + present_batch
# --------------------------------------------------------------------------- #
async def _source_bytes_or_degrade(gateway, ui, file_id: str | None) -> tuple[bytes | None, bool]:
    """Descarga la imagen fuente → (bytes, False) o degrada user-safe (M2).

    ``(None, True)`` cuando el file_id no se pudo recuperar (el handler debe
    retornar; ya se envió el mensaje degradado). ``(None, False)`` sin file_id.
    Nunca loguea el ``file_id``.
    """
    if not file_id:
        return None, False
    source_image = await fetch_source_bytes(gateway, file_id)
    if source_image is None:
        await ui.send_text(SOURCE_MEDIA_UNAVAILABLE_MSG)
        return None, True
    return source_image, False


async def _run_variables_batch(
    deps: BotDeps,
    message: types.Message,
    count: int,
    *,
    source_image: bytes | None,
    source_file_id: str | None,
    mode: str,
) -> None:
    ui = ChatUI.for_message(deps.gateway, message)
    uid = message.from_user.id
    cfg = deps.sessions.get_config(uid)
    from grokbot.application.run_variable_batch import RandomComboStrategy

    events = deps.run_batch.run(
        user_id=uid,
        count=count,
        strategy=RandomComboStrategy(deps.variables),
        source_image=source_image,
        source_file_id=source_file_id,
        mode=mode,
    )
    is_text = mode == "text"
    await present_batch(
        ui,
        events,
        verb="generando" if is_text else "editando",
        count=count,
        model=model_display(cfg),
        sender=make_sender(deps),
        style="variables",
        fail_label="Generación" if is_text else "Edición",
        refine_uc=deps.refine_uc,
        cfg=cfg,
        user_id=uid,
        job_manager=deps.job_manager,
        reply_to=message.message_id,
    )


async def _run_var_batch(
    deps: BotDeps,
    message: types.Message,
    count: int,
    prompt: str,
    *,
    source_image: bytes | None,
    source_file_id: str | None,
    mode: str,
) -> None:
    from grokbot.application.run_variable_batch import FixedPromptStrategy

    ui = ChatUI.for_message(deps.gateway, message)
    uid = message.from_user.id
    cfg = deps.sessions.get_config(uid)
    template = deps.variables.get_template()
    final_prompt = PromptTemplate(template).render_inline(prompt.split(","))
    events = deps.run_batch.run(
        user_id=uid,
        count=count,
        strategy=FixedPromptStrategy(final_prompt),
        source_image=source_image,
        source_file_id=source_file_id,
        mode=mode,
    )
    is_text = mode == "text"
    await present_batch(
        ui,
        events,
        verb="generando" if is_text else "editando",
        count=count,
        model=model_display(cfg),
        sender=make_sender(deps),
        style="var",
        fail_label="Generación" if is_text else "Edición",
        refine_uc=deps.refine_uc,
        cfg=cfg,
        user_id=uid,
        job_manager=deps.job_manager,
        reply_to=message.message_id,
    )


# --------------------------------------------------------------------------- #
# /variables
# --------------------------------------------------------------------------- #
async def cmd_variables(message: types.Message, deps: BotDeps) -> None:
    """Texto 'variables' (Command filter casa text o caption)."""
    if message.reply_to_message is not None:
        await cmd_variables_reply(message, deps)
        return
    if message.photo and not (isinstance(message.media_group_id, str) and message.media_group_id):
        await cmd_variables_photo(message, deps)
        return
    count = parse_variables_count(message.text) if isinstance(message.text, str) else None
    if count is not None:
        await _run_variables_batch(
            deps, message, count, source_image=None, source_file_id=None, mode="text"
        )
        return
    ui = ChatUI.for_message(deps.gateway, message)
    await ui.send_text(_VARIABLES_USAGE)


async def cmd_variables_photo(message: types.Message, deps: BotDeps) -> None:
    """Foto con caption '/variables N'."""
    count = parse_variables_count(message.caption)
    if count is None:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text(
            f"Uso: envía la foto con el caption <b>/variables N</b> (N = 1-{VARIABLES_MAX}).\n\n"
            "Gestiona las listas con <b>/listas</b>."
        )
        return
    file_id = largest_photo(message)
    ui = ChatUI.for_message(deps.gateway, message)
    source_image, degraded = await _source_bytes_or_degrade(deps.gateway, ui, file_id)
    if degraded:
        return
    await _run_variables_batch(
        deps, message, count,
        source_image=source_image, source_file_id=file_id, mode="edit",
    )


async def cmd_variables_reply(message: types.Message, deps: BotDeps) -> None:
    """'/variables N' como reply a una foto."""
    count = parse_variables_count(message.text or message.caption)
    if count is None:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text(
            f"Uso: responde a una foto con <b>/variables N</b> (N = 1-{VARIABLES_MAX}).\n\n"
            "Gestiona las listas con <b>/listas</b>."
        )
        return
    reply = message.reply_to_message
    if reply is None or not reply.photo:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text("Responde a una foto para editarla con /variables.")
        return
    file_id = largest_photo(reply)
    ui = ChatUI.for_message(deps.gateway, message)
    source_image, degraded = await _source_bytes_or_degrade(deps.gateway, ui, file_id)
    if degraded:
        return
    await _run_variables_batch(
        deps, message, count,
        source_image=source_image, source_file_id=file_id, mode="edit",
    )


# --------------------------------------------------------------------------- #
# /var
# --------------------------------------------------------------------------- #
async def cmd_var(message: types.Message, deps: BotDeps) -> None:
    """'/var texto' — Command filter casa text o caption."""
    if message.reply_to_message is not None:
        await cmd_var_reply(message, deps)
        return
    if message.photo and not (isinstance(message.media_group_id, str) and message.media_group_id):
        await cmd_var_photo(message, deps)
        return
    count, prompt = (
        parse_var_count_and_text(message.text) if isinstance(message.text, str) else (1, None)
    )
    if prompt is None:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text(_var_usage())
        return
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        ui = ChatUI.for_message(deps.gateway, message)
        await ui.send_text(prompt_err)
        return
    await _run_var_batch(
        deps, message, count, prompt,
        source_image=None, source_file_id=None, mode="text",
    )


async def cmd_var_photo(message: types.Message, deps: BotDeps) -> None:
    """Foto con caption '/var [N] texto'."""
    count, prompt = parse_var_count_and_text(message.caption)
    ui = ChatUI.for_message(deps.gateway, message)
    if prompt is None:
        await ui.send_text("Uso: envía la foto con el caption <b>/var [N] texto</b>.\n\n" + _var_usage())
        return
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await ui.send_text(prompt_err)
        return
    file_id = largest_photo(message)
    source_image, degraded = await _source_bytes_or_degrade(deps.gateway, ui, file_id)
    if degraded:
        return
    await _run_var_batch(
        deps, message, count, prompt,
        source_image=source_image, source_file_id=file_id, mode="edit",
    )


async def cmd_var_reply(message: types.Message, deps: BotDeps) -> None:
    """'/var [N] texto' como reply a una foto."""
    count, prompt = parse_var_count_and_text(message.text or message.caption)
    ui = ChatUI.for_message(deps.gateway, message)
    if prompt is None:
        await ui.send_text("Uso: responde a una foto con <b>/var [N] texto</b>.\n\n" + _var_usage())
        return
    reply = message.reply_to_message
    if reply is None or not reply.photo:
        await ui.send_text("Responde a una foto para editarla con /var.")
        return
    prompt_err = validate_prompt(prompt)
    if prompt_err:
        await ui.send_text(prompt_err)
        return
    file_id = largest_photo(reply)
    source_image, degraded = await _source_bytes_or_degrade(deps.gateway, ui, file_id)
    if degraded:
        return
    await _run_var_batch(
        deps, message, count, prompt,
        source_image=source_image, source_file_id=file_id, mode="edit",
    )


# --------------------------------------------------------------------------- #
# Registro (antes que generation — los Command ganan captions/replies)
# --------------------------------------------------------------------------- #
def register_variables(dp: Dispatcher, deps: BotDeps) -> None:
    dp.message.register(partial(cmd_variables, deps=deps), Command("variables"))
    dp.message.register(partial(cmd_var, deps=deps), Command("var"))


__all__ = [
    "register_variables",
    "cmd_variables",
    "cmd_var",
    "parse_var_count_and_text",
    "parse_var_prompt",
    "parse_variables_count",
    "is_album",
    "_var_usage",
    "_VARIABLES_USAGE",
]
