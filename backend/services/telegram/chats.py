"""TelegramService mixin: 聊天中心（对话列表 / 消息收发）。

复用账号服务同款取客户端路径：_build_account_client + 账号锁；
对话与消息只读走 get_dialogs / get_chat_history，发消息走 send_message。
键盘分两种：内联键盘（url / callback，点按走 request_callback_answer），
回复键盘（纯文本按钮，点按即把按钮文案当文本消息发出）。
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


def _message_buttons(msg: Any) -> list:
    """内联键盘按钮（Drive 1.0.129 press_message_button 同款语义）。

    只取可操作的两种：带 url 的链接按钮与带 callback_data 的回调按钮；
    其余类型（switch_inline 等）面板内无法执行，不返回。
    回复键盘（ReplyKeyboardMarkup）不在这里处理：它的按钮是纯文本，
    点按即当作普通文本消息发出，走 send_chat_message，不走点按接口。
    """
    markup = getattr(msg, "reply_markup", None)
    rows = getattr(markup, "inline_keyboard", None) if markup is not None else None
    if not rows:
        return []
    buttons = []
    for row_idx, row in enumerate(rows):
        for col_idx, button in enumerate(row or []):
            text = getattr(button, "text", None) or ""
            url = getattr(button, "url", None)
            callback_data = getattr(button, "callback_data", None)
            if url:
                buttons.append(
                    {
                        "row": row_idx,
                        "col": col_idx,
                        "text": str(text),
                        "kind": "url",
                        "url": str(url),
                    }
                )
            elif callback_data is not None:
                buttons.append(
                    {
                        "row": row_idx,
                        "col": col_idx,
                        "text": str(text),
                        "kind": "callback",
                        "url": None,
                    }
                )
    return buttons


def _message_reply_keyboard(msg: Any) -> list:
    """回复键盘按钮（ReplyKeyboardMarkup，如 SpamBot 的选项）。

    只取纯文本按钮：点按即把按钮文案当作普通文本消息发出。
    带索取项的按钮（request_contact / request_location / request_poll）
    面板内无法执行，不返回。
    """
    markup = getattr(msg, "reply_markup", None)
    rows = getattr(markup, "keyboard", None) if markup is not None else None
    if not rows:
        return []
    buttons = []
    for row_idx, row in enumerate(rows):
        for col_idx, button in enumerate(row or []):
            if (
                getattr(button, "request_contact", None)
                or getattr(button, "request_location", None)
                or getattr(button, "request_poll", None)
            ):
                continue
            # 真实 Pyrogram 是 KeyboardButton 对象（.text）；测试替身
            # 有时直接用纯字符串行，两种都按文本按钮处理。
            text = (
                button
                if isinstance(button, str)
                else getattr(button, "text", None) or ""
            )
            if not str(text).strip():
                continue
            buttons.append(
                {
                    "row": row_idx,
                    "col": col_idx,
                    "text": str(text),
                    "kind": "reply",
                    "url": None,
                }
            )
    return buttons


def _message_keyboard_type(msg: Any) -> Optional[str]:
    """消息键盘类型：有可渲染按钮时返回 "inline" 或 "reply"，否则 None。"""
    if _message_reply_keyboard(msg):
        return "reply"
    if _message_buttons(msg):
        return "inline"
    return None


def _message_media_type(msg: Any) -> Optional[str]:
    """目前只做图片：带 photo 即为图片消息；视频/文件以后再说。"""
    if getattr(msg, "photo", None) is not None:
        return "photo"
    return None


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
                        "media_type": _message_media_type(msg),
                        "keyboard_type": _message_keyboard_type(msg),
                        "buttons": _message_buttons(msg) + _message_reply_keyboard(msg),
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
            "media_type": None,
            "keyboard_type": None,
            "buttons": [],
        }

    async def download_chat_media(
        self,
        account_name: str,
        chat_id: Union[int, str],
        message_id: int,
        thumb: bool = True,
        timeout_seconds: float = 30.0,
    ) -> bytes:
        """下载一条图片消息：thumb=True 取最小缩略图，否则取原图。

        只做图片（视频/文件以后再说）；无图的消息返回 404 语义的
        ValueError("MEDIA_NOT_FOUND")。返回 JPEG 字节。
        """
        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")
        target = parse_chat_id(chat_id)
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            raise ValueError("MEDIA_NOT_FOUND") from None

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        timeout_seconds = max(1.0, min(float(timeout_seconds or 30.0), 60.0))

        async def _download() -> bytes:
            msg = await client.get_messages(target, message_id)
            if msg is None or getattr(msg, "photo", None) is None:
                raise ValueError("MEDIA_NOT_FOUND")
            if thumb:
                thumbs = list(getattr(msg.photo, "thumbs", None) or [])
                thumbs = [t for t in thumbs if getattr(t, "file_size", None)]
                if not thumbs:
                    raise ValueError("MEDIA_NOT_FOUND")
                smallest = min(thumbs, key=lambda t: int(t.file_size or 0))
                data = await client.download_media(smallest, in_memory=True)
            else:
                data = await client.download_media(msg, in_memory=True)
            if data is None:
                raise ValueError("MEDIA_NOT_FOUND")
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            read = getattr(data, "read", None)
            if callable(read):
                return bytes(read())
            raise ValueError("MEDIA_NOT_FOUND")

        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                async with client:
                    return await asyncio.wait_for(_download(), timeout=timeout_seconds)
        except AccountLockTimeout as e:
            logger.warning("下载图片获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e

    async def press_chat_button(
        self,
        account_name: str,
        chat_id: Union[int, str],
        message_id: int,
        row: int,
        col: int,
        timeout_seconds: float = 12.0,
    ) -> Dict[str, Any]:
        """点一条消息的内联键盘按钮（Drive 1.0.129 press_message_button 语义）。

        链接按钮只返回 url（由前端打开，不代点）；回调按钮调
        request_callback_answer（按按钮的 callback_data 发送，不按文本
        发送），返回 Bot 的回答文本/是否弹窗/跳转链接。只操作用户点的
        这一个按钮，不批量、不代发其他内容。
        回复键盘按钮正常由前端直接调发消息接口（点按即把按钮文案
        当文本发出去）；旧版前端没有回复分支、误把回复按钮发到这里时，
        这里代发按钮文案并返回 action="sent"，而不是只报 BUTTON_NOT_FOUND。
        """
        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")
        target = parse_chat_id(chat_id)
        try:
            message_id = int(message_id)
            row = int(row)
            col = int(col)
        except (TypeError, ValueError):
            raise ValueError("BUTTON_NOT_FOUND") from None

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        timeout_seconds = max(1.0, min(float(timeout_seconds or 12.0), 30.0))

        async def _press() -> Dict[str, Any]:
            msg = await client.get_messages(target, message_id)
            if msg is None:
                raise ValueError("BUTTON_NOT_FOUND")
            buttons = _message_buttons(msg)
            match = next(
                (b for b in buttons if b["row"] == row and b["col"] == col),
                None,
            )
            if match is None:
                # 回复键盘兜底：旧版前端没有回复分支，会把回复按钮发到
                # 点按接口；点回复按钮的语义就是把文案当文本发出去，
                # 这里直接代发，旧客户端点按后不再只看到 BUTTON_NOT_FOUND。
                # 注意：已持有账号锁，不要调 send_chat_message（会重入取锁），
                # 直接用已打开的 client 发送。
                reply_match = next(
                    (
                        b
                        for b in _message_reply_keyboard(msg)
                        if b["row"] == row and b["col"] == col
                    ),
                    None,
                )
                if reply_match is None:
                    raise ValueError("BUTTON_NOT_FOUND")
                content = (reply_match["text"] or "").strip()
                if not content or len(content) > 4096:
                    raise ValueError("BUTTON_NOT_FOUND")
                await client.send_message(target, content)
                return {"action": "sent", "message": ""}
            if match["kind"] == "url":
                return {"action": "url", "url": match["url"]}
            # 回调按钮：取原始 callback_data（_message_buttons 只保留了
            # kind，原始值从 reply_markup 再取一次，避免转义失真）。
            markup = getattr(msg, "reply_markup", None)
            rows = getattr(markup, "inline_keyboard", None) or []
            try:
                raw_button = rows[row][col]
            except (IndexError, TypeError):
                raise ValueError("BUTTON_NOT_FOUND") from None
            callback_data = getattr(raw_button, "callback_data", None)
            if callback_data is None:
                raise ValueError("BUTTON_NOT_FOUND")
            answer = await client.request_callback_answer(
                target, message_id, callback_data
            )
            return {
                "action": "callback",
                "message": getattr(answer, "message", None) or "",
                "alert": bool(getattr(answer, "alert", False)),
                "url": getattr(answer, "url", None),
            }

        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                async with client:
                    return await asyncio.wait_for(_press(), timeout=timeout_seconds)
        except AccountLockTimeout as e:
            logger.warning("点按钮获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e
