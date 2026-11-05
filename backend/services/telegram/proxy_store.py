"""代理池存储与路由服务（fork 新增）。

代理池是一张 SQLAlchemy 表（backend/models/proxy.py）；账号绑定存在
accounts.json 的账号条目里（proxy_mode + proxy_id + proxy URL 字符串）：
  * proxy_mode: inherit（默认，继承全局代理）/ direct（直连）
    / pool（指定池子里的代理，proxy_id 指向池子行）
    / custom（自由填写的 URL 字符串，存在 proxy 字段）
  * 旧账号没有 proxy_mode 时按 proxy 字符串推断：
    有字符串 → custom，没有 → inherit。
账号生效代理的唯一解析入口是 resolve_account_proxy_url()，
_client/_resolve_proxy_and_device_kwargs 与 verify_account_proxy 都走它。
在线账号改代理会被拒绝（先断开再改），绝不静默回落直连。
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

from backend.core.config import get_settings
from backend.models.proxy import Proxy
from backend.services.telegram.proxy_geo import (
    is_auto_label,
    is_blocked_geo,
    lookup_geo,
    region_name,
)
from backend.utils.atomic_io import write_json_atomic
from backend.utils.proxy import build_proxy_dict, format_proxy_url, probe_proxy_exit
from backend.utils.tg_session import (
    get_account_profile,
    list_account_names,
    set_account_profile,
)
from backend.utils.time import utc_now_iso

logger = logging.getLogger("backend.proxy_store")

DEFAULT_SUBSCRIPTION_URL = (
    "https://freeproxydb.com/api/proxy/subscribe"
    "?country=&protocol=socks5,socks4,http,mtproto&anonymity=&speed=0,60"
    "&https=0&count=500&subscribe_format=original"
)

PROXY_MODES = ("inherit", "direct", "pool", "custom")

_SUBSCRIPTION_FILE = ".proxy_subscription.json"
_GEO_ENRICH_BATCH = 100
_GEO_ENRICH_INTERVAL = 1.4

# 后台任务状态（进程内，与 telegram-manager 的 _proxy_sync_job 同款做法）
_sync_job: dict[str, Any] = {
    "status": "idle",
    "fetched": 0,
    "tested": 0,
    "added": 0,
    "kept": 0,
    "removed": 0,
    "blocked_removed": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_test_all_job: dict[str, Any] = {
    "status": "idle",
    "mode": None,
    "total": 0,
    "done": 0,
    "ok": 0,
    "fail": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_cleanup_job: dict[str, Any] = {
    "status": "idle",
    "total": 0,
    "done": 0,
    "removed": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_sync_loop_started = False


# ---------- DB 会话 ----------


def _db():
    from backend.core.database import get_session_local

    return get_session_local()()


# ---------- 凭据加解密 ----------


def _encrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    from tg_signer.security import encrypt_secret

    return encrypt_secret(value)


def _decrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    from tg_signer.security import decrypt_secret

    try:
        return decrypt_secret(value)
    except Exception:
        logger.warning("代理凭据解密失败（密钥不匹配或数据损坏），视为无凭据")
        return None


# ---------- 序列化（永不回传密码 / secret 明文） ----------


def accounts_count_by_proxy() -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in list_account_names():
        profile = get_account_profile(name) or {}
        pid = profile.get("proxy_id")
        if pid:
            counts[str(pid)] = counts.get(str(pid), 0) + 1
    return counts


def serialize_proxy(row: Proxy, accounts_count: int = 0) -> dict:
    return {
        "id": row.id,
        "label": row.label or "",
        "ptype": row.ptype,
        "host": row.host,
        "port": row.port,
        "has_password": bool(row.password),
        "has_secret": bool(row.secret),
        "source": row.source,
        "country_code": row.country_code,
        "reg_country": row.reg_country,
        "isp": row.isp,
        "org": row.org,
        "is_hosting": row.is_hosting,
        "announced_asn": row.announced_asn,
        "announced_prefix": row.announced_prefix,
        "latency_ms": row.latency_ms,
        "last_tested_at": row.last_tested_at.isoformat()
        if row.last_tested_at
        else None,
        "last_test_ok": row.last_test_ok,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "display_url": canonical_display_url(row),
        "accounts_count": accounts_count,
        "enrich_pending": bool(row.enrich_pending),
    }


def canonical_url(row: Proxy) -> str:
    """从存储字段重建完整代理 URL（含凭据，供连接/探测内部使用）。"""
    if row.ptype == "mtproto":
        secret = _decrypt(row.secret) or ""
        return f"mtproto://{urllib.parse.quote(secret, safe='')}@{row.host}:{row.port}"
    auth = ""
    if row.username:
        password = _decrypt(row.password) or ""
        auth = (
            urllib.parse.quote(row.username, safe="")
            + (f":{urllib.parse.quote(password, safe='')}" if password else "")
            + "@"
        )
    return f"{row.ptype}://{auth}{row.host}:{row.port}"


def canonical_display_url(row: Proxy) -> str:
    """展示用 URL：不含任何凭据（用户名、密码、secret 全不出现）。"""
    return f"{row.ptype}://{row.host}:{row.port}"


def proxy_dict_for_row(row: Proxy) -> Optional[dict]:
    return build_proxy_dict(canonical_url(row))


# ---------- CRUD ----------


def _new_proxy_id() -> str:
    return uuid.uuid4().hex[:12]


def list_proxy_rows() -> list[Proxy]:
    with _db() as db:
        rows = db.query(Proxy).order_by(Proxy.created_at.asc()).all()
        for row in rows:
            db.expunge(row)
        return rows


def list_proxies() -> list[dict]:
    counts = accounts_count_by_proxy()
    return [serialize_proxy(r, counts.get(r.id, 0)) for r in list_proxy_rows()]


def get_proxy_row(proxy_id: str) -> Optional[Proxy]:
    with _db() as db:
        row = db.query(Proxy).filter(Proxy.id == proxy_id).first()
        if row is None:
            return None
        db.expunge(row)
        return row


def _dedup_key(ptype: str, host: str, port: int, secret: Optional[str]) -> tuple:
    if ptype == "mtproto":
        return ("mtproto", host.lower(), port, secret or "")
    return (ptype, host.lower(), port)


def _existing_keys(source: Optional[str] = None) -> dict[tuple, str]:
    keys: dict[tuple, str] = {}
    for row in list_proxy_rows():
        if source and row.source != source:
            continue
        keys[_dedup_key(row.ptype, row.host, row.port, _decrypt(row.secret))] = row.id
    return keys


def create_proxy(
    *,
    ptype: str,
    host: str,
    port: int,
    username: Optional[str] = None,
    password: Optional[str] = None,
    secret: Optional[str] = None,
    label: Optional[str] = None,
    source: str = "manual",
    auto_name: bool = True,
    region: Optional[str] = None,
) -> Proxy:
    """创建一行代理：校验类型/端口/secret 并去重，非法直接抛 ValueError。"""
    from backend.utils.mtproxy import normalize_mtproto_secret
    from backend.utils.time import utc_now_naive

    ptype = (ptype or "").strip().lower()
    if ptype not in ("socks5", "socks4", "http", "mtproto"):
        raise ValueError(f"不支持的代理类型: {ptype}")
    host = (host or "").strip().lower()
    if not host:
        raise ValueError("服务器地址不能为空")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("端口必须是 1-65535 的整数")
    secret_clean = (secret or "").strip() or None
    if ptype == "mtproto":
        if not secret_clean:
            raise ValueError("MTProto 代理必须提供 Secret")
        try:
            secret_clean = normalize_mtproto_secret(secret_clean)
        except ValueError as exc:
            raise ValueError(f"Secret 无效: {exc}") from exc
    if _dedup_key(ptype, host, port, secret_clean) in _existing_keys():
        raise ValueError("该代理已存在")

    with _db() as db:
        row = Proxy(
            id=_new_proxy_id(),
            label=(label or "").strip() or None,
            ptype=ptype,
            host=host,
            port=port,
            username=(username or "").strip() or None,
            password=_encrypt((password or "").strip() or None),
            secret=_encrypt(secret_clean),
            source=source,
            country_code=(region or "").strip().upper() or None,
            enrich_pending=True,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )
        if not auto_name and not row.label:
            row.label = "待命名"
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def delete_proxy_row(proxy_id: str) -> list[str]:
    """删除一行代理；绑了它的账号改回直连。返回被重置的账号名。"""
    reset = reset_accounts_bound_to(proxy_id)
    with _db() as db:
        row = db.query(Proxy).filter(Proxy.id == proxy_id).first()
        if row is not None:
            db.delete(row)
            db.commit()
    return reset


def reset_accounts_bound_to(proxy_id: str) -> list[str]:
    """把绑定了某代理的账号改回直连（删代理 / 中伊剔除时调用）。"""
    reset: list[str] = []
    for name in list_account_names():
        profile = get_account_profile(name) or {}
        if str(profile.get("proxy_id") or "") == str(proxy_id):
            set_account_profile(name, proxy_mode="direct", proxy_id=None, proxy="")
            reset.append(name)
    return reset


def rename_proxy(proxy_id: str, label: str) -> Optional[dict]:
    label = (label or "").strip()
    if not label:
        return None
    counts = accounts_count_by_proxy()
    with _db() as db:
        row = db.query(Proxy).filter(Proxy.id == proxy_id).first()
        if row is None:
            return None
        row.label = label
        db.commit()
        db.refresh(row)
        result = serialize_proxy(row, counts.get(row.id, 0))
        db.expunge(row)
        return result


def update_proxy_enrichment(proxy_id: str, **fields: Any) -> None:
    with _db() as db:
        row = db.query(Proxy).filter(Proxy.id == proxy_id).first()
        if row is None:
            return
        for key, value in fields.items():
            if hasattr(row, key):
                setattr(row, key, value)
        db.commit()


# ---------- TCP 连通测试（仅端口连通性，不证明能登录 Telegram） ----------


def tcp_test(host: str, port: int, timeout: float = 3.0) -> tuple[bool, Optional[int]]:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, int((time.monotonic() - t0) * 1000)
    except Exception:
        return False, None


async def tcp_test_async(
    host: str, port: int, timeout: float = 3.0
) -> tuple[bool, Optional[int]]:
    return await asyncio.to_thread(tcp_test, host, port, timeout)


# ---------- 归属地补全（后台） ----------


async def enrich_proxy(proxy_id: str) -> dict:
    """单条代理的归属地补全：TCP 测速 + geo 查询 + 自动命名。

    中伊代理直接删除（返回 blocked=True）。查不到归属地不阻塞，
    只留未知，等下次同步/清理再补。
    """
    from backend.utils.time import utc_now_naive

    row = get_proxy_row(proxy_id)
    if row is None:
        return {"ok": False, "blocked": False, "reason": "not_found"}
    ok, latency = await tcp_test_async(row.host, row.port)
    geo = await lookup_geo(row.host)
    now = utc_now_naive()
    if geo and is_blocked_geo(geo):
        delete_proxy_row(proxy_id)
        return {
            "ok": ok,
            "latency_ms": latency,
            "blocked": True,
            "country_code": geo.get("country_code"),
            "reg_country": geo.get("reg_country"),
        }
    label = row.label
    if (not label or is_auto_label(label)) and geo:
        labels = [r.label or "" for r in list_proxy_rows()]
        label = region_name(
            geo.get("country_code"),
            geo.get("is_hosting"),
            existing_labels=labels,
            exclude_label=row.label,
        )
    elif not label:
        label = "待命名"
    update_proxy_enrichment(
        proxy_id,
        latency_ms=latency,
        last_tested_at=now,
        last_test_ok=ok,
        country_code=(geo or {}).get("country_code") or row.country_code,
        reg_country=(geo or {}).get("reg_country"),
        isp=(geo or {}).get("isp"),
        org=(geo or {}).get("org"),
        is_hosting=(geo or {}).get("is_hosting"),
        announced_asn=(geo or {}).get("announced_asn"),
        announced_prefix=(geo or {}).get("announced_prefix"),
        label=label,
        enrich_pending=False,
    )
    return {
        "ok": ok,
        "latency_ms": latency,
        "blocked": False,
        "country_code": (geo or {}).get("country_code"),
        "label": label,
    }


async def enrich_unknown_proxies(limit: int = _GEO_ENRICH_BATCH) -> int:
    """给还没有归属地的代理补归属地（每轮最多 limit 条，间隔节流）。"""
    rows = [r for r in list_proxy_rows() if not r.country_code][:limit]
    for row in rows:
        await enrich_proxy(row.id)
        await asyncio.sleep(_GEO_ENRICH_INTERVAL)
    return len(rows)


# ---------- 账号绑定 ----------


def get_account_proxy_binding(account_name: str) -> dict:
    """账号当前的代理绑定（mode / proxy_id / 展示标签 / 生效 URL）。"""
    from backend.services.config import get_config_service

    profile = get_account_profile(account_name) or {}
    mode = (profile.get("proxy_mode") or "").strip().lower()
    if mode not in PROXY_MODES:
        # 旧账号没有 proxy_mode：有字符串 → custom，没有 → inherit
        mode = "custom" if (profile.get("proxy") or "").strip() else "inherit"
    proxy_id = profile.get("proxy_id")
    proxy_url = (profile.get("proxy") or "").strip() or None
    global_proxy = get_config_service().get_global_proxy()

    if mode == "pool" and proxy_id:
        row = get_proxy_row(str(proxy_id))
        if row is None:
            return {
                "mode": "pool",
                "proxy_id": str(proxy_id),
                "proxy_label": None,
                "effective_url": None,
                "effective_label": "代理已删除，请重新选择",
                "dangling": True,
            }
        return {
            "mode": "pool",
            "proxy_id": str(proxy_id),
            "proxy_label": row.label or canonical_display_url(row),
            "effective_url": canonical_url(row),
            "effective_label": row.label or canonical_display_url(row),
            "dangling": False,
        }
    if mode == "custom" and proxy_url:
        display = format_proxy_url(proxy_url) or proxy_url
        return {
            "mode": "custom",
            "proxy_id": None,
            "proxy_label": None,
            "effective_url": proxy_url,
            "effective_label": display,
            "dangling": False,
        }
    if mode == "direct":
        return {
            "mode": "direct",
            "proxy_id": None,
            "proxy_label": None,
            "effective_url": None,
            "effective_label": "直连",
            "dangling": False,
        }
    # inherit
    if global_proxy and str(global_proxy).strip():
        display = format_proxy_url(str(global_proxy).strip()) or "全局代理"
        return {
            "mode": "inherit",
            "proxy_id": None,
            "proxy_label": None,
            "effective_url": str(global_proxy).strip(),
            "effective_label": f"继承全局代理（{display}）",
            "dangling": False,
        }
    return {
        "mode": "inherit",
        "proxy_id": None,
        "proxy_label": None,
        "effective_url": None,
        "effective_label": "继承全局代理（未设置）",
        "dangling": False,
    }


def resolve_account_proxy_url(account_name: str) -> Optional[str]:
    """账号生效代理 URL（None = 直连）。池子行被删则视为直连并记警告。"""
    binding = get_account_proxy_binding(account_name)
    if binding.get("dangling"):
        logger.warning("账号 %s 绑定的代理已被删除，视为直连", account_name)
    return binding.get("effective_url")


def is_account_online(account_name: str) -> bool:
    """账号当前是否有已连接的客户端（内存里）或正被任务持有。"""
    try:
        from tg_signer.core.client import (
            _CLIENT_INSTANCES,
            get_client_refcount,
        )
    except Exception:
        return False
    try:
        session_dir = get_settings().resolve_session_dir()
        base_key = str(Path(session_dir).joinpath(account_name).resolve())
    except Exception:
        return False
    for key, client in list(_CLIENT_INSTANCES.items()):
        if key != base_key and not key.startswith(base_key + "::"):
            continue
        if getattr(client, "is_connected", False):
            return True
        if get_client_refcount(key) > 0:
            return True
    return False


def set_account_proxy_binding(
    account_name: str,
    mode: str,
    proxy_id: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> dict:
    """设置账号代理绑定。在线账号拒绝修改（先断开再改）。"""
    mode = (mode or "").strip().lower()
    if mode not in PROXY_MODES:
        raise ValueError(f"proxy_mode 必须是 {PROXY_MODES} 之一")
    if is_account_online(account_name):
        raise RuntimeError(
            "ONLINE_PROXY_CHANGE_BLOCKED: 账号当前在线，改代理前请先断开连接"
        )
    if mode == "pool":
        if not proxy_id:
            raise ValueError("指定代理需要 proxy_id")
        row = get_proxy_row(str(proxy_id))
        if row is None:
            raise ValueError("指定的代理不存在")
        set_account_profile(
            account_name, proxy_mode="pool", proxy_id=str(proxy_id), proxy=""
        )
        return get_account_proxy_binding(account_name)
    if mode == "custom":
        url = (proxy_url or "").strip()
        if not url:
            raise ValueError("自定义线路需要填写代理 URL")
        if build_proxy_dict(url) is None:
            raise ValueError("PROXY_INVALID_BLOCKED: 代理 URL 无效")
        set_account_profile(account_name, proxy_mode="custom", proxy_id=None, proxy=url)
        return get_account_proxy_binding(account_name)
    if mode == "direct":
        set_account_profile(account_name, proxy_mode="direct", proxy_id=None, proxy="")
        return get_account_proxy_binding(account_name)
    set_account_profile(account_name, proxy_mode="inherit", proxy_id=None, proxy="")
    return get_account_proxy_binding(account_name)


def list_account_routes() -> list[dict]:
    """账号路由列表：每个账号一行，含当前线路与在线状态。"""
    routes = []
    for name in list_account_names():
        binding = get_account_proxy_binding(name)
        routes.append(
            {
                "account_name": name,
                "mode": binding["mode"],
                "proxy_id": binding.get("proxy_id"),
                "proxy_label": binding.get("proxy_label"),
                "effective_label": binding["effective_label"],
                "online": is_account_online(name),
            }
        )
    return routes


# ---------- 订阅设置 ----------


def _subscription_file() -> Path:
    return get_settings().resolve_workdir() / _SUBSCRIPTION_FILE


def get_subscription_url() -> Optional[str]:
    try:
        data = json.loads(_subscription_file().read_text(encoding="utf-8"))
        url = (data or {}).get("url")
        return url.strip() if isinstance(url, str) and url.strip() else None
    except Exception:
        return None


def get_effective_subscription_url() -> str:
    return get_subscription_url() or DEFAULT_SUBSCRIPTION_URL


def set_subscription_url(url: Optional[str]) -> Optional[str]:
    """保存订阅链接；传空恢复默认链接。"""
    cleaned = (url or "").strip() or None
    write_json_atomic(
        _subscription_file(),
        {"url": cleaned, "updated_at": utc_now_iso()},
    )
    return cleaned


# ---------- 订阅同步（后台任务） ----------


def sync_job_state() -> dict:
    return dict(_sync_job)


def test_all_job_state() -> dict:
    return dict(_test_all_job)


def cleanup_job_state() -> dict:
    return dict(_cleanup_job)


async def _fetch_subscription_text(url: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"User-Agent": "tg-signpulse-proxy-sync"})
        resp.raise_for_status()
        return resp.text


async def run_proxy_sync() -> dict:
    """订阅同步：拉取 → 解析 → 20 并发 TCP 测试 → 只保留连通的自动代理。

    手动代理永不删改；不在新列表里或这次 TCP 没通过的自动代理删除，
    绑了它的账号改回直连；中伊自动代理直接剔除。
    """
    from backend.services.telegram.proxy_parse import parse_subscription_text
    from backend.utils.time import utc_now_naive

    global _sync_job
    if _sync_job.get("status") == "running":
        return dict(_sync_job)
    _sync_job.update(
        {
            "status": "running",
            "fetched": 0,
            "tested": 0,
            "added": 0,
            "kept": 0,
            "removed": 0,
            "blocked_removed": 0,
            "started_at": utc_now_iso(),
            "finished_at": None,
            "error": None,
        }
    )
    try:
        sync_url = get_effective_subscription_url()
        text = await _fetch_subscription_text(sync_url)
        candidates = parse_subscription_text(text)
        _sync_job["fetched"] = len(candidates)

        # 手动代理按短键（不含 secret）也跳过，避免同址不同 secret 的重复
        manual_short_keys = {
            (r.ptype, r.host.lower(), r.port)
            for r in list_proxy_rows()
            if r.source != "freeproxydb"
        }
        auto_map = {
            _dedup_key(r.ptype, r.host, r.port, _decrypt(r.secret)): r.id
            for r in list_proxy_rows()
            if r.source == "freeproxydb"
        }

        sem = asyncio.Semaphore(20)

        async def _test_one(cand: dict) -> tuple[bool, Optional[int]]:
            async with sem:
                return await tcp_test_async(cand["host"], cand["port"], timeout=3.0)

        results = await asyncio.gather(*[_test_one(c) for c in candidates])
        passed_auto_keys: set[tuple] = set()
        now = utc_now_naive()
        for cand, (reachable, latency) in zip(candidates, results, strict=True):
            key = _dedup_key(
                cand["ptype"], cand["host"], cand["port"], cand.get("secret")
            )
            short_key = (cand["ptype"], cand["host"].lower(), cand["port"])
            if short_key in manual_short_keys:
                continue
            _sync_job["tested"] += 1
            if not reachable:
                continue
            if key in auto_map:
                passed_auto_keys.add(key)
                _sync_job["kept"] += 1
                update_proxy_enrichment(
                    auto_map[key],
                    latency_ms=latency,
                    last_tested_at=now,
                    last_test_ok=True,
                )
                continue
            try:
                row = create_proxy(
                    ptype=cand["ptype"],
                    host=cand["host"],
                    port=cand["port"],
                    username=cand.get("username"),
                    password=cand.get("password"),
                    secret=cand.get("secret"),
                    label=f"自动 {cand['ptype']} {cand['host']}",
                    source="freeproxydb",
                    auto_name=False,
                )
            except ValueError as exc:
                # 订阅源里的个别脏数据（如非法 MTProto secret）只跳过这一条，
                # 绝不能让整轮同步报错中断（之前一个坏 secret 就把整轮标 error，
                # 后面的补归属地/中伊剔除/过期清理全都没跑）。
                logger.warning(
                    "订阅同步跳过无效代理 %s:%s: %s",
                    cand["host"],
                    cand["port"],
                    exc,
                )
                continue
            passed_auto_keys.add(key)
            _sync_job["added"] += 1
            # 新加的自动代理后台补归属地（不阻塞同步结果）
            asyncio.create_task(enrich_proxy(row.id))

        # 补归属地（只补还没有的，每轮最多 100 条）
        await enrich_unknown_proxies()

        # 已知中伊的自动代理剔除，不管它在不在新列表里
        for row in list_proxy_rows():
            if row.source != "freeproxydb":
                continue
            if is_blocked_geo(
                {"country_code": row.country_code, "reg_country": row.reg_country}
            ):
                delete_proxy_row(row.id)
                _sync_job["blocked_removed"] += 1
        # 不在新列表里或这次 TCP 没通过的自动代理删除
        for row in list_proxy_rows():
            if row.source != "freeproxydb":
                continue
            key = _dedup_key(row.ptype, row.host, row.port, _decrypt(row.secret))
            if key not in passed_auto_keys:
                delete_proxy_row(row.id)
                _sync_job["removed"] += 1
        _sync_job["status"] = "done"
    except Exception as exc:
        logger.exception("代理订阅同步失败: %s", exc)
        _sync_job["status"] = "error"
        _sync_job["error"] = str(exc)[:300]
    finally:
        _sync_job["finished_at"] = utc_now_iso()
    return dict(_sync_job)


async def _proxy_sync_loop() -> None:
    """启动约 5 分钟后首轮同步，之后每 24 小时一轮。"""
    await asyncio.sleep(300)
    while True:
        try:
            await run_proxy_sync()
        except Exception as exc:
            logger.warning("代理订阅同步失败: %s", exc)
        await asyncio.sleep(24 * 3600)


def start_proxy_sync_loop() -> None:
    """在应用启动时调用一次；重复调用不重复启动。"""
    global _sync_loop_started
    if _sync_loop_started:
        return
    _sync_loop_started = True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_proxy_sync_loop())


# ---------- 检测全部 / 测速（后台任务） ----------


async def run_test_all(mode: str = "probe") -> dict:
    """检测全部代理：probe = 真实探测（出口 IP / mtproto 握手），
    tcp = 仅 TCP 端口测速（只更新延迟，不改通断判定）。"""
    global _test_all_job
    if _test_all_job.get("status") == "running":
        return dict(_test_all_job)
    rows = list_proxy_rows()
    _test_all_job.update(
        {
            "status": "running",
            "mode": mode,
            "total": len(rows),
            "done": 0,
            "ok": 0,
            "fail": 0,
            "started_at": utc_now_iso(),
            "finished_at": None,
            "error": None,
        }
    )
    try:
        sem = asyncio.Semaphore(10)

        async def _test_one(row: Proxy) -> None:
            from backend.utils.time import utc_now_naive

            async with sem:
                now = utc_now_naive()
                if mode == "tcp":
                    ok, latency = await tcp_test_async(row.host, row.port)
                    update_proxy_enrichment(
                        row.id, latency_ms=latency, last_tested_at=now
                    )
                else:
                    proxy_dict = proxy_dict_for_row(row)
                    if proxy_dict is None:
                        ok = False
                    else:
                        status, _detail = await probe_proxy_exit(proxy_dict)
                        ok = status.name == "OK"
                        update_proxy_enrichment(
                            row.id,
                            latency_ms=None,
                            last_tested_at=now,
                            last_test_ok=ok,
                        )
                _test_all_job["done"] += 1
                _test_all_job["ok" if ok else "fail"] += 1

        await asyncio.gather(*[_test_one(r) for r in rows])
        _test_all_job["status"] = "done"
    except Exception as exc:
        logger.exception("检测全部代理失败: %s", exc)
        _test_all_job["status"] = "error"
        _test_all_job["error"] = str(exc)[:300]
    finally:
        _test_all_job["finished_at"] = utc_now_iso()
    return dict(_test_all_job)


async def run_cleanup_blocked() -> dict:
    """一键清理中伊代理：扫全部代理（含手动），宣告或注册任一是 CN/IR
    就删，绑了它的账号改回直连；没归属地的先查一次再判断，
    查不到的不乱删。"""
    global _cleanup_job
    if _cleanup_job.get("status") == "running":
        return dict(_cleanup_job)
    rows = list_proxy_rows()
    _cleanup_job.update(
        {
            "status": "running",
            "total": len(rows),
            "done": 0,
            "removed": 0,
            "started_at": utc_now_iso(),
            "finished_at": None,
            "error": None,
        }
    )
    try:
        for row in rows:
            geo = None
            if not row.country_code and not row.reg_country:
                geo = await lookup_geo(row.host)
                await asyncio.sleep(_GEO_ENRICH_INTERVAL)
            known = {
                "country_code": row.country_code,
                "reg_country": row.reg_country,
            }
            if geo:
                known = {
                    "country_code": geo.get("country_code"),
                    "reg_country": geo.get("reg_country"),
                }
            if is_blocked_geo(known):
                delete_proxy_row(row.id)
                _cleanup_job["removed"] += 1
            _cleanup_job["done"] += 1
        _cleanup_job["status"] = "done"
    except Exception as exc:
        logger.exception("清理中伊代理失败: %s", exc)
        _cleanup_job["status"] = "error"
        _cleanup_job["error"] = str(exc)[:300]
    finally:
        _cleanup_job["finished_at"] = utc_now_iso()
    return dict(_cleanup_job)
