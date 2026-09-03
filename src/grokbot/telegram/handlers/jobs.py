"""Callbacks transaccionales: cancel de jobs y decisión de refine (handlers, item 5).

Parity grok bot.py 1181-1199 (``cancel_job``), 1274-1278 (``refine_noop``) y
1281-1302 (``refine:<token>:yes|no``). El gate de owner de refine usa
:meth:`ResolveRefineUseCase.owner_of` (token desconocido O ya resuelto → None),
de modo que el copy diferencia "La confirmación ya se procesó." de
"No es tu confirmación.".

El soft-signal de cancel edita el status con ``⏹ Cancelando…`` (el loop del job
pinta el terminal real después). Todo outbound por gateway/``answer_callback``.
"""

from __future__ import annotations

from functools import partial

from aiogram import Dispatcher, types

from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.handlers._common import answer_callback

_CANCEL_HINT = "⏹ Cancelando…"


async def handle_cancel_job(callback: types.CallbackQuery, deps: BotDeps) -> None:
    """``cancel_job[:<job_id>]`` — cancelar el job indicado (o el más reciente)."""
    data = callback.data or ""
    job_id = data.split(":", 1)[1] if data.startswith("cancel_job:") else None
    uid = callback.from_user.id
    gateway = deps.gateway
    if deps.job_manager.cancel(uid, job_id):
        await answer_callback(gateway, callback, "Cancelando…")
        if callback.message is not None:
            ui = ChatUI.for_message(gateway, callback.message)
            current = callback.message.text or callback.message.caption or ""
            text = f"{current}\n\n{_CANCEL_HINT}" if current and _CANCEL_HINT not in current else (current or _CANCEL_HINT)
            await ui.edit_text(callback.message.message_id, text, reply_markup=None)
        return
    await answer_callback(gateway, callback, "No hay proceso en curso.", show_alert=True)


async def handle_refine_noop(callback: types.CallbackQuery, deps: BotDeps) -> None:
    """Placeholder "Refinando…": no-op (solo responde para quitar el spinner)."""
    await answer_callback(deps.gateway, callback)


async def handle_refine_decision(callback: types.CallbackQuery, deps: BotDeps) -> None:
    """``refine:<token>:yes|no`` — resolver la decisión del dueño (idempotente)."""
    parts = (callback.data or "").split(":")
    gateway = deps.gateway
    if len(parts) != 3:
        await answer_callback(gateway, callback, "Acción inválida.")
        return
    token, choice = parts[1], parts[2]
    uid = callback.from_user.id
    owner = deps.refine_uc.owner_of(token)
    if owner is None:
        await answer_callback(gateway, callback, "La confirmación ya se procesó.", show_alert=True)
        return
    if owner != uid:
        await answer_callback(gateway, callback, "No es tu confirmación.", show_alert=True)
        return
    if choice == "yes":
        deps.refine_uc.decide(token, uid, "yes")
        await answer_callback(gateway, callback, "Refinando…")
        return
    deps.refine_uc.decide(token, uid, "no")
    await answer_callback(gateway, callback, "Listo, imagen final.")


def register_jobs(dp: Dispatcher, deps: BotDeps) -> None:
    dp.callback_query.register(
        partial(handle_cancel_job, deps=deps),
        lambda c: bool(c.data) and c.data.startswith("cancel_job"),
    )
    dp.callback_query.register(
        partial(handle_refine_noop, deps=deps),
        lambda c: c.data == "refine_noop",
    )
    dp.callback_query.register(
        partial(handle_refine_decision, deps=deps),
        lambda c: bool(c.data) and c.data.startswith("refine:"),
    )


__all__ = [
    "register_jobs",
    "handle_cancel_job",
    "handle_refine_noop",
    "handle_refine_decision",
]
