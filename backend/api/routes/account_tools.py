"""账号工具 API（fork patch，Drive 1.0.129 account_tools 移植）。

会话包导出：原样复制当前 auth_key 的 ZIP（见
backend/services/telegram/session_package.py），与派生独立会话
（POST /api/accounts/{name}/session-exports，ExportLoginToken 流程）
是两个入口：前者是同一登录态的备份，后者是全新的独立设备。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.core.rate_limit import compose_rate_limit_key, get_rate_limiter
from backend.core.security import verify_password
from backend.models.user import User
from backend.services.telegram.session_package import build_session_zip
from backend.utils.names import validate_storage_name

logger = logging.getLogger(__name__)

router = APIRouter()
rate_limiter = get_rate_limiter()


class ExportSessionRequest(BaseModel):
    password: str


def _apply_export_rate_limit(request: Request, account_name: str) -> None:
    """10 分钟内最多 5 次导出，超了封 15 分钟（1.0.129 同款参数）。"""
    key = compose_rate_limit_key(request, account_name)
    rate_limiter.hit(
        scope="accounts.export.session",
        key=key,
        max_attempts=5,
        window_seconds=600,
        block_seconds=900,
        detail="导出会话包过于频繁，请稍后再试",
    )


@router.post("/{account_name}/export-session")
async def export_session(
    account_name: str,
    request: ExportSessionRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """导出会话包 ZIP（原样复制当前 auth_key）。

    面板密码二次确认 + 限流；成功后经后台任务发 Bot 提醒。
    响应为 application/zip，Cache-Control: no-store。
    """
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    _apply_export_rate_limit(http_request, account_name)

    password = (request.password or "").strip()
    if not password or not verify_password(password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="PANEL_PASSWORD_WRONG"
        )

    try:
        zip_bytes, filename = build_session_zip(
            account_name, exported_by=current_user.username
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("ACCOUNT_NOT_FOUND"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="ACCOUNT_NOT_FOUND"
            ) from exc
        if message.startswith("CREDENTIALS_RESOLUTION_FAILED"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=message
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SESSION_NOT_EXPORTABLE",
        ) from exc

    logger.info(
        "account session exported: %s (panel user %s)",
        account_name,
        current_user.username,
    )
    try:
        from backend.services.config import get_config_service
        from backend.services.push_notifications import (
            send_session_export_notification,
        )

        settings = get_config_service().get_global_settings()
        background_tasks.add_task(
            send_session_export_notification,
            settings,
            account_name=account_name,
            username=current_user.username,
        )
    except Exception as exc:
        logger.warning("会话导出通知入队失败: %s", exc)

    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
