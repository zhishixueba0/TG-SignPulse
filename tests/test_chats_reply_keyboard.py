"""Tests for chat-center reply keyboards (Round 4 fix).

SpamBot-style ReplyKeyboardMarkup buttons are plain text: tapping one
sends the button text as a normal message (via send_chat_message),
never via the inline-button press endpoint. Inline keyboards keep
their existing behaviour (url buttons return a link, callback buttons
call request_callback_answer with callback_data).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.telegram.chats import (
    _message_buttons,
    _message_keyboard_type,
    _message_reply_keyboard,
)
from tests.fixtures.messages import (
    make_inline_keyboard_message,
    make_reply_keyboard_message,
)
from tests.mocks.telegram import MockMessage
from tests.test_api import _auth, _login, api_client, db  # noqa: F401  (fixtures)


@pytest.fixture
def svc(isolated_env: Path, monkeypatch):
    """指向临时数据目录的真实 TelegramService（只用本地文件，不连 Telegram）。"""
    from backend.core.config import get_settings
    from backend.services.telegram.runtime import TelegramService

    service = TelegramService()
    # runtime 模块级 settings 在 import 时绑定，用当前解析结果覆盖
    service.session_dir = get_settings().resolve_session_dir()
    service.session_dir.mkdir(parents=True, exist_ok=True)
    yield service


def _make_session(svc, name: str) -> None:
    (svc.session_dir / f"{name}.session").write_bytes(b"x" * 16)


async def _agen(items):
    for item in items:
        yield item


def _reply_btn(text, **kw):
    return SimpleNamespace(
        text=text,
        request_contact=kw.get("request_contact", False),
        request_location=kw.get("request_location", False),
        request_poll=kw.get("request_poll", None),
    )


def _spambot_markup():
    """SpamBot 同款回复键盘：4 个纯文本按钮，单列。"""
    return SimpleNamespace(
        keyboard=[
            [_reply_btn("OK")],
            [_reply_btn("What is spam?")],
            [_reply_btn("I was wrong, please release me")],
            [_reply_btn("This is a mistake")],
        ]
    )


def _msg(*, markup=None):
    return SimpleNamespace(reply_markup=markup)


class TestReplyKeyboardHelpers:
    def test_extracts_plain_text_buttons(self):
        buttons = _message_reply_keyboard(_msg(markup=_spambot_markup()))
        assert buttons == [
            {"row": 0, "col": 0, "text": "OK", "kind": "reply", "url": None},
            {"row": 1, "col": 0, "text": "What is spam?", "kind": "reply", "url": None},
            {
                "row": 2,
                "col": 0,
                "text": "I was wrong, please release me",
                "kind": "reply",
                "url": None,
            },
            {
                "row": 3,
                "col": 0,
                "text": "This is a mistake",
                "kind": "reply",
                "url": None,
            },
        ]

    def test_skips_request_buttons_and_empty_text(self):
        markup = SimpleNamespace(
            keyboard=[
                [_reply_btn("Share contact", request_contact=True)],
                [_reply_btn("Share location", request_location=True)],
                [_reply_btn("Poll", request_poll=SimpleNamespace(type="quiz"))],
                [_reply_btn("   ")],
                [_reply_btn("Help")],
            ]
        )
        assert _message_reply_keyboard(_msg(markup=markup)) == [
            {"row": 4, "col": 0, "text": "Help", "kind": "reply", "url": None}
        ]

    def test_plain_string_buttons_from_test_doubles(self):
        # 既有测试替身（MockReplyKeyboardMarkup）直接用纯字符串行。
        msg = make_reply_keyboard_message(buttons=[["签到", "查询"], ["帮助"]])
        assert _message_reply_keyboard(msg) == [
            {"row": 0, "col": 0, "text": "签到", "kind": "reply", "url": None},
            {"row": 0, "col": 1, "text": "查询", "kind": "reply", "url": None},
            {"row": 1, "col": 0, "text": "帮助", "kind": "reply", "url": None},
        ]

    def test_keyboard_type_reply_inline_none(self):
        assert _message_keyboard_type(_msg(markup=_spambot_markup())) == "reply"
        inline = SimpleNamespace(
            inline_keyboard=[
                [
                    SimpleNamespace(
                        text="Go", url="https://example.com", callback_data=None
                    )
                ]
            ]
        )
        assert _message_keyboard_type(_msg(markup=inline)) == "inline"
        assert _message_keyboard_type(_msg(markup=None)) is None
        assert _message_keyboard_type(_msg(markup=SimpleNamespace(keyboard=[]))) is None

    def test_inline_buttons_ignore_reply_markup(self):
        # 回复键盘按钮绝不能进内联点按路径。
        assert _message_buttons(_msg(markup=_spambot_markup())) == []
        assert _message_buttons(make_reply_keyboard_message()) == []


class TestListMessagesKeyboardTypes:
    @pytest.mark.asyncio
    async def test_each_message_keeps_its_own_keyboard_type(self, svc):
        _make_session(svc, "a")
        reply_msg = make_reply_keyboard_message(
            message_id=10,
            text="What is spam?",
            buttons=[["OK"], ["What is spam?"]],
        )
        inline_msg = make_inline_keyboard_message(
            message_id=11, text="pick one", buttons=[["A", "B"]]
        )
        plain_msg = MockMessage(message_id=12, text="hello")
        fake_client = MagicMock()
        # get_chat_history 最新在前；服务端 reverse 后按时间升序返回。
        fake_client.get_chat_history = MagicMock(
            return_value=_agen([plain_msg, inline_msg, reply_msg])
        )
        with (
            patch.object(svc, "_build_account_client", return_value=(fake_client, {})),
            patch.object(svc, "verify_account_proxy", new=AsyncMock(return_value=None)),
        ):
            messages = await svc.list_chat_messages("a", "spambot")
        # 服务端按时间升序返回；三条消息日期都为空，保持原顺序。
        assert [m["keyboard_type"] for m in messages] == ["reply", "inline", None]
        assert [m["id"] for m in messages] == [10, 11, 12]
        assert messages[0]["buttons"] == [
            {"row": 0, "col": 0, "text": "OK", "kind": "reply", "url": None},
            {"row": 1, "col": 0, "text": "What is spam?", "kind": "reply", "url": None},
        ]
        assert all(b["kind"] == "callback" for b in messages[1]["buttons"])
        assert messages[2]["buttons"] == []

    @pytest.mark.asyncio
    async def test_press_reply_button_sends_text(self, svc):
        # 旧版前端没有回复分支，会把回复按钮发到点按接口；服务端代发文案。
        _make_session(svc, "a")
        msg = make_reply_keyboard_message(message_id=10, buttons=[["OK"]])
        fake_client = MagicMock()
        fake_client.get_messages = AsyncMock(return_value=msg)
        fake_client.send_message = AsyncMock(
            return_value=SimpleNamespace(id=99, date=None, text="OK")
        )
        fake_client.request_callback_answer = AsyncMock()
        with (
            patch.object(svc, "_build_account_client", return_value=(fake_client, {})),
            patch.object(svc, "verify_account_proxy", new=AsyncMock(return_value=None)),
        ):
            result = await svc.press_chat_button("a", "spambot", 10, 0, 0)
        assert result == {"action": "sent", "message": ""}
        fake_client.send_message.assert_awaited_once_with("spambot", "OK")
        fake_client.request_callback_answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_press_unknown_button_still_not_found(self, svc):
        # 内联与回复里都找不到的按钮仍报 BUTTON_NOT_FOUND，且不代发任何内容。
        _make_session(svc, "a")
        msg = make_reply_keyboard_message(message_id=10, buttons=[["OK"]])
        fake_client = MagicMock()
        fake_client.get_messages = AsyncMock(return_value=msg)
        fake_client.send_message = AsyncMock()
        with (
            patch.object(svc, "_build_account_client", return_value=(fake_client, {})),
            patch.object(svc, "verify_account_proxy", new=AsyncMock(return_value=None)),
        ):
            with pytest.raises(ValueError, match="BUTTON_NOT_FOUND"):
                await svc.press_chat_button("a", "spambot", 10, 5, 5)
        fake_client.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_press_inline_callback_unchanged(self, svc):
        _make_session(svc, "a")
        msg = make_inline_keyboard_message(message_id=20, text="pick", buttons=[["Go"]])
        fake_client = MagicMock()
        fake_client.get_messages = AsyncMock(return_value=msg)
        fake_client.request_callback_answer = AsyncMock(
            return_value=SimpleNamespace(message="done", alert=False, url=None)
        )
        with (
            patch.object(svc, "_build_account_client", return_value=(fake_client, {})),
            patch.object(svc, "verify_account_proxy", new=AsyncMock(return_value=None)),
        ):
            result = await svc.press_chat_button("a", "chat", 20, 0, 0)
        assert result == {
            "action": "callback",
            "message": "done",
            "alert": False,
            "url": None,
        }
        fake_client.request_callback_answer.assert_awaited_once()


class TestMessagesRouteKeyboardType:
    def test_route_serializes_keyboard_type(self, api_client, db):  # noqa: F811
        token = _login(api_client)
        svc = MagicMock()
        svc.list_chat_messages = AsyncMock(
            return_value=[
                {
                    "id": 10,
                    "date": "2026-11-06T10:00:00Z",
                    "text": "What is spam?",
                    "outgoing": False,
                    "sender_name": "SpamBot",
                    "sender_id": 178000000,
                    "media_type": None,
                    "keyboard_type": "reply",
                    "buttons": [
                        {
                            "row": 0,
                            "col": 0,
                            "text": "OK",
                            "kind": "reply",
                            "url": None,
                        }
                    ],
                }
            ]
        )
        with patch("backend.api.routes.chats.get_telegram_service", return_value=svc):
            resp = api_client.get(
                "/api/chats/a1/messages?chat_id=178000000", headers=_auth(token)
            )
        assert resp.status_code == 200
        item = resp.json()["messages"][0]
        assert item["keyboard_type"] == "reply"
        assert item["buttons"][0]["kind"] == "reply"
        assert item["buttons"][0]["text"] == "OK"


class TestPressRouteSentAction:
    def test_press_reply_button_route_returns_sent(self, api_client, db):  # noqa: F811
        token = _login(api_client)
        svc = MagicMock()
        svc.press_chat_button = AsyncMock(
            return_value={"action": "sent", "message": ""}
        )
        with patch("backend.api.routes.chats.get_telegram_service", return_value=svc):
            resp = api_client.post(
                "/api/chats/a1/button",
                json={
                    "chat_id": "178000000",
                    "message_id": 10,
                    "row": 0,
                    "col": 0,
                },
                headers=_auth(token),
            )
        assert resp.status_code == 200
        assert resp.json() == {
            "action": "sent",
            "message": "",
            "alert": False,
            "url": None,
        }
        # chat_id 是 int | str：纯数字字符串会被 pydantic 转成 int 再传给 service。
        svc.press_chat_button.assert_awaited_once_with("a1", 178000000, 10, 0, 0)
