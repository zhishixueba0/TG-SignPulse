"""user_id -> 注册月份离线估计（纯函数，零网络、零 Telegram 调用）。

对照表来源：tg-id-mcp（github.com/barkoszloy/tg-id-mcp，MIT 协议），41 个参考点，
2013-08 至 2024-12。表作者自称精度 ±2-3 个月（未经验证）。

取数方式：阶梯下限。取不大于 user_id 的最大阈值对应的月份。
- 表内：status 'aprox'，月份为该阈值对应的月，卡片显示时带“约”。
- 高于表最大值（75 亿，2024-12）：status 'newer_than'，月份为表最大月
  （2024-12），卡片显示为“2024年12月之后”，绝不能把钳制值当成估计月。
- 低于表最小值：status 'older_than'，月份为表最小月（2013-08），
  卡片显示为“2013年8月之前”。
- 无/非法 user_id：(None, 'unknown')，卡片显示“未知”，绝不编造。

注意：2022-04（21 亿）到 2023-02（50 亿）之间约 10 个月没有参考点，
这段最粗；全网没有 2025 年对照表。Telegram 官方从未承诺 ID 按顺序发放，
ID 递增只是观测到的规律，不是保证。
"""

from __future__ import annotations

from typing import Optional, Tuple

# (user_id 下限阈值, 该阈值对应的注册月份)，按阈值升序。
# 含义：ID 达到该阈值时，对应月份即为该 ID 段的注册月下限。
REGISTRATION_THRESHOLDS: Tuple[Tuple[int, str], ...] = (
    (1, "2013-08"),
    (1000000, "2013-10"),
    (10000000, "2014-01"),
    (30000000, "2014-06"),
    (50000000, "2014-07"),
    (80000000, "2014-10"),
    (100000000, "2015-02"),
    (150000000, "2015-07"),
    (200000000, "2016-01"),
    (250000000, "2016-05"),
    (300000000, "2016-08"),
    (350000000, "2016-12"),
    (400000000, "2017-03"),
    (450000000, "2017-07"),
    (500000000, "2018-01"),
    (550000000, "2018-04"),
    (600000000, "2018-06"),
    (650000000, "2018-08"),
    (700000000, "2018-09"),
    (750000000, "2018-11"),
    (800000000, "2019-01"),
    (850000000, "2019-03"),
    (900000000, "2019-05"),
    (950000000, "2019-07"),
    (1000000000, "2019-09"),
    (1100000000, "2020-02"),
    (1200000000, "2020-06"),
    (1300000000, "2020-10"),
    (1400000000, "2021-01"),
    (1500000000, "2021-04"),
    (1600000000, "2021-06"),
    (1700000000, "2021-08"),
    (1800000000, "2021-10"),
    (1900000000, "2022-01"),
    (2000000000, "2022-03"),
    (2100000000, "2022-04"),
    (5000000000, "2023-02"),
    (5500000000, "2023-06"),
    (6000000000, "2023-09"),
    (6500000000, "2024-01"),
    (7000000000, "2024-06"),
    (7500000000, "2024-12"),
)


def estimate_registration_month(
    user_id: Optional[int],
) -> Tuple[Optional[str], str]:
    """由 user_id 离线估计注册月份。

    Returns:
        (month, status)：month 为 "YYYY-MM" 或 None；status 为
        'aprox'（表内插值/阶梯）、'newer_than'（高于表最大）、
        'older_than'（低于表最小）、'unknown'（无/非法 user_id）。
    """
    if not isinstance(user_id, int) or user_id <= 0:
        return (None, "unknown")
    month: Optional[str] = None
    for threshold, threshold_month in REGISTRATION_THRESHOLDS:
        if user_id >= threshold:
            month = threshold_month
        else:
            break
    if month is None:
        return (REGISTRATION_THRESHOLDS[0][1], "older_than")
    if user_id > REGISTRATION_THRESHOLDS[-1][0]:
        return (REGISTRATION_THRESHOLDS[-1][1], "newer_than")
    return (month, "aprox")
