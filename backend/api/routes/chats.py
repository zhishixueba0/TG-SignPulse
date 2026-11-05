"""聊天中心 API 路由（Batch 6）：对话列表 / 消息收发；图片与内联按钮（Round 4）。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models.user import User
from backend.services.telegram import get_telegram_service
from backend.utils.account_locks import AccountLockTimeout
from backend.utils.names import validate_storage_name

router = APIRouter()
logger = logging.getLogger("backend.chats_api")


class ChatDialogItem(BaseModel):
    """最近对话条目"""

    chat_id: int | str
    title: str
    type: str = ""
    username: Optional[str] = None
    unread_count: int = 0
    last_message_text: str = ""
    last_message_date: Optional[str] = None


class ChatDialogsResponse(BaseModel):
    dialogs: list[ChatDialogItem]
    total: int


class ChatButtonItem(BaseModel):
    """键盘按钮：url 由前端打开，callback 调 Telegram 回调，reply 当文本发出。"""

    row: int
    col: int
    text: str
    kind: str  # url | callback | reply
    url: Optional[str] = None


class ChatMessageItem(BaseModel):
    """单条消息（文本 + 图片 + 键盘：内联或回复）"""

    id: Optional[int] = None
    date: Optional[str] = None
    text: str = ""
    outgoing: bool = False
    sender_name: str = ""
    sender_id: Optional[int] = None
    media_type: Optional[str] = None  # 目前只有 "photo"
    keyboard_type: Optional[str] = None  # inline | reply
    buttons: list[ChatButtonItem] = []


class ChatMessagesResponse(BaseModel):
    messages: list[ChatMessageItem]
    total: int


class SendChatMessageRequest(BaseModel):
    """发文本消息：只发用户在输入框里写的文本"""

    chat_id: int | str
    text: str


class PressChatButtonRequest(BaseModel):
    """点内联键盘按钮：只点用户点的这一个按钮。"""

    chat_id: int | str
    message_id: int
    row: int
    col: int


class PressChatButtonResponse(BaseModel):
    action: str  # url | callback
    message: str = ""
    alert: bool = False
    url: Optional[str] = None


class SendChatMessageResponse(BaseModel):
    success: bool
    message: ChatMessageItem


def _handle_service_error(account_name: str, exc: Exception) -> HTTPException:
    if isinstance(exc, AccountLockTimeout):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="ACCOUNT_BUSY"
        )
    if isinstance(exc, ValueError):
        detail = str(exc)
        if detail == "账号不存在":
            return HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="ACCOUNT_NOT_FOUND"
            )
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    logger.error("聊天接口失败 account=%s: %s", account_name, exc, exc_info=True)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="CHAT_REQUEST_FAILED",
    )


@router.get("/dialogs", response_model=ChatDialogsResponse)
async def list_dialogs(
    account: str = Query(..., description="账号编号"),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """最近对话列表（默认 50 条）。"""
    try:
        account = validate_storage_name(account, field_name="account_name")
        dialogs = await get_telegram_service().list_account_dialogs(
            account, limit=limit
        )
        return ChatDialogsResponse(
            dialogs=[ChatDialogItem(**item) for item in dialogs],
            total=len(dialogs),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _handle_service_error(account, e)


@router.get("/{account_name}/messages", response_model=ChatMessagesResponse)
async def list_messages(
    account_name: str,
    chat_id: str = Query(..., description="对话 id（数字或用户名）"),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """最近消息（默认 50 条，按时间升序）。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        messages = await get_telegram_service().list_chat_messages(
            account_name, chat_id, limit=limit
        )
        return ChatMessagesResponse(
            messages=[ChatMessageItem(**item) for item in messages],
            total=len(messages),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _handle_service_error(account_name, e)


@router.get(
    "/{account_name}/media/{message_id}",
    response_class=Response,
)
async def download_chat_media(
    account_name: str,
    message_id: int,
    chat_id: str = Query(..., description="对话 id（数字或用户名）"),
    thumb: bool = Query(True, description="thumb=1 缩略图，thumb=0 原图"),
    current_user: User = Depends(get_current_user),
):
    """下载一条图片消息（JPEG）。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        data = await get_telegram_service().download_chat_media(
            account_name, chat_id, message_id, thumb=thumb
        )
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _handle_service_error(account_name, e)


@router.post("/{account_name}/button", response_model=PressChatButtonResponse)
async def press_chat_button(
    account_name: str,
    request: PressChatButtonRequest,
    current_user: User = Depends(get_current_user),
):
    """点一条消息的内联键盘按钮。链接按钮返回 url；回调按钮调 Telegram 回调。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        result = await get_telegram_service().press_chat_button(
            account_name,
            request.chat_id,
            request.message_id,
            request.row,
            request.col,
        )
        return PressChatButtonResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise _handle_service_error(account_name, e)


@router.post("/{account_name}/messages", response_model=SendChatMessageResponse)
async def send_message(
    account_name: str,
    request: SendChatMessageRequest,
    current_user: User = Depends(get_current_user),
):
    """发一条文本消息。"""
    try:
        account_name = validate_storage_name(account_name, field_name="account_name")
        sent = await get_telegram_service().send_chat_message(
            account_name, request.chat_id, request.text
        )
        return SendChatMessageResponse(success=True, message=ChatMessageItem(**sent))
    except HTTPException:
        raise
    except Exception as e:
        raise _handle_service_error(account_name, e)
