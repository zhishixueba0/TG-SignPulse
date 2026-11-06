"""SpamBot 限制检测（Batch 3）：双向检测。

给 Telegram 官方 @SpamBot 发 /start，读它的回答，按开头分类，
判断账号能不能主动私聊陌生人（双向）。

分类规则（James 2026-11-06 给的三条原文模板，全账户通用，不含问候语）：
- 正常："Good news, no limits are currently applied to your account.
  You're free as a bird!" → free（正常，无限制）
- 部分双向："Unfortunately, some phone numbers may trigger a harsh response
  from our anti-spam systems. ... subscribe to Telegram Premium to get
  less strict limits." → limited（部分双向）
- 完全限制（双向）："I'm very sorry that you had to contact me.
  Unfortunately, some actions can trigger a harsh response ... While the
  account is limited, you will not be able to send messages to people who
  do not have your number in their phone contacts ..." → banned（双向）
- 已冻结："Your account was blocked for violations of the Telegram Terms
  of Service based on user reports confirmed by our moderators."
  → frozen（已冻结，第四类，James 2026-11-06 提出，网上实测文案）。
  冻结号能给 SpamBot 发消息，靠这句文案判；调不允许的接口时报
  FROZEN_METHOD_INVALID / FROZEN_PARTICIPANT_MISSING（兜底）。

"Unfortunately" 两条受限模板里都有，不能只靠它区分部分还是完全，
要靠标志句：
- "will not be able to send messages to people who do not have your
  number"（只能发给有你号码的人）→ 完全限制，先检查；
- "some phone numbers" / "Telegram Premium ... less strict limits" → 部分双向；
- 兜底才看开头：Unfortunately 开头 → 部分，Sorry 开头 → 完全。

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

# 冻结号调不允许的接口时报的 Pyrogram 错误（按类名判，不 import
# Pyrogram 也能用）。注意：冻结号能给 SpamBot 发消息，所以发 /start
# 不会因此失败；冻结主要靠 SpamBot 回复里的 blocked 文案来判，这里
# 是调其他接口时报 FROZEN 错误的兜底。UserDeactivated 是号被删/注销，
# 不是冻结，不用。
FROZEN_ERROR_NAMES = frozenset({"FrozenMethodInvalid", "FrozenParticipantMissing"})


def is_frozen_error(exc: BaseException) -> bool:
    """是不是冻结号调不允许的接口报的 FROZEN 错误。"""
    if type(exc).__name__ in FROZEN_ERROR_NAMES:
        return True
    text = str(exc) or ""
    return "FROZEN_METHOD_INVALID" in text or "FROZEN_PARTICIPANT_MISSING" in text


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
    if (
        "no limits" in low
        or "free as a bird" in low
        or "good news" in low
        or "没有限制" in (text or "")
    ):
        return "free", "正常：无限制"
    # 冻结：SpamBot 回 "Your account was blocked for violations of the
    # Telegram Terms of Service based on user reports confirmed by our
    # moderators."（James 2026-11-06：第四类；网上实测文案）。
    # 必须在通用 blocked 检查之前，因为这句里也有 blocked。
    if (
        "blocked for violations of the telegram terms of service" in low
        or "your account was blocked for violations" in low
    ):
        return "frozen", "已冻结：账号已被冻结，无法发送任何消息"
    # 完全限制的标志句：只能发给有你号码的人（必须是双向联系人）。
    # 先于 "unfortunately" 检查，因为完全限制模板里也有 Unfortunately。
    if "will not be able to send messages to people who do not have your number" in low:
        return "banned", "双向：必须是双向联系人才可以互发"
    # 部分双向的标志：phone numbers 触发 + Premium 可放宽。
    if "some phone numbers" in low or (
        "telegram premium" in low and "less strict" in low
    ):
        return "limited", "部分双向：只能给特定区域的用户随意互发"
    if stripped.startswith("unfortunately"):
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
    - frozen → frozen（已冻结，无法发送任何消息）
    - unknown → unknown（未测）
    """
    if status == "free":
        return "both", "正常"
    if status == "limited":
        return "partial", "部分双向"
    if status == "banned":
        return "mutual_only", "双向"
    if status == "frozen":
        return "frozen", "已冻结"
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
