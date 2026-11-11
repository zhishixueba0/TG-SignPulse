from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from backend.core.config import get_settings
from backend.utils.atomic_io import write_text_atomic
from backend.utils.names import natural_sort_key
from backend.utils.time import utc_now, utc_now_iso

# session_string 校验与格式常量统一收敛到 tg_signer.compat（全项目唯一判定入口），
# 面板与 CLI 两条加载路径共享同一判定，避免同串不同判。
# 常量见 pyrogram/storage/storage.py：旧格式无 api_id，新格式含 api_id，均无版本前缀。
from tg_signer.compat import _SESSION_STRING_FORMAT, is_valid_session_string

_logger = logging.getLogger("backend.tg_session")

_SESSION_MODE_ENV = "TG_SESSION_MODE"
_SESSION_MODE_FILE = "file"
_SESSION_MODE_STRING = "string"

_GLOBAL_SEMAPHORE: Optional[asyncio.Semaphore] = None


def get_session_mode() -> str:
    mode = os.getenv(_SESSION_MODE_ENV, _SESSION_MODE_FILE).strip().lower()
    return _SESSION_MODE_STRING if mode == _SESSION_MODE_STRING else _SESSION_MODE_FILE


def is_string_session_mode() -> bool:
    return get_session_mode() == _SESSION_MODE_STRING


def get_global_semaphore() -> asyncio.Semaphore:
    global _GLOBAL_SEMAPHORE
    if _GLOBAL_SEMAPHORE is None:
        limit = _resolve_concurrency_limit()
        _GLOBAL_SEMAPHORE = asyncio.Semaphore(limit)
    return _GLOBAL_SEMAPHORE


async def mark_client_online(client: Any, account_name: str = "") -> bool:
    """登录瞬间显式向 Telegram 刷一次在线状态（best-effort，失败不影响登录）。

    只在客户端已连接时调用（update_status(offline=False) 即 account.UpdateStatus）；
    调用后由调用方按原流程断开连接，服务器会把最后上线时间记为断开时刻。
    连接本身已能让服务器看到在线，但短连接有时来不及登记，显式发一次更稳。
    """
    try:
        if not getattr(client, "is_connected", False):
            return False
        update_status = getattr(client, "update_status", None)
        if update_status is None:
            return False
        await asyncio.wait_for(update_status(offline=False), timeout=10)
        return True
    except Exception as exc:
        _logger.debug("刷新在线状态失败 %s: %s", account_name or "?", exc)
        return False


def _resolve_concurrency_limit() -> int:
    # Priority: env var > global settings > default 1
    raw = (os.getenv("TG_GLOBAL_CONCURRENCY") or "").strip()
    if raw:
        try:
            return max(int(raw), 1)
        except ValueError:
            pass
    try:
        from backend.services.config import get_config_service

        settings = get_config_service().get_global_settings()
        val = settings.get("tg_global_concurrency")
        if val is not None:
            return max(int(val), 1)
    except Exception:
        pass
    # 默认：根据 CPU 核心数动态计算，上限为 5
    return min(os.cpu_count() or 4, 5)


def update_global_semaphore(new_limit: int) -> None:
    """Update the global semaphore with a new concurrency limit at runtime."""
    global _GLOBAL_SEMAPHORE
    if new_limit < 1:
        new_limit = 1
    _GLOBAL_SEMAPHORE = asyncio.Semaphore(new_limit)


def _account_store_path() -> Path:
    settings = get_settings()
    session_dir = settings.resolve_session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "accounts.json"


# accounts.json 进程内缓存：账号列表/状态/会话读取散落在登录、签到、
# 监听与状态检查等热路径，短窗口内复用避免每次请求全量读盘；
# 写入路径 _save_account_store 显式失效，保证读写一致
_ACCOUNT_STORE_CACHE_TTL = 2.0
_account_store_cache: dict[str, tuple[float, dict]] = {}


def _cached_account_store() -> dict:
    """带 2s TTL 的账户存储读取；TTL 与全局设置缓存策略一致。"""
    path = _account_store_path()
    key = str(path)
    now = time.monotonic()
    hit = _account_store_cache.get(key)
    if hit is not None and now - hit[0] < _ACCOUNT_STORE_CACHE_TTL:
        return hit[1]
    data = _read_account_store_file(path)
    _account_store_cache[key] = (now, data)
    return data


def _read_account_store_file(path: Path) -> dict:
    if not path.exists():
        return {"accounts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # 损坏的 accounts.json 若静默返回空表，后续 _save_account_store 会用空数据
        # 原子覆盖原文件，导致账号存储数据不可恢复地丢失。这里先把损坏文件改名
        # 备份保留，再告警，供人工恢复或排查。
        try:
            # 时间戳用于文件名，不能含冒号等路径不友好字符，改用纯数字格式
            stamp = utc_now().strftime("%Y%m%dT%H%M%S")
            backup = path.with_name(f"accounts.json.corrupt-{stamp}")
            path.replace(backup)
        except OSError:
            backup = path
        _logger.warning(
            "accounts.json 读取失败，已备份到 %s 并回退为空存储: %s",
            backup,
            exc,
        )
        return {"accounts": {}}
    if not isinstance(data, dict):
        return {"accounts": {}}
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        data["accounts"] = {}
    return data


def _load_account_store() -> dict:
    return _cached_account_store()


def _save_account_store(data: dict) -> None:
    # 统一走 write_json_atomic：进程内写锁 + 唯一临时名 + fsync + rename，
    # 避免多协程/线程并发写固定 .tmp 文件时交错覆盖导致 accounts.json 损坏
    from backend.utils.atomic_io import write_json_atomic

    path = _account_store_path()
    write_json_atomic(path, data)
    # 写后失效缓存，下次读取重新落盘
    _account_store_cache.pop(str(path), None)


def list_account_names() -> list[str]:
    data = _load_account_store()
    return sorted(data["accounts"].keys(), key=natural_sort_key)


def get_account_session_string(account_name: str) -> Optional[str]:
    data = _load_account_store()
    entry = data.get("accounts", {}).get(account_name)
    if not isinstance(entry, dict):
        return None
    session_string = entry.get("session_string")
    if isinstance(session_string, str) and session_string.strip():
        cleaned = session_string.strip()
        from tg_signer.security import (
            SecretKeyError,
            decrypt_secret,
            is_encrypted_secret,
        )

        if is_encrypted_secret(cleaned):
            try:
                cleaned = decrypt_secret(cleaned) or ""
            except SecretKeyError:
                raise
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to decrypt session_string for account {account_name}"
                ) from exc
        # 非法串（含历史错误导出）视为不存在，便于调用方回退到文件导出
        if is_valid_session_string(cleaned):
            return cleaned
    return None


def get_account_storage_id(account_name: str) -> str:
    """Return the immutable identity used for account-scoped plugin storage.

    Legacy account records have no id and continue using their name until they
    are written again, preserving existing namespaces. Newly created records
    receive a random id so a delete/recreate cycle cannot inherit old state.
    """
    data = _load_account_store()
    entry = data.get("accounts", {}).get(account_name)
    if not isinstance(entry, dict):
        return str(account_name)
    storage_id = str(entry.get("storage_id") or "").strip()
    return storage_id or str(account_name)


def set_account_session_string(account_name: str, session_string: str) -> None:
    data = _load_account_store()
    accounts = data["accounts"]
    entry = accounts.get(account_name)
    legacy_entry = isinstance(entry, dict)
    if not isinstance(entry, dict):
        entry = {}
    _stamp_created_at(entry)
    # Keep legacy name-based namespaces stable for existing accounts. New
    # accounts get an immutable id so delete/recreate cannot inherit state.
    entry.setdefault("storage_id", account_name if legacy_entry else uuid.uuid4().hex)
    cleaned = session_string.strip()
    if not is_valid_session_string(cleaned):
        raise ValueError("invalid pyrogram session_string")
    from tg_signer.security import encrypt_secret

    stored = encrypt_secret(cleaned)
    entry["session_string"] = stored
    entry["updated_at"] = utc_now_iso()
    accounts[account_name] = entry
    _save_account_store(data)
    # 新建条目：首次落盘即自动分配设备指纹（排重）；老号不动。
    if not legacy_entry:
        auto_assign_device_profile(account_name)


def delete_account_session_string(account_name: str) -> None:
    data = _load_account_store()
    accounts = data["accounts"]
    if account_name in accounts:
        # Rotate legacy name-based storage identity before removal so a later
        # account with the same display name cannot inherit old plugin state.
        entry = accounts.get(account_name)
        if isinstance(entry, dict) and not entry.get("storage_id"):
            entry["storage_id"] = uuid.uuid4().hex
        accounts.pop(account_name, None)
        _save_account_store(data)


def rename_account_entry(old_account_name: str, new_account_name: str) -> None:
    if old_account_name == new_account_name:
        return

    data = _load_account_store()
    accounts = data["accounts"]

    entry = accounts.pop(old_account_name, None)
    if entry is None:
        return
    if new_account_name in accounts:
        raise ValueError(f"account_name {new_account_name} already exists")

    if not isinstance(entry, dict):
        entry = {}
    entry["updated_at"] = utc_now_iso()
    accounts[new_account_name] = entry
    _save_account_store(data)


def get_account_profile(account_name: str) -> dict[str, Any]:
    data = _load_account_store()
    entry = data.get("accounts", {}).get(account_name)
    if not isinstance(entry, dict):
        return {}
    return {
        "remark": entry.get("remark"),
        "proxy": entry.get("proxy"),
        "proxy_mode": entry.get("proxy_mode"),
        "proxy_id": entry.get("proxy_id"),
        "created_at": entry.get("created_at"),
        "user_id": entry.get("user_id"),
        "first_name": entry.get("first_name"),
        "username": entry.get("username"),
        "phone": entry.get("phone"),
        "status": entry.get("status"),
        "status_message": entry.get("status_message"),
        "status_code": entry.get("status_code"),
        "status_checked_at": entry.get("status_checked_at"),
        "needs_relogin": bool(entry.get("needs_relogin", False)),
        "invalid_notified_at": entry.get("invalid_notified_at"),
        "tags": list(entry.get("tags") or []),
        "device_family": entry.get("device_family"),
        "device_profile": entry.get("device_profile"),
        "proxy_probe_policy": entry.get("proxy_probe_policy", "strict"),
        # SpamBot 限制检测（Batch 3，双向检测）：status free/limited/banned/unknown
        "spam_status": entry.get("spam_status"),
        "spam_reply": entry.get("spam_reply"),
        "spam_checked_at": entry.get("spam_checked_at"),
        "spam_summary": entry.get("spam_summary"),
        "spam_until": entry.get("spam_until"),
        "bidirectional": entry.get("bidirectional"),
        # 自定义顺序（Batch 2）：None 表示老账号尚未回填，由列表接口一次性回填
        "sort_order": entry.get("sort_order"),
        "paused": bool(entry.get("paused", False)),
    }


def _stamp_created_at(entry: dict) -> None:
    """新账号条目第一次落盘时记一次添加时间，之后永不覆盖。

    老账号没有 created_at，调用方按“未知”展示，绝不伪造。
    """
    if not entry.get("created_at"):
        entry["created_at"] = utc_now_iso()


def get_account_proxy(account_name: str) -> Optional[str]:
    profile = get_account_profile(account_name)
    proxy = profile.get("proxy")
    if isinstance(proxy, str) and proxy.strip():
        return proxy.strip()
    return None


def _next_sort_order(accounts: dict[str, Any]) -> int:
    """新账号的自定义顺序：当前最大值 +1（追加到末尾）。"""
    orders = [
        entry.get("sort_order")
        for entry in accounts.values()
        if isinstance(entry, dict) and isinstance(entry.get("sort_order"), int)
    ]
    return (max(orders) + 1) if orders else 0


def auto_assign_device_profile(account_name: str) -> Optional[dict[str, str]]:
    """新号首次落盘时自动分配一条未被占用的设备指纹（best-effort，永不抛错）。

    只在账号没有 device_profile 时分配；已有指纹的不覆盖；池子耗尽
    （89 条全被占用）时返回 None 并记 warning，不悄悄撞车、不阻断登录。
    调用方只在新建条目时触发，已有条目的账号（含未设指纹的老号）不会走到这里。
    """
    try:
        from backend.services.telegram.device_profiles import (
            pick_unused_random_profile,
            profile_key,
            validate_device_profile,
        )

        data = _load_account_store()
        accounts = data.get("accounts", {})
        entry = accounts.get(account_name)
        if isinstance(entry, dict):
            existing = entry.get("device_profile")
            if isinstance(existing, dict) and existing.get("device_model"):
                return None
        used: set[tuple[str, ...]] = set()
        for other in accounts.values():
            if not isinstance(other, dict):
                continue
            other_profile = other.get("device_profile")
            if isinstance(other_profile, dict) and other_profile.get("device_model"):
                used.add(profile_key(other_profile))
        picked = pick_unused_random_profile(used)
        if picked is None:
            _logger.warning("设备指纹池已耗尽，账号 %s 未自动分配指纹", account_name)
            return None
        validated = validate_device_profile(picked)
        set_account_profile(account_name, device_profile=validated)
        _logger.info(
            "账号 %s 自动分配设备指纹：%s / %s",
            account_name,
            validated.get("device_model"),
            validated.get("system_version"),
        )
        return validated
    except Exception:
        _logger.warning(
            "账号 %s 自动分配设备指纹失败（不影响登录）", account_name, exc_info=True
        )
        return None


def set_account_profile(
    account_name: str,
    *,
    remark: Optional[str] = None,
    proxy: Optional[str] = None,
    proxy_mode: Optional[str] = None,
    proxy_id: Optional[str] = None,
    proxy_probe_policy: Optional[str] = None,
    tags: Optional[list[str]] = None,
    device_family: Optional[str] = None,
    device_profile: Optional[dict] = None,
    sort_order: Optional[int] = None,
    paused: Optional[bool] = None,
    spam_status: Optional[str] = None,
    spam_reply: Optional[str] = None,
    spam_checked_at: Optional[str] = None,
    spam_summary: Optional[str] = None,
    spam_until: Optional[str] = None,
    bidirectional: Optional[str] = None,
) -> None:
    data = _load_account_store()
    accounts = data["accounts"]
    entry = accounts.get(account_name)
    is_new = not isinstance(entry, dict)
    if not isinstance(entry, dict):
        entry = {}
    _stamp_created_at(entry)
    # 新建条目且未显式指定顺序时追加到末尾；老账号首次写入也一样
    # （已有明确顺序的批量回填走列表接口，不在这里改写）。
    if sort_order is not None:
        entry["sort_order"] = int(sort_order)
    elif not isinstance(entry.get("sort_order"), int):
        entry["sort_order"] = _next_sort_order(accounts)
    if paused is not None:
        entry["paused"] = bool(paused)
    if remark is not None:
        entry["remark"] = remark.strip() if isinstance(remark, str) else remark
    if proxy is not None:
        entry["proxy"] = proxy.strip() or None if isinstance(proxy, str) else proxy
    if proxy_mode is not None:
        entry["proxy_mode"] = str(proxy_mode).strip().lower() or None
        # 模式一变，proxy_id 跟着定：pool 记所选 id，其余模式清空旧 id
        entry["proxy_id"] = (
            str(proxy_id).strip() or None if proxy_id is not None else None
        )
    elif proxy_id is not None:
        entry["proxy_id"] = str(proxy_id).strip() or None
    if tags is not None:
        entry["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if device_family is not None:
        entry["device_family"] = (
            str(device_family).strip()
            if isinstance(device_family, str)
            else device_family
        )
    if device_profile is not None:
        entry["device_profile"] = device_profile
    if proxy_probe_policy is not None:
        entry["proxy_probe_policy"] = str(proxy_probe_policy).strip().lower()
    # SpamBot 限制检测（Batch 3）：只有显式传入才写，None 表示不改
    if spam_status is not None:
        entry["spam_status"] = spam_status
    if spam_reply is not None:
        entry["spam_reply"] = spam_reply
    if spam_checked_at is not None:
        entry["spam_checked_at"] = spam_checked_at
    if spam_summary is not None:
        entry["spam_summary"] = spam_summary
    if spam_until is not None:
        entry["spam_until"] = spam_until
    if bidirectional is not None:
        entry["bidirectional"] = bidirectional
    entry["updated_at"] = utc_now_iso()
    accounts[account_name] = entry
    _save_account_store(data)
    # 新建条目且本次未显式传指纹：首次落盘即自动分配（排重）；老号不动。
    if is_new and device_profile is None:
        auto_assign_device_profile(account_name)


def set_account_identity(
    account_name: str,
    *,
    user_id: Optional[int] = None,
    first_name: Optional[str] = None,
    username: Optional[str] = None,
    phone: Optional[str] = None,
) -> None:
    """缓存一次 get_me 拿到的身份信息，供账号卡片展示用（不连 Telegram 也能读）。

    只写本次拿到的非空字段，不覆盖已有的旧值；绝不伪造不存在的字段。
    """
    data = _load_account_store()
    accounts = data["accounts"]
    entry = accounts.get(account_name)
    if not isinstance(entry, dict):
        entry = {}
    _stamp_created_at(entry)
    if user_id:
        entry["user_id"] = int(user_id)
    if first_name:
        entry["first_name"] = str(first_name)
    if username:
        entry["username"] = str(username)
    if phone:
        entry["phone"] = str(phone)
    entry["updated_at"] = utc_now_iso()
    accounts[account_name] = entry
    _save_account_store(data)


def get_account_session_identity(account_name: str) -> Optional[dict]:
    """从 .session SQLite 读 dc_id / user_id / phone（只读，不连 Telegram）。

    dc_id / user_id 来自 sessions 表；phone 来自 peers 表里本人
    （id=user_id，type='user'）的 phone_number 缓存——有效会话本地就有，
    不用等 get_me 跑完。任一读不到返回对应字段为 None，调用方按字段缺失处理。
    老格式 session 可能没有 peers 表/列，读不到就留空，不报错。
    """
    import sqlite3

    session_file = get_settings().resolve_session_dir() / f"{account_name}.session"
    if not session_file.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{session_file}?mode=ro", uri=True, timeout=5)
    except Exception:
        return None
    try:
        row = conn.execute("SELECT dc_id, user_id FROM sessions").fetchone()
        if not row:
            return None
        dc_id, user_id = row
        if not user_id:
            return None
        phone = None
        try:
            peer = conn.execute(
                "SELECT phone_number FROM peers WHERE id=? AND type='user'",
                (int(user_id),),
            ).fetchone()
            if peer and peer[0]:
                phone = str(peer[0])
        except Exception:
            phone = None
    except Exception:
        return None
    finally:
        conn.close()
    return {
        "dc_id": int(dc_id) if dc_id is not None else None,
        "user_id": int(user_id),
        "phone": phone,
    }


# DC 位置：DC1/DC3 迈阿密，DC2/DC4 阿姆斯特丹，DC5 新加坡
_DC_LOCATIONS = {
    1: "美国·迈阿密",
    3: "美国·迈阿密",
    2: "欧洲·阿姆斯特丹",
    4: "欧洲·阿姆斯特丹",
    5: "新加坡",
}


def dc_location_label(dc_id: Optional[int]) -> Optional[str]:
    if dc_id is None:
        return None
    return _DC_LOCATIONS.get(int(dc_id))


def get_account_status(account_name: str) -> dict[str, Any]:
    profile = get_account_profile(account_name)
    status = profile.get("status")
    return {
        "status": status if isinstance(status, str) and status else "connected",
        "message": profile.get("status_message") or "",
        "code": profile.get("status_code"),
        "checked_at": profile.get("status_checked_at"),
        "needs_relogin": bool(profile.get("needs_relogin", False)),
        "invalid_notified_at": profile.get("invalid_notified_at"),
    }


def set_account_status(
    account_name: str,
    *,
    status: str,
    message: str = "",
    code: Optional[str] = None,
    needs_relogin: bool = False,
    invalid_notified_at: Optional[str] = None,
) -> None:
    data = _load_account_store()
    accounts = data["accounts"]
    entry = accounts.get(account_name)
    if not isinstance(entry, dict):
        entry = {}
    _stamp_created_at(entry)
    entry["status"] = status
    entry["status_message"] = message or ""
    entry["status_code"] = code
    entry["status_checked_at"] = utc_now_iso()
    entry["needs_relogin"] = bool(needs_relogin)
    if invalid_notified_at is not None:
        entry["invalid_notified_at"] = invalid_notified_at
    if status != "invalid":
        entry.pop("invalid_notified_at", None)
    entry["updated_at"] = utc_now_iso()
    accounts[account_name] = entry
    _save_account_store(data)


def session_string_file_path(session_dir: Path, account_name: str) -> Path:
    return session_dir / f"{account_name}.session_string"


# is_valid_session_string 由 tg_signer.compat 提供（见文件头部导入），
# 此处不再重复实现，保证 CLI 与面板判定完全一致


def load_session_string_file(session_dir: Path, account_name: str) -> Optional[str]:
    path = session_string_file_path(session_dir, account_name)
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8").strip()
        except Exception:
            content = ""

        from tg_signer.security import (
            SecretKeyError,
            decrypt_secret,
            is_encrypted_secret,
        )

        if is_encrypted_secret(content):
            try:
                decrypted = decrypt_secret(content)
            except SecretKeyError:
                raise
            except Exception:
                decrypted = None
            if decrypted and is_valid_session_string(decrypted):
                return decrypted
            return None
        elif content and is_valid_session_string(content):
            # Legacy plaintext: migrate atomically to ciphertext.
            # Fail-closed: if migration fails (e.g. SecretKeyError), let it raise
            # without corrupting the original file.
            save_session_string_file(session_dir, account_name, content)
            return content

        # 坏缓存（含历史错误导出的 357 字符串）：删除后从 .session 重导
        try:
            path.unlink()
        except Exception:
            pass
    return _export_session_string_from_file(session_dir, account_name)


def _export_session_string_from_file(
    session_dir: Path, account_name: str
) -> Optional[str]:
    """从 .session SQLite 导出 Pyrogram 新版 session_string 并缓存。

    格式必须与 ``Client.export_session_string()`` 一致：
    ``struct.pack(">BI?256sQ?", dc_id, api_id, test_mode, auth_key, user_id, is_bot)``
    再 urlsafe_b64encode 并去掉 ``=`` padding。**禁止** Telethon 的 ``1`` 版本前缀。
    """
    import base64
    import sqlite3
    import struct

    session_file = session_dir / f"{account_name}.session"
    if not session_file.exists():
        return None

    try:
        conn = sqlite3.connect(str(session_file), timeout=10, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
        except Exception:
            pass

        try:
            row = conn.execute(
                "SELECT dc_id, api_id, test_mode, auth_key, user_id, is_bot FROM sessions"
            ).fetchone()
        except Exception as exc:
            # 未登录的 .session 无 sessions 表属常态，仅 debug；表损坏也会被归入此类
            _logger.debug("读取 sessions 表失败 account=%s: %s", account_name, exc)
            conn.close()
            return None

        if not row:
            conn.close()
            return None

        dc_id, api_id, test_mode, auth_key, user_id, is_bot = row
        conn.close()

        if not auth_key or not user_id:
            return None

        if isinstance(auth_key, memoryview):
            auth_key = auth_key.tobytes()
        elif not isinstance(auth_key, (bytes, bytearray)):
            auth_key = bytes(auth_key)
        auth_key = bytes(auth_key)
        if len(auth_key) < 256:
            auth_key = auth_key.ljust(256, b"\x00")
        elif len(auth_key) > 256:
            auth_key = auth_key[:256]

        # 新版 Pyrogram/kurigram 格式（含 api_id，无版本前缀）
        packed = struct.pack(
            _SESSION_STRING_FORMAT,
            int(dc_id),
            int(api_id or 0),
            bool(test_mode),
            auth_key,
            int(user_id),
            bool(is_bot),
        )
        session_string = base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")

        if not is_valid_session_string(session_string):
            return None

        # Cache it to .session_string file for future use
        # 原子写：会话字符串是登录态凭据，半截文件会让下次加载直接判定会话失效
        try:
            save_session_string_file(session_dir, account_name, session_string)
        except Exception:
            pass

        return session_string
    except Exception as exc:
        # sqlite 打开失败等异常需可观测：静默返回 None 会被误报为「session_string 不存在」
        _logger.warning("导出 session_string 失败 account=%s: %s", account_name, exc)
        return None


def save_session_string_file(
    session_dir: Path, account_name: str, session_string: str
) -> None:
    """原子写入加密会话字符串缓存（临时文件 + 权限收敛 0600 + fsync + rename）。

    Fail-closed: 遇到 SecretKeyError 必须抛出异常，绝不写入明文。
    """
    path = session_string_file_path(session_dir, account_name)
    cleaned = session_string.strip()
    from tg_signer.security import encrypt_secret

    stored = encrypt_secret(cleaned)
    write_text_atomic(path, stored)


def delete_session_string_file(session_dir: Path, account_name: str) -> None:
    path = session_string_file_path(session_dir, account_name)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass


def load_account_session_string(
    account_name: str,
    *,
    session_dir: Path,
    session_mode: str,
) -> Optional[str]:
    """按会话模式加载账号 session_string；无可用值返回 None。

    string 模式：优先账号存储，回退会话文件导出；
    文件模式：仅尝试会话文件导出。
    """
    if session_mode == "string":
        return get_account_session_string(account_name) or load_session_string_file(
            session_dir, account_name
        )
    return load_session_string_file(session_dir, account_name)


def resolve_effective_proxy(
    account_name: str, global_proxy: Optional[str] = None
) -> Optional[str]:
    """解析账号生效代理：池子绑定 / 自定义 URL / 继承全局 / 直连。

    绑定解析走 proxy_store 的唯一入口。显式直连、池子绑定
    （含行被删）绝不回退全局；只有 inherit 模式才用全局代理。
    解析异常时回退到旧的“账号字符串优先、其次全局”逻辑。
    """
    try:
        from backend.services.telegram.proxy_store import get_account_proxy_binding

        binding = get_account_proxy_binding(account_name)
        if binding["mode"] in ("direct", "pool", "custom"):
            return binding.get("effective_url")
        # inherit：全局代理优先用传入值，其次读配置
        if isinstance(global_proxy, str) and global_proxy.strip():
            return global_proxy.strip()
        return binding.get("effective_url")
    except Exception:
        pass
    proxy_value = get_account_proxy(account_name)
    if proxy_value:
        return proxy_value
    if isinstance(global_proxy, str) and global_proxy.strip():
        return global_proxy.strip()
    return None
