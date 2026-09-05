"""Refine ComfyUI 2-stage sobre un resultado ya producido (item 5, R1).

Sub-orquestador de la capa telegram que reproduce ``_send_comfyui_confirm_refine``
de grok (bot.py 4313-4527) sobre el vocabulario de eventos de ``application``:

1. El caller detecta ``refine_uc.offer(cfg.comfyui, item.result)`` y llama
   :func:`run_refine_flow` con el ``ItemResult`` (el generator del use case queda
   suspendido en el ``yield`` — el ``async for`` del presenter lo reanuda al volver).
2. Se envía la BASE con el teclado de confirmación: single → el keyboard va en la
   propia foto; álbum → la base no lleva markup y el confirm va en un mensaje de
   texto aparte (paridad grok 4341-4367).
3. ``await_decision`` resuelve yes/no/timeout/cancelled (el hook del JobManager
   resuelve a `cancelled` si el usuario cancela el job durante la espera; R7).
4. yes → marca "Refinando…", corre ``refine_uc.refine(item.request,
   meta["comfyui_remotes"])`` — el segundo stage usa el ``request`` del ítem, NUNCA
   re-resuelve con la config actual (D2b) — y envía la refinada por el sender.
   no/timeout → la base es final (regen keyboard o "Imagen final." en álbum).
   cancelled → la base queda, sin refinada.

El sender persiste ``generation_refs`` POST-envío con message_id real; este módulo
solo orquesta forma/decisiones y NUNCA loguea remotes/IDs de contenido.
"""

from __future__ import annotations

import asyncio
import time

from grokbot.application.events import ItemResult
from grokbot.application.refine_flow import RefineDecision, ResolveRefineUseCase
from grokbot.telegram.chat_ui import ChatUI
from grokbot.telegram.keyboards import (
    cancel_job_keyboard,
    image_regenerate_keyboard,
    refine_confirm_keyboard,
    refining_keyboard,
)
from grokbot.telegram.sender import ResultSender, SentItem, is_album_item

_REFINE_START_TEXT = "Refinando…"
_ALBUM_CONFIRM_TEXT = "¿Refinar las imágenes generadas?"
_ALBUM_FINAL_TEXT = "Imagen final."
_REFINE_ERR_FALLBACK = "No se pudo refinar la imagen."
_SEND_REFINED_FAIL_SINGLE = "No se pudo enviar la imagen refinada."
_SEND_REFINED_FAIL_ALBUM = "No se pudieron enviar las imágenes refinadas."


def _user_safe_message(exc: Exception) -> str:
    """Mensaje user-safe de un error de refine (R6): nunca expone remotes/URLs."""
    return str(getattr(exc, "user_message", None) or _REFINE_ERR_FALLBACK)


async def run_refine_flow(
    ui: ChatUI,
    *,
    item: ItemResult,
    sender: ResultSender,
    refine_uc: ResolveRefineUseCase,
    prefix: str,
    status_id: int | None,
    delete_status: bool,
    caption_model: dict | None = None,
    caption_prompt: bool = False,
    user_id: int,
    owner_uid: int | None = None,
    reply_to: int | None = None,
    token: str | None = None,
    job_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> RefineDecision:
    """Orquesta la confirmación de refine de un resultado refinable.

    Devuelve la decisión resuelta (yes/no/timeout/cancelled). El caller reanuda su
    generator al retornar. ``reply_to`` (message_id del invocador) se propaga a los
    envíos de la base y de la refinada.
    """
    meta = item.result.meta or {}
    is_album = is_album_item(item)
    token = token or refine_uc.register(user_id=user_id, job_id=job_id)
    kb = refine_confirm_keyboard(token)

    # --- enviar base (single con kb en la foto; álbum con confirm aparte) -----
    if is_album:
        base = await sender.send_image(
            ui, item, prefix,
            delete_status=False, save_ref=True,
            caption_model=caption_model, caption_prompt=caption_prompt,
            owner_uid=owner_uid, reply_to=reply_to,
        )
        if base is None:
            refine_uc.drop(token)
            return RefineDecision.no  # base ilegible; no hay nada que confirmar
        confirm_id = (await ui.send_text(_ALBUM_CONFIRM_TEXT, reply_markup=kb)).message_id
    else:
        base = await sender.send_image(
            ui, item, prefix,
            reply_markup=kb, delete_status=False, save_ref=True,
            caption_model=caption_model, caption_prompt=caption_prompt,
            owner_uid=owner_uid, reply_to=reply_to,
        )
        if base is None:
            refine_uc.drop(token)
            return RefineDecision.no
        confirm_id = None

    decision = await refine_uc.await_decision(token)
    if cancel_event is not None and cancel_event.is_set():
        decision = RefineDecision.cancelled

    # --- cancelled -------------------------------------------------------------
    if decision == RefineDecision.cancelled:
        if is_album:
            if confirm_id is not None:
                await ui.delete(confirm_id)
        else:
            # Base final SIN teclado muerto de confirmación (grok 4504-4506).
            await ui.edit_reply_markup(base.primary.message_id, None)
        return RefineDecision.cancelled

    # --- no / timeout → la base es final ---------------------------------------
    if decision in (RefineDecision.no, RefineDecision.timeout):
        if is_album:
            if confirm_id is not None:
                await ui.edit_text(confirm_id, _ALBUM_FINAL_TEXT, reply_markup=None)
        else:
            await ui.edit_reply_markup(
                base.primary.message_id, image_regenerate_keyboard()
            )
        if delete_status and status_id is not None:
            await ui.delete(status_id)
        return decision

    # --- yes → refinar ---------------------------------------------------------
    if is_album:
        if confirm_id is not None:
            await ui.edit_text(confirm_id, _REFINE_START_TEXT, reply_markup=None)
    else:
        await ui.edit_reply_markup(base.primary.message_id, refining_keyboard())
    if status_id is not None:
        await ui.edit_text(
            status_id,
            _REFINE_START_TEXT,
            reply_markup=cancel_job_keyboard(job_id) if job_id else None,
        )

    # El 2º stage cronometra su propio tiempo (paridad grok bot.py:4407-4411) y lo
    # escribe en el meta de la refinada para que su caption muestre el tiempo real.
    started = time.monotonic()
    try:
        refined = await refine_uc.refine(item.request, list(meta.get("comfyui_remotes") or []))
        # ``meta=None`` (que el sender tolera con ``or {}``) no debe falsear un error.
        if refined.meta is not None:
            refined.meta["elapsed_sec"] = int(time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 — todo error se traduce user-safe
        message = _user_safe_message(exc)
        if status_id is not None:
            await ui.edit_text(status_id, message, reply_markup=None)
        if is_album:
            if confirm_id is not None:
                await ui.delete(confirm_id)
        else:
            await ui.edit_reply_markup(base.primary.message_id, image_regenerate_keyboard())
        return RefineDecision.yes

    if cancel_event is not None and cancel_event.is_set():
        # Cancel durante el refine: no entregar la refinada; base final (paridad 4413-4428).
        if is_album:
            if confirm_id is not None:
                await ui.delete(confirm_id)
        else:
            await ui.edit_reply_markup(base.primary.message_id, image_regenerate_keyboard())
        return RefineDecision.cancelled

    refined_item = ItemResult(
        result=refined,
        prompt=item.prompt,
        index=item.index,
        total=item.total,
        combos=item.combos,
        regen_context=item.regen_context,
        request=item.request,
    )
    refined_sent = await sender.send_image(
        ui, refined_item, prefix,
        delete_status=delete_status, save_ref=True,
        caption_model=caption_model, caption_prompt=caption_prompt,
        owner_uid=owner_uid, reply_to=reply_to,
    )
    if refined_sent is None:
        # La refinada no se pudo enviar: reportar, restaurar la base a su estado final.
        if status_id is not None:
            fail_text = _SEND_REFINED_FAIL_ALBUM if is_album else _SEND_REFINED_FAIL_SINGLE
            await ui.edit_text(status_id, fail_text, reply_markup=None)
        if is_album:
            if confirm_id is not None:
                await ui.delete(confirm_id)
        else:
            await ui.edit_reply_markup(base.primary.message_id, image_regenerate_keyboard())
        return RefineDecision.yes

    if is_album:
        if confirm_id is not None:
            await ui.delete(confirm_id)
    else:
        await ui.delete(base.primary.message_id)
    return RefineDecision.yes


__all__ = ["run_refine_flow", "is_album_item", "SentItem", "RefineDecision"]
