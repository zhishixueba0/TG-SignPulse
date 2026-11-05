"""代理池模型（fork 新增）。

一张表存全部出站代理（手动添加 + 订阅同步），账号通过 accounts.json
里的 proxy_id / proxy_mode 绑定到池子里的某一行，而不是直接存 URL。
password / secret 在库里用 tg_signer.security 的 Fernet 加密存放，
读出时才解密；列表接口只返回 has_password / has_secret，永不回传明文。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text

from backend.core.database import Base
from backend.utils.time import utc_now_naive


class Proxy(Base):
    __tablename__ = "proxies"

    __table_args__ = (
        Index("ix_proxies_source_created", "source", "created_at"),
        Index("ix_proxies_country", "country_code"),
    )

    # 12 位 hex，与 telegram-manager 的代理 id 格式一致
    id = Column(String(32), primary_key=True, index=True)
    # 自动命名：旗帜-地区-IP类型，如 🇺🇸-US-ISP；用户可改名
    label = Column(String(128), nullable=True)
    # socks5 | socks4 | http | mtproto（URL 形式）；库内 dict 形式为 mtproxy
    ptype = Column(String(16), nullable=False, default="socks5")
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    # 用户名 / 密码：密码加密存放（tg_signer.security）
    username = Column(String(128), nullable=True)
    password = Column(Text, nullable=True)
    # mtproto secret：加密存放
    secret = Column(Text, nullable=True)
    # manual | freeproxydb（订阅同步来的）；同步永不删改 manual
    source = Column(String(32), nullable=False, default="manual")
    # 归属地：country_code 永远是宣告地区（BGP geofeed），
    # reg_country 是注册地（与宣告不一致时才存）
    country_code = Column(String(8), nullable=True)
    reg_country = Column(String(8), nullable=True)
    isp = Column(String(255), nullable=True)
    org = Column(String(255), nullable=True)
    is_hosting = Column(Boolean, nullable=True)
    announced_asn = Column(String(32), nullable=True)
    announced_prefix = Column(String(64), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    last_tested_at = Column(DateTime, nullable=True)
    last_test_ok = Column(Boolean, nullable=True)
    # 后台归属地补全进行中（刚添加、还没查到归属地时为 True）
    enrich_pending = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )
