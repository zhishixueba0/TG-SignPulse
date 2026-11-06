"""代理池 API（fork patch）。

Proxies page 的后端：代理池 CRUD、粘贴/订阅导入、真实探测、
订阅同步、账号路由绑定、全局代理。密码/密钥永不进响应与日志。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models.user import User
from backend.services.telegram import proxy_store
from backend.utils.proxy import build_proxy_dict, probe_proxy_exit

logger = logging.getLogger(__name__)

router = APIRouter()


def _mask_url(url: Optional[str]) -> Optional[str]:
    """把代理 URL 里的账号密码/密钥打码，只留 scheme://host:port。"""
    if not url:
        return None
    parsed = build_proxy_dict(url)
    if not parsed:
        return None
    host = parsed.get("hostname") or parsed.get("host") or ""
    port = parsed.get("port")
    scheme = parsed.get("scheme") or "socks5"
    if scheme == "mtproxy":
        scheme = "mtproto"
    base = f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"
    if parsed.get("username") or parsed.get("password") or parsed.get("secret"):
        return base + " (已设置凭据)"
    return base


# ---------- 请求/响应模型 ----------


class ProxyCreateRequest(BaseModel):
    label: Optional[str] = None
    auto_name: bool = True
    ptype: str = "socks5"
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    secret: Optional[str] = None
    region: Optional[str] = None
    source: str = "manual"


class ProxyImportRequest(BaseModel):
    text: str = ""


class ProxyBatchDeleteRequest(BaseModel):
    ids: list[str] = []


class ProxyRenameRequest(BaseModel):
    label: str


class ProxyTestRequest(BaseModel):
    mode: str = "probe"  # probe | tcp


class ProxyTestAllRequest(BaseModel):
    mode: str = "probe"  # probe | tcp


class SubscriptionSetRequest(BaseModel):
    url: Optional[str] = None


class RouteSetRequest(BaseModel):
    account_name: str
    mode: str
    proxy_id: Optional[str] = None
    proxy_url: Optional[str] = None


class GlobalProxySetRequest(BaseModel):
    proxy_url: Optional[str] = None


# ---------- 列表 / 增删改 ----------


@router.get("")
async def list_proxies(current_user: User = Depends(get_current_user)):
    counts = proxy_store.accounts_count_by_proxy()
    proxies = [
        proxy_store.serialize_proxy(r, counts.get(r.id, 0))
        for r in proxy_store.list_proxy_rows()
    ]
    return {"proxies": proxies, "total": len(proxies)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_proxy(
    request: ProxyCreateRequest, current_user: User = Depends(get_current_user)
):
    try:
        row = proxy_store.create_proxy(
            ptype=request.ptype,
            host=request.host,
            port=request.port,
            username=request.username,
            password=request.password,
            secret=request.secret,
            label=request.label,
            auto_name=request.auto_name,
            source=request.source,
            region=request.region,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {
        "success": True,
        "proxy": proxy_store.serialize_proxy(row),
        "message": "代理已添加",
    }


@router.post("/import")
async def import_proxies(
    request: ProxyImportRequest, current_user: User = Depends(get_current_user)
):
    from backend.services.telegram.proxy_parse import parse_proxy_lines

    text = request.text or ""
    parsed = parse_proxy_lines(text)
    # 解析器静默丢掉重复/无效行：行数 - 解析数就是被丢掉的条数
    # （整段 base64 订阅一块可解出多条，负数按 0 算）
    raw_lines = [line for line in text.splitlines() if line.strip()]
    skipped = max(0, len(raw_lines) - len(parsed))
    added_ids: list[str] = []
    for cand in parsed:
        try:
            row = proxy_store.create_proxy(
                ptype=cand["ptype"],
                host=cand["host"],
                port=cand["port"],
                username=cand.get("username"),
                password=cand.get("password"),
                secret=cand.get("secret"),
                auto_name=True,
                source="manual",
            )
        except ValueError:
            skipped += 1
            continue
        added_ids.append(row.id)
    # 归属地与中伊剔除在后台补（不阻塞导入响应）
    for proxy_id in added_ids:
        asyncio.create_task(proxy_store.enrich_proxy(proxy_id))
    return {
        "success": True,
        "added": len(added_ids),
        "skipped": skipped,
        "blocked": 0,
        "added_ids": added_ids,
        "message": f"已添加 {len(added_ids)} 条，跳过 {skipped} 条（重复或无效）",
    }


@router.post("/batch-delete")
async def batch_delete_proxies(
    request: ProxyBatchDeleteRequest, current_user: User = Depends(get_current_user)
):
    deleted = 0
    reset_accounts: list[str] = []
    for proxy_id in request.ids or []:
        reset = proxy_store.delete_proxy_row(str(proxy_id))
        if reset is not None:
            deleted += 1
            reset_accounts.extend(reset)
    return {
        "success": True,
        "deleted": deleted,
        "reset_accounts": sorted(set(reset_accounts)),
    }


@router.delete("/{proxy_id}")
async def delete_proxy(proxy_id: str, current_user: User = Depends(get_current_user)):
    reset = proxy_store.delete_proxy_row(proxy_id)
    if reset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="代理不存在")
    return {
        "success": True,
        "message": "代理已删除",
        "reset_accounts": reset,
    }


@router.post("/{proxy_id}/rename")
async def rename_proxy(
    proxy_id: str,
    request: ProxyRenameRequest,
    current_user: User = Depends(get_current_user),
):
    # 名字允许留空：留空时按已存归属地自动命名（见 proxy_store.rename_proxy）。
    label = (request.label or "").strip()
    row = proxy_store.rename_proxy(proxy_id, label)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="代理不存在")
    return {"success": True, "proxy": row}


@router.get("/{proxy_id}/link")
async def get_proxy_link(proxy_id: str, current_user: User = Depends(get_current_user)):
    """取单个代理的可分享节点链接（卡片“复制链接”用）。

    按需单条返回：MTProto 返回 tg://proxy 链接（含 secret），其余返回完整 URL。
    列表响应仍不含任何凭据，secret 只在用户明确点复制某一条时才返回这一条。
    """
    row = proxy_store.get_proxy_row(proxy_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="代理不存在")
    return {"link": proxy_store.build_proxy_share_link(row)}


# ---------- 探测 ----------


@router.post("/{proxy_id}/test")
async def test_proxy(
    proxy_id: str,
    request: Optional[ProxyTestRequest] = None,
    current_user: User = Depends(get_current_user),
):
    """手动测试单个代理：强制真实探测（跳过 600s 缓存），不走熔断门禁，
    失败不累计熔断、不写缓存。latency_ms 是探测内实测延迟（缓存命中时
    为 None，绝不把 0ms 当实测延迟）。mode=tcp 时只测端口连通性，
    明确标注“端口通（不等同于可用）”。"""
    from backend.utils.time import utc_now_naive

    mode = ((request.mode if request else "probe") or "probe").strip().lower()
    if mode not in ("probe", "tcp"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode 只能是 probe 或 tcp",
        )
    row = proxy_store.get_proxy_row(proxy_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="代理不存在")
    now = utc_now_naive()
    if mode == "tcp":
        ok, latency = await proxy_store.tcp_test_async(row.host, row.port)
        code = "TCP_OK" if ok else "PROXY_CONNECT_FAILED"
        detail = (
            "端口通（不等同于可用）：仅证明 TCP 端口可连，不能证明能登录 Telegram"
            if ok
            else "端口不可达（3 秒内无响应）"
        )
        proxy_store.update_proxy_enrichment(
            row.id,
            latency_ms=latency,
            last_tested_at=now,
            last_test_ok=ok,
        )
        proxy_store.record_proxy_test(
            row.id, ok=ok, latency_ms=latency, code=code, source="manual"
        )
        return {
            "success": True,
            "ok": ok,
            "latency_ms": latency,
            "cached": False,
            "code": code,
            "detail": detail,
            "last_test_ok": ok,
        }
    proxy_dict = proxy_store.proxy_dict_for_row(row)
    if proxy_dict is None:
        return {
            "success": False,
            "ok": False,
            "latency_ms": None,
            "cached": False,
            "code": "PROXY_NOT_CONFIGURED",
            "detail": "代理配置无效",
            "last_test_ok": False,
        }
    outcome = await probe_proxy_exit(proxy_dict, force=True, manual=True)
    ok = outcome.status.name == "OK"
    proxy_store.update_proxy_enrichment(
        row.id,
        latency_ms=outcome.latency_ms if ok else None,
        last_tested_at=now,
        last_test_ok=ok,
    )
    proxy_store.record_proxy_test(
        row.id,
        ok=ok,
        latency_ms=outcome.latency_ms if ok else None,
        code=outcome.code,
        source="manual",
    )
    return {
        "success": True,
        "ok": ok,
        "latency_ms": outcome.latency_ms if ok else None,
        "cached": outcome.cached,
        "code": outcome.code,
        "detail": outcome.detail,
        "tcp_ok": outcome.tcp_ok,
        "last_test_ok": ok,
    }


@router.post("/test-all")
async def test_all_proxies(
    request: ProxyTestAllRequest, current_user: User = Depends(get_current_user)
):
    mode = (request.mode or "probe").strip().lower()
    if mode not in ("probe", "tcp"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode 只能是 probe 或 tcp",
        )
    state = proxy_store.test_all_job_state()
    if state.get("status") == "running":
        return {"success": True, "started": False, "already_running": True}
    asyncio.create_task(proxy_store.run_test_all(mode))
    return {"success": True, "started": True}


@router.get("/test-all/job")
async def test_all_job(current_user: User = Depends(get_current_user)):
    return proxy_store.test_all_job_state()


@router.post("/cleanup-blocked")
async def cleanup_blocked(current_user: User = Depends(get_current_user)):
    state = proxy_store.cleanup_job_state()
    if state.get("status") == "running":
        return {"success": True, "started": False, "already_running": True}
    asyncio.create_task(proxy_store.run_cleanup_blocked())
    return {"success": True, "started": True}


@router.get("/cleanup-blocked/job")
async def cleanup_blocked_job(current_user: User = Depends(get_current_user)):
    return proxy_store.cleanup_job_state()


# ---------- 订阅 ----------


@router.get("/subscription")
async def get_subscription(current_user: User = Depends(get_current_user)):
    return {
        "url": proxy_store.get_effective_subscription_url(),
        "default_url": proxy_store.DEFAULT_SUBSCRIPTION_URL,
        "custom": proxy_store.get_subscription_url() is not None,
    }


@router.post("/subscription")
async def set_subscription(
    request: SubscriptionSetRequest, current_user: User = Depends(get_current_user)
):
    saved = proxy_store.set_subscription_url(request.url)
    return {
        "success": True,
        "url": saved or proxy_store.DEFAULT_SUBSCRIPTION_URL,
    }


@router.post("/sync")
async def sync_subscription(current_user: User = Depends(get_current_user)):
    state = proxy_store.sync_job_state()
    if state.get("status") == "running":
        return {"success": True, "started": False, "already_running": True}
    asyncio.create_task(proxy_store.run_proxy_sync())
    return {"success": True, "started": True}


@router.get("/sync/job")
async def sync_job(current_user: User = Depends(get_current_user)):
    return proxy_store.sync_job_state()


# ---------- 账号路由 ----------


@router.get("/routes")
async def list_routes(current_user: User = Depends(get_current_user)):
    from backend.services.config import get_config_service

    global_url = get_config_service().get_global_proxy()
    return {
        "routes": proxy_store.list_account_routes(),
        "global_proxy": {"set": bool(global_url), "display_url": _mask_url(global_url)},
    }


@router.post("/routes")
async def set_route(
    request: RouteSetRequest, current_user: User = Depends(get_current_user)
):
    from backend.utils.tg_session import set_account_profile

    mode = (request.mode or "").strip().lower()
    if mode not in ("inherit", "direct", "pool", "custom"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode 只能是 inherit / direct / pool / custom",
        )
    account_name = (request.account_name or "").strip()
    if not account_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="account_name 不能为空"
        )
    proxy_id: Optional[str] = None
    proxy_url: Optional[str] = None
    if mode == "pool":
        if not request.proxy_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="指定代理时必须选择一个代理",
            )
        if proxy_store.get_proxy_row(str(request.proxy_id)) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="所选代理不存在"
            )
        proxy_id = str(request.proxy_id)
    elif mode == "custom":
        if not (request.proxy_url or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="自定义线路时必须填写代理地址",
            )
        if not build_proxy_dict((request.proxy_url or "").strip()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="代理地址格式无效"
            )
        proxy_url = (request.proxy_url or "").strip()
    current = proxy_store.get_account_proxy_binding(account_name)
    changed = (
        mode != current["mode"]
        or (proxy_id or None) != (current.get("proxy_id") or None)
        or (mode == "custom" and proxy_url != (current.get("proxy_url") or ""))
    )
    if changed and proxy_store.is_account_online(account_name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="账号在线时不能更换线路，请先断开连接再试",
        )
    set_account_profile(
        account_name,
        proxy=proxy_url if mode == "custom" else "",
        proxy_mode=mode,
        proxy_id=proxy_id,
    )
    return {"success": True, "message": "线路已更新"}


# ---------- 全局代理 ----------


@router.get("/global")
async def get_global_proxy(current_user: User = Depends(get_current_user)):
    from backend.services.config import get_config_service

    url = get_config_service().get_global_proxy()
    return {"set": bool(url), "display_url": _mask_url(url)}


@router.post("/global")
async def set_global_proxy(
    request: GlobalProxySetRequest, current_user: User = Depends(get_current_user)
):
    from backend.services.config import get_config_service

    url = (request.proxy_url or "").strip() or None
    if url and not build_proxy_dict(url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="代理地址格式无效"
        )
    cfg_svc = get_config_service()
    if not cfg_svc.save_global_settings({"global_proxy": url}):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="保存全局代理失败",
        )
    return {"success": True, "set": bool(url)}


@router.post("/global/test")
async def test_global_proxy(current_user: User = Depends(get_current_user)):
    from backend.services.config import get_config_service

    url = get_config_service().get_global_proxy()
    if not url:
        return {
            "success": False,
            "ok": False,
            "latency_ms": None,
            "cached": False,
            "code": "PROXY_NOT_CONFIGURED",
            "detail": "全局代理未设置",
        }
    proxy_dict = build_proxy_dict(url)
    if not proxy_dict:
        return {
            "success": False,
            "ok": False,
            "latency_ms": None,
            "cached": False,
            "code": "PROXY_NOT_CONFIGURED",
            "detail": "全局代理格式无效",
        }
    outcome = await probe_proxy_exit(proxy_dict, force=True, manual=True)
    ok = outcome.status.name == "OK"
    return {
        "success": True,
        "ok": ok,
        "latency_ms": outcome.latency_ms if ok else None,
        "cached": outcome.cached,
        "code": outcome.code,
        "detail": outcome.detail,
        "tcp_ok": outcome.tcp_ok,
    }
