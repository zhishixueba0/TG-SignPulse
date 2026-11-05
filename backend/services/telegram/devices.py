"""TelegramService mixin: devices."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from backend.core.config import get_settings
from backend.utils.account_locks import (
    AccountLockTimeout,
    acquire_account_lock_with_timeout,
)
from backend.utils.time import utc_from_timestamp_iso_z

settings = get_settings()


logger = logging.getLogger("backend.telegram.devices")

# 会话保留时长（Telegram 原生 TTL，Drive 1.0.129 session_tools 同款）：
# authorization_ttl_days = 闲置会话在服务端保留天数，到期自动终止；
# account_ttl_days = 账号闲置天数，到期账号自动删除。面板只负责设置与
# 读取，执行在 Telegram 服务端，不设每日定时删除任务。
AUTHORIZATION_TTL_RANGE = (7, 365)
ACCOUNT_TTL_RANGE = (30, 730)


class TelegramDevicesMixin:
    async def verify_account_proxy(
        self, account_name: str, proxy_dict: Optional[dict] = None
    ) -> None:
        """存根方法，实际逻辑由 TelegramAccountsMixin 或子类实现。"""
        pass

    async def list_account_devices(
        self,
        account_name: str,
        timeout_seconds: float = 12.0,
    ) -> List[Dict[str, Any]]:
        """列出账号当前 Telegram 已登录设备/授权会话。"""
        from pyrogram import raw

        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        timeout_seconds = max(1.0, min(float(timeout_seconds or 12.0), 30.0))
        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                # 客户端连接纳入账号锁内：引用计数共享，出栈即断开，
                # 避免锁释放后仍持有连接（异常路径会泄漏，也可能被误停）
                async with client:
                    result = await asyncio.wait_for(
                        client.invoke(raw.functions.account.GetAuthorizations()),
                        timeout=timeout_seconds,
                    )
        except AccountLockTimeout as e:
            logger.warning("获取设备列表获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e

        devices = []
        for item in getattr(result, "authorizations", []) or []:
            devices.append(
                {
                    "hash": str(getattr(item, "hash", "")),
                    "current": bool(getattr(item, "current", False)),
                    "official_app": bool(getattr(item, "official_app", False)),
                    "password_pending": bool(getattr(item, "password_pending", False)),
                    "device_model": getattr(item, "device_model", "") or "",
                    "platform": getattr(item, "platform", "") or "",
                    "system_version": getattr(item, "system_version", "") or "",
                    "app_name": getattr(item, "app_name", "") or "",
                    "app_version": getattr(item, "app_version", "") or "",
                    "date_created": utc_from_timestamp_iso_z(
                        getattr(item, "date_created", 0)
                    )
                    if getattr(item, "date_created", None)
                    else None,
                    "date_active": utc_from_timestamp_iso_z(
                        getattr(item, "date_active", 0)
                    )
                    if getattr(item, "date_active", None)
                    else None,
                    "ip": getattr(item, "ip", "") or "",
                    "country": getattr(item, "country", "") or "",
                    "region": getattr(item, "region", "") or "",
                }
            )
        return devices

    async def terminate_account_device(
        self,
        account_name: str,
        auth_hash: int | str,
        timeout_seconds: float = 12.0,
    ) -> bool:
        """踢下线指定 Telegram 授权会话。不能踢当前正在使用的会话。"""
        from pyrogram import raw

        try:
            parsed_hash = int(auth_hash)
        except (TypeError, ValueError):
            raise ValueError("无效的授权 hash")

        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")

        devices = await self.list_account_devices(
            account_name, timeout_seconds=timeout_seconds
        )
        target = next(
            (d for d in devices if str(d.get("hash")) == str(auth_hash)), None
        )
        if not target:
            raise ValueError("设备不存在或已下线")
        if target.get("current"):
            raise ValueError("不能踢下线当前正在使用的会话")

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        timeout_seconds = max(1.0, min(float(timeout_seconds or 12.0), 30.0))
        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                # 客户端连接纳入账号锁内，出栈即断开，见 list_account_devices
                async with client:
                    result = await asyncio.wait_for(
                        client.invoke(
                            raw.functions.account.ResetAuthorization(hash=parsed_hash)
                        ),
                        timeout=timeout_seconds,
                    )
            return bool(result)
        except AccountLockTimeout as e:
            logger.warning("踢下线设备获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e

    async def reset_account_authorizations(
        self,
        account_name: str,
        timeout_seconds: float = 12.0,
    ) -> bool:
        """
        一键清退账号除当前面板外的所有已授权设备与会话。
        在同一次 acquire_account_lock_with_timeout 获取内完成。
        调用 raw.functions.account.ResetAuthorizations() / raw.functions.auth.ResetAuthorizations()。
        """
        from pyrogram import raw

        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        timeout_seconds = max(1.0, min(float(timeout_seconds or 12.0), 30.0))
        reset_rpc_cls = getattr(
            raw.functions.account, "ResetAuthorizations", None
        ) or getattr(raw.functions.auth, "ResetAuthorizations", None)
        if not reset_rpc_cls:
            raise RuntimeError(
                "ResetAuthorizations RPC function not found in Pyrogram raw definitions"
            )

        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                # 客户端连接纳入账号锁内，出栈即断开，见 list_account_devices
                async with client:
                    result = await asyncio.wait_for(
                        client.invoke(reset_rpc_cls()),
                        timeout=timeout_seconds,
                    )
            return bool(result)
        except AccountLockTimeout as e:
            logger.warning("清退其他设备获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e
        except Exception as e:
            err_str = str(e).upper()
            if (
                "FRESH_RESET_AUTHORISATION_FORBIDDEN" in err_str
                or getattr(e, "CODE", None) == 406
                or getattr(e, "code", None) == 406
            ):
                raise ValueError(
                    "FRESH_RESET_AUTHORISATION_FORBIDDEN: 新登录会话在初始保护期内无法重置其他设备，请在几小时后再试"
                ) from e
            raise

    async def get_session_settings(
        self,
        account_name: str,
        timeout_seconds: float = 20.0,
    ) -> Dict[str, Any]:
        """读取账号的 Telegram 原生保留时长（GetAuthorizations + GetAccountTTL）。

        返回 {"authorization_ttl_days": int|None, "account_ttl_days": int|None}。
        """
        from pyrogram import raw

        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        timeout_seconds = max(1.0, min(float(timeout_seconds or 20.0), 30.0))
        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                async with client:
                    authorizations = await asyncio.wait_for(
                        client.invoke(raw.functions.account.GetAuthorizations()),
                        timeout=timeout_seconds,
                    )
                    account_ttl = await asyncio.wait_for(
                        client.invoke(raw.functions.account.GetAccountTTL()),
                        timeout=timeout_seconds,
                    )
        except AccountLockTimeout as e:
            logger.warning("读取会话保留时长获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e

        authorization_ttl_days = getattr(authorizations, "authorization_ttl_days", None)
        account_ttl_days = getattr(account_ttl, "days", None)
        return {
            "authorization_ttl_days": (
                int(authorization_ttl_days)
                if authorization_ttl_days is not None
                else None
            ),
            "account_ttl_days": (
                int(account_ttl_days) if account_ttl_days is not None else None
            ),
        }

    async def set_session_settings(
        self,
        account_name: str,
        *,
        authorization_ttl_days: Optional[int] = None,
        account_ttl_days: Optional[int] = None,
        timeout_seconds: float = 20.0,
    ) -> Dict[str, Any]:
        """设置账号的 Telegram 原生保留时长并重读返回。

        两项都空 → SESSION_SETTINGS_EMPTY；超范围 → *_TTL_INVALID。
        SetAuthorizationTTL / SetAccountTTL 由 Telegram 服务端执行，
        面板不做定时删除。
        """
        from pyrogram import raw

        if authorization_ttl_days is None and account_ttl_days is None:
            raise ValueError("SESSION_SETTINGS_EMPTY")
        if authorization_ttl_days is not None:
            low, high = AUTHORIZATION_TTL_RANGE
            if not low <= int(authorization_ttl_days) <= high:
                raise ValueError("AUTHORIZATION_TTL_INVALID")
        if account_ttl_days is not None:
            low, high = ACCOUNT_TTL_RANGE
            if not low <= int(account_ttl_days) <= high:
                raise ValueError("ACCOUNT_TTL_INVALID")

        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        timeout_seconds = max(1.0, min(float(timeout_seconds or 20.0), 30.0))
        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                async with client:
                    if authorization_ttl_days is not None:
                        await asyncio.wait_for(
                            client.invoke(
                                raw.functions.account.SetAuthorizationTTL(
                                    authorization_ttl_days=int(authorization_ttl_days)
                                )
                            ),
                            timeout=timeout_seconds,
                        )
                    if account_ttl_days is not None:
                        await asyncio.wait_for(
                            client.invoke(
                                raw.functions.account.SetAccountTTL(
                                    ttl=raw.types.AccountDaysTTL(
                                        days=int(account_ttl_days)
                                    )
                                )
                            ),
                            timeout=timeout_seconds,
                        )
        except AccountLockTimeout as e:
            logger.warning("设置会话保留时长获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e

        # 写完重读一次返回（1.0.129 同款）
        return await self.get_session_settings(
            account_name, timeout_seconds=timeout_seconds
        )

    async def list_official_messages(
        self,
        account_name: str,
        limit: int = 20,
        timeout_seconds: float = 12.0,
    ) -> List[Dict[str, Any]]:
        """读取账号与 Telegram 官方服务号 777000 的最近消息。"""
        account_name = self._normalize_account_name(account_name)
        if not self.account_exists(account_name):
            raise ValueError("账号不存在")

        client, proxy_dict = self._build_account_client(account_name, no_updates=True)
        await self.verify_account_proxy(account_name, proxy_dict)

        limit = max(1, min(int(limit or 20), 50))
        timeout_seconds = max(1.0, min(float(timeout_seconds or 12.0), 30.0))

        async def _read_messages() -> List[Dict[str, Any]]:
            messages: List[Dict[str, Any]] = []
            async for msg in client.get_chat_history(777000, limit=limit):
                text = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
                messages.append(
                    {
                        "id": getattr(msg, "id", None),
                        "date": utc_from_timestamp_iso_z(int(msg.date.timestamp()))
                        if getattr(msg, "date", None)
                        else None,
                        "text": text,
                        "outgoing": bool(getattr(msg, "outgoing", False)),
                    }
                )
            return messages

        try:
            async with acquire_account_lock_with_timeout(
                account_name, timeout=timeout_seconds
            ):
                # 客户端连接纳入账号锁内，出栈即断开，见 list_account_devices
                async with client:
                    return await asyncio.wait_for(
                        _read_messages(), timeout=timeout_seconds
                    )
        except AccountLockTimeout as e:
            logger.warning("读取官方消息获取锁超时 %s: %s", account_name, e)
            raise AccountLockTimeout("ACCOUNT_BUSY") from e
