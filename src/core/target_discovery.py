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
from dataclasses import dataclass

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


@dataclass(frozen=True)
class TargetDeviceInfo:
    """目标设备元数据：名称 + 厂商 + Flash/RAM 地址与大小。"""

    name: str
    vendor: str
    flash_addr: int | None
    flash_size: int | None
    ram_addr: int | None
    ram_size: int | None


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


def _extract_flash_ram_from_memory_map(mm) -> tuple[int | None, int | None, int | None, int | None]:
    """从 pyOCD memory_map 中提取首个 FlashRegion / RamRegion 的地址与大小。"""
    flash_addr: int | None = None
    flash_size: int | None = None
    ram_addr: int | None = None
    ram_size: int | None = None
    if mm is None:
        return flash_addr, flash_size, ram_addr, ram_size

    try:
        from pyocd.core.memory_map import FlashRegion, RamRegion
    except Exception:  # pragma: no cover - pyOCD 未装或版本差异
        return flash_addr, flash_size, ram_addr, ram_size

    regions = getattr(mm, "regions", None) or ()
    for region in regions:
        if flash_addr is None and isinstance(region, FlashRegion):
            flash_addr = int(getattr(region, "start", 0) or 0)
            flash_size = int(getattr(region, "length", 0) or 0)
        if ram_addr is None and isinstance(region, RamRegion):
            ram_addr = int(getattr(region, "start", 0) or 0)
            ram_size = int(getattr(region, "length", 0) or 0)
        if flash_addr is not None and ram_addr is not None:
            break
    return flash_addr, flash_size, ram_addr, ram_size


def _pick_main_region(areas, legacy_addr, legacy_size) -> tuple[int | None, int | None]:
    """从 SEGGER aFlashArea / aRAMArea 数组里挑主区域（最大 Size）。

    pylink `supported_device()` 的 legacy `FlashAddr`/`FlashSize`（顶层字段）对带
    括号注释的变体（如 `STM32F030C8 (allow opt. bytes)`）常是选项字节区垃圾
    （如 FlashAddr=0x06000000 FlashSize=65552），不可信。真实主 Flash/RAM 在
    `aFlashArea`/`aRAMArea` 数组里。策略：
    1. 数组里若有 Size>0 的区域，取 Size 最大者（主 Flash，滤掉选项字节/配置
       小区域，如 STM32F030C8 的 area[0] 16B 选项字节）。
    2. 数组全空才回退 legacy 顶层字段。
    """
    best_addr: int | None = None
    best_size = 0
    try:
        n = len(areas)
    except Exception:
        n = 0
    for k in range(n):
        try:
            area = areas[k]
            size = int(getattr(area, "Size", 0) or 0)
            addr = int(getattr(area, "Addr", 0) or 0)
        except Exception:
            continue
        if size > best_size:
            best_size = size
            best_addr = addr
    if best_addr is not None and best_size > 0:
        return best_addr, best_size
    # 数组空 -> 回退 legacy（若合法）
    la = int(legacy_addr) if legacy_addr is not None else None
    ls = int(legacy_size) if legacy_size is not None else None
    if la is not None and ls:
        return la, ls
    return None, None


@functools.cache
def get_pylink_target_infos() -> tuple[TargetDeviceInfo, ...]:
    """从 pylink-square / J-Link DLL 读取支持的 MCU 设备信息，返回排序去重元组。"""
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

    infos: list[TargetDeviceInfo] = []
    seen: set[str] = set()
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
            if not name or name in seen:
                continue
            seen.add(name)
            vendor = str(getattr(dev, "manufacturer", "") or "")
            # legacy FlashAddr/FlashSize 对注释变体不可信；优先 aFlashArea/aRAMArea 主区域
            flash_addr, flash_size = _pick_main_region(
                getattr(dev, "aFlashArea", ()),
                getattr(dev, "FlashAddr", None),
                getattr(dev, "FlashSize", None),
            )
            ram_addr, ram_size = _pick_main_region(
                getattr(dev, "aRAMArea", ()),
                getattr(dev, "RAMAddr", None),
                getattr(dev, "RAMSize", None),
            )
            infos.append(
                TargetDeviceInfo(
                    name=name,
                    vendor=vendor,
                    flash_addr=flash_addr,
                    flash_size=flash_size,
                    ram_addr=ram_addr,
                    ram_size=ram_size,
                )
            )
        except Exception:
            # 单条读取失败跳过，不影响整体枚举
            continue

    return tuple(sorted(infos, key=lambda info: info.name))


@functools.cache
def get_pylink_target_names() -> tuple[str, ...]:
    """从 pylink-square / J-Link DLL 读取支持的 MCU 设备名，返回大写排序去重元组。"""
    return tuple(info.name for info in get_pylink_target_infos())


@functools.cache
def get_pyocd_target_infos() -> tuple[TargetDeviceInfo, ...]:
    """从 pyOCD 读取内置 target + 已安装 CMSIS-Pack 的 part_number，返回排序去重元组。

    注意：pyOCD import 较重（~500ms）且 CMSIS-Pack 枚举依赖磁盘索引；本函数只在用户
    真正切到 CMSIS-DAP / ST-Link 烧录器时才触发，避免冷启动阻塞。
    """
    infos: list[TargetDeviceInfo] = []
    seen: set[str] = set()

    # 1) 内置 target（约 200 个， lowercase key）
    try:
        from pyocd.target import TARGET

        for key, cls in TARGET.items():
            norm = _normalize_name(key)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            mm = None
            try:
                instance = cls(None)
                mm = getattr(instance, "memory_map", None)
                vendor = getattr(instance, "vendor", "") or ""
            except Exception:
                vendor = getattr(cls, "vendor", "") or ""
                mm = getattr(cls, "memory_map", None)
            flash_addr, flash_size, ram_addr, ram_size = _extract_flash_ram_from_memory_map(mm)
            infos.append(
                TargetDeviceInfo(
                    name=norm,
                    vendor=str(vendor),
                    flash_addr=flash_addr,
                    flash_size=flash_size,
                    ram_addr=ram_addr,
                    ram_size=ram_size,
                )
            )
    except Exception as e:  # pragma: no cover - 未装 pyOCD
        _logger.warning(f"pyOCD TARGET 读取失败：{e}")

    # 2) 已安装 CMSIS-Pack 的 target
    try:
        from pyocd.target.pack.pack_target import ManagedPacks

        packs = ManagedPacks.get_installed_targets() or []
        for dev in packs:
            part = getattr(dev, "part_number", "") or ""
            norm = _normalize_name(part)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            vendor = getattr(dev, "vendor", "") or ""
            mm = getattr(dev, "memory_map", None)
            flash_addr, flash_size, ram_addr, ram_size = _extract_flash_ram_from_memory_map(mm)
            infos.append(
                TargetDeviceInfo(
                    name=norm,
                    vendor=str(vendor),
                    flash_addr=flash_addr,
                    flash_size=flash_size,
                    ram_addr=ram_addr,
                    ram_size=ram_size,
                )
            )
    except Exception as e:  # pragma: no cover - 无 pack 或索引损坏
        _logger.warning(f"pyOCD CMSIS-Pack 枚举失败：{e}")

    return tuple(sorted(infos, key=lambda info: info.name))


@functools.cache
def get_pyocd_target_names() -> tuple[str, ...]:
    """从 pyOCD 读取内置 target + 已安装 CMSIS-Pack 的 part_number，返回大写排序去重元组。"""
    return tuple(info.name for info in get_pyocd_target_infos())


def target_names_for_burner_kind(kind: str) -> tuple[str, ...]:
    """按烧录器 kind 返回对应目标设备名列表。

    - BURNER_KIND_JLINK -> pylink
    - 其它（cmsisdap / stlink）-> pyOCD
    """
    return tuple(info.name for info in target_infos_for_burner_kind(kind))


@functools.cache
def target_infos_for_burner_kind(kind: str) -> tuple[TargetDeviceInfo, ...]:
    """按烧录器 kind 返回对应目标设备信息列表。

    - BURNER_KIND_JLINK -> pylink
    - 其它（cmsisdap / stlink）-> pyOCD
    """
    from .probe.base import BURNER_KIND_JLINK

    if kind == BURNER_KIND_JLINK:
        return get_pylink_target_infos()
    return get_pyocd_target_infos()
