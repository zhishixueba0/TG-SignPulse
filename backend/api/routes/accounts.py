"""
账号管理 API 路由（重构版）
基于原项目逻辑，使用手机号登录
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from backend.api.routes.accounts_helpers import (
    build_status_check_error_item,
    clamp_status_check_timeout,
    find_account_by_name,
    normalize_unique_account_names,
    qr_uri_to_data_url,
    resolve_account_rename_target,
)
from backend.api.routes.accounts_schemas import (
    AccountDeviceItem,
    AccountDevicesResponse,
    AccountInfo,
    AccountListResponse,
    AccountLogItem,
    AccountMoveRequest,
    AccountMoveResponse,
    AccountPauseResponse,
    AccountStatusCheckRequest,
    AccountStatusCheckResponse,
    AccountStatusItem,
    AccountStatusJobStartRequest,
    AccountUpdateRequest,
    AccountUpdateResponse,
    ClearAccountLogsResponse,
    DeleteAccountResponse,
    FolderItem,
    ImportSessionRequest,
    ImportSessionResponse,
    LoginStartRequest,
    LoginStartResponse,
    LoginVerifyRequest,
    LoginVerifyResponse,
    OfficialMessageItem,
    OfficialMessagesResponse,
    QrLoginCancelRequest,
    QrLoginCancelResponse,
    QrLoginPasswordRequest,
    QrLoginPasswordResponse,
    QrLoginStartRequest,
    QrLoginStartResponse,
    QrLoginStatusResponse,
    ResetAuthorizationsResponse,
    SessionSettingsResponse,
    SessionSettingsUpdateRequest,
    SpamCheckResponse,
    StandaloneSessionExportRequest,
    StandaloneSessionExportResponse,
    TerminateDeviceResponse,
    TopicItem,
    _extract_last_bot_message,
)
from backend.core.auth import get_current_user
from backend.core.rate_limit import compose_rate_limit_key, get_rate_limiter
from backend.models.user import User
from backend.services.account_lifecycle import (
    AccountChangePlan,
    apply_account_change_plan,
    build_account_change_plan,
)
from backend.services.telegram import get_telegram_service
from backend.utils.account_locks import AccountLockTimeout
from backend.utils.names import validate_storage_name

router = APIRouter()
logger = logging.getLogger("backend.accounts_api")
rate_limiter = get_rate_limiter()


def _apply_rate_limit(
    scope: str,
    request: Request,
    detail: str,
    *parts: str,
    max_attempts: int,
    window_seconds: int,
    block_seconds: int,
) -> str:
    if getattr(request.state, f"rate_limit_checked_{scope}", False):
        return getattr(request.state, f"rate_limit_key_{scope}")
    key = compose_rate_limit_key(request, *parts)
    rate_limiter.hit(
        scope=scope,
        key=key,
        max_attempts=max_attempts,
        window_seconds=window_seconds,
        block_seconds=block_seconds,
        detail=detail,
    )
    return key


@router.post("/login/start", response_model=LoginStartResponse)
async def start_account_login(
    request: LoginStartRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    开始账号登录流程（发送验证码）

    1. 用户输入账号名和手机号
    2. 系统发送验证码到手机
    3. 返回 phone_code_hash 用于后续验证
    """
    try:
        limit_key = _apply_rate_limit(
            "accounts.login.start",
            http_request,
            "Too many account login code requests. Please try again later.",
            request.account_name,
            request.phone_number,
            max_attempts=6,
            window_seconds=600,
            block_seconds=900,
        )
        result = await get_telegram_service().start_login(
            account_name=request.account_name,
            phone_number=request.phone_number,
            proxy=request.proxy,
            proxy_id=request.proxy_id,
            proxy_mode=request.proxy_mode,
        )
        rate_limiter.reset("accounts.login.start", limit_key)

        return LoginStartResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(
            "发送验证码失败 account=%s: %s", request.account_name, e, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="发送验证码失败，请稍后重试",
        )


@router.post("/login/verify", response_model=LoginVerifyResponse)
async def verify_account_login(
    request: LoginVerifyRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    验证账号登录（输入验证码和可选的2FA密码）

    1. 用户输入验证码
    2. 如果启用了2FA，还需要输入2FA密码
    3. 验证成功后，生成 session 文件
    """
    try:
        limit_key = _apply_rate_limit(
            "accounts.login.verify",
            http_request,
            "Too many account login verification attempts. Please try again later.",
            request.account_name,
            request.phone_number,
            max_attempts=8,
            window_seconds=600,
            block_seconds=900,
        )
        result = await get_telegram_service().verify_login(
            account_name=request.account_name,
            phone_number=request.phone_number,
            phone_code=request.phone_code,
            phone_code_hash=request.phone_code_hash,
            password=request.password,
            proxy=request.proxy,
            proxy_id=request.proxy_id,
            proxy_mode=request.proxy_mode,
        )
        rate_limiter.reset("accounts.login.verify", limit_key)

        return LoginVerifyResponse(
            success=True,
            user_id=result.get("user_id"),
            first_name=result.get("first_name"),
            username=result.get("username"),
            message="登录成功",
        )

    except ValueError as e:
        message = str(e)
        lowered = message.lower()
        # 稳定错误码替代中文文案透传：前端按码分支，不随文案/语言漂移
        if "两步验证" in message or "session_password_needed" in lowered:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SESSION_PASSWORD_NEEDED",
            ) from e
        if "2fa 密码错误" in lowered or "passwordhashinvalid" in lowered:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PASSWORD_HASH_INVALID",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=message
        ) from e
    except Exception as e:
        logger.error(
            "登录验证失败 account=%s: %s", request.account_name, e, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="登录验证失败，请稍后重试",
        )


@router.post("/qr/start", response_model=QrLoginStartResponse)
async def start_qr_login(
    request: QrLoginStartRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
):
    """开始扫码登录流程"""
    try:
        limit_key = _apply_rate_limit(
            "accounts.qr.start",
            http_request,
            "Too many QR login requests. Please try again later.",
            request.account_name,
            max_attempts=8,
            window_seconds=600,
            block_seconds=900,
        )
        result = await get_telegram_service().start_qr_login(
            account_name=request.account_name,
            proxy=request.proxy,
            proxy_id=request.proxy_id,
            proxy_mode=request.proxy_mode,
        )
        rate_limiter.reset("accounts.qr.start", limit_key)

        return QrLoginStartResponse(
            login_id=result["login_id"],
            qr_uri=result["qr_uri"],
            qr_image=qr_uri_to_data_url(result.get("qr_uri") or ""),
            expires_at=result["expires_at"],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(
            "开始扫码登录失败 account=%s: %s", request.account_name, e, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="开始扫码登录失败，请稍后重试",
        )


@router.get("/qr/status", response_model=QrLoginStatusResponse)
async def get_qr_login_status(
    login_id: str, current_user: User = Depends(get_current_user)
):
    """获取扫码登录状态"""
    try:
        result = await get_telegram_service().get_qr_login_status(login_id)
        account = result.get("account")
        if account:
            account = AccountInfo(**account)
        return QrLoginStatusResponse(
            status=result.get("status"),
            expires_at=result.get("expires_at"),
            message=result.get("message"),
            account=account,
            user_id=result.get("user_id"),
            first_name=result.get("first_name"),
            username=result.get("username"),
        )
    except Exception as e:
        logger.error("获取扫码状态失败 login_id=%s: %s", login_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取扫码状态失败，请稍后重试",
        )


@router.post("/qr/password", response_model=QrLoginPasswordResponse)
async def submit_qr_login_password(
    request: QrLoginPasswordRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
):
    """提交扫码登录 2FA 密码"""
    try:
        limit_key = _apply_rate_limit(
            "accounts.qr.password",
            http_request,
            "Too many QR password attempts. Please try again later.",
            request.login_id,
            max_attempts=5,
            window_seconds=600,
            block_seconds=900,
        )
        result = await get_telegram_service().submit_qr_password(
            request.login_id, request.password
        )
        rate_limiter.reset("accounts.qr.password", limit_key)
        account = result.get("account")
        if account:
            account = AccountInfo(**account)
        return QrLoginPasswordResponse(
            success=True,
            message=result.get("message", "登录成功"),
            account=account,
            user_id=result.get("user_id"),
            first_name=result.get("first_name"),
            username=result.get("username"),
        )
    except ValueError as e:
        logger.warning("QR 密码校验失败 login_id=%s error=%s", request.login_id, e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(
            "提交 2FA 密码失败 login_id=%s: %s", request.login_id, e, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="提交 2FA 密码失败，请稍后重试",
        )


@router.post("/qr/cancel", response_model=QrLoginCancelResponse)
async def cancel_qr_login(
    request: QrLoginCancelRequest, current_user: User = Depends(get_current_user)
):
    """取消扫码登录"""
    try:
        success = await get_telegram_service().cancel_qr_login(request.login_id)
        return QrLoginCancelResponse(
            success=success,
            message="已取消" if success else "登录已失效",
        )
    except Exception as e:
        logger.error(
            "取消扫码登录失败 login_id=%s: %s", request.login_id, e, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="取消扫码登录失败，请稍后重试",
        )


def _extract_session_from_zip(
    zip_bytes: bytes, account_name: str = ""
) -> Optional[bytes]:
    """从 ZIP 里找 Telethon/Pyrogram .session 文件并返回其内容。

    tg-manager 导出的会话包就是这种 ZIP（<手机号>.session + .json），
    不是 TData。路由不能一见 ZIP 就当 TData，否则这种包会报
    TDATA_INVALID_STRUCTURE。返回 None 表示 ZIP 里没有 .session 文件，
    调用方再走 TData 路径（由它校验结构并报错）。
    优先取与账号名匹配的 .session，没有匹配时取第一个。
    """
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = [
                n
                for n in zf.namelist()
                if n.lower().endswith(".session") and not n.endswith("/")
            ]
            if not names:
                return None
            if account_name:
                for n in names:
                    base = n.rsplit("/", 1)[-1]
                    if base.rsplit(".session", 1)[0] == account_name:
                        return zf.read(n)
            return zf.read(names[0])
    except Exception:
        return None


def _is_zip_payload(payload: bytes | str) -> bool:
    """Check whether session payload is a ZIP archive (e.g. Telegram Desktop TData)."""
    if isinstance(payload, bytes):
        return (
            payload.startswith(b"PK")
            or payload.startswith(b"PK")
            or payload.startswith(b"PK")
        )
    if isinstance(payload, str):
        cleaned = payload.strip()
        try:
            raw = base64.b64decode(cleaned)
            return (
                raw.startswith(b"PK") or raw.startswith(b"PK") or raw.startswith(b"PK")
            )
        except Exception:
            return False
    return False


@router.post("/import-session", response_model=ImportSessionResponse)
async def import_session(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """导入外部 Telegram 会话（Telethon SQLite, Pyrogram SQLite, Pyrogram StringSession, 或 TData zip）。"""
    content_type = request.headers.get("content-type", "")

    account_name = ""
    session_type = "auto"
    session_content: Optional[str] = None
    force = False
    proxy: Optional[str] = None
    proxy_id: Optional[str] = None
    proxy_mode: Optional[str] = None
    tdata_password: Optional[str] = None
    payload: bytes | str = ""

    if "multipart/form-data" in content_type:
        form = await request.form()
        account_name = str(form.get("account_name") or "").strip()
        session_type = str(form.get("session_type") or "auto").strip()
        force_raw = form.get("force")
        force = (
            str(force_raw).lower() in ("true", "1") if force_raw is not None else False
        )
        proxy_raw = form.get("proxy")
        proxy = str(proxy_raw).strip() if proxy_raw else None
        proxy_id_raw = form.get("proxy_id")
        proxy_id = str(proxy_id_raw).strip() if proxy_id_raw else None
        proxy_mode_raw = form.get("proxy_mode")
        proxy_mode = str(proxy_mode_raw).strip() if proxy_mode_raw else None
        tdata_pwd_raw = form.get("tdata_password")
        tdata_password = str(tdata_pwd_raw).strip() if tdata_pwd_raw else None

        file = form.get("file")
        if file and hasattr(file, "read"):
            payload = await file.read()
        else:
            session_content = form.get("session_content")
            if session_content:
                payload = str(session_content).strip()
    else:
        try:
            body = await request.json()
            req = ImportSessionRequest(**body)
            account_name = req.account_name.strip()
            session_type = req.session_type
            session_content = req.session_content
            force = req.force
            proxy = req.proxy
            proxy_id = req.proxy_id
            proxy_mode = req.proxy_mode
            tdata_password = req.tdata_password.strip() if req.tdata_password else None
            if not session_content:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="session_content is required",
                )
            payload = session_content.strip()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON request: {exc}",
            ) from exc

    if not account_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="account_name is required",
        )

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No session payload provided (file or session_content is required)",
        )

    MAX_SESSION_UPLOAD_SIZE = 50 * 1024 * 1024
    if (isinstance(payload, bytes) and len(payload) > MAX_SESSION_UPLOAD_SIZE) or (
        isinstance(payload, str) and len(payload) > MAX_SESSION_UPLOAD_SIZE * 4 // 3
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Session payload exceeds 50MB limit",
        )

    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    svc = get_telegram_service()
    try:
        if _is_zip_payload(payload):
            zip_bytes = (
                payload
                if isinstance(payload, bytes)
                else base64.b64decode(payload.strip())
            )
            # ZIP 里含 .session 文件（如 tg-manager 导出包）→ 走 Telethon 导入；
            # 不含才走 TData（由它校验 key_datas 并报错）。
            session_bytes = _extract_session_from_zip(zip_bytes, account_name)
            if session_bytes is not None:
                res = await svc.import_session(
                    account_name=account_name,
                    payload=session_bytes,
                    session_type=session_type,
                    force=force,
                    proxy=proxy,
                    proxy_id=proxy_id,
                    proxy_mode=proxy_mode,
                )
            else:
                res = await svc.import_tdata_session(
                    account_name=account_name,
                    zip_payload=zip_bytes,
                    proxy_id=proxy_id,
                    proxy_mode=proxy_mode,
                    password=tdata_password,
                    force=force,
                    proxy=proxy,
                )
        else:
            res = await svc.import_session(
                account_name=account_name,
                payload=payload,
                session_type=session_type,
                force=force,
                proxy=proxy,
                proxy_id=proxy_id,
                proxy_mode=proxy_mode,
            )
        return ImportSessionResponse(
            success=True,
            account_name=account_name,
            user_id=res.get("user_id"),
            first_name=res.get("first_name"),
            username=res.get("username"),
            message="会话导入成功",
        )
    except AccountLockTimeout:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ACCOUNT_BUSY",
        )
    except RuntimeError as e:
        err_msg = str(e)
        if "TDATA_CONVERTER_UNAVAILABLE" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TDATA_CONVERTER_UNAVAILABLE",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg,
        )
    except ValueError as e:
        err_msg = str(e)
        if "TDATA_PASSWORD_REQUIRED" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TDATA_PASSWORD_REQUIRED",
            )
        if "TDATA_PASSWORD_INVALID" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TDATA_PASSWORD_INVALID",
            )
        if "already exists" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=err_msg,
            )
        if "IMPORTED_SESSION_UNAUTHORIZED" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="IMPORTED_SESSION_UNAUTHORIZED",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg,
        )
    except Exception as e:
        logger.error("Import session failed for %s: %s", account_name, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import session failed: {e}",
        )


@router.get("/device-profiles")
def list_device_profiles(current_user: User = Depends(get_current_user)):
    """设备指纹预设列表（编辑账号时选择用）。

    返回各设备族（desktop / android / macos / ios / linux）的预设画像，
    每条含 device_model / system_version / app_version / lang_code /
    system_lang_code。前端不硬编码预设，后端改了这里自动跟上。
    """
    from backend.services.telegram.device_profiles import DEVICE_PROFILES

    return {
        "success": True,
        "families": {
            family: [dict(profile) for profile in presets]
            for family, presets in DEVICE_PROFILES.items()
        },
    }


@router.get("", response_model=AccountListResponse)
def list_accounts(current_user: User = Depends(get_current_user)):
    """
    获取所有账号列表

    返回所有 session 文件对应的账号
    """
    try:
        accounts = get_telegram_service().list_accounts()

        return AccountListResponse(
            accounts=[AccountInfo(**acc) for acc in accounts], total=len(accounts)
        )

    except Exception as e:
        logger.error("获取账号列表失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ACCOUNTS_LOAD_FAILED",
        )


@router.post("/status/check", response_model=AccountStatusCheckResponse)
async def check_accounts_status(
    request: AccountStatusCheckRequest, current_user: User = Depends(get_current_user)
):
    """
    批量检测账号状态（同步，兼容旧客户端）。

    账号较多时建议改用 POST /accounts/status/check-jobs 异步 Job。
    说明：
    - 默认按当前账号列表检测；
    - 顺序检测并做轻微节流，避免刷新页面时触发请求洪峰。
    - 超过 8 个账号时仍同步执行，但前端批量入口已切到 Job API。
    """
    service = get_telegram_service()
    try:
        fallback = [item.get("name", "") for item in service.list_accounts()]
        names = normalize_unique_account_names(
            request.account_names,
            fallback_names=fallback,
        )
        # 暂停账号不参与批量检测（Batch 5）；手动单账号检测不受影响
        from backend.utils.tg_session import get_account_profile

        names = [
            name
            for name in names
            if not (get_account_profile(name) or {}).get("paused")
        ]
        timeout_seconds = clamp_status_check_timeout(request.timeout_seconds)
        results: list[AccountStatusItem] = []
        for idx, name in enumerate(names):
            try:
                item = await service.check_account_status(
                    name, timeout_seconds=timeout_seconds
                )
            except Exception as exc:
                item = build_status_check_error_item(name, exc)
            results.append(AccountStatusItem(**item))
            if idx < len(names) - 1:
                await asyncio.sleep(0.15)

        return AccountStatusCheckResponse(results=results)
    except Exception as e:
        logger.error("账号状态检测失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ACCOUNT_CHECK_FAILED",
        )


@router.post("/status/check-jobs", status_code=status.HTTP_201_CREATED)
async def start_account_status_check_job(
    request: AccountStatusJobStartRequest,
    current_user: User = Depends(get_current_user),
):
    """启动账号会话状态批量检测 Job（可取消、可查询进度）。"""
    from backend.services.account_status_jobs import start_account_status_check_job

    try:
        return start_account_status_check_job(
            account_names=request.account_names,
            timeout_seconds=request.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        logger.exception("启动账号状态检测任务失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ACCOUNT_CHECK_START_FAILED",
        )


@router.get("/status/check-jobs")
def list_account_status_check_jobs(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
):
    from backend.services.account_status_jobs import list_account_status_jobs
    from tg_signer.utils import clamp

    return {"jobs": list_account_status_jobs(limit=clamp(limit, 1, 50))}


@router.get("/status/check-jobs/{job_id}")
def get_account_status_check_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    from backend.services.account_status_jobs import get_account_status_job

    job = get_account_status_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="JOB_NOT_FOUND"
        )
    return job


@router.post("/status/check-jobs/{job_id}/cancel")
def cancel_account_status_check_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    from backend.services.account_status_jobs import cancel_account_status_job

    if not cancel_account_status_job(job_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="JOB_NOT_CANCELABLE",
        )
    return {"ok": True, "job_id": job_id}


def _build_history_log_item(
    item: dict, idx: int, account_name: Optional[str] = None
) -> dict:
    """历史日志条目统一构造：任务名兜底、消息/摘要/状态文案与失败分类归一。

    供最近日志与账号日志两个列表端点复用，避免展示字段漂移。
    """
    task_name = item.get("task_name") or "未知任务"
    success = bool(item.get("success", False))
    return {
        "id": idx + 1,
        "account_name": account_name or item.get("account_name", ""),
        "task_name": task_name,
        "message": item.get("message") or ("执行成功" if success else "执行失败"),
        "summary": f"任务: {task_name} {'成功' if success else '失败'}",
        "bot_message": _extract_last_bot_message(item) or None,
        "success": success,
        "created_at": item.get("time", ""),
        "failure_category": item.get("failure_category") or None,
    }


@router.get("/logs/recent", response_model=list[dict])
def get_recent_account_logs(
    limit: int = 50, current_user: User = Depends(get_current_user)
):
    from backend.services.sign_tasks import get_sign_task_service
    from tg_signer.utils import clamp

    limit = clamp(limit, 1, 200)

    history = get_sign_task_service().get_recent_history_logs(limit=limit)
    return [_build_history_log_item(item, idx) for idx, item in enumerate(history)]


class AccountLifecyclePreviewRequest(BaseModel):
    operation: Literal["delete", "rename"]
    new_name: str | None = None
    history_policy: Literal["retain", "delete"] = "retain"


class AccountLifecycleApplyRequest(BaseModel):
    plan: AccountChangePlan


def _lifecycle_http_error(exc: ValueError) -> HTTPException:
    detail = str(exc)
    code = (
        409
        if detail.startswith(
            ("ACCOUNT_BUSY", "ACCOUNT_PLAN_STALE", "ACCOUNT_ALREADY_EXISTS")
        )
        else 404
        if detail == "ACCOUNT_NOT_FOUND"
        else 400
    )
    return HTTPException(status_code=code, detail=detail)


@router.post("/{account_name}/lifecycle/preview")
async def preview_account_lifecycle(
    account_name: str,
    request: AccountLifecyclePreviewRequest,
    current_user: User = Depends(get_current_user),
):
    """Resolve every affected resource without changing account state."""
    try:
        return (
            await build_account_change_plan(
                account_name,
                request.operation,
                request.new_name,
                request.history_policy,
            )
        ).to_dict()
    except ValueError as exc:
        raise _lifecycle_http_error(exc) from exc


@router.post("/{account_name}/lifecycle/apply")
async def apply_account_lifecycle(
    account_name: str,
    request: AccountLifecycleApplyRequest,
    current_user: User = Depends(get_current_user),
):
    """Revalidate the reviewed plan before applying the transaction."""
    try:
        if (
            validate_storage_name(account_name, field_name="account_name")
            != request.plan.old_name
        ):
            raise ValueError("Account path does not match plan")
        return await apply_account_change_plan(request.plan)
    except ValueError as exc:
        raise _lifecycle_http_error(exc) from exc


@router.delete("/{account_name}", response_model=DeleteAccountResponse)
async def delete_account(
    account_name: str, current_user: User = Depends(get_current_user)
):
    """
    删除账号（删除 session 文件）

    注意：删除后无法恢复，需要重新登录
    """
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        success = await get_telegram_service().delete_account(account_name)

        if success:
            # 清理签到服务内存中的账号痕迹：冷却时间戳与账号锁，
            # 避免同名重建账号继承旧冷却、锁实例残留
            from backend.services.sign_tasks import get_sign_task_service

            sign_svc = get_sign_task_service()
            sign_svc._account_last_run_end.pop(account_name, None)
            sign_svc._account_locks.pop(account_name, None)
            return DeleteAccountResponse(
                success=True, message=f"账号 {account_name} 已删除"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="ACCOUNT_NOT_FOUND",
            )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error("删除账号失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ACCOUNT_DELETE_FAILED",
        )


@router.get("/{account_name}/exists")
def check_account_exists(
    account_name: str, current_user: User = Depends(get_current_user)
):
    """检查账号是否存在"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    exists = get_telegram_service().account_exists(account_name)
    return {"exists": exists, "account_name": account_name}


@router.get("/{account_name}/devices", response_model=AccountDevicesResponse)
async def list_account_devices(
    account_name: str, current_user: User = Depends(get_current_user)
):
    """获取账号已登录设备/授权会话列表。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        devices = await get_telegram_service().list_account_devices(account_name)
        return AccountDevicesResponse(
            devices=[AccountDeviceItem(**item) for item in devices],
            total=len(devices),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("获取设备列表失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取设备列表失败，请稍后重试",
        )


@router.delete(
    "/{account_name}/devices/{auth_hash}", response_model=TerminateDeviceResponse
)
async def terminate_account_device(
    account_name: str,
    auth_hash: str,
    current_user: User = Depends(get_current_user),
):
    """踢下线指定已登录设备。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        success = await get_telegram_service().terminate_account_device(
            account_name, int(auth_hash)
        )
        return TerminateDeviceResponse(
            success=success,
            message="设备已下线" if success else "设备下线失败",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("设备下线失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="设备下线失败，请稍后重试",
        )


@router.post(
    "/{account_name}/session-exports",
    response_model=StandaloneSessionExportResponse,
)
async def export_standalone_session(
    account_name: str,
    request: Optional[StandaloneSessionExportRequest] = None,
    current_user: User = Depends(get_current_user),
):
    """派生独立 SessionString 导出（基于 Telegram 官方扫码授权协议）。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        if not get_telegram_service().account_exists(account_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="账号不存在",
            )
        device_model = (
            request.device_model
            if request and request.device_model
            else "TG-SignPulse Exported Session"
        )
        timeout_seconds = (
            request.timeout_seconds if request and request.timeout_seconds else 60.0
        )

        result = await get_telegram_service().export_standalone_session(
            account_name,
            device_model=device_model,
            timeout_seconds=timeout_seconds,
        )
        if not result.success:
            if result.error == "ACCOUNT_BUSY":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="ACCOUNT_BUSY",
                )
            if result.error == "ACCOUNT_NOT_FOUND":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="账号不存在",
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.error or "派生独立 Session 失败",
            )
        return StandaloneSessionExportResponse(
            success=True,
            session_string=result.session_string,
            dc_id=result.dc_id,
            user_id=result.user_id,
            message="派生独立 Session 成功",
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("派生独立 Session 失败 %s: %s", account_name, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"派生独立 Session 失败: {e}",
        )


@router.post(
    "/{account_name}/devices/reset-others", response_model=ResetAuthorizationsResponse
)
async def reset_account_authorizations(
    account_name: str,
    current_user: User = Depends(get_current_user),
):
    """一键清退账号除当前会话外的所有已授权设备。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        if not get_telegram_service().account_exists(account_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="账号不存在",
            )
        await get_telegram_service().reset_account_authorizations(account_name)
        return ResetAuthorizationsResponse(
            success=True,
            message="已成功清退其他设备",
        )
    except HTTPException:
        raise
    except AccountLockTimeout:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ACCOUNT_BUSY",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("清退其他设备失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="清退其他设备失败，请稍后重试",
        )


@router.get("/{account_name}/session-settings", response_model=SessionSettingsResponse)
async def get_account_session_settings(
    account_name: str,
    http_request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
):
    """读取账号的 Telegram 原生保留时长（读限流 40 次/60s，no-store）。"""
    _apply_rate_limit(
        "accounts.session_settings.read",
        http_request,
        "读取保留时长过于频繁，请稍后再试",
        account_name,
        max_attempts=40,
        window_seconds=60,
        block_seconds=60,
    )
    response.headers["Cache-Control"] = "no-store"
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        settings = await get_telegram_service().get_session_settings(account_name)
        return SessionSettingsResponse(**settings)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except AccountLockTimeout:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="ACCOUNT_BUSY"
        ) from None
    except Exception as e:
        logger.error("读取保留时长失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="读取保留时长失败，请稍后重试",
        ) from e


@router.put("/{account_name}/session-settings", response_model=SessionSettingsResponse)
async def update_account_session_settings(
    account_name: str,
    request: SessionSettingsUpdateRequest,
    http_request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
):
    """设置账号的 Telegram 原生保留时长（写限流 12 次/600s，no-store）。

    两项都空 → SESSION_SETTINGS_EMPTY；超范围 → *_TTL_INVALID（400）。
    """
    _apply_rate_limit(
        "accounts.session_settings.write",
        http_request,
        "设置保留时长过于频繁，请稍后再试",
        account_name,
        max_attempts=12,
        window_seconds=600,
        block_seconds=600,
    )
    response.headers["Cache-Control"] = "no-store"
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        settings = await get_telegram_service().set_session_settings(
            account_name,
            authorization_ttl_days=request.authorization_ttl_days,
            account_ttl_days=request.account_ttl_days,
        )
        return SessionSettingsResponse(**settings)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except AccountLockTimeout:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="ACCOUNT_BUSY"
        ) from None
    except Exception as e:
        logger.error("设置保留时长失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="设置保留时长失败，请稍后重试",
        ) from e


@router.get(
    "/{account_name}/official-messages", response_model=OfficialMessagesResponse
)
async def list_account_official_messages(
    account_name: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
):
    """读取账号和 Telegram 官方服务号 777000 的最近消息。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        messages = await get_telegram_service().list_official_messages(
            account_name, limit=limit
        )
        return OfficialMessagesResponse(
            messages=[OfficialMessageItem(**item) for item in messages],
            total=len(messages),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("获取官方消息失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取官方消息失败，请稍后重试",
        )


@router.get("/{account_name}/avatar")
async def get_account_avatar(
    account_name: str, current_user: User = Depends(get_current_user)
):
    """获取账号 Telegram 头像（带本地缓存）"""
    from fastapi.responses import Response

    from backend.core.config import get_settings
    from backend.services import avatar_cache

    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    settings = get_settings()
    avatar_cache_dir = settings.resolve_workdir() / "avatars"
    avatar_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = avatar_cache_dir / f"{account_name}.jpg"
    no_avatar_marker = avatar_cache_dir / f"{account_name}.no_avatar"

    # 如果已标记为无头像（7天内），直接返回 404
    if avatar_cache.marker_hits_no_avatar(no_avatar_marker):
        raise HTTPException(status_code=404, detail="No avatar available")

    # 如果缓存存在且不超过 7 天，直接返回
    cached = avatar_cache.read_cached_avatar(cache_file)
    if cached is not None:
        return Response(content=cached, media_type="image/jpeg")

    # 尝试下载头像
    try:
        avatar_bytes = await avatar_cache.get_avatar_bytes(
            cache_file,
            no_avatar_marker,
            lambda: get_telegram_service().download_account_avatar(account_name),
        )
        if avatar_bytes:
            return Response(content=avatar_bytes, media_type="image/jpeg")
        else:
            # 明确判定无头像才写标记
            avatar_cache.mark_no_avatar(no_avatar_marker)
    except Exception:
        # 瞬时下载失败：回退缓存即可，不写"无头像"标记，下次请求重试
        logger.warning("获取账号头像失败 account=%s", account_name, exc_info=True)
        stale = avatar_cache.read_avatar_file(cache_file)
        if stale is not None:
            return Response(content=stale, media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="No avatar available")


@router.patch("/{account_name}", response_model=AccountUpdateResponse)
async def update_account(
    account_name: str,
    request: AccountUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    更新账号备注/代理/标签/设备画像（不影响登录状态）
    """
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    service = get_telegram_service()
    accounts = service.list_accounts(force_refresh=True)
    current_account = find_account_by_name(accounts, account_name)
    if not current_account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_name} not found",
        )

    try:
        from backend.utils.tg_session import set_account_profile

        actual_account_name = str(current_account.get("name") or account_name).strip()

        # ---- 代理绑定变更：在线账号拒绝，先断开再换线路 ----
        proxy_mode_to_save = request.proxy_mode
        proxy_id_to_save = request.proxy_id
        proxy_to_save = request.proxy
        binding_requested = (
            request.proxy_mode is not None
            or request.proxy_id is not None
            or request.proxy is not None
        )
        if binding_requested:
            from backend.services.telegram.proxy_store import (
                get_account_proxy_binding,
                get_proxy_row,
                is_account_online,
            )
            from tg_signer.core import is_account_client_active as _is_active

            current_binding = get_account_proxy_binding(actual_account_name)
            target_mode = (request.proxy_mode or current_binding["mode"]).lower()
            target_proxy_id = request.proxy_id
            if request.proxy_mode == "pool":
                if not target_proxy_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="指定代理时必须选择一个代理",
                    )
                if get_proxy_row(str(target_proxy_id)) is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="所选代理不存在",
                    )
            elif request.proxy_mode == "custom":
                target_proxy_id = None
                if (
                    not (request.proxy or "").strip()
                    and not (current_account.get("proxy") or "").strip()
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="自定义线路时必须填写代理地址",
                    )
            elif request.proxy_mode in ("inherit", "direct"):
                target_proxy_id = None
                proxy_to_save = ""
            elif request.proxy_mode is None and request.proxy is not None:
                # 老版只传代理字符串：非空当自定义，清空当继承全局
                target_mode = "custom" if (request.proxy or "").strip() else "inherit"
                target_proxy_id = None
            changed = (
                target_mode != current_binding["mode"]
                or (target_proxy_id or None)
                != (current_binding.get("proxy_id") or None)
                or (
                    target_mode == "custom"
                    and (request.proxy or "").strip()
                    and (request.proxy or "").strip()
                    != (current_account.get("proxy") or "").strip()
                )
            )
            if changed and (
                is_account_online(actual_account_name)
                or _is_active(
                    actual_account_name, getattr(service, "session_dir", None)
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="账号在线时不能更换线路，请先断开连接再试",
                )
            proxy_mode_to_save = target_mode
            proxy_id_to_save = target_proxy_id

        device_profile_to_save = None
        device_family_to_save = None
        if request.device_family is not None or request.device_profile is not None:
            from backend.services.telegram.device_profiles import (
                get_random_profile,
                validate_device_profile,
            )
            from tg_signer.core import is_account_client_active

            if is_account_client_active(
                actual_account_name, getattr(service, "session_dir", None)
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="ACCOUNT_BUSY",
                )

            if request.device_profile is not None:
                profile_dict = dict(request.device_profile)
                if request.device_family is not None:
                    profile_dict["device_family"] = request.device_family
                device_profile_to_save = validate_device_profile(profile_dict)
                device_family_to_save = (
                    device_profile_to_save.get("device_family") or request.device_family
                )
            elif request.device_family is not None:
                device_family_to_save = request.device_family
                device_profile_to_save = get_random_profile(request.device_family)

        target_account_name, renamed = resolve_account_rename_target(
            actual_account_name,
            request.new_account_name,
        )
        if renamed:
            target_account_name = await service.rename_account(
                actual_account_name,
                target_account_name,
            )

        set_account_profile(
            target_account_name,
            remark=request.remark,
            proxy=proxy_to_save,
            proxy_mode=proxy_mode_to_save,
            proxy_id=proxy_id_to_save,
            tags=request.tags,
            device_family=device_family_to_save,
            device_profile=device_profile_to_save,
        )

        if renamed:
            from backend.scheduler import sync_jobs

            try:
                await sync_jobs()
            except Exception as exc:
                # 调度同步失败不应阻断改名主流程；与 config.py/batch.py 的兜底策略一致
                logger.warning("账号改名后同步调度任务失败: %s", exc)

        try:
            from backend.services.keyword_monitor import get_keyword_monitor_service

            await get_keyword_monitor_service().restart_from_tasks()
        except Exception as exc:
            # 监控重启失败仅告警，避免静默保持旧账号监控
            logger.warning("账号更新后重启关键词监控失败: %s", exc)

        updated = find_account_by_name(
            service.list_accounts(force_refresh=True),
            target_account_name,
        )
        if not updated:
            raise ValueError("账号信息更新后未找到对应账号")

        return AccountUpdateResponse(
            success=True,
            message="Account updated",
            account=AccountInfo(**updated),
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error("更新账号信息失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ACCOUNT_UPDATE_FAILED",
        )


@router.post("/{account_name}/refresh-profile", response_model=AccountUpdateResponse)
async def refresh_account_profile(
    account_name: str, current_user: User = Depends(get_current_user)
):
    """刷新账号资料：调 get_me 重拉名字/用户名/手机号/DC 并更新缓存。

    会话无效时返回 401 且不改身份数据；账号忙返回 409。
    """
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        entry = await get_telegram_service().refresh_account_profile(account_name)
        return AccountUpdateResponse(
            success=True, message="账号资料已刷新", account=AccountInfo(**entry)
        )
    except AccountLockTimeout:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ACCOUNT_BUSY")
    except HTTPException:
        raise
    except ValueError as e:
        detail = str(e)
        if detail == "ACCOUNT_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=detail
            ) from e
        if detail.startswith("ACCOUNT_SESSION_INVALID"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=detail
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from e
    except Exception as e:
        logger.error("刷新账号资料失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ACCOUNT_REFRESH_FAILED",
        )


@router.post("/{account_name}/spam-check", response_model=SpamCheckResponse)
async def check_account_spam(
    account_name: str,
    http_request: Request,
    current_user: User = Depends(get_current_user),
):
    """SpamBot 限制检测（Batch 3，双向检测）。

    给官方 @SpamBot 发 /start，读回复按开头分类，判断账号能不能
    主动私聊陌生人。只和官方 @SpamBot 对话，不给陌生人发消息。

    限流 6 次/600 秒；全局锁排队，绝不同时批量操作。
    16 秒内无回复返回 unknown，不覆盖上次结果。
    """
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        _apply_rate_limit(
            "accounts.spam_check",
            http_request,
            "Too many SpamBot checks. Please try again later.",
            account_name,
            max_attempts=6,
            window_seconds=600,
            block_seconds=900,
        )
        result = await get_telegram_service().check_spam_status(account_name)
        return SpamCheckResponse(
            success=True,
            account_name=result["account_name"],
            status=result["status"],
            bidirectional=result["bidirectional"],
            bidirectional_label=result["bidirectional_label"],
            summary=result["summary"],
            message=result["message"],
            until=result["until"],
            checked_at=result["checked_at"],
        )
    except AccountLockTimeout:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ACCOUNT_BUSY")
    except HTTPException:
        raise
    except ValueError as e:
        detail = str(e)
        if detail == "ACCOUNT_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=detail
            ) from e
        if detail.startswith("ACCOUNT_SESSION_INVALID"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=detail
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from e
    except Exception as e:
        logger.error("SpamBot 限制检测失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SPAM_CHECK_FAILED",
        )


@router.post("/{account_name}/move", response_model=AccountMoveResponse)
async def move_account(
    account_name: str,
    request: AccountMoveRequest,
    current_user: User = Depends(get_current_user),
):
    """上移/下移账号（自定义顺序持久化，Batch 2）。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        result = get_telegram_service().move_account(
            account_name, (request.direction or "").strip().lower()
        )
        return AccountMoveResponse(
            success=True,
            moved=bool(result.get("moved")),
            message=str(result.get("message") or ""),
            account=AccountInfo(**result["account"]),
        )
    except HTTPException:
        raise
    except ValueError as e:
        detail = str(e)
        if detail == "ACCOUNT_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=detail
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from e
    except Exception as e:
        logger.error("移动账号失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ACCOUNT_MOVE_FAILED",
        )


@router.post("/{account_name}/pause", response_model=AccountPauseResponse)
async def pause_account(
    account_name: str, current_user: User = Depends(get_current_user)
):
    """暂停账号（Batch 5）：不参与批量检测与自动巡检，任务执行时跳过；
    手动检测与健康检查照常，账号保持在线。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        entry = get_telegram_service().set_account_paused(account_name, True)
        return AccountPauseResponse(
            success=True, message="账号已暂停", account=AccountInfo(**entry)
        )
    except ValueError as e:
        detail = str(e)
        if detail == "ACCOUNT_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=detail
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from e
    except Exception as e:
        logger.error("暂停账号失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ACCOUNT_PAUSE_FAILED",
        )


@router.post("/{account_name}/resume", response_model=AccountPauseResponse)
async def resume_account(
    account_name: str, current_user: User = Depends(get_current_user)
):
    """恢复账号（Batch 5）：重新参与批量检测与自动巡检、任务执行。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        entry = get_telegram_service().set_account_paused(account_name, False)
        return AccountPauseResponse(
            success=True, message="账号已恢复", account=AccountInfo(**entry)
        )
    except ValueError as e:
        detail = str(e)
        if detail == "ACCOUNT_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=detail
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from e
    except Exception as e:
        logger.error("恢复账号失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ACCOUNT_RESUME_FAILED",
        )


@router.post("/logs/clear", response_model=ClearAccountLogsResponse)
def clear_recent_account_logs(current_user: User = Depends(get_current_user)):
    """清理全部最近任务执行日志"""
    try:
        from backend.services.sign_tasks import get_sign_task_service

        result = get_sign_task_service().clear_all_history_logs()
        return ClearAccountLogsResponse(
            success=True,
            cleared=result.get("removed_entries", 0),
            message="All logs cleared",
            code="LOGS_CLEARED",
        )
    except Exception:
        logger.exception("清理任务执行日志失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CLEAR_LOGS_FAILED",
        )


@router.get("/{account_name}/logs", response_model=list[AccountLogItem])
def get_account_logs(
    account_name: str, limit: int = 100, current_user: User = Depends(get_current_user)
):
    """获取账号的任务执行历史日志"""
    from backend.services.sign_tasks import get_sign_task_service
    from tg_signer.utils import clamp

    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    limit = clamp(limit, 1, 200)

    history = get_sign_task_service().get_account_history_logs(
        account_name, limit=limit
    )

    return [
        AccountLogItem(**_build_history_log_item(item, i, account_name=account_name))
        for i, item in enumerate(history[:limit])
    ]


@router.post("/{account_name}/logs/clear", response_model=ClearAccountLogsResponse)
def clear_account_logs(
    account_name: str, current_user: User = Depends(get_current_user)
):
    """清理账号的历史日志"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if not get_telegram_service().account_exists(account_name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ACCOUNT_NOT_FOUND",
        )
    try:
        from backend.services.sign_tasks import get_sign_task_service

        result = get_sign_task_service().clear_account_history_logs(account_name)
        return ClearAccountLogsResponse(
            success=True,
            cleared=result.get("removed_entries", 0),
            message="Logs cleared",
            code="LOGS_CLEARED",
        )
    except Exception:
        logger.exception("清理任务执行日志失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CLEAR_LOGS_FAILED",
        )


@router.get("/{account_name}/logs/export")
def export_account_logs(
    account_name: str, current_user: User = Depends(get_current_user)
):
    """导出账号日志为 txt 文件"""
    from fastapi.responses import Response

    from backend.services.sign_tasks import get_sign_task_service

    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    history = get_sign_task_service().get_account_history_logs(account_name, limit=None)

    content = f"账号日志: {account_name}\n"
    content += "=" * 40 + "\n\n"

    for item in history:
        time_str = item.get("time", "").replace("T", " ")[:19]
        status_text = "成功" if item.get("success") else "失败"
        content += f"[{time_str}] 任务: {item.get('task_name')} | 状态: {status_text}\n"
        if item.get("message"):
            content += f"消息: {item.get('message')}\n"
        content += "-" * 20 + "\n"

    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_acc = (
        "".join(c for c in account_name if c.isalnum() or c in ("-", "_")) or "account"
    )
    filename = f"{safe_acc}_logs_{stamp}.txt"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{account_name}/folders", response_model=list[FolderItem])
async def list_account_folders(
    account_name: str,
    timeout_seconds: float = 12.0,
    current_user: User = Depends(get_current_user),
):
    """获取指定账号的 Telegram 对话文件夹（Dialog Filters）列表。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        if not get_telegram_service().account_exists(account_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="账号不存在",
            )
        folders = await get_telegram_service().list_account_folders(
            account_name, timeout_seconds=timeout_seconds
        )
        return [FolderItem(**item) for item in folders]
    except HTTPException:
        raise
    except AccountLockTimeout:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ACCOUNT_BUSY",
        )
    except (asyncio.TimeoutError, TimeoutError) as e:
        logger.warning("获取文件夹列表超时 %s: %s", account_name, e)
        return []
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.warning("获取文件夹列表失败 %s: %s", account_name, e)
        return []


@router.get("/{account_name}/chats/{chat_id}/topics", response_model=list[TopicItem])
async def list_account_forum_topics(
    account_name: str,
    chat_id: str,
    limit: int = 100,
    timeout_seconds: float = 12.0,
    current_user: User = Depends(get_current_user),
):
    """获取指定群组的论坛话题（Forum Topics）列表。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        if not get_telegram_service().account_exists(account_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="账号不存在",
            )
        try:
            target_chat_id: int | str = int(chat_id)
        except ValueError:
            target_chat_id = chat_id

        # 快捷短路：非超级群组（非以 -100 开头的负数）不可能是 Forum 群组，直接返回空列表
        if isinstance(target_chat_id, int):
            if target_chat_id > 0 or not str(target_chat_id).startswith("-100"):
                return []

        topics = await get_telegram_service().list_forum_topics(
            account_name,
            chat_id=target_chat_id,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )
        return [TopicItem(**item) for item in topics]
    except HTTPException:
        raise
    except AccountLockTimeout:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ACCOUNT_BUSY",
        )
    except (asyncio.TimeoutError, TimeoutError) as e:
        logger.warning("获取论坛话题列表超时 %s/%s: %s", account_name, chat_id, e)
        return []
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.warning("获取论坛话题列表失败 %s/%s: %s", account_name, chat_id, e)
        return []
