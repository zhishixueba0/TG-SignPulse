"""SpamBot 限制检测（Batch 3）：双向检测。

给 Telegram 官方 @SpamBot 发 /start，读它的回答，按开头分类，
判断账号能不能主动私聊陌生人（双向）。

分类规则（James 2026-11-05 实测 + 2026-11-06 确认，开头优先）：
- free as a bird / no limits / good news → free（正常，无限制）
- Unfortunately 开头 → limited（部分双向，只能给特定区域的用户随意互发）
- Sorry / I'm sorry 开头 → banned（双向，必须是双向联系人才可以互发）
- blocked / banned / terminated / 被封 → banned
- 含 limit / 限制 → limited（兜底）
- 其他 → unknown

开头优先的原因：Sorry 开头的全限制回复里也可能出现 limit 这个词，
先按 limit 判会错判成 partial。

只和官方 @SpamBot 对话，不给任何陌生人发消息。get_me / UserFull 里
没有 spam 限制字段，MTProto 也没有只读的限制查询接口，@SpamBot 是
唯一可见渠道。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# @SpamBot 的用户 ID（Telegram 官方）
SPAMBOT_USERNAME = "spambot"

# 回复原文截断（存 profile 时）
REPLY_MAX_CHARS = 500
SUMMARY_MAX_CHARS = 200

# 轮询参数（telegram-manager 实测）：最多 8 轮 × 2 秒 = 16 秒
FETCH_MAX_ROUNDS = 8
FETCH_POLL_SECONDS = 2.0

# 全局锁：所有账号的 SpamBot 检查排队，绝不同时批量操作
_SPAMBOT_LOCK = asyncio.Lock()


def classify_spam_reply(text: str) -> tuple[str, str]:
    """按 SpamBot 回复开头分类。

    返回 (status, summary)：
    - status: "free" | "limited" | "banned" | "unknown"
    - summary: 简短中文说明
    """
    low = (text or "").lower()
    stripped = low.strip()
    if stripped.startswith("unfortunately"):
        return "limited", "部分双向：只能给特定区域的用户随意互发"
    if (
        stripped.startswith("sorry")
        or stripped.startswith("i'm sorry")
        or stripped.startswith("i am sorry")
    ):
        return "banned", "双向：必须是双向联系人才可以互发"
    if (
        "no limits" in low
        or "free as a bird" in low
        or "good news" in low
        or "没有限制" in (text or "")
    ):
        return "free", "正常：无限制"
    if (
        "blocked" in low
        or "banned" in low
        or "terminated" in low
        or "被封" in (text or "")
        or "已被封禁" in (text or "")
    ):
        return "banned", "双向：必须是双向联系人才可以互发"
    if "limit" in low or "限制" in (text or ""):
        return "limited", "部分双向：只能给特定区域的用户随意互发"
    return "unknown", "未知：SpamBot 回复里没有明确的限制关键词，请看原文判断"


def bidirectional_of(status: str) -> tuple[str, str]:
    """把 SpamBot 状态映射为双向能力（James 2026-11-06 定义）。

    返回 (bidirectional, label)：
    - free → both（正常，无限制）
    - limited → partial（部分双向，只能给特定区域的用户随意互发）
    - banned → mutual_only（双向，必须是双向联系人才可以互发）
    - unknown → unknown（未测）
    """
    if status == "free":
        return "both", "正常"
    if status == "limited":
        return "partial", "部分双向"
    if status == "banned":
        return "mutual_only", "双向"
    return "unknown", "未测"


def extract_until(text: str) -> str | None:
    """从 SpamBot 回复里提取解除日期（如果有）。

    匹配 until/till + 日期，或 直到/至 + 日期。只做尽力提取，
    没找到返回 None，绝不编造。
    """
    if not text:
        return None
    # 英文：until / till + 日期（如 until 2026-11-13, till November 13）
    m = re.search(
        r"(?:until|till)\s+([A-Za-z]+\s+\d{1,2}(?:,?\s*\d{4})?|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # 中文：直到 / 至 + 日期
    m = re.search(r"(?:直到|至)\s*(\d{4}[-年]\d{1,2}[-月]\d{1,2}日?)", text)
    if m:
        return m.group(1).strip()
    return None


async def fetch_spambot_reply(client: Any) -> str:
    """给 @SpamBot 发 /start，轮询取最新一条对方回复。

    跳过自己发出的消息、跳过发送时间之前的旧消息。
    16 秒内无回复返回空串。
    """
    sent = await client.send_message(SPAMBOT_USERNAME, "/start")
    send_time = getattr(sent, "date", None)
    reply_text = ""
    for _ in range(FETCH_MAX_ROUNDS):
        await asyncio.sleep(FETCH_POLL_SECONDS)
        try:
            msgs = await client.get_messages(SPAMBOT_USERNAME, limit=5)
        except Exception as exc:
            logger.debug("读取 SpamBot 回复失败 %s: %s", SPAMBOT_USERNAME, exc)
            continue
        for m in msgs:
            if getattr(m, "out", False):
                continue
            m_date = getattr(m, "date", None)
            if m_date and send_time and m_date < send_time:
                continue
            if getattr(m, "text", None):
                reply_text = m.text
                break
        if reply_text:
            break
    return reply_text
