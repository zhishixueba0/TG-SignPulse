"""SpamBot 限制检测（Batch 3，双向检测）：分类 + 回复抓取。

回归：分类必须开头优先——Sorry 开头的全限制回复里也可能出现 limit，
先按 limit 判会错判成 partial。James 2026-11-05 实测规则。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.services.telegram.spam_check import (
    bidirectional_of,
    classify_spam_reply,
    extract_until,
    fetch_spambot_reply,
)


class TestClassifySpamReply:
    def test_free_as_a_bird_is_free(self):
        status, _ = classify_spam_reply(
            "Good news, no limits are currently applied to your account. "
            "You're free as a bird!"
        )
        assert status == "free"

    def test_unfortunately_opening_is_limited(self):
        status, _ = classify_spam_reply(
            "Unfortunately, some actions on your account are limited."
        )
        assert status == "limited"

    def test_sorry_opening_is_banned(self):
        status, _ = classify_spam_reply(
            "Sorry, your account is limited because of spam."
        )
        assert status == "banned"

    def test_im_sorry_opening_is_banned(self):
        status, _ = classify_spam_reply("I'm sorry, but your account has been limited.")
        assert status == "banned"

    def test_sorry_reply_containing_limit_is_still_banned(self):
        # 开头优先的关键用例：Sorry 开头的全限制回复里也出现 limit，
        # 绝不能先按 limit 判成 partial。
        status, _ = classify_spam_reply(
            "Sorry, your account is currently limited due to spam reports. "
            "The limit will be lifted after review."
        )
        assert status == "banned"

    def test_limit_keyword_only_is_limited(self):
        status, _ = classify_spam_reply(
            "Your account has a limit on sending messages to strangers."
        )
        assert status == "limited"

    def test_blocked_keyword_is_banned(self):
        status, _ = classify_spam_reply("Your account has been blocked.")
        assert status == "banned"

    def test_empty_is_unknown(self):
        status, _ = classify_spam_reply("")
        assert status == "unknown"

    def test_unrelated_text_is_unknown(self):
        status, _ = classify_spam_reply("Hello, how can I help you?")
        assert status == "unknown"


class TestBidirectionalOf:
    def test_free_is_both(self):
        bidir, label = bidirectional_of("free")
        assert bidir == "both"
        assert label == "正常"

    def test_limited_is_partial(self):
        bidir, label = bidirectional_of("limited")
        assert bidir == "partial"
        assert label == "部分双向"

    def test_banned_is_mutual_only(self):
        bidir, label = bidirectional_of("banned")
        assert bidir == "mutual_only"
        assert label == "双向"

    def test_unknown_is_unknown(self):
        bidir, _ = bidirectional_of("unknown")
        assert bidir == "unknown"


class TestExtractUntil:
    def test_until_date(self):
        assert extract_until("limited until 2026-11-13") == "2026-11-13"

    def test_no_until_is_none(self):
        assert extract_until("free as a bird") is None
        assert extract_until("") is None


class FakeMessage:
    def __init__(self, text, out=False, date=None):
        self.text = text
        self.out = out
        self.date = date


class FakeClient:
    """模拟 Telethon 客户端：send_message 发 /start，get_messages 返回预设回复。"""

    def __init__(self, replies):
        self.replies = replies
        self.sent = None
        self.calls = 0

    async def send_message(self, peer, text):
        now = datetime.now(timezone.utc)
        self.sent = FakeMessage(text, out=True, date=now)
        return self.sent

    async def get_messages(self, peer, limit=5):
        self.calls += 1
        # 第 1 轮还没回复，第 2 轮 SpamBot 回了
        if self.calls < 2:
            return [self.sent]
        reply = FakeMessage(self.replies, out=False, date=datetime.now(timezone.utc))
        return [reply, self.sent]


class TestFetchSpambotReply:
    def test_returns_spambot_reply(self):
        client = FakeClient("Unfortunately, some actions are limited.")
        reply = asyncio.run(fetch_spambot_reply(client))
        assert reply == "Unfortunately, some actions are limited."

    def test_skips_outgoing_and_old_messages(self):
        client = FakeClient("free as a bird")
        reply = asyncio.run(fetch_spambot_reply(client))
        assert "free as a bird" in reply
