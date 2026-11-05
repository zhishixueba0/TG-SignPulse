"""聊天中心 API 路由（Batch 6）：对话列表 / 消息收发，仅文本。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
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


class ChatMessageItem(BaseModel):
    """单条消息（文本）"""

    id: Optional[int] = None
    date: Optional[str] = None
    text: str = ""
    outgoing: bool = False
    sender_name: str = ""
    sender_id: Optional[int] = None


class ChatMessagesResponse(BaseModel):
    messages: list[ChatMessageItem]
    total: int


class SendChatMessageRequest(BaseModel):
    """发文本消息：只发用户在输入框里写的文本"""

    chat_id: int | str
    text: str


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
