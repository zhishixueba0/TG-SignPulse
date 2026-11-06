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

    def test_real_limited_reply_with_greeting_is_limited(self):
        # 真实回复（2026-11-06，账号 12098814721）：开头是问候语，第二段
        # "I'm very sorry" 只是客套，标志词 "Unfortunately" 在后面。
        # 绝不能把客套的 sorry 当成全限制，也不能只靠 limit 兜底。
        status, _ = classify_spam_reply(
            "Hello Nora Hutton!\n\n"
            "I’m very sorry that you had to contact me. Unfortunately, "
            "some actions can trigger a harsh response from our anti-spam "
            "systems. If you think your account was limited by mistake, "
            "you can submit a complaint to our moderators.\n\n"
            "While the account is limited, you will not be able to send "
            "messages to people who do not have your number in their phone "
            "contacts or add them to groups and channels. Of course, when "
            "people contact you first, you can always reply to them."
        )
        assert status == "limited"

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
    def __init__(self, text, outgoing=False, date=None):
        self.text = text
        self.outgoing = outgoing
        self.date = date


class FakeClient:
    """模拟 Pyrogram 客户端：send_message 发 /start，get_chat_history 返回预设回复。"""

    def __init__(self, replies):
        self.replies = replies
        self.sent = None
        self.calls = 0

    async def send_message(self, peer, text):
        now = datetime.now(timezone.utc)
        self.sent = FakeMessage(text, outgoing=True, date=now)
        return self.sent

    async def get_chat_history(self, peer, limit=5):
        self.calls += 1
        # 第 1 轮还没回复，第 2 轮 SpamBot 回了
        if self.calls < 2:
            yield self.sent
            return
        reply = FakeMessage(
            self.replies, outgoing=False, date=datetime.now(timezone.utc)
        )
        yield reply
        yield self.sent


class TestFetchSpambotReply:
    def test_returns_spambot_reply(self):
        client = FakeClient("Unfortunately, some actions are limited.")
        reply = asyncio.run(fetch_spambot_reply(client))
        assert reply == "Unfortunately, some actions are limited."

    def test_skips_outgoing_and_old_messages(self):
        client = FakeClient("free as a bird")
        reply = asyncio.run(fetch_spambot_reply(client))
        assert "free as a bird" in reply
