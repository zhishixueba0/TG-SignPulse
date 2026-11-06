"""注册日期第一步：user_id 离线估计（阶梯下限）的边界测试。"""

from __future__ import annotations

from backend.utils.registration_estimate import estimate_registration_month


def test_below_min_is_older_than():
    month, status = estimate_registration_month(0)
    assert (month, status) == (None, "unknown")
    # 最小阈值是 1，任何合法 user_id 都 >= 1；负数同样 unknown
    month, status = estimate_registration_month(-5)
    assert (month, status) == (None, "unknown")


def test_within_table_returns_step_lower_bound():
    # 恰好落在阈值上
    assert estimate_registration_month(1000000) == ("2013-10", "aprox")
    assert estimate_registration_month(2100000000) == ("2022-04", "aprox")
    # 阈值之间取下限（不插值、不外推）
    assert estimate_registration_month(1500000000) == ("2021-04", "aprox")
    # 2022-04（21 亿）到 2023-02（50 亿）之间约 10 个月无参考点：取下限 2022-04
    assert estimate_registration_month(3000000000) == ("2022-04", "aprox")
    assert estimate_registration_month(4999999999) == ("2022-04", "aprox")
    assert estimate_registration_month(5000000000) == ("2023-02", "aprox")
    # 表内最大值恰好等于上限阈值时仍是 aprox
    assert estimate_registration_month(7500000000) == ("2024-12", "aprox")


def test_above_max_is_newer_than_not_a_fake_month():
    # 高于表最大值 75 亿：只给“之后”，绝不能把钳制值当成估计月
    month, status = estimate_registration_month(7500000001)
    assert (month, status) == ("2024-12", "newer_than")
    # James 自己的账号 user_id 8789956089（约 87.9 亿）高于表最大值
    month, status = estimate_registration_month(8789956089)
    assert (month, status) == ("2024-12", "newer_than")


def test_none_is_unknown():
    assert estimate_registration_month(None) == (None, "unknown")
