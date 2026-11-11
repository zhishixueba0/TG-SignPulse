"""设备指纹画像池与管理 (Task 6)。

借鉴 moeacgx/Telegram-Panel 设备画像机制及风控实践，
提供结构化的设备画像预设（Desktop, Android, iOS, macOS），
严格匹配 device_model, system_version, app_version, lang_code, system_lang_code，
杜绝特征违和拼装。

画像库来源（MIT 协议，见各项目 LICENSE）：
- SilentVGA/tgapi 的 DEVICE_POOL（39 条，Telegram 11.x / Desktop 5.10-5.11）；
- castawayGG/Ameba 的 _DEVICE_POOL（50 条真机型，Android 10.x / iOS 15-17，
  其 "Android 13" 按 sdk_version 规范化为 "SDK 33"，缺语言字段默认 en-US）。
Linux 独立成族（不归入 desktop）：Telegram Desktop 在 Windows 与 Linux 上共用
"PC 64bit" 型号，由 system_version 区分（Windows → desktop，Ubuntu/Debian → linux）。
"""

from __future__ import annotations

import random
from typing import Any, Literal, Optional

DeviceFamily = Literal["desktop", "android", "macos", "ios", "linux"]

DEFAULT_DEVICE_FAMILY: DeviceFamily = "desktop"
VALID_DEVICE_FAMILIES = ("desktop", "android", "macos", "ios", "linux")
REQUIRED_DEVICE_PROFILE_KEYS = (
    "device_model",
    "system_version",
    "app_version",
    "lang_code",
    "system_lang_code",
)

DEVICE_PROFILES: dict[str, list[dict[str, str]]] = {
    "desktop": [
        {
            "device_model": "Desktop",
            "system_version": "Windows 10",
            "app_version": "5.10.1 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Desktop",
            "system_version": "Windows 11",
            "app_version": "5.10.1 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Desktop",
            "system_version": "Windows 11",
            "app_version": "5.11.1 x64",
            "lang_code": "en",
            "system_lang_code": "en-GB",
        },
        {
            "device_model": "Desktop",
            "system_version": "Windows 10",
            "app_version": "5.9.2 x64",
            "lang_code": "de",
            "system_lang_code": "de-DE",
        },
    ],
    "linux": [
        {
            "device_model": "Desktop",
            "system_version": "Ubuntu 24.04 LTS",
            "app_version": "5.10.1 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Desktop",
            "system_version": "Linux Mint 21.3",
            "app_version": "5.10.1 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Desktop",
            "system_version": "Fedora Linux 40",
            "app_version": "5.11.1 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Desktop",
            "system_version": "Arch Linux",
            "app_version": "5.11.1 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Desktop",
            "system_version": "Debian GNU/Linux 12",
            "app_version": "5.9.2 x64",
            "lang_code": "fr",
            "system_lang_code": "fr-FR",
        },
    ],
    "macos": [
        {
            "device_model": "MacBookPro18,1",
            "system_version": "macOS 14.6.1 23G93",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "MacBookAir10,1",
            "system_version": "macOS 13.7 22H19",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Macmini9,1",
            "system_version": "macOS 15.3.1 24D70",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "MacBookPro17,1",
            "system_version": "macOS 12.7.6 21H1320",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-GB",
        },
    ],
    "android": [
        {
            "device_model": "samsung SM-S918B",
            "system_version": "SDK 34",
            "app_version": "11.2.1 (250924198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "samsung SM-A546B",
            "system_version": "SDK 34",
            "app_version": "11.2.1 (250924198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "samsung SM-G990B",
            "system_version": "SDK 33",
            "app_version": "11.1.0 (250324198)",
            "lang_code": "en",
            "system_lang_code": "en-GB",
        },
        {
            "device_model": "samsung SM-M536B",
            "system_version": "SDK 33",
            "app_version": "11.2.1 (250924198)",
            "lang_code": "es",
            "system_lang_code": "es-ES",
        },
        {
            "device_model": "Xiaomi 23049RAD8G",
            "system_version": "SDK 33",
            "app_version": "11.2.1 (250924198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 2201117TG",
            "system_version": "SDK 33",
            "app_version": "11.1.0 (250324198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi M2012K11G",
            "system_version": "SDK 32",
            "app_version": "11.0.2 (249824198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 23127PN0CC",
            "system_version": "SDK 34",
            "app_version": "11.2.1 (250924198)",
            "lang_code": "zh",
            "system_lang_code": "zh-CN",
        },
        {
            "device_model": "Google Pixel 7",
            "system_version": "SDK 34",
            "app_version": "11.2.1 (250924198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 8 Pro",
            "system_version": "SDK 35",
            "app_version": "11.2.1 (250924198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 6a",
            "system_version": "SDK 33",
            "app_version": "11.1.0 (250324198)",
            "lang_code": "de",
            "system_lang_code": "de-DE",
        },
        {
            "device_model": "OnePlus CPH2447",
            "system_version": "SDK 34",
            "app_version": "11.2.1 (250924198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus NE2215",
            "system_version": "SDK 33",
            "app_version": "11.1.0 (250324198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2343",
            "system_version": "SDK 33",
            "app_version": "11.2.1 (250924198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "vivo V2170",
            "system_version": "SDK 33",
            "app_version": "11.1.0 (250324198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "realme RMX3031",
            "system_version": "SDK 32",
            "app_version": "11.0.2 (249824198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "motorola XT2143-1",
            "system_version": "SDK 32",
            "app_version": "11.0.2 (249824198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HUAWEI ALS-L29",
            "system_version": "SDK 31",
            "app_version": "10.9.0 (246824198)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy S23",
            "system_version": "SDK 33",
            "app_version": "10.6.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy S22",
            "system_version": "SDK 32",
            "app_version": "10.5.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy S21",
            "system_version": "SDK 32",
            "app_version": "10.4.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy A54",
            "system_version": "SDK 33",
            "app_version": "10.6.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy A34",
            "system_version": "SDK 33",
            "app_version": "10.6.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy S23 Ultra",
            "system_version": "SDK 33",
            "app_version": "10.6.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 13 Pro",
            "system_version": "SDK 33",
            "app_version": "10.6.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 12",
            "system_version": "SDK 32",
            "app_version": "10.5.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi Redmi Note 12",
            "system_version": "SDK 33",
            "app_version": "10.5.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi Redmi Note 11",
            "system_version": "SDK 32",
            "app_version": "10.5.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi POCO X5 Pro",
            "system_version": "SDK 33",
            "app_version": "10.6.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi Redmi 12",
            "system_version": "SDK 33",
            "app_version": "10.6.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 8 Pro",
            "system_version": "SDK 34",
            "app_version": "10.6.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 8",
            "system_version": "SDK 34",
            "app_version": "10.6.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 7 Pro",
            "system_version": "SDK 33",
            "app_version": "10.5.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 7",
            "system_version": "SDK 33",
            "app_version": "10.5.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus 11",
            "system_version": "SDK 33",
            "app_version": "10.6.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus Nord 3",
            "system_version": "SDK 33",
            "app_version": "10.5.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO Find X6 Pro",
            "system_version": "SDK 33",
            "app_version": "10.6.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO Reno 10",
            "system_version": "SDK 33",
            "app_version": "10.5.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Realme 11 Pro",
            "system_version": "SDK 33",
            "app_version": "10.5.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Motorola Edge 40",
            "system_version": "SDK 33",
            "app_version": "10.5.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony Xperia 1 V",
            "system_version": "SDK 33",
            "app_version": "10.6.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Nokia G60",
            "system_version": "SDK 32",
            "app_version": "10.4.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy Z Fold 5",
            "system_version": "SDK 33",
            "app_version": "10.6.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy Z Flip 5",
            "system_version": "SDK 33",
            "app_version": "10.6.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 13 Ultra",
            "system_version": "SDK 33",
            "app_version": "10.6.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Huawei P60 Pro",
            "system_version": "SDK 32",
            "app_version": "10.5.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Vivo X90 Pro",
            "system_version": "SDK 33",
            "app_version": "10.5.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Asus Zenfone 10",
            "system_version": "SDK 33",
            "app_version": "10.5.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 6 Pro",
            "system_version": "SDK 33",
            "app_version": "10.5.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy A53",
            "system_version": "SDK 32",
            "app_version": "10.4.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi Redmi 10C",
            "system_version": "SDK 32",
            "app_version": "10.4.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Motorola Moto G73",
            "system_version": "SDK 33",
            "app_version": "10.5.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy M54",
            "system_version": "SDK 33",
            "app_version": "10.5.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi POCO M5",
            "system_version": "SDK 32",
            "app_version": "10.4.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Realme C55",
            "system_version": "SDK 33",
            "app_version": "10.5.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus 10T",
            "system_version": "SDK 33",
            "app_version": "10.5.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony Xperia 10 V",
            "system_version": "SDK 33",
            "app_version": "10.5.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 7a",
            "system_version": "SDK 34",
            "app_version": "10.6.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Samsung Galaxy A14",
            "system_version": "SDK 33",
            "app_version": "10.5.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi POCO F5",
            "system_version": "SDK 33",
            "app_version": "10.5.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
    ],
    "ios": [
        {
            "device_model": "iPhone 14,5",
            "system_version": "iOS 18.3.1 22D72",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 15,3",
            "system_version": "iOS 18.3.1 22D72",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 16,2",
            "system_version": "iOS 18.3.1 22D72",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-GB",
        },
        {
            "device_model": "iPhone 17,1",
            "system_version": "iOS 18.3.1 22D72",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 13,2",
            "system_version": "iOS 17.6.1 21G93",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 12,2",
            "system_version": "iOS 16.6.1 20G81",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14,7",
            "system_version": "iOS 17.4.1 21E236",
            "app_version": "11.2.1",
            "lang_code": "fr",
            "system_lang_code": "fr-FR",
        },
        {
            "device_model": "iPad 13,1",
            "system_version": "iOS 17.5.1 21F90",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 15 Pro",
            "system_version": "iOS 17.1.2",
            "app_version": "10.6.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 15",
            "system_version": "iOS 17.1.1",
            "app_version": "10.6.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14 Pro Max",
            "system_version": "iOS 16.6.1",
            "app_version": "10.5.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14",
            "system_version": "iOS 16.6",
            "app_version": "10.5.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 13 Pro",
            "system_version": "iOS 16.5",
            "app_version": "10.4.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 13",
            "system_version": "iOS 16.4.1",
            "app_version": "10.4.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 12 Pro",
            "system_version": "iOS 15.8.1",
            "app_version": "10.3.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 12",
            "system_version": "iOS 15.8",
            "app_version": "10.3.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
    ],
}

DEVICE_PRESET_POOL = DEVICE_PROFILES


def get_random_profile(family: str = DEFAULT_DEVICE_FAMILY) -> dict[str, str]:
    """从指定设备族预设中随机获取一套画像（返回独立副本）。"""
    family_normalized = (family or "").strip().lower()
    if family_normalized not in DEVICE_PROFILES:
        raise ValueError(
            f"Unknown device family: '{family}'. Expected one of {list(DEVICE_PROFILES.keys())}"
        )
    preset = random.choice(DEVICE_PROFILES[family_normalized]).copy()
    preset["device_family"] = family_normalized
    return preset


def all_preset_profiles() -> list[dict[str, str]]:
    """全库画像（各族拼成一个列表，返回独立副本）。"""
    profiles: list[dict[str, str]] = []
    for family, presets in DEVICE_PROFILES.items():
        for preset in presets:
            item = dict(preset)
            item["device_family"] = family
            profiles.append(item)
    return profiles


def profile_key(profile: dict[str, Any]) -> tuple[str, ...]:
    """画像的去重键：5 个核心字段（族不参与去重）。"""
    return tuple(str(profile.get(k, "")).strip() for k in REQUIRED_DEVICE_PROFILE_KEYS)


def pick_unused_random_profile(
    used_keys: set[tuple[str, ...]],
) -> Optional[dict[str, str]]:
    """从全库随机挑一条未被占用的画像；池子耗尽返回 None（不悄悄撞车）。"""
    candidates = [p for p in all_preset_profiles() if profile_key(p) not in used_keys]
    if not candidates:
        return None
    return random.choice(candidates)


def _detect_model_family(model: str) -> Optional[str]:
    m = model.lower()
    if "iphone" in m or "ipad" in m:
        return "ios"
    if "macbook" in m or "mac" in m:
        return "macos"
    if any(k in m for k in ("samsung", "xiaomi", "sm-", "redmi", "pixel", "android")):
        return "android"
    if "linux" in m:
        return "linux"
    if "windows" in m:
        return "desktop"
    # "PC 64bit" 是 Telegram Desktop 在 Windows 与 Linux 上的通用型号，
    # 本身不锁定系统族，由 system_version 区分（Windows 10/11 → desktop，
    # Ubuntu/Debian → linux），这里返回 None 交给系统端判断。
    return None


def _detect_system_family(system: str) -> Optional[str]:
    s = system.lower()
    if "ios" in s:
        return "ios"
    if "macos" in s or "os x" in s:
        return "macos"
    if "sdk" in s or "android" in s:
        return "android"
    if any(
        k in s
        for k in (
            "linux",
            "ubuntu",
            "debian",
            "fedora",
            "arch",
            "centos",
            "mint",
            "opensuse",
        )
    ):
        return "linux"
    if "windows" in s or "win" in s:
        return "desktop"
    return None


def validate_device_profile(profile: dict[str, Any]) -> dict[str, str]:
    """校验设备画像完整性，杜绝跨族拼装与非法参数。"""
    if not isinstance(profile, dict):
        raise ValueError("device_profile must be a dictionary")

    for key in REQUIRED_DEVICE_PROFILE_KEYS:
        val = profile.get(key)
        if val is None or not str(val).strip():
            raise ValueError(f"Missing required device profile key: '{key}'")

    model_family = _detect_model_family(str(profile["device_model"]))
    system_family = _detect_system_family(str(profile["system_version"]))

    if model_family and system_family and model_family != system_family:
        raise ValueError(
            f"Cross-family device profile mismatch: device_model indicates '{model_family}', "
            f"but system_version indicates '{system_family}'"
        )

    explicit_family = profile.get("device_family")
    if explicit_family is not None and str(explicit_family).strip():
        fam = str(explicit_family).strip().lower()
        if fam not in VALID_DEVICE_FAMILIES:
            raise ValueError(
                f"Unknown device family: '{fam}'. Expected one of {VALID_DEVICE_FAMILIES}"
            )
        if model_family and model_family != fam:
            raise ValueError(
                f"Cross-family device profile mismatch: explicit family '{fam}' conflicts with device_model '{profile['device_model']}' ({model_family})"
            )
        if system_family and system_family != fam:
            raise ValueError(
                f"Cross-family device profile mismatch: explicit family '{fam}' conflicts with system_version '{profile['system_version']}' ({system_family})"
            )

    result = {k: str(profile[k]).strip() for k in REQUIRED_DEVICE_PROFILE_KEYS}
    if explicit_family:
        result["device_family"] = str(explicit_family).strip().lower()
    elif model_family:
        result["device_family"] = model_family
    elif system_family:
        result["device_family"] = system_family
    return result
