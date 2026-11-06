"""SpamBot 限制检测（Batch 3）：双向检测。

给 Telegram 官方 @SpamBot 发 /start，读它的回答，按开头分类，
判断账号能不能主动私聊陌生人（双向）。

分类规则（James 2026-11-05 实测 + 2026-11-06 确认，开头优先）：
- free as a bird / no limits / good news → free（正常，无限制）
- Unfortunately → limited（部分双向，只能给特定区域的用户随意互发）
- Sorry / I'm sorry 开头 → banned（双向，必须是双向联系人才可以互发）
- blocked / banned / terminated / 被封 → banned
- 含 limit / 限制 → limited（兜底）
- 其他 → unknown

注意：真实 SpamBot 回复开头常带问候语（"Hello {name}!"），"Unfortunately"
可能在第二段、"I'm very sorry" 只是客套（"很抱歉让你来找我"），所以：
- "unfortunately" 按全文匹配（部分限制的标志词），且在 "sorry" 之前检查——
  部分限制的回复里同时有 "I'm very sorry" 和 "Unfortunately"，先按 sorry
  判会错判成全限制；
- "sorry" 只看去掉问候语后的开头，避免把客套的 "I'm very sorry" 当成全限制。

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
    # 去掉开头的问候语段落（"Hello {name}!"），分类看后面的正文开头。
    stripped = low.strip()
    if stripped.startswith("hello"):
        parts = stripped.split("\n\n", 1)
        if len(parts) == 2:
            stripped = parts[1].strip()
    if (
        "no limits" in low
        or "free as a bird" in low
        or "good news" in low
        or "没有限制" in (text or "")
    ):
        return "free", "正常：无限制"
    # "unfortunately" 全文匹配且先于 sorry：部分限制回复里同时有
    # "I'm very sorry"（客套）和 "Unfortunately"（标志词）。
    if "unfortunately" in low:
        return "limited", "部分双向：只能给特定区域的用户随意互发"
    if (
        stripped.startswith("sorry")
        or stripped.startswith("i'm sorry")
        or stripped.startswith("i am sorry")
    ):
        return "banned", "双向：必须是双向联系人才可以互发"
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

    注意：fork 用 Pyrogram（Kurigram），取历史用 get_chat_history
    （异步生成器），不是 Telethon 的 get_messages(limit=)；判断
    发出方用 Message.outgoing，不是 Telethon 的 Message.out。
    """
    sent = await client.send_message(SPAMBOT_USERNAME, "/start")
    send_time = getattr(sent, "date", None)
    reply_text = ""
    for _ in range(FETCH_MAX_ROUNDS):
        await asyncio.sleep(FETCH_POLL_SECONDS)
        try:
            msgs = []
            async for m in client.get_chat_history(SPAMBOT_USERNAME, limit=5):
                msgs.append(m)
        except Exception as exc:
            logger.debug("读取 SpamBot 回复失败 %s: %s", SPAMBOT_USERNAME, exc)
            continue
        for m in msgs:
            # Pyrogram: Message.outgoing；Telethon: Message.out
            if hasattr(m, "outgoing"):
                is_out = bool(m.outgoing)
            else:
                is_out = bool(getattr(m, "out", False))
            if is_out:
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
