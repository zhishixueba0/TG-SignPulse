"""代理解析器测试：粘贴导入的全部格式 + 去重 + 订阅块。"""

from __future__ import annotations

from backend.services.telegram.proxy_parse import (
    parse_proxy_lines,
    parse_subscription_text,
)


def test_url_with_auth():
    rows = parse_proxy_lines("socks5://user:pass@1.2.3.4:1080")
    assert len(rows) == 1
    assert rows[0]["ptype"] == "socks5"
    assert rows[0]["host"] == "1.2.3.4"
    assert rows[0]["port"] == 1080
    assert rows[0]["username"] == "user"
    assert rows[0]["password"] == "pass"


def test_tg_proxy_link_to_mtproto():
    rows = parse_proxy_lines(
        "tg://proxy?server=1.2.3.4&port=443&secret=ee0123456789abcdef0123456789abcdef"
    )
    assert len(rows) == 1
    assert rows[0]["ptype"] == "mtproto"
    assert rows[0]["secret"] == "ee0123456789abcdef0123456789abcdef"


def test_tme_proxy_and_socks_links():
    rows = parse_proxy_lines(
        "https://t.me/proxy?server=1.2.3.4&port=443&secret=dd0123456789abcdef0123456789abcdef\n"
        "https://t.me/socks?server=5.6.7.8&port=1080&user=u&pass=p"
    )
    assert len(rows) == 2
    assert rows[0]["ptype"] == "mtproto"
    assert rows[1]["ptype"] == "socks5"
    assert rows[1]["username"] == "u"
    assert rows[1]["password"] == "p"


def test_bare_and_colon_forms():
    rows = parse_proxy_lines(
        "1.2.3.4:1080\n"
        "5.6.7.8:8080:user:pass\n"
        "9.9.9.9:3128:user:pass\n"
        "10.0.0.1:1080@user:pass"
    )
    assert len(rows) == 4
    assert rows[0]["ptype"] == "socks5"  # 裸 IP:端口默认 SOCKS5
    assert rows[0]["username"] is None
    assert rows[1]["username"] == "user" and rows[1]["password"] == "pass"
    assert rows[2]["username"] == "user"
    assert rows[3]["username"] == "user" and rows[3]["password"] == "pass"


def test_base64_line_and_whole_block():
    import base64

    line = base64.b64encode(b"socks5://1.2.3.4:1080").decode()
    rows = parse_proxy_lines(f"socks://{line}")
    assert len(rows) == 1
    assert rows[0]["host"] == "1.2.3.4"

    block = base64.b64encode(b"socks5://1.1.1.1:1080\nhttp://2.2.2.2:8080").decode()
    rows = parse_proxy_lines(block)
    assert len(rows) == 2
    assert rows[1]["ptype"] == "http"


def test_dedupe_and_invalid_lines():
    rows = parse_proxy_lines(
        "socks5://1.2.3.4:1080\n1.2.3.4:1080\nnot a proxy line\n999.999.999.999:99999\n"
    )
    assert len(rows) == 1  # 重复与无效行都跳过


def test_subscription_mixed_lines():
    rows = parse_subscription_text(
        "socks5://1.2.3.4:1080\n"
        "tg://proxy?server=5.6.7.8&port=443&secret=ee0123456789abcdef0123456789abcdef\n"
        "https://example.com/not-a-proxy\n"
    )
    assert len(rows) == 2
    assert rows[1]["ptype"] == "mtproto"
