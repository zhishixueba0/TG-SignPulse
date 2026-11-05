from fastapi import APIRouter

from backend.api.routes import (
    account_tools,
    accounts,
    auth,
    batch,
    chats,
    config,
    data_dict,
    events,
    keyword_hits,
    logs,
    ops,
    passkey,
    plugins,
    proxies,
    semantic,
    sign_tasks_v2,
    user,
)

router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(user.router, prefix="/user", tags=["user"])
router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
# Fork patch: 账号工具（会话包导出，Drive 1.0.129 account_tools 移植）。
router.include_router(
    account_tools.router, prefix="/account-tools", tags=["account-tools"]
)
# Fork patch: 聊天中心（对话列表 / 文本消息收发）。Not upstream.
router.include_router(chats.router, prefix="/chats", tags=["chats"])
# 旧版 /api/tasks 已移除；请使用 /sign-tasks
router.include_router(sign_tasks_v2.router, prefix="/sign-tasks", tags=["sign-tasks"])
router.include_router(logs.router, prefix="/logs", tags=["logs"])
router.include_router(config.router, prefix="/config", tags=["config"])
router.include_router(events.router, prefix="/events", tags=["events"])
router.include_router(batch.router, prefix="/batch", tags=["batch"])
router.include_router(ops.router, prefix="/ops", tags=["ops"])
router.include_router(
    keyword_hits.router, prefix="/keyword-hits", tags=["keyword-hits"]
)
router.include_router(plugins.router, prefix="/plugins", tags=["plugins"])
# Fork patch: KeePassXC passkey login (Telethon). Not upstream.
router.include_router(passkey.router, prefix="/passkey", tags=["passkey"])
# Fork patch: proxy pool + per-account routing. Not upstream.
router.include_router(proxies.router, prefix="/proxies", tags=["proxies"])
router.include_router(data_dict.router, prefix="/data-dict", tags=["data-dict"])
router.include_router(semantic.router)
