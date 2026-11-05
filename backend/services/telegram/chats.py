"""TelegramService mixin: 聊天中心（对话列表 / 消息收发，仅文本）。

复用账号服务同款取客户端路径：_build_account_client + 账号锁；
对话与消息只读走 get_dialogs / get_chat_history，发消息走 send_message。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union

from backend.utils.account_locks import (
    AccountLockTimeout,
    acquire_account_lock_with_timeout,
)
from backend.utils.time import utc_from_timestamp_iso_z

logger = logging.getLogger("backend.telegram.chats")


def parse_chat_id(chat_id: Union[int, str]) -> Union[int, str]:
    """解析对话 id：数字（含负数群组 id）转 int，其余按用户名原样透传。"""
    if isinstance(chat_id, int):
        return chat_id
    raw = str(chat_id or "").strip()
    if not raw:
        raise ValueError("chat_id is required")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw.lstrip("@")


def _dialog_title(chat: Any) -> str:
    title = getattr(chat, "title", None) or ""
    if title:
        return str(title)
    first = getattr(chat, "first_name", None) or ""
    last = getattr(chat, "last_name", None) or ""
    full = f"{first} {last}".strip()
    if full:
        return full
    username = getattr(chat, "username", None) or ""
    if username:
        return str(username)
    return str(getattr(chat, "id", ""))


def _message_text(msg: Any) -> str:
    return getattr(msg, "text", None) or getattr(msg, "caption", None) or ""


def _message_date_iso(msg: Any) -> Optional[str]:
    date = getattr(msg, "date", None)
    if date is None:
        return None
    try:
        return utc_from_timestamp_iso_z(int(date.timestamp()))
    except Exception:
        return None


def _sender_name(msg: Any) -> str:
    from_user = getattr(msg, "from_user", None)
    if from_user is not None:
        first = getattr(from_user, "first_name", None) or ""
        last = getattr(from_user, "last_name", None) or ""
        full = f"{first} {last}".strip()
        if full:
            return full
        username = getattr(from_user, "username", None)
        if username:
            return str(username)
        return str(getattr(from_user, "id", ""))
    sender_chat = getattr(msg, "sender_chat", None)
    if sender_chat is not None:
        return _dialog_title(sender_chat)
    return ""


class TelegramChatsMixin:
    async def list_account_dialogs(
        self,
        account_name: str,
        limit: int = 50,
        timeout_seconds: float = 12.0,
    ) -> List[Dict[str, Any]]:
        """最近对话列表（默认 50 条）：标题/类型/未读/最后消息摘要与时间。"""
        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        limit = max(1, min(int(limit or 50), 100))
        timeout_seconds = max(1.0, min(float(timeout_seconds or 12.0), 30.0))

        async def _read_dialogs() -> List[Dict[str, Any]]:
            dialogs: List[Dict[str, Any]] = []
            async for dialog in client.get_dialogs(limit=limit):
                chat = getattr(dialog, "chat", None)
                if chat is None:
                    continue
                chat_id = getattr(chat, "id", None)
                if chat_id is None:
                    continue
                top = getattr(dialog, "top_message", None)
                chat_type = getattr(chat, "type", None)
                dialogs.append(
                    {
                        "chat_id": chat_id,
                        "title": _dialog_title(chat),
                        "type": (
                            str(getattr(chat_type, "name", chat_type)).lower()
                            if chat_type is not None
                            else ""
                        ),
                        "username": getattr(chat, "username", None),
                        "unread_count": int(
                            getattr(dialog, "unread_messages_count", 0) or 0
                        ),
                        "last_message_text": _message_text(top) if top else "",
                        "last_message_date": _message_date_iso(top) if top else None,
                    }
                )
            return dialogs

        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                async with client:
                    return await asyncio.wait_for(
                        _read_dialogs(), timeout=timeout_seconds
                    )
        except AccountLockTimeout as e:
            logger.warning("读取对话列表获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e

    async def list_chat_messages(
        self,
        account_name: str,
        chat_id: Union[int, str],
        limit: int = 50,
        timeout_seconds: float = 12.0,
    ) -> List[Dict[str, Any]]:
        """最近消息（默认 50 条，按时间升序返回，供消息窗自上而下展示）。"""
        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")
        target = parse_chat_id(chat_id)

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        limit = max(1, min(int(limit or 50), 100))
        timeout_seconds = max(1.0, min(float(timeout_seconds or 12.0), 30.0))

        async def _read_messages() -> List[Dict[str, Any]]:
            messages: List[Dict[str, Any]] = []
            async for msg in client.get_chat_history(target, limit=limit):
                from_user = getattr(msg, "from_user", None)
                messages.append(
                    {
                        "id": getattr(msg, "id", None),
                        "date": _message_date_iso(msg),
                        "text": _message_text(msg),
                        "outgoing": bool(getattr(msg, "outgoing", False)),
                        "sender_name": _sender_name(msg),
                        "sender_id": (
                            getattr(from_user, "id", None)
                            if from_user is not None
                            else None
                        ),
                    }
                )
            # get_chat_history 最新在前；消息窗按时间升序展示
            messages.reverse()
            return messages

        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                async with client:
                    return await asyncio.wait_for(
                        _read_messages(), timeout=timeout_seconds
                    )
        except AccountLockTimeout as e:
            logger.warning("读取消息获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e

    async def send_chat_message(
        self,
        account_name: str,
        chat_id: Union[int, str],
        text: str,
        timeout_seconds: float = 12.0,
    ) -> Dict[str, Any]:
        """发一条文本消息（只发用户在输入框里写的文本，不做媒体/转发）。"""
        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")
        target = parse_chat_id(chat_id)
        content = (text or "").strip()
        if not content:
            raise ValueError("消息内容不能为空")
        if len(content) > 4096:
            raise ValueError("消息内容过长（最多 4096 字符）")

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        timeout_seconds = max(1.0, min(float(timeout_seconds or 12.0), 30.0))
        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                async with client:
                    sent = await asyncio.wait_for(
                        client.send_message(target, content),
                        timeout=timeout_seconds,
                    )
        except AccountLockTimeout as e:
            logger.warning("发送消息获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e

        return {
            "id": getattr(sent, "id", None),
            "date": _message_date_iso(sent),
            "text": _message_text(sent) or content,
            "outgoing": True,
            "sender_name": _sender_name(sent),
            "sender_id": None,
        }
