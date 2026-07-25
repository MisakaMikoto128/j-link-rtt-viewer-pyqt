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

from threading import Thread

from .base import BURNER_KIND_CMSIS_DAP, BURNER_KIND_STLINK, ProbeInfo

# 只扫这两类；J-Link / Picoprobe / remote 一律不碰。
# key 与 pyocd.probe.aggregator.PROBE_CLASSES 一致，类惰性 import 避免拖累启动。
_PROBE_CLASS_PATHS: dict[str, str] = {
    BURNER_KIND_CMSIS_DAP: "pyocd.probe.cmsis_dap_probe.CMSISDAPProbe",
    BURNER_KIND_STLINK: "pyocd.probe.stlink_probe.StlinkProbe",
}

_pyocd_prepared = False
_prepare_thread: Thread | None = None  # 后台预热线程（main.py 早期起、worker 启动前 join）


def prepare_pyocd_for_flash(*, background: bool = False) -> Thread | None:
    """主线程预 import pyocd + 打桩 J-Link plugin（幂等，失败静默）。

    **必须在任何 worker 线程启动前、于主线程调用**（main.py）。若拖到 FlashWorker
    线程首次 import pyocd，aggregator 扫描就会在该线程建 pylink.JLink（见模块 docstring
    的根因分析）。失败静默：pyocd 内部结构变化只是回到「可能并发」的旧行为，不影响
    CMSIS-DAP/ST-Link 枚举本身。

    ``background=True``（main.py 启动期用）：在本线程之外的 daemon 子线程跑预热，
    并把扫描**与主线程后续 import/构造并行**。该子线程满足安全充要条件：「扫描在
    worker_thread.start() 之前结束」——调用方必须在 worker 启动前调
    :func:`wait_for_pyocd_prepare` join。扫描发生在该预热线程而非 FlashWorker 线程，
    且此时无任何 worker 在跑 → 无 DLL 并发。
    """
    global _pyocd_prepared, _prepare_thread
    if _pyocd_prepared:
        return None
    if background and _prepare_thread is None:
        _prepare_thread = Thread(target=_do_prepare_pyocd, name="pyocd-prepare", daemon=True)
        _prepare_thread.start()
        return _prepare_thread
    # 非后台：同步执行（worker 已启动 / 测试环境 / 兜底）
    _do_prepare_pyocd()
    return None


def wait_for_pyocd_prepare(timeout: float = 30.0) -> bool:
    """等后台预热线程结束（worker_thread.start 之前调用）。返回是否在超时内完成。"""
    global _prepare_thread
    t = _prepare_thread
    if t is None:
        return True  # 没起后台线程（同步路径已跑完）
    t.join(timeout=timeout)
    done = not t.is_alive()
    if done:
        _prepare_thread = None
    return done


def _do_prepare_pyocd() -> None:
    global _pyocd_prepared
    if _pyocd_prepared:
        return
    try:
        # 1) 主线程先 import jlink_probe 拿到 plugin 类，立即打桩 should_load /
        #    _get_jlink。注意：这一步本身就会级联触发 aggregator 扫描（pyocd 包
        #    __init__ → helpers → aggregator），扫描在主线程（或后台预热线程）完成。
        from pyocd.probe import jlink_probe

        if hasattr(jlink_probe, "JLinkProbePlugin"):
            jlink_probe.JLinkProbePlugin.should_load = lambda self: False
        if hasattr(jlink_probe, "JLinkProbe"):
            jlink_probe.JLinkProbe._get_jlink = classmethod(lambda cls: None)

        # 2) 显式确保 aggregator 已 import（扫描已在主线程（或预热线程）完成，之后任何线程不再扫）。
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
            out.append(
                ProbeInfo(
                    kind=kind,
                    serial=getattr(p, "unique_id", "") or "",
                    product=getattr(p, "product_name", "") or kind,
                )
            )
    return out
