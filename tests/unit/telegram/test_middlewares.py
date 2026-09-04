"""Tests de middlewares de gate (item 5): allowlist + chat privado.

Verifica que al denegar NO se llama al handler y que el deny sale por el
gateway (0 ``message.answer`` directo). Sin red ni Telegram.
"""

from __future__ import annotations

import asyncio

from grokbot.telegram.middlewares import (
    AllowlistMiddleware,
    PrivateChatOnlyMiddleware,
    chat_is_private,
)
from conftest import (
    FakeTelegramGateway,
    callback_query,
    make_chat,
    text_message,
)

OTHER_USER = 222222222


def _capture_handler(calls: list):
    async def handler(event, data):
        calls.append(event)
        return "handled"

    return handler


def _run(mw, handler, event, data=None):
    async def _inner():
        return await mw(handler, event, data or {})

    return asyncio.run(_inner())


def test_allowlist_none_passes_everyone():
    gateway = FakeTelegramGateway()
    mw = AllowlistMiddleware(gateway, None)
    calls = []
    event = text_message("hola", user_id=OTHER_USER)
    result = _run(mw, _capture_handler(calls), event)
    assert result == "handled"
    assert calls == [event]
    assert gateway.calls_by_method("send_message") == []


def test_allowlist_deny_message():
    gateway = FakeTelegramGateway()
    mw = AllowlistMiddleware(gateway, {OTHER_USER})
    calls = []
    event = text_message("hola", user_id=123456789)  # user no permitido
    result = _run(mw, _capture_handler(calls), event)
    assert result is None
    assert calls == []
    sent = gateway.calls_by_method("send_message")
    assert len(sent) == 1
    assert sent[0]["text"] == "No tienes permiso para usar este bot."


def test_allowlist_allows_matching_user_message():
    gateway = FakeTelegramGateway()
    mw = AllowlistMiddleware(gateway, {OTHER_USER})
    calls = []
    event = text_message("hola", user_id=OTHER_USER)
    result = _run(mw, _capture_handler(calls), event)
    assert result == "handled"
    assert calls == [event]
    assert gateway.calls_by_method("send_message") == []


def test_allowlist_deny_callback_alert():
    gateway = FakeTelegramGateway()
    mw = AllowlistMiddleware(gateway, {OTHER_USER})
    calls = []
    cb = callback_query("confirm:yes", user_id=123456789)
    result = _run(mw, _capture_handler(calls), cb)
    assert result is None
    assert calls == []
    answered = gateway.calls_by_method("answer_callback")
    assert len(answered) == 1
    assert answered[0]["text"] == "No tienes permiso para usar este bot."
    assert answered[0]["show_alert"] is True


def test_allowlist_allows_callback_for_matching_user():
    gateway = FakeTelegramGateway()
    mw = AllowlistMiddleware(gateway, {OTHER_USER})
    calls = []
    cb = callback_query("confirm:yes", user_id=OTHER_USER)
    result = _run(mw, _capture_handler(calls), cb)
    assert result == "handled"
    assert calls == [cb]


def test_private_only_deny_group_message():
    gateway = FakeTelegramGateway()
    mw = PrivateChatOnlyMiddleware(gateway)
    calls = []
    event = text_message("/config", chat_type="group", user_id=OTHER_USER)
    result = _run(mw, _capture_handler(calls), event)
    assert result is None
    assert calls == []
    sent = gateway.calls_by_method("send_message")
    assert sent[0]["text"] == "Este bot solo funciona en chats privados."


def test_private_only_allow_private_message():
    gateway = FakeTelegramGateway()
    mw = PrivateChatOnlyMiddleware(gateway)
    calls = []
    event = text_message("/config", chat_type="private")
    result = _run(mw, _capture_handler(calls), event)
    assert result == "handled"
    assert calls == [event]


def test_private_only_deny_group_callback():
    gateway = FakeTelegramGateway()
    mw = PrivateChatOnlyMiddleware(gateway)
    calls = []
    cb = callback_query("cfg:model:grok", chat_type="group")
    result = _run(mw, _capture_handler(calls), cb)
    assert result is None
    assert calls == []
    answered = gateway.calls_by_method("answer_callback")
    assert answered[0]["text"] == "Este bot solo funciona en chats privados."
    assert answered[0]["show_alert"] is True


def test_chat_is_private_helper():
    assert chat_is_private(make_chat(type="private")) is True
    assert chat_is_private(make_chat(type="group")) is False
    assert chat_is_private(make_chat(type="supergroup")) is False
    assert chat_is_private(make_chat(type="channel")) is False
