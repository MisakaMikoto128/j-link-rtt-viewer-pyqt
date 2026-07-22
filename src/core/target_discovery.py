"""目标设备名自动发现：从 pylink / pyOCD 读取支持的 MCU 列表，替代 config.json 的 chip_models。

设计要点：
1. 延迟枚举 + 进程级缓存：函数首次调用时才 import pylink/pyOCD 并枚举，结果用
   functools.lru_cache 缓存，避免每次打开下拉都重新扫 DLL。
2. 过滤噪声：pylink 设备库 11130+ 条，包含大量内核名 / FPGA / SOC；只保留常见
   MCU 前缀，避免下拉被淹没。
3. 错误隔离：J-Link / pyOCD 未安装或初始化失败时返回空元组并记 warning，不影响
   UI 其它功能。
4. RTT 页只用 pylink；Flash 页按当前烧录器 kind 选择 pylink（J-Link）或 pyOCD
   （CMSIS-DAP / ST-Link）数据源。
"""

from __future__ import annotations

import functools

from .logger import get_logger

_logger = get_logger()

# 常见 MCU 前缀：pylink 11130+ 条里只保留这些，否则下拉会被 ARM7/Cortex-A/FPGA 淹没。
_COMMON_MCU_PREFIXES = (
    "STM32",
    "STM8",
    "NRF",
    "GD32",
    "EFM32",
    "LPC",
    "MK",
    "KL",
    "KV",
    "KW",
    "KINETIS",
    "MAX",
    "MSP432",
    "CC13",
    "CC26",
    "MSP430",
    "R7FA",
    "R7FS",
    "PSOC",
    "CY8C",
    "ATSAM",
    "SAM",
    "SAMD",
    "SAML",
    "MM32",
    "HC32",
    "CH32",
    "APM32",
    "AT32",
    "WCH",
)

# SEGGER 常在设备名后加括号注释，如 "STM32F030C8 (allow opt. bytes)"；
# 这些注释对应同一颗芯片的额外 Flash 算法选项， stripped 后归并到基础名。
_SEGGER_ANNOTATIONS = (
    " (ALLOW OPT. BYTES)",
    " (ALLOW SECURITY BYTES)",
    " (ALLOW TRUSTZONE)",
)


def _normalize_name(name: str) -> str:
    """统一成大写并去首尾空格。"""
    return name.strip().upper()


def _strip_segger_annotation(name: str) -> str:
    """去掉 SEGGER 设备名后的括号注释，返回基础名。"""
    upper = name.upper()
    for ann in _SEGGER_ANNOTATIONS:
        if upper.endswith(ann):
            return name[: -len(ann)].strip()
    return name.strip()


@functools.cache
def get_pylink_target_names() -> tuple[str, ...]:
    """从 pylink-square / J-Link DLL 读取支持的 MCU 设备名，返回大写排序去重元组。"""
    try:
        import pylink
    except Exception as e:  # pragma: no cover - 运行环境未装 pylink 时降级
        _logger.warning(f"pylink 不可用，无法枚举 J-Link 设备名：{e}")
        return ()

    try:
        jlink = pylink.JLink()
    except Exception as e:  # pragma: no cover - 未装 SEGGER / DLL 缺失
        _logger.warning(f"无法创建 JLink 对象：{e}")
        return ()

    try:
        count = jlink.num_supported_devices()
    except Exception as e:  # pragma: no cover
        _logger.warning(f"num_supported_devices 失败：{e}")
        return ()

    names: list[str] = []
    for i in range(count):
        try:
            dev = jlink.supported_device(i)
            raw = (dev.name or "").strip()
            if not raw:
                continue
            # 只保留常见 MCU 前缀，过滤 ARM7/Cortex-A/FPGA 等噪声
            if not raw.upper().startswith(_COMMON_MCU_PREFIXES):
                continue
            name = _normalize_name(_strip_segger_annotation(raw))
            if name and name not in names:
                names.append(name)
        except Exception:
            # 单条读取失败跳过，不影响整体枚举
            continue

    return tuple(sorted(names))


@functools.cache
def get_pyocd_target_names() -> tuple[str, ...]:
    """从 pyOCD 读取内置 target + 已安装 CMSIS-Pack 的 part_number，返回大写排序去重元组。

    注意：pyOCD import 较重（~500ms）且 CMSIS-Pack 枚举依赖磁盘索引；本函数只在用户
    真正切到 CMSIS-DAP / ST-Link 烧录器时才触发，避免冷启动阻塞。
    """
    names: list[str] = []

    # 1) 内置 target（约 200 个， lowercase key）
    try:
        from pyocd.target import TARGET

        for key in TARGET:
            norm = _normalize_name(key)
            if norm and norm not in names:
                names.append(norm)
    except Exception as e:  # pragma: no cover - 未装 pyOCD
        _logger.warning(f"pyOCD TARGET 读取失败：{e}")

    # 2) 已安装 CMSIS-Pack 的 target
    try:
        from pyocd.target.pack.pack_target import ManagedPacks

        packs = ManagedPacks.get_installed_targets() or []
        for dev in packs:
            part = getattr(dev, "part_number", "") or ""
            norm = _normalize_name(part)
            if norm and norm not in names:
                names.append(norm)
    except Exception as e:  # pragma: no cover - 无 pack 或索引损坏
        _logger.warning(f"pyOCD CMSIS-Pack 枚举失败：{e}")

    return tuple(sorted(names))


def target_names_for_burner_kind(kind: str) -> tuple[str, ...]:
    """按烧录器 kind 返回对应目标设备名列表。

    - BURNER_KIND_JLINK -> pylink
    - 其它（cmsisdap / stlink）-> pyOCD
    """
    from .probe.base import BURNER_KIND_JLINK

    if kind == BURNER_KIND_JLINK:
        return get_pylink_target_names()
    return get_pyocd_target_names()
