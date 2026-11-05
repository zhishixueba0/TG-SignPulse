"""Login-time proxy selection, shared by all four login entries.

SMS code login, QR login, session import and passkey login all accept the
same proxy choice: inherit the global proxy (default), direct, a pool
proxy by id, or a custom proxy URL string. This module resolves that
choice once, so every entry behaves the same:

- ``pool`` looks the proxy up in the pool by id; an unknown id raises
  ``PROXY_NOT_FOUND`` and the login never silently falls back to direct.
- ``custom`` parses the URL string; an invalid one raises
  ``PROXY_INVALID_BLOCKED``.
- ``direct`` means no proxy and no global fallback; when the panel policy
  requires a proxy it raises ``PROXY_REQUIRED_BLOCKED`` (fail closed).
- ``inherit`` (or no choice at all) falls back to the global proxy,
  exactly like the old bare-``proxy`` parameter did.

The returned selection also carries the profile binding to persist
(``proxy_mode`` / ``proxy_id`` / ``proxy``), so the account keeps using
the proxy the user picked at login time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

LOGIN_PROXY_MODES = ("inherit", "direct", "pool", "custom")


@dataclass(frozen=True)
class LoginProxySelection:
    """A resolved login proxy choice."""

    mode: str  # inherit | direct | pool | custom
    proxy_url: Optional[str]  # raw URL handed to the client builder (None = direct)
    proxy_dict: Optional[dict]  # Telethon/Pyrogram proxy dict (None = direct)
    proxy_id: Optional[str]  # pool proxy id, only when mode == "pool"
    proxy: Optional[str]  # profile `proxy` string to persist (custom URL or "")

    def persist_kwargs(self) -> dict:
        """Profile fields to persist for the account binding."""
        return {
            "proxy_mode": self.mode,
            "proxy_id": self.proxy_id,
            "proxy": self.proxy or "",
        }


def resolve_login_proxy(
    *,
    proxy: Optional[str] = None,
    proxy_id: Optional[str] = None,
    proxy_mode: Optional[str] = None,
    config_service=None,
) -> LoginProxySelection:
    """Resolve a login proxy choice to a client-ready selection.

    ``proxy`` is the legacy free-text URL; ``proxy_id`` selects a pool
    proxy; ``proxy_mode`` is one of ``inherit`` / ``direct`` / ``pool`` /
    ``custom``. When ``proxy_mode`` is omitted it is inferred: a proxy id
    means pool, a proxy URL means custom, otherwise inherit. Raises
    ``ValueError`` with a stable code (``PROXY_NOT_FOUND`` /
    ``PROXY_INVALID_BLOCKED`` / ``PROXY_REQUIRED_BLOCKED``) on any
    problem -- never returns a direct connection for a failed choice.
    """
    from backend.services.config import get_config_service
    from backend.services.telegram.proxy_store import (
        canonical_url,
        get_proxy_row,
        proxy_dict_for_row,
    )
    from backend.utils.proxy import build_proxy_dict

    cfg = config_service if config_service is not None else get_config_service()
    mode = (proxy_mode or "").strip().lower() or None
    proxy = (proxy or "").strip() or None
    proxy_id = (proxy_id or "").strip() or None
    if mode is None:
        if proxy_id:
            mode = "pool"
        elif proxy:
            mode = "custom"
        else:
            mode = "inherit"
    if mode not in LOGIN_PROXY_MODES:
        raise ValueError(f"PROXY_INVALID_BLOCKED: 未知的登录代理模式 {mode}")

    if mode == "pool":
        if not proxy_id:
            raise ValueError("PROXY_NOT_FOUND: 指定代理需要 proxy_id")
        row = get_proxy_row(proxy_id)
        if row is None:
            raise ValueError("PROXY_NOT_FOUND: 指定的代理不存在")
        proxy_dict = proxy_dict_for_row(row)
        if proxy_dict is None:
            raise ValueError("PROXY_INVALID_BLOCKED: 指定的代理配置无效")
        return LoginProxySelection(
            mode="pool",
            proxy_url=canonical_url(row),
            proxy_dict=proxy_dict,
            proxy_id=proxy_id,
            proxy="",
        )

    if mode == "custom":
        if not proxy:
            raise ValueError("PROXY_INVALID_BLOCKED: 自定义线路需要填写代理 URL")
        proxy_dict = build_proxy_dict(proxy)
        if proxy_dict is None:
            raise ValueError(
                "PROXY_INVALID_BLOCKED: Configured proxy string is invalid"
            )
        return LoginProxySelection(
            mode="custom",
            proxy_url=proxy,
            proxy_dict=proxy_dict,
            proxy_id=None,
            proxy=proxy,
        )

    if mode == "direct":
        if cfg.require_proxy_for_telegram():
            raise ValueError(
                "PROXY_REQUIRED_BLOCKED: Global policy requires a proxy "
                "for Telegram connections"
            )
        return LoginProxySelection(
            mode="direct",
            proxy_url=None,
            proxy_dict=None,
            proxy_id=None,
            proxy="",
        )

    # inherit: fall back to the global proxy, as the old bare `proxy`
    # parameter did when it was empty.
    global_proxy = cfg.get_global_proxy()
    if global_proxy:
        proxy_dict = build_proxy_dict(global_proxy)
        if proxy_dict is None:
            raise ValueError("PROXY_INVALID_BLOCKED: 全局代理配置无效")
        return LoginProxySelection(
            mode="inherit",
            proxy_url=global_proxy,
            proxy_dict=proxy_dict,
            proxy_id=None,
            proxy="",
        )
    if cfg.require_proxy_for_telegram():
        raise ValueError(
            "PROXY_REQUIRED_BLOCKED: Global policy requires a proxy "
            "for Telegram connections"
        )
    return LoginProxySelection(
        mode="inherit",
        proxy_url=None,
        proxy_dict=None,
        proxy_id=None,
        proxy="",
    )
