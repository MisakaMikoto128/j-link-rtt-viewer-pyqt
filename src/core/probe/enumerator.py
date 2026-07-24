"""pyOCD 烧录器枚举。

J-Link 完全由 pylink 管（RTT 页 / Flash 页 J-Link 下拉），pyOCD **绝不能**碰它。

根因（实测定位，详见 CLAUDE.md）：pyOCD 的 probe plugin 机制在**首次** import
``pyocd.probe.aggregator``（或任何级联到它的 pyocd 子模块）时，模块顶层调
``load_plugin_classes_of_type`` 扫描所有已注册 probe plugin，对每个调
``should_load()``；``JLinkProbePlugin.should_load`` 内部 ``JLinkProbe._get_jlink()``
→ ``pylink.JLink()``。若这次扫描发生在 **FlashWorker 线程**（200ms 枚举 timer 首次
import pyocd），就会在该线程创建一个 pylink.JLink，与 RTT worker 线程对**同一个
JLinkARM DLL 全局单句柄**的 open()/close()/connected_emulators() 并发 → DLL 内部
断言 → access violation 0x14 / 0xc000001d（RTT 页彻底报废的根因）。

对策（两件事，缺一不可）：
1. ``prepare_pyocd_for_flash()`` 在 **主线程**（worker 启动前，main.py 调用）预先
   import pyocd.probe.aggregator——让 plugin 扫描在主线程一次性完成（此时建的探测
   JLink 在主线程，扫完即释放，不与任何 worker 并发）。
2. 同函数里打桩 ``JLinkProbePlugin.should_load`` 返回 False +
   ``JLinkProbe._get_jlink`` 返回 None：之后任何线程再 import pyocd / 误用
   aggregator，都不会为 J-Link 创建 pylink.JLink。

之后 FlashWorker 线程 import pyocd 时 aggregator 已在 sys.modules，**不再扫描**，
也就不会在 FlashWorker 线程建 pylink.JLink。
"""
from __future__ import annotations

from .base import BURNER_KIND_CMSIS_DAP, BURNER_KIND_STLINK, ProbeInfo

# 只扫这两类；J-Link / Picoprobe / remote 一律不碰。
# key 与 pyocd.probe.aggregator.PROBE_CLASSES 一致，类惰性 import 避免拖累启动。
_PROBE_CLASS_PATHS: dict[str, str] = {
    BURNER_KIND_CMSIS_DAP: "pyocd.probe.cmsis_dap_probe.CMSISDAPProbe",
    BURNER_KIND_STLINK: "pyocd.probe.stlink_probe.StlinkProbe",
}

_pyocd_prepared = False


def prepare_pyocd_for_flash() -> None:
    """主线程预 import pyocd + 打桩 J-Link plugin（幂等，失败静默）。

    **必须在任何 worker 线程启动前、于主线程调用**（main.py）。若拖到 FlashWorker
    线程首次 import pyocd，aggregator 扫描就会在该线程建 pylink.JLink（见模块 docstring
    的根因分析）。失败静默：pyocd 内部结构变化只是回到「可能并发」的旧行为，不影响
    CMSIS-DAP/ST-Link 枚举本身。
    """
    global _pyocd_prepared
    if _pyocd_prepared:
        return
    try:
        # 1) 主线程先 import jlink_probe 拿到 plugin 类，立即打桩 should_load /
        #    _get_jlink。注意：这一步本身就会级联触发 aggregator 扫描（pyocd 包
        #    __init__ → helpers → aggregator），扫描在主线程完成。
        from pyocd.probe import jlink_probe

        if hasattr(jlink_probe, "JLinkProbePlugin"):
            jlink_probe.JLinkProbePlugin.should_load = lambda self: False
        if hasattr(jlink_probe, "JLinkProbe"):
            jlink_probe.JLinkProbe._get_jlink = classmethod(lambda cls: None)

        # 2) 显式确保 aggregator 已 import（扫描已在主线程完成，之后任何线程不再扫）。
        import pyocd.probe.aggregator  # noqa: F401

        _pyocd_prepared = True
    except Exception:
        pass


def _load_probe_class(dotted: str):
    module_name, _, cls_name = dotted.rpartition(".")
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, cls_name)


def enumerate_pyocd_probes() -> list[ProbeInfo]:
    """枚举 CMSIS-DAP / ST-Link probe（不含 J-Link）。失败返回空列表（不抛）。

    依赖 ``prepare_pyocd_for_flash()`` 已在主线程完成（main.py 启动时调用）。
    若未被调用（如单元测试直接调本函数），这里补一次——此时 aggregator 扫描会
    在当前线程完成；测试环境无 RTT worker 并发，安全。
    """
    prepare_pyocd_for_flash()
    out: list[ProbeInfo] = []
    for kind, dotted in _PROBE_CLASS_PATHS.items():
        try:
            cls = _load_probe_class(dotted)
            probes = cls.get_all_connected_probes()
        except Exception:
            continue
        for p in probes or []:
            out.append(ProbeInfo(
                kind=kind,
                serial=getattr(p, "unique_id", "") or "",
                product=getattr(p, "product_name", "") or kind,
            ))
    return out
