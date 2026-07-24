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
from pathlib import Path

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

# SEGGER 常在设备名后加括号注释，如 "STM32xx (allow opt. bytes)"；
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
    括号注释的变体（如 `STM32xx (allow opt. bytes)`）常是选项字节区垃圾
    （如 FlashAddr=0x06000000 FlashSize=65552），不可信。真实主 Flash/RAM 在
    `aFlashArea`/`aRAMArea` 数组里。策略：
    1. 数组里若有 Size>0 的区域，取 Size 最大者（主 Flash，滤掉选项字节/配置
       小区域，如 STM32xx 的 area[0] 16B 选项字节）。
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
    """从 pylink-square / J-Link DLL 读取支持的 MCU 设备信息，返回排序去重元组。

    **缓存策略**：supported_device 枚举（11130 次 DLL 调用）会永久损坏 J-Link DLL
    的线程本地状态，worker 线程的后续 connect 读到损坏状态 → access violation 0x14
    （实测：枚举后 connect 0/10，禁枚举 10/10）。因此：
    1. 优先读磁盘缓存（%APPDATA%/JLinkRTTViewer/target_devices_cache.json），
       缓存命中则零 DLL 调用，彻底避开崩溃。
    2. 缓存未命中才枚举，枚举后写缓存。
    3. 缓存带 J-Link DLL 版本号，DLL 升级后自动重建。

    整个 DLL 枚举持进程级 dll_lock：与 RTT worker 线程的 connect 串行。
    """
    from core._dll_global import dll_lock

    # 先尝试读磁盘缓存（零 DLL 调用）
    cached = _read_cache()
    if cached is not None:
        return cached

    # 缓存未命中：枚举 + 写缓存
    with dll_lock():
        infos = _read_pylink_target_infos_locked()
    if infos:
        _write_cache(infos)
    return infos


def _cache_path() -> Path:
    """缓存文件路径：%APPDATA%/JLinkRTTViewer/target_devices_cache.json。"""
    from core.config_service import ConfigService

    # 复用 ConfigService 的 %APPDATA% 路径逻辑，避免硬编码
    prefs_path = ConfigService._compute_user_prefs_path()
    return prefs_path.parent / "target_devices_cache.json"


_dll_version_cache: str | None = None

# 进程内磁盘缓存镜像：_read_cache 首次磁盘命中后缓存，_write_cache 写盘后刷新。
# None = 尚未加载或磁盘无缓存；tuple = 已加载的磁盘内容（可能空元组表示已尝试但空）。
# 目的：UI 高频读下拉候选时避免每次读 600KB JSON；worker 写盘后 UI 立即看到新值。
_disk_cache_memory: tuple[TargetDeviceInfo, ...] | None = None


def _jlink_dll_version() -> str:
    """读 J-Link DLL 版本号（缓存失效依据）。失败返回空串。

    **进程内缓存**：JLINKARM_GetDLLVersion 是 DLL 调用，主线程频繁触发会初始化
    DLL 线程本地状态 → 损坏 TLS → worker connect 崩 0x14。只读一次缓存结果。
    """
    global _dll_version_cache
    if _dll_version_cache is not None:
        return _dll_version_cache
    try:
        import pylink

        j = pylink.JLink()
        # JLINKARM_GetDLLVersion 返回 int，如 79600 = v7.96.0
        ver = j._dll.JLINKARM_GetDLLVersion()
        _dll_version_cache = str(ver)
        return _dll_version_cache
    except Exception:
        _dll_version_cache = ""
        return ""


def _read_cache() -> tuple[TargetDeviceInfo, ...] | None:
    """读磁盘缓存。命中返回元组，未命中/损坏返回 None。

    **不调 _jlink_dll_version()**：那是 DLL 调用，主线程触发会初始化 DLL TLS →
    worker connect 崩 0x14。版本校验改为：缓存文件里的版本号与硬编码的期望版本
    比较（期望版本在 _write_cache 时写入）。DLL 升级后期望版本手动更新——罕见，
    可接受。
    """
    global _disk_cache_memory
    if _disk_cache_memory is not None:
        return _disk_cache_memory
    import json

    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("devices", [])
        if not isinstance(items, list) or not items:
            return None
        infos = tuple(
            TargetDeviceInfo(
                name=str(d["name"]),
                vendor=str(d.get("vendor", "")),
                flash_addr=d.get("flash_addr"),
                flash_size=d.get("flash_size"),
                ram_addr=d.get("ram_addr"),
                ram_size=d.get("ram_size"),
            )
            for d in items
            if isinstance(d, dict) and "name" in d
        )
    except Exception:
        return None
    if infos:
        _disk_cache_memory = infos
    return infos


def _write_cache(infos: tuple[TargetDeviceInfo, ...]) -> None:
    """写磁盘缓存（原子替换，防半截写入）。"""
    import json

    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "dll_version": _jlink_dll_version(),
            "devices": [
                {
                    "name": info.name,
                    "vendor": info.vendor,
                    "flash_addr": info.flash_addr,
                    "flash_size": info.flash_size,
                    "ram_addr": info.ram_addr,
                    "ram_size": info.ram_size,
                }
                for info in infos
            ],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        _logger.warning(f"target_devices 缓存写入失败：{e}")
        return
    # 写盘成功：刷新进程内镜像，UI 立即读到新值
    global _disk_cache_memory
    _disk_cache_memory = infos


def _read_pylink_target_infos_locked() -> tuple[TargetDeviceInfo, ...]:
    """get_pylink_target_infos 的持锁实现（仅供本模块内部调用）。"""
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


def _lookup_pylink_cached_info(name: str) -> TargetDeviceInfo | None:
    """从 pylink 磁盘缓存按名字查设备元数据（J-Link 库元数据完整）。

    供 pyOCD builtin target 回退补元数据：builtin target 无 probe 时 memory_map
    空（如 STM32F103RC 的 cls(None) 需要 probe 对象），J-Link 库元数据完整，
    按 name 查 J-Link 缓存补 vendor/flash/ram。
    """
    for info in read_cached_target_infos():
        if info.name == name:
            return info
    return None


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
            # builtin target 无 probe 时 memory_map 空（cls(None) 需要 probe），
            # 回退查 pylink 磁盘缓存补元数据（J-Link 库元数据完整）
            if flash_addr is None and ram_addr is None:
                _jlink = _lookup_pylink_cached_info(norm)
                if _jlink is not None:
                    vendor = vendor or _jlink.vendor
                    flash_addr, flash_size = _jlink.flash_addr, _jlink.flash_size
                    ram_addr, ram_size = _jlink.ram_addr, _jlink.ram_size
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


# ============================================================
# UI 安全只读 API：永不枚举，只读缓存
# ============================================================
# 设计动机（见 CLAUDE.md「设备下拉时序竞态」）：
# get_* / target_*_for_burner_kind 是「读缓存 + 未命中枚举」的 ensure 语义，
# 供 worker 线程调用。UI 主线程一旦调用，缓存未命中时会触发主线程枚举
# （pylink supported_device 11130 次调用损坏 J-Link DLL TLS -> connect 崩 0x14；
# pyOCD ~500ms 阻塞 UI）。因此 UI 必须用下面这组只读 API：
# - 磁盘缓存命中（含内存镜像）-> 返回非空元组
# - 缓存未就绪（worker 尚未枚举完）-> 返回空元组，**绝不枚举**
# worker 枚举完写缓存后 emit target_infos_ready 信号，UI 收到后再读一次即拿到。


def read_cached_target_infos() -> tuple[TargetDeviceInfo, ...]:
    """只读 J-Link 设备缓存（磁盘 + 内存镜像），永不枚举。UI 线程安全。

    缓存就绪返回非空元组；worker 尚未枚举完返回空元组。
    """
    cached = _read_cache()
    return cached if cached is not None else ()


def read_cached_target_names() -> tuple[str, ...]:
    """只读 J-Link 设备名（磁盘 + 内存镜像），永不枚举。UI 线程安全。"""
    return tuple(info.name for info in read_cached_target_infos())


def read_cached_pyocd_target_infos() -> tuple[TargetDeviceInfo, ...]:
    """只读 pyOCD 设备列表（进程内 functools.cache），永不枚举。UI 线程安全。

    pyOCD 枚举不碰 J-Link DLL（不崩），但 ~500ms 阻塞主线程不可接受。worker
    线程在 FlashWorker.initialize 调 get_pyocd_target_infos() 填充 cache 后，
    本函数返回非空；此前返回空。
    """
    if get_pyocd_target_infos.cache_info().currsize == 0:
        return ()
    return get_pyocd_target_infos()


def read_cached_pyocd_target_names() -> tuple[str, ...]:
    """只读 pyOCD 设备名（进程内 cache），永不枚举。UI 线程安全。"""
    return tuple(info.name for info in read_cached_pyocd_target_infos())


def read_cached_target_infos_for_burner_kind(kind: str) -> tuple[TargetDeviceInfo, ...]:
    """按烧录器 kind 路由到对应只读 API。UI 线程安全，永不枚举。

    - BURNER_KIND_JLINK -> read_cached_target_infos（磁盘 + 内存镜像）
    - 其它（cmsisdap / stlink）-> read_cached_pyocd_target_infos（进程内 cache）
    """
    from .probe.base import BURNER_KIND_JLINK

    if kind == BURNER_KIND_JLINK:
        return read_cached_target_infos()
    return read_cached_pyocd_target_infos()


def read_cached_target_names_for_burner_kind(kind: str) -> tuple[str, ...]:
    """按烧录器 kind 路由到对应只读设备名 API。UI 线程安全，永不枚举。"""
    return tuple(info.name for info in read_cached_target_infos_for_burner_kind(kind))
