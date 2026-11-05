"""Tests for chat-center media + inline-keyboard helpers (Round 4 item 4).

Pure-function coverage: photo detection and inline-keyboard extraction
from Pyrogram-shaped messages (SimpleNamespace stand-ins). The live
round-trip (real download / real button press) is covered by the VPS
deploy verification, not here.
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.services.telegram.chats import _message_buttons, _message_media_type


def _btn(text, *, url=None, callback_data=None):
    return SimpleNamespace(
        text=text, url=url, callback_data=callback_data, switch_inline_query=None
    )


def _msg(*, photo=None, markup=None):
    return SimpleNamespace(photo=photo, reply_markup=markup)


def test_media_type_photo_only():
    assert _message_media_type(_msg(photo=SimpleNamespace())) == "photo"
    assert _message_media_type(_msg(photo=None)) is None


def test_buttons_url_and_callback():
    markup = SimpleNamespace(
        inline_keyboard=[
            [
                _btn("官网", url="https://example.com"),
                _btn("开始", callback_data=b"start"),
            ],
            [_btn("帮助", callback_data="help")],
        ]
    )
    buttons = _message_buttons(_msg(markup=markup))
    assert buttons == [
        {
            "row": 0,
            "col": 0,
            "text": "官网",
            "kind": "url",
            "url": "https://example.com",
        },
        {"row": 0, "col": 1, "text": "开始", "kind": "callback", "url": None},
        {"row": 1, "col": 0, "text": "帮助", "kind": "callback", "url": None},
    ]


def test_buttons_skip_unsupported_and_empty():
    # switch_inline buttons cannot be pressed from the panel: not returned.
    markup = SimpleNamespace(
        inline_keyboard=[
            [
                SimpleNamespace(
                    text="分享", url=None, callback_data=None, switch_inline_query="x"
                )
            ]
        ]
    )
    assert _message_buttons(_msg(markup=markup)) == []
    assert _message_buttons(_msg(markup=None)) == []
    assert _message_buttons(_msg(markup=SimpleNamespace(inline_keyboard=[]))) == []
