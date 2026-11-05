from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text

from backend.core.database import Base
from backend.utils.time import utc_now_naive


class Account(Base):
    __tablename__ = "accounts"

    __table_args__ = (Index("ix_accounts_status_created", "status", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String(100), unique=True, nullable=False, index=True)
    api_id = Column(String(64), nullable=False)
    api_hash = Column(String(128), nullable=False)
    proxy = Column(Text, nullable=True)  # store JSON string for proxy config
    status = Column(String(32), default="idle", nullable=False)
    # 自定义顺序（Batch 2，与 profile JSON 的 sort_order 同步）与暂停标志
    # （Batch 5）。账号列表的实时读写走 profile JSON，本表仅作模型层备案。
    sort_order = Column(Integer, default=0, nullable=False)
    paused = Column(Boolean, default=False, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )
