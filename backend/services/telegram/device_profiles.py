"""设备指纹画像池与管理 (Task 6)。

借鉴 moeacgx/Telegram-Panel 设备画像机制及风控实践，
提供结构化的设备画像预设（Desktop, Android, iOS, macOS），
严格匹配 device_model, system_version, app_version, lang_code, system_lang_code，
杜绝特征违和拼装。

画像库来源（全部为真实机型，网上核实），共 203 条、200 个不同机型：
- fork 原有 8 条预设（全部保留，语言统一为 en-US；账号已设的指纹不受影响；
  其中 "PC 64bit" 一个型号占 4 条：Windows 10/11、Ubuntu 24.04、Debian 12，
  靠 system_version 区分，所以不同机型数是 200 不是 203）；
- Mac 47 条：MacBook Air/Pro、Mac mini、iMac、Mac Studio、Mac Pro 近几年型号，
  真实型号标识符（MacBookPro18,1 等），Apple Support / EveryMac 核实；
- iOS 39 条：iPhone 12–17 全系（27 条）+ iPad 近几年（12 条），真实型号标识符
  （iPhone 13,1 等），theiphonewiki / Lirum Labs 核实；
- Android 109 条：三星/小米/Redmi/POCO/Pixel/一加/OPPO/vivo/realme/华为/荣耀/
  摩托罗拉/索尼/Nothing/华硕/Tecno/Infinix 近几年机型，Google Play 官方支持设备
  CSV 与 GSMArena 逐条核实，SDK 31–35 按发布年份；
- Telegram 版本均为真实历史版本：Android 10.3–11.2.1、iOS 10.10.1–13.2、
  macOS 11.2.2–13.2.0、Desktop 5.4.1–5.5.0（原 8 条）；新机型不配旧版本
  （如 iPhone 17 只配 Telegram 13.x）。
- 系统版本打散：同一构建号不再扎堆——Mac 分散在 macOS 11.7.10–15.5 共 25 个
  真实版本/构建号上（每版最多 5 条），iPhone/iPad 分散在 iOS/iPadOS 15.7–18.5
  及 27.2（每版最多 5 条），Android 按 SDK 31/33/34/35 分布（22/29/43/17）；
  每个机型只配它能跑的系统（M1→macOS 11+、M2→12+、M3→14+、M4→15+；
  iPhone 12→iOS 14.1+、13→15.0+、14→16.0+、15→17.0+、16→18.0+、17→26+；
  iPadOS 16.7.x 用 iPad 专属构建号）。
- 全部设备系统语言默认英文（en-US），地区由 Muse 定。
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
            "device_model": "PC 64bit",
            "system_version": "Windows 10",
            "app_version": "5.4.1 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "PC 64bit",
            "system_version": "Windows 11",
            "app_version": "5.5.0 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
    ],
    "android": [
        {
            "device_model": "Samsung SM-S918B",
            "system_version": "SDK 34",
            "app_version": "11.1.3 (5234)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 23127PN0CC",
            "system_version": "SDK 33",
            "app_version": "11.0.0 (5120)",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "ASUS ZS590KS",
            "system_version": "SDK 31",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "ASUS ZS673KS",
            "system_version": "SDK 31",
            "app_version": "10.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 6",
            "system_version": "SDK 31",
            "app_version": "10.6",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 6 Pro",
            "system_version": "SDK 31",
            "app_version": "10.5",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HONOR LSA-AN00",
            "system_version": "SDK 31",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HONOR NTH-AN00",
            "system_version": "SDK 31",
            "app_version": "10.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HUAWEI JAD-AL50",
            "system_version": "SDK 31",
            "app_version": "10.6",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HUAWEI NAM-AL00",
            "system_version": "SDK 31",
            "app_version": "10.5",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Infinix X662",
            "system_version": "SDK 31",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Infinix X693",
            "system_version": "SDK 31",
            "app_version": "10.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2159",
            "system_version": "SDK 31",
            "app_version": "10.6",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2173",
            "system_version": "SDK 31",
            "app_version": "10.5",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2235",
            "system_version": "SDK 31",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus DN2103",
            "system_version": "SDK 31",
            "app_version": "10.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus LE2101",
            "system_version": "SDK 31",
            "app_version": "10.6",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus LE2113",
            "system_version": "SDK 31",
            "app_version": "10.5",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus LE2123",
            "system_version": "SDK 31",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus MT2110",
            "system_version": "SDK 31",
            "app_version": "10.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "POCO M2012K11AG",
            "system_version": "SDK 31",
            "app_version": "10.6",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "POCO M2102J20SG",
            "system_version": "SDK 31",
            "app_version": "10.5",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony XQ-BC52",
            "system_version": "SDK 31",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony XQ-BQ52",
            "system_version": "SDK 31",
            "app_version": "10.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "ASUS AI2201",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "ASUS AI2202",
            "system_version": "SDK 33",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 6a",
            "system_version": "SDK 33",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 7",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 7 Pro",
            "system_version": "SDK 33",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HONOR ANB-AN00",
            "system_version": "SDK 33",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HONOR FNE-AN00",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HUAWEI CET-AL00",
            "system_version": "SDK 33",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HUAWEI DCO-AL00",
            "system_version": "SDK 33",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Infinix X676C",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Infinix X6815",
            "system_version": "SDK 33",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Infinix X6817",
            "system_version": "SDK 33",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Nothing A063",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2305",
            "system_version": "SDK 33",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2359",
            "system_version": "SDK 33",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2371",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus CPH2415",
            "system_version": "SDK 33",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus EB2103",
            "system_version": "SDK 33",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus NE2215",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "POCO 2201116PG",
            "system_version": "SDK 33",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony XQ-CC54",
            "system_version": "SDK 33",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony XQ-CQ54",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony XQ-CT54",
            "system_version": "SDK 33",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "TECNO CI6",
            "system_version": "SDK 33",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 2201116TG",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 2201117TG",
            "system_version": "SDK 33",
            "app_version": "10.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 2201122G",
            "system_version": "SDK 33",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 2201123G",
            "system_version": "SDK 33",
            "app_version": "11.0.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "ASUS AI2205",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "ASUS AI2302",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 7a",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 8",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 8 Pro",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 8a",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel Fold",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HONOR MAA-AN00",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HONOR PGT-AN00",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HONOR REA-AN00",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HUAWEI ADY-AL00",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HUAWEI MNA-AL00",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Infinix X6716",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Infinix X6838",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Nothing A065",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Nothing A142",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2495",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2531",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2599",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO PGEM10",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO PHZ110",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus CPH2447",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus CPH2487",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus CPH2493",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus CPH2581",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus CPH2585",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "POCO 22101320G",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "POCO 23049PCD8G",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "POCO 2311DRK48G",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "POCO 24069PC21G",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony XQ-DC54",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony XQ-DE54",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony XQ-DQ54",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "TECNO AD8",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "TECNO CK6",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "TECNO KI5",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 22101316G",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 2210132G",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 2211133G",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 23028RA60L",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 2306EPN60G",
            "system_version": "SDK 34",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 23078PND5G",
            "system_version": "SDK 34",
            "app_version": "11.1.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "ASUS AI2401",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 9",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 9 Pro",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 9 Pro XL",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Google Pixel 9a",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HONOR BVL-AN00",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "HONOR ELP-AN00",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO CPH2629",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OPPO PKB110",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "OnePlus CPH2649",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Sony XQ-EC54",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 2407FPN8EG",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 24117RN76G",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Xiaomi 24129PN74G",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "samsung SM-F741B",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "samsung SM-F956B",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "samsung SM-S931B",
            "system_version": "SDK 35",
            "app_version": "11.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
    ],
    "macos": [
        {
            "device_model": "MacBook Pro",
            "system_version": "macOS 14.5",
            "app_version": "10.15.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,15",
            "system_version": "macOS 12.6 21G115",
            "app_version": "11.2.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,12",
            "system_version": "macOS 14.5 23F79",
            "app_version": "12.4.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,13",
            "system_version": "macOS 14.6.1 23G93",
            "app_version": "12.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,12",
            "system_version": "macOS 15.0 24A335",
            "app_version": "13.2.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,13",
            "system_version": "macOS 15.0.1 24A348",
            "app_version": "12.10",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,10",
            "system_version": "macOS 12.6.3 21G419",
            "app_version": "11.15.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,6",
            "system_version": "macOS 13.5.1 22G90",
            "app_version": "12.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,3",
            "system_version": "macOS 14.7 23H124",
            "app_version": "12.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,6",
            "system_version": "macOS 14.7.2 23H311",
            "app_version": "12.2.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,8",
            "system_version": "macOS 14.7.4 23H420",
            "app_version": "12.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,10",
            "system_version": "macOS 14.7.5 23H527",
            "app_version": "12.4.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,7",
            "system_version": "macOS 15.0.1 24A348",
            "app_version": "12.8",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,9",
            "system_version": "macOS 15.1 24B83",
            "app_version": "12.84",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,11",
            "system_version": "macOS 15.2 24C101",
            "app_version": "13.2.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,1",
            "system_version": "macOS 15.1 24B83",
            "app_version": "12.8",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,8",
            "system_version": "macOS 15.1.1 24B91",
            "app_version": "12.84",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,6",
            "system_version": "macOS 15.2 24C101",
            "app_version": "13.2.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,7",
            "system_version": "macOS 15.3.1 24D70",
            "app_version": "12.10",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,5",
            "system_version": "macOS 15.3.2 24D81",
            "app_version": "12.8",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,12",
            "system_version": "macOS 13.6.5 22G621",
            "app_version": "11.15.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,10",
            "system_version": "macOS 15.4 24E248",
            "app_version": "12.84",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,11",
            "system_version": "macOS 15.4.1 24E263",
            "app_version": "13.2.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,4",
            "system_version": "macOS 15.3.2 24D81",
            "app_version": "12.10",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,5",
            "system_version": "macOS 15.4 24E248",
            "app_version": "12.8",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,2",
            "system_version": "macOS 15.5 24F74",
            "app_version": "12.10",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,3",
            "system_version": "macOS 15.5 24F74",
            "app_version": "12.8",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,14",
            "system_version": "macOS 13.7.1 22H221",
            "app_version": "12.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac16,9",
            "system_version": "macOS 15.4.1 24E263",
            "app_version": "12.84",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac15,14",
            "system_version": "macOS 15.5 24F74",
            "app_version": "12.84",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,8",
            "system_version": "macOS 14.6 23G80",
            "app_version": "12.4.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "MacBookAir10,1",
            "system_version": "macOS 11.7.10 20G1427",
            "app_version": "11.2.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,2",
            "system_version": "macOS 14.7 23H124",
            "app_version": "12.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "MacBookPro17,1",
            "system_version": "macOS 12.6.2 21G320",
            "app_version": "11.15.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "MacBookPro18,3",
            "system_version": "macOS 13.5.1 22G90",
            "app_version": "12.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "MacBookPro18,4",
            "system_version": "macOS 13.6.5 22G621",
            "app_version": "11.15.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "MacBookPro18,1",
            "system_version": "macOS 13.7.1 22H221",
            "app_version": "12.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "MacBookPro18,2",
            "system_version": "macOS 14.6 23G80",
            "app_version": "12.4.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,7",
            "system_version": "macOS 14.7.3 23H417",
            "app_version": "12.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,9",
            "system_version": "macOS 15.0 24A335",
            "app_version": "13.2.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,5",
            "system_version": "macOS 15.1.1 24B91",
            "app_version": "12.10",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Macmini9,1",
            "system_version": "macOS 14.7.2 23H311",
            "app_version": "12.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,3",
            "system_version": "macOS 15.3.2 24D81",
            "app_version": "12.8",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iMac21,1",
            "system_version": "macOS 14.7.5 23H527",
            "app_version": "12.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iMac21,2",
            "system_version": "macOS 15.1 24B83",
            "app_version": "13.2.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac13,1",
            "system_version": "macOS 15.3.2 24D81",
            "app_version": "12.10",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac13,2",
            "system_version": "macOS 15.5 24F74",
            "app_version": "12.8",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "Mac14,13",
            "system_version": "macOS 15.5 24F74",
            "app_version": "12.84",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
    ],
    "ios": [
        {
            "device_model": "iPhone 15 Pro",
            "system_version": "iOS 17.5.1",
            "app_version": "10.14.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 18,3",
            "system_version": "iOS 27.2 24B115",
            "app_version": "13.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 18,1",
            "system_version": "iOS 27.2 24B115",
            "app_version": "13.1.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 18,2",
            "system_version": "iOS 27.2 24B115",
            "app_version": "13.0",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 18,4",
            "system_version": "iOS 27.2 24B115",
            "app_version": "13.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 18,5",
            "system_version": "iOS 27.2 24B115",
            "app_version": "13.1.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 13,2",
            "system_version": "iOS 15.7 19H12",
            "app_version": "10.13.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 13,4",
            "system_version": "iOS 16.6.1 20G81",
            "app_version": "11.8",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14,5",
            "system_version": "iOS 15.7 19H12",
            "app_version": "10.13.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14,3",
            "system_version": "iOS 15.8.3 19H386",
            "app_version": "10.13",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14,8",
            "system_version": "iOS 16.6.1 20G81",
            "app_version": "11.8.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 15,3",
            "system_version": "iOS 17.6.1 21G93",
            "app_version": "11.14.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 15,4",
            "system_version": "iOS 17.5 21F79",
            "app_version": "11.15",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 15,5",
            "system_version": "iOS 17.7.2 21H221",
            "app_version": "11.14.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 16,1",
            "system_version": "iOS 18.2 22C152",
            "app_version": "12.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 16,2",
            "system_version": "iOS 18.5 22F76",
            "app_version": "12.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 17,3",
            "system_version": "iOS 18.0 22A3354",
            "app_version": "12.9.4",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 17,4",
            "system_version": "iOS 18.1 22B83",
            "app_version": "12.9.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 17,1",
            "system_version": "iOS 18.2.1 22C161",
            "app_version": "12.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 17,2",
            "system_version": "iOS 18.3.2 22D82",
            "app_version": "12.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 17,5",
            "system_version": "iOS 18.5 22F76",
            "app_version": "12.9",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 13,1",
            "system_version": "iOS 18.0 22A3354",
            "app_version": "12.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 13,3",
            "system_version": "iOS 18.5 22F76",
            "app_version": "12.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14,4",
            "system_version": "iOS 17.6 21G80",
            "app_version": "11.14",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14,2",
            "system_version": "iOS 18.1 22B83",
            "app_version": "12.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14,6",
            "system_version": "iOS 18.5 22F76",
            "app_version": "12.9",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 14,7",
            "system_version": "iOS 18.1.1 22B91",
            "app_version": "12.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPhone 15,2",
            "system_version": "iOS 18.5 22F76",
            "app_version": "12.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 13,1",
            "system_version": "iPadOS 15.7 19H12",
            "app_version": "10.13.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 13,4",
            "system_version": "iPadOS 17.6 21G80",
            "app_version": "11.14.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 13,8",
            "system_version": "iPadOS 18.5 22F76",
            "app_version": "12.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 13,16",
            "system_version": "iPadOS 15.7 19H12",
            "app_version": "10.13.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 13,18",
            "system_version": "iPadOS 16.6.1 20G81",
            "app_version": "11.8.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 14,1",
            "system_version": "iPadOS 18.5 22F76",
            "app_version": "12.9.3",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 14,3",
            "system_version": "iPadOS 17.7.2 21H221",
            "app_version": "11.14.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 14,5",
            "system_version": "iPadOS 18.5 22F76",
            "app_version": "12.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 14,8",
            "system_version": "iPadOS 17.5 21F79",
            "app_version": "11.15",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 14,10",
            "system_version": "iPadOS 17.7.2 21H221",
            "app_version": "11.14.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 16,3",
            "system_version": "iPadOS 18.2 22C152",
            "app_version": "12.9.2",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "iPad 16,5",
            "system_version": "iPadOS 18.5 22F76",
            "app_version": "12.9.1",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
    ],
    "linux": [
        {
            "device_model": "PC 64bit",
            "system_version": "Ubuntu 24.04",
            "app_version": "5.4.1 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
        },
        {
            "device_model": "PC 64bit",
            "system_version": "Debian 12",
            "app_version": "5.5.0 x64",
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
