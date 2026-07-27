"""ST-Link / CMSIS-DAP / DAPLink 烧录后端：基于 pyOCD 0.45。

实现 ProbeBackend 协议。pyOCD 一套 API 跨多 probe（CMSIS-DAP / ST-Link /
J-Link），但本 backend 只用于非 J-Link probe--J-Link 仍走 PylinkBackend：
pylink RTT 由 J-Link 固件后台轮询（host 零 SWD），效率优于 pyOCD host 端 SWD
轮询（详见 memory pyocd-rtt-api benchmark）；且 enumerator.py 打桩
JLinkProbePlugin 防 aggregator 扫描在 worker 线程建 pylink.JLink 与 RTT worker
并发抢 DLL 单句柄崩 0x14。RTT 监控页面的非 J-Link probe 走 PyocdRttBackend。

实测 API（scratch/probe_pyocd_api.py on pyOCD 0.45）：
- ConnectHelper.get_all_connected_probes() -> list[DebugProbe]
- ConnectHelper.session_with_chosen_probe(unique_id=, target_override=, options=)
- FileProgrammer(session, progress=cb, chip_erase=...)  # progress cb 单参数
- target.halt / mass_erase / reset / reset_and_halt / resume / read_memory_block8

target_override：用户填的 device_name（如 STM32xx）。pyOCD 先查内置
target（lowercase），再查 CMSIS-Pack（part number 原样，首次可能下载 pack）。
"""

from __future__ import annotations

import contextlib

from core.pack_service import get_pack_cache, wildcard_eq

from .base import (
    ERASE_MODE_CHIP,
    FORMAT_BIN,
    LogCallback,
    ProbeError,
    ProbeNotConnected,
    ProbeParams,
    ProgressCallback,
    VerifyMismatch,
)

# CMSIS-Pack part_number 的 'x' 通配匹配，单点真源在 core.pack_service.wildcard_eq。
# 保留别名仅供测试兼容（tests/test_pyocd_backend.py import _pack_part_wildcard_eq）。
_pack_part_wildcard_eq = wildcard_eq


# SWD 通信失败的特征串：匹配到则给排查提示（接线 / VREF / 多 probe 冲突）。
_SWD_ERR_KEYWORDS = (
    "idcode",
    "dp error",
    "dp fault",
    "dp parity",
    "ap fault",
    "transfer fault",
    "transfer error",
    "memory transfer fault",
)


def _swd_err_hint(msg: str) -> str:
    """SWD 通信类错误追加接线排查提示；其它错误原样返回。

    实验依据：ST-Link + STM32xx 在 10kHz–4MHz 全频率、default/under_reset/attach
    全模式下 SWD 均失败（Get IDCODE error / DP error / DP parity），同一目标下
    DAPLink 正常 -> 属信号完整性问题，非 pyOCD 配置。STLinkV3 必须接 VREF。
    """
    low = msg.lower()
    if any(k in low for k in _SWD_ERR_KEYWORDS):
        return (
            f"{msg}\nSWD 通信失败：请检查烧录器接线（SWDIO/SWCLK/GND/VREF），"
            "STLinkV3 必须接 VREF；确保仅一个烧录器连接目标（拔掉 DAPLink）；"
            "可尝试降低 SWD 速率。"
        )
    return msg


class PyOCDBackend:
    """CMSIS-DAP / ST-Link via pyOCD 0.45。"""

    def __init__(self, log: LogCallback) -> None:
        self._log = log
        self._session = None
        self._target = None
        self._params: ProbeParams | None = None

    def _ensure_pack_installed(self, device_name: str) -> bool:
        """device_name 的 pack 未装时自动下载（委托 pack_service.download_pack）。

        pack_service 统一 pack 存储/搜索/下载/删除逻辑。本方法仅作 connect 时的
        自动下载入口，下载到 pack_service.get_pack_data_path() 配置的目录。
        """
        from core.pack_service import download_pack

        return download_pack(device_name, log=self._log)

    # ============================================================
    # 连接
    # ============================================================
    def connect(self, params: ProbeParams) -> None:
        import pyocd.core.session as _sess
        from pyocd.core.helpers import ConnectHelper

        # Windows 上 CMSIS-DAP v2 走 pyusb_v2_backend → libusb claim_interface 对 HID
        # 设备返回 LIBUSB_ERROR_NOT_SUPPORTED。强制 v1（hidapi）避免该问题。
        # 但 prefer_v1 会让 ST-Link 从枚举中消失（ST-Link 不是 CMSIS-DAP），所以
        # 连接 CMSIS-DAP 时临时设置，连接后恢复（不影响后续 ST-Link 连接）。
        _opts = _sess.Session.get_current().options
        _had_prefer_v1 = _opts.is_set("cmsis_dap.prefer_v1") and _opts.get("cmsis_dap.prefer_v1")
        _opts["cmsis_dap.prefer_v1"] = True
        try:
            self._connect_impl(params, ConnectHelper)
        finally:
            # 恢复 prefer_v1（无论成功失败，不影响后续 ST-Link 枚举）
            if not _had_prefer_v1:
                _opts["cmsis_dap.prefer_v1"] = False

    def _connect_impl(self, params: ProbeParams, ConnectHelper) -> None:
        """connect 的实现（prefer_v1 已在外层设置/恢复）。"""
        self._params = params
        # target_override：用户填的 device_name（如 STM32xx）。pyOCD 的 pack
        # part number 通常带封装后缀（如 STM32xxTx），_resolve_target_type
        # 模糊匹配到已注册的 part number。
        target_override = self._resolve_target_type(params.device_name)
        # pack 未装则自动下载（cmsis_pack_manager 查询+下载），再 resolve 一次
        if target_override is None and self._ensure_pack_installed(params.device_name):
            target_override = self._resolve_target_type(params.device_name)
        if target_override is None:
            raise ProbeNotConnected(
                f"未知 target：{params.device_name}\n"
                f"pyOCD 库无此型号。请确认 device 与实际芯片一致，或装 pack：\n"
                f"pyocd pack install {params.device_name}"
            )
        options = {
            "transport": "swd" if params.interface == "SWD" else "jtag",
            "frequency": int(params.speed_khz) * 1000,
        }
        try:
            self._session = ConnectHelper.session_with_chosen_probe(
                unique_id=(params.serial or None),
                target_override=target_override,
                options=options,
            )
        except Exception as e:
            raise ProbeNotConnected(f"probe open failed: {e}") from e
        if self._session is None:
            self._log("warn", "未检测到所选烧录器，请检查 USB 连接或刷新设备列表")
            raise ProbeNotConnected("no probe")
        try:
            self._session.open()
        except Exception as e:
            msg = _swd_err_hint(str(e))
            self._log("warn", f"target open failed: {msg}")
            with contextlib.suppress(Exception):
                self._session.close()
            self._session = None
            raise ProbeNotConnected(msg) from e
        self._target = self._session.target
        # open() 不一定在 SWD 通信失败时抛异常（ST-Link 实测会 false-OK：open 过，
        # 但 halt/DP 访问报 DP error）。读 DP IDCODE（寄存器 0）校验通信是否真正建立。
        # read_reg 是 pyOCD DebugPort 的标准读法；API 不匹配时跳过校验，不阻断连接。
        dp = getattr(self._target, "dp", None)
        read_reg = getattr(dp, "read_reg", None) if dp is not None else None
        if read_reg is not None:
            try:
                read_reg(0)  # DP IDCODE：触发一次 SWD 读，失败说明 SWD 未通
            except AttributeError:
                # API 差异（read_reg 存在但调用异常）：跳过校验，不破坏可用连接
                pass
            except Exception as e:
                msg = _swd_err_hint(f"SWD 校验失败：{e}")
                self._log("warn", msg)
                with contextlib.suppress(Exception):
                    self._session.close()
                self._session = None
                raise ProbeNotConnected(msg) from e
        # halt before erase/program（pyOCD 连接后默认 halt，显式再 halt 一次保险）
        with contextlib.suppress(Exception):
            self._target.halt()
        probe = self._session.probe
        self._log("info", f"Probe: {probe.product_name} (S/N: {probe.unique_id})")
        self._log("info", f"Target connected: {params.device_name}")

    # ============================================================
    # 擦除
    # ============================================================
    def erase(self, mode: str) -> None:
        if mode == ERASE_MODE_CHIP:
            # mass_erase 的 flash algo 要求目标处于 halt；目标正在运行时偶发
            # "target was not halted as expected after calling flash algorithm
            # routine (IPSR=3)"（CMSIS-DAP/DAPLink 实测）。先 halt 再擦，
            # 失败再 reset_and_halt 兜底，仍失败则由 mass_erase 抛原始错误。
            try:
                self._target.halt()
            except Exception:
                with contextlib.suppress(Exception):
                    self._target.reset_and_halt()
            self._target.mass_erase()
            self._log("info", "chip erase OK")
        # sector 模式由 FileProgrammer 内含，不显式 erase

    # ============================================================
    # 编程
    # ============================================================
    def program(self, on_progress: ProgressCallback) -> None:
        if self._params is None or self._target is None:
            raise ProbeError("not connected")
        from pyocd.flash.file_programmer import FileProgrammer

        p = self._params
        fmt = self._file_format(p.file_format)
        addr = p.bin_start_addr if p.file_format == FORMAT_BIN else None
        chip_erase = "chip" if p.erase_mode == ERASE_MODE_CHIP else "sector"

        def progress_cb(value) -> None:
            """FileProgrammer progress：单参数百分比（pyOCD 实测 0.0-1.0）。"""
            try:
                v = float(value)
            except (TypeError, ValueError):
                return
            pct = int(v * 100) if v <= 1.0 else int(v)
            on_progress(pct, 100)

        fp = FileProgrammer(self._session, progress=progress_cb, chip_erase=chip_erase)
        kwargs = {"file_format": fmt}
        if addr is not None:
            kwargs["base_address"] = addr
        fp.program(p.file_path, **kwargs)
        self._log("info", "program OK")

    # ============================================================
    # 校验（逐字节，复用 flash_file_parser.to_intelhex）
    # ============================================================
    def verify(self) -> None:
        if self._params is None or self._target is None:
            raise ProbeError("not connected")
        from core import flash_file_parser as fp

        ih = fp.to_intelhex(self._params.file_path, self._params.bin_start_addr)
        for start, end in ih.segments():
            expected = bytes(ih.tobinarray(start=start, end=end - 1))
            self._verify_range(start, expected)

    def _verify_range(self, addr: int, expected: bytes) -> None:
        CHUNK = 4096
        off = 0
        while off < len(expected):
            n = min(CHUNK, len(expected) - off)
            got = bytes(self._target.read_memory_block8(addr + off, n))
            if got != expected[off : off + n]:
                raise VerifyMismatch(addr + off, n)
            off += n

    # ============================================================
    # 复位
    # ============================================================
    def reset(self, halt: bool, run: bool) -> None:
        """复位目标。语义同 PylinkBackend.reset。"""
        if run:
            self._target.reset()
            self._log("info", "CPU running")
        elif halt:
            self._target.reset_and_halt()
            self._log("info", "CPU halted after reset")
        else:
            # halt=False run=False：复位后保持 halt（与 PylinkBackend 一致语义）
            self._target.reset_and_halt()

    # ============================================================
    # 断开
    # ============================================================
    def close(self) -> None:
        if self._session is None:
            return
        try:
            self._session.close()
        except Exception as e:
            self._log("warn", f"close warn: {e}")
        self._session = None
        self._target = None

    def connected_serial(self) -> str:
        if self._session and self._session.probe:
            return self._session.probe.unique_id or ""
        return ""

    @staticmethod
    def _file_format(fmt: str):
        return {"elf": "elf", "hex": "hex", "bin": "bin"}.get(fmt)

    @staticmethod
    def _resolve_target_type(device_name: str) -> str | None:
        """把用户填的 device_name 解析成 pyOCD target type。

        pyOCD builtin target 在 TARGET dict（lowercase key）。CMSIS-Pack target
        不在 TARGET dict，要经 ManagedPacks.get_installed_targets() 查 part_number。
        pack part number 用 'x' 作封装/等级后缀通配（如 STM32xxTx）。

        用户在 UI 填的 device_name 可能是：
        - SEGGER 短名：STM32xx（不带后缀）
        - 完整型号：STM32xxT6（带封装后缀 T6）
        都要匹配到 pack 的 part_number（STM32xxTx）。

        匹配顺序：
        1. device_name.lower() 直接命中 builtin TARGET dict。
        2. pack part_number 与 device_name 等长且 'x' 通配命中（Tx ~ T6）。
        3. pack part_number 以 device_name 为前缀（短名 -> Tx），或反之。
        4. 否则返回 None（connect 报 ProbeNotConnected 提示装 pack）。

        pack 需预装：pyocd pack install "<part>*" -u
        """
        from pyocd.target import TARGET

        key = device_name.lower().strip()
        if not key:
            return None
        if key in TARGET:
            return device_name
        try:
            from pyocd.target.pack.pack_target import ManagedPacks

            # 用 pack_service 统一的 cache（自定义 pack_data_path），否则
            # ManagedPacks 默认读 %LOCALAPPDATA%，与 pack 管理页面不同源。
            packs = ManagedPacks.get_installed_targets(cache=get_pack_cache()) or []
        except Exception:
            return None
        for dev in packs:
            part = getattr(dev, "part_number", "") or ""
            pl = part.lower()
            if not pl:
                continue
            if pl == key or wildcard_eq(pl, key) or pl.startswith(key) or key.startswith(pl):
                return part
        return None
