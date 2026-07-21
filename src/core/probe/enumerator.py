"""pyOCD 烧录器枚举。

pyOCD 的 ConnectHelper.get_all_connected_probes 返回所有连上的 CMSIS-DAP /
ST-Link / J-Link / Picoprobe 等 probe。J-Link 在此过滤掉（仍归 pylink 管，
避免与 RTT 页 / Flash 页 J-Link 下拉重复出现）。

probe 类型识别用 type(p).__name__ 字符串匹配（pyOCD 0.45 实测）：
- CMSISDAPProbe  -> cmsisdap（DAPLink / H7-TOOL 等也归此类）
- StlinkProbe    -> stlink
- JLinkProbe     -> 过滤（返回 None）
- 其他           -> cmsisdap（默认）
"""
from __future__ import annotations

from .base import BURNER_KIND_CMSIS_DAP, BURNER_KIND_STLINK, ProbeInfo


def _probe_kind(probe) -> str | None:
    """按 probe 类型名标 kind。J-Link 返回 None（过滤，归 pylink）。"""
    name = type(probe).__name__
    if "JLink" in name:
        return None  # J-Link 归 pylink，不重复出现在 pyOCD 下拉
    if "Stlink" in name or "STLink" in name:
        return BURNER_KIND_STLINK
    return BURNER_KIND_CMSIS_DAP


def enumerate_pyocd_probes() -> list[ProbeInfo]:
    """枚举所有 pyOCD 可见 probe（J-Link 过滤）。失败返回空列表（不抛，避免影响 UI）。"""
    try:
        from pyocd.core.helpers import ConnectHelper
    except ImportError:
        return []
    try:
        probes = ConnectHelper.get_all_connected_probes()
    except Exception:
        return []
    out: list[ProbeInfo] = []
    for p in probes or []:
        kind = _probe_kind(p)
        if kind is None:
            continue
        out.append(ProbeInfo(
            kind=kind,
            serial=getattr(p, "unique_id", "") or "",
            product=getattr(p, "product_name", "") or kind,
        ))
    return out
