"""CMSIS-Pack 管理服务：存储路径、枚举、搜索、下载、删除。

pyOCD 烧录依赖 CMSIS-Pack 提供 target 的 flash algo。pack 体积大（每包几 MB-
几十 MB），不随软件分发。本服务用 cmsis-pack-manager 按需下载管理：

- 存储 path 可配置（默认 user_prefs 同级 packs/ 目录）
- 已装 pack 枚举：读 data_path 下 .pack 文件，文件名解析 vendor/name/version，
  不解析 CmsisPack（避免每个 pack 解析 XML，保性能）
- 搜索 pack 索引：cache.index 按 part_number 子串匹配
- 下载 pack：cache.download_pack_list
- 删除 pack：删 .pack 文件

Cache 实例模块级缓存：data_path 不变时复用；变更或 pack 增删后调
invalidate_cache() 重建，否则 Cache.index 读不到新状态。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .logger import get_logger

_logger = get_logger()


@dataclass(frozen=True)
class PackInfo:
    """已装 pack 描述项。

    CMSIS-Pack 文件名约定 ``Vendor.Name.Version.pack``（如
    ``Keil.STM32F0_DFP.1.0.0.pack``），由 _parse_pack_file_name 解析。

    Attributes:
        file_name: .pack 文件名（含扩展名）
        vendor: 包厂商（文件名首段，如 Keil）
        name: 包名（文件名中段，如 STM32F0_DFP）
        version: 版本（文件名末段，如 1.0.0）
        size_bytes: 文件字节数
        file_path: 绝对路径
    """

    file_name: str
    vendor: str
    name: str
    version: str
    size_bytes: int
    file_path: str


_cache_obj = None  # type: ignore[assignment]
_cache_data_path: str | None = None


def wildcard_eq(pattern: str, text: str) -> bool:
    """CMSIS-Pack part_number 的 ``x`` 通配匹配（封装/等级后缀）。

    等长比较，``pattern`` 的 ``x`` 匹配 ``text`` 任意单字符：

    >>> wildcard_eq("stm32f030c8tx", "stm32f030c8t6")
    True
    >>> wildcard_eq("stm32f030c8tx", "stm32f030c8")
    False

    Args:
        pattern: 含 ``x`` 通配的 part_number（pack 索引侧）
        text: 具体 part_number（用户输入侧）
    """
    if len(pattern) != len(text):
        return False
    for pc, tc in zip(pattern, text, strict=True):
        if pc == "x":
            continue
        if pc != tc:
            return False
    return True


_DEFAULT_PACK_PATH: str | None = None


def get_pack_data_path() -> str:
    """返回 pack 存储目录。

    空串配置 -> cmsis-pack-manager 全局默认目录（``%LOCALAPPDATA%/cmsis-pack-manager/
    cmsis-pack-manager/``）。用户旧 pack 在此，默认即可看到，无需迁移。
    """
    global _DEFAULT_PACK_PATH
    from .config_service import ConfigService

    cfg = ConfigService()
    path = str(cfg.get("pack_data_path") or "")
    if path:
        return path
    if _DEFAULT_PACK_PATH is None:
        try:
            import cmsis_pack_manager as cpm

            _DEFAULT_PACK_PATH = cpm.Cache(True, False).data_path
        except ImportError:
            _DEFAULT_PACK_PATH = str(ConfigService._compute_user_prefs_path().parent / "packs")
    return _DEFAULT_PACK_PATH


def set_pack_data_path(path: str) -> None:
    """配置 pack 存储目录。空串 -> 用默认。立即落盘并废弃 Cache 实例。

    自定义路径后 index 独立（json_path=data_path=新路径），首次搜索需重新下载
    CMSIS-Pack 索引（约 1-2 分钟）。旧 pack 不会自动迁移，用「迁移旧 pack」按钮。
    """
    from .config_service import ConfigService

    cfg = ConfigService()
    cfg.set("pack_data_path", path)
    cfg.flush()
    invalidate_cache()


def get_pack_cache():
    """返回绑定 pack_data_path 的 cmsis_pack_manager.Cache。

    ``json_path`` 与 ``data_path`` 同设为 pack_data_path：index 与 .pack 文件同目录。
    若 json_path 用全局默认，download_pack_list 会检查全局 index 认为「已下载」
    而不下载到 data_path（实测 data_path 目录空），导致 list_installed_packs 读不到。
    模块级缓存：path 不变时复用 Cache 实例。cmsis_pack_manager 未装返回 None。
    """
    global _cache_obj, _cache_data_path
    path = get_pack_data_path()
    if _cache_obj is not None and _cache_data_path == path:
        return _cache_obj
    try:
        import cmsis_pack_manager
    except ImportError:
        return None
    from pathlib import Path

    Path(path).mkdir(parents=True, exist_ok=True)
    _cache_obj = cmsis_pack_manager.Cache(True, False, json_path=path, data_path=path)
    _cache_data_path = path
    return _cache_obj


def invalidate_cache() -> None:
    """废弃 Cache 实例。pack 增删或路径变更后调，下次 get_pack_cache 重建。"""
    global _cache_obj, _cache_data_path
    _cache_obj = None
    _cache_data_path = None


def _parse_pack_file_name(file_name: str) -> tuple[str, str, str]:
    """解析 ``Vendor.Name.Version.pack`` -> (vendor, name, version)。

    解析失败时 vendor/version 置空串，name 置去扩展名的 stem。
    """
    stem = file_name[:-5] if file_name.endswith(".pack") else file_name
    parts = stem.split(".")
    if len(parts) >= 2:
        # Vendor.Name.Version：Version 可含点（1.0.0），故 name 取第 2 段，version 取余下
        return parts[0], parts[1], ".".join(parts[2:])
    return "", stem, ""


def list_installed_packs() -> list[PackInfo]:
    """枚举已装 pack。递归扫 data_path 下所有 .pack 文件。

    CMSIS-Pack 下载结构为 ``data_path/Vendor/Pack/Version.pack``（如
    ``Keil/STM32F0xx_DFP/3.1.1.pack``），故用 ``os.walk`` 递归扫描。
    根目录的扁平 ``Vendor.Name.Version.pack`` 也兼容（旧格式 / 用户手动放入）。
    """
    path = get_pack_data_path()
    if not os.path.isdir(path):
        return []
    result: list[PackInfo] = []
    for root, _dirs, files in os.walk(path):
        for fname in files:
            if not fname.endswith(".pack"):
                continue
            file_path = os.path.join(root, fname)
            try:
                size = os.path.getsize(file_path)
            except OSError:
                size = 0
            rel = os.path.relpath(file_path, path).replace("\\", "/")
            vendor, name, version = _parse_pack_rel_path(rel)
            result.append(
                PackInfo(
                    file_name=rel,
                    vendor=vendor,
                    name=name,
                    version=version,
                    size_bytes=size,
                    file_path=file_path,
                )
            )
    result.sort(key=lambda p: (p.vendor, p.name, p.version))
    return result


def _parse_pack_rel_path(rel: str) -> tuple[str, str, str]:
    """按相对路径解析 (vendor, name, version)。

    - 子目录结构 ``Vendor/Pack/Version.pack`` -> (Vendor, Pack, Version)
    - 根目录扁平 ``Vendor.Name.Version.pack`` -> 走 _parse_pack_file_name

    解析失败时 vendor/version 置空，name 置去扩展名的 stem。
    """
    parts = rel.split("/")
    if len(parts) >= 3:
        # Vendor/Pack/Version.pack：version 去 .pack 扩展名
        return parts[0], parts[1], parts[-1][:-5] if parts[-1].endswith(".pack") else parts[-1]
    # 根目录扁平文件名
    return _parse_pack_file_name(parts[-1])


def search_packs(query: str, limit: int = 500) -> list[str]:
    """按 part_number 子串搜索 pack 索引（可下载的 part）。

    Args:
        query: 搜索串（大小写不敏感）。空串返回空列表。
        limit: 最多返回条数（防止单字符搜索命中过多淹没 UI）。前端按页大小
            切片分页，默认 500 足以覆盖常见型号搜索结果。

    Returns:
        匹配的 part_number 列表（原样大小写），升序。首次需下载索引（网络）。
    """
    cache = get_pack_cache()
    if cache is None:
        return []
    try:
        if not cache.index:
            cache.cache_descriptors()
    except Exception as e:
        _logger.warning(f"pack 索引下载失败：{e}")
        return []
    q = query.strip().upper()
    if not q:
        return []
    matches: list[str] = []
    for name in cache.index:
        if q in name.upper():
            matches.append(name)
            if len(matches) >= limit:
                break
    matches.sort()
    return matches


def _invalidate_pyocd_target_cache() -> None:
    """失效 pyOCD target 枚举缓存（pack 增删后 target 列表变化）。

    ``get_pyocd_target_infos`` 用 ``functools.cache``，下载/删除 pack 后必须清空，
    否则目标设备下拉看不到新 pack（要重启进程才出现）。延迟导入避免循环依赖。
    """
    try:
        from core.target_discovery import (
            _get_managed_packs_cached,
            get_pyocd_target_infos,
            resolve_pyocd_target_memory_map,
        )

        get_pyocd_target_infos.cache_clear()
        _get_managed_packs_cached.cache_clear()
        resolve_pyocd_target_memory_map.cache_clear()
    except Exception:
        pass


def download_pack(part_number: str, log=None) -> str:
    """下载 part_number 对应的 pack 到 data_path。

    匹配 cache.index（精确/前缀/``x`` 通配），下载 pack 文件。成功后
    invalidate_cache 使后续 get_installed_packs 读到新 pack，并失效 pyOCD
    target 枚举缓存（新 pack 提供新 target）。

    Args:
        part_number: 用户填的 device（如 STM32F103C8T6 或 STM32F103C8）
        log: 可选日志回调 ``(level, msg)``，level ∈ {"info","warn","error"}

    Returns:
        ``"downloaded"`` 新下载；``"skipped"`` 已安装跳过；``"failed"`` 未匹配/失败。
    """
    cache = get_pack_cache()
    if cache is None:
        if log:
            log("warn", "cmsis-pack-manager 未安装，无法下载 pack")
        return "failed"
    try:
        if not cache.index:
            if log:
                log("info", "下载 CMSIS-Pack 索引（首次较慢）...")
            cache.cache_descriptors()
    except Exception as e:
        if log:
            log("warn", f"pack 索引下载失败：{e}")
        return "failed"
    key = part_number.lower().strip()
    matches = set()
    for name in cache.index:
        nl = name.lower()
        if nl == key or wildcard_eq(nl, key) or nl.startswith(key) or key.startswith(nl):
            matches.add(name)
    if not matches:
        if log:
            log("warn", f"CMSIS-Pack 索引中无 {part_number}")
        return "failed"
    try:
        devices = [cache.index[dev] for dev in matches]
        packs = cache.packs_for_devices(devices)
        # 下载前查重：已存在的 pack 跳过，避免重复下载
        data_path = get_pack_data_path()
        missing = []
        for p in packs:
            pack_path = os.path.join(data_path, p.vendor, p.pack, p.version + ".pack")
            if not os.path.exists(pack_path):
                missing.append(p)
        if not missing:
            if log:
                log("info", "已安装，跳过下载")
            return "skipped"
        if log:
            log("info", f"下载 pack: {[f'{p.vendor}.{p.pack}.{p.version}' for p in missing]}")
        cache.download_pack_list(missing)
        if log:
            log("info", "pack 下载完成")
        invalidate_cache()
        _invalidate_pyocd_target_cache()
        return "downloaded"
    except Exception as e:
        if log:
            log("warn", f"pack 下载失败：{e}")
        return "failed"


def delete_pack(file_name: str) -> bool:
    """删除 data_path 下的 .pack 文件（允许子目录路径）。

    Args:
        file_name: 相对 data_path 的路径（如 ``Keil/STM32F0xx_DFP/3.1.1.pack``）。
        含绝对路径 / ``..`` 越界 / 非 .pack 扩展名则拒绝，防路径穿越。

    Returns:
        True 表示已删除；False 表示文件不存在/拒绝/删除失败。
    """
    path = get_pack_data_path()
    safe = os.path.normpath(file_name).replace("\\", "/")
    if not safe.endswith(".pack") or safe.startswith("../") or os.path.isabs(safe):
        return False
    file_path = os.path.join(path, safe)
    # 双重校验：最终路径必须在 data_path 内
    if os.path.commonpath([os.path.abspath(path), os.path.abspath(file_path)]) != os.path.abspath(
        path
    ):
        return False
    if not os.path.isfile(file_path):
        return False
    try:
        os.remove(file_path)
        invalidate_cache()
        _invalidate_pyocd_target_cache()
        return True
    except OSError as e:
        _logger.warning(f"pack 删除失败：{e}")
        return False


def get_legacy_packs_dir() -> str | None:
    """返回 cmsis-pack-manager 全局默认目录（用户切换到自定义路径前的旧 pack 所在）。

    cmsis_pack_manager 未装或目录不存在返回 None。
    """
    try:
        import cmsis_pack_manager as cpm
    except ImportError:
        return None
    # 默认 Cache（不传 data_path）的 data_path 即全局目录
    legacy = cpm.Cache(True, False).data_path
    return legacy if os.path.isdir(legacy) else None


def migrate_legacy_packs(log=None) -> int:
    """把全局 cmsis-pack-manager 目录的旧 .pack 复制到当前 pack_data_path。

    保留 ``Vendor/Pack/Version.pack`` 子目录结构。已存在的跳过（不覆盖）。

    Args:
        log: 可选日志回调 ``(level, msg)``。

    Returns:
        实际复制的 pack 数量。
    """
    import shutil

    legacy = get_legacy_packs_dir()
    if not legacy:
        if log:
            log("info", "无旧 pack 目录可迁移")
        return 0
    dest = get_pack_data_path()
    if os.path.abspath(legacy) == os.path.abspath(dest):
        if log:
            log("info", "当前路径即全局默认路径，无需迁移")
        return 0
    count = 0
    for pack_file in _iter_pack_files(legacy):
        rel = os.path.relpath(pack_file, legacy)
        dest_path = os.path.join(dest, rel)
        if os.path.exists(dest_path):
            continue
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        try:
            shutil.copy2(pack_file, dest_path)
            count += 1
            if log:
                log("info", f"迁移 {rel}")
        except OSError as e:
            if log:
                log("warn", f"迁移失败 {rel}：{e}")
    if count:
        invalidate_cache()
        _invalidate_pyocd_target_cache()
    if log:
        log("info", f"迁移完成，共 {count} 个 pack")
    return count


def _iter_pack_files(root: str):
    """递归遍历 root 下所有 .pack 文件路径。"""
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if fname.endswith(".pack"):
                yield os.path.join(dirpath, fname)
