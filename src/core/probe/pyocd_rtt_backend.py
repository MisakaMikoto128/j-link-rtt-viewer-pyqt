"""ST-Link / CMSIS-DAP RTT backend：pyOCD 0.45。

实现 RttBackend 协议。pyOCD 一套 API 跨 ST-Link / CMSIS-DAP / DAPLink，
RTT 走 ``pyocd.debug.rtt.RTTControlBlock``（软件 SWD 轮询，host 端每次 poll 真往返）。

**与 PylinkRttBackend 的根本差异**（详见 memory pyocd-rtt-api）：
- pylink RTT 由 J-Link 固件后台轮询，host ``rtt_read`` 零 SWD；pyOCD 每次 ``up.read()``
  是真实 SWD 往返（空 poll 1 次读 8B，满 poll 3 次往返）。J-Link 上 pylink 完胜，
  但 ST-Link/CMSIS-DAP pylink 用不了，pyOCD 是唯一选择。

**连接关键坑（真硬件踩出）**：
- 2 阶段：先无 target_override 读 IDCODE 确认芯片，再带 target_override 重连。
  不指定 target 时 pyOCD 用通用 cortex_m，内存映射错，rtt.start() 扫 RAM TransferFault。
  本 backend 要求 UI 传入有效 pyOCD target name（``_resolve_target_type`` 解析），
  解析失败则尝试自动检测（读 IDCODE + 反查 target）。
- CMSIS-DAP 必须 ``prefer_v1``：Windows CMSIS-DAP v2 libusb claim_interface 对 HID
  返回 NOT_SUPPORTED。ST-Link 不受影响但设了无害。
- ``target.resume()`` 必须：pyOCD ``session.open()`` 默认 halt CPU，不 resume 则 WrOff
  永不前进，``up.read()`` 永远空（最隐蔽的坑）。
"""

from __future__ import annotations

import contextlib

from ..logger import get_logger
from .base import LogCallback, ProbeNotConnected
from .pyocd_backend import _swd_err_hint
from .rtt_backend import (
    RTT_RESET_HALT,
    RTT_RESET_NORMAL,
    RTT_RESET_PRE_RECONNECT,
    RttConnectParams,
    RttConnectResult,
)

# DBGMCU_IDCODE 地址候选（4 类 STM32 分布）：
# 0xE0042000 大多数 / 0x40015800 F0,L0,U0 / 0x5C001000 H7 / 0xE0044000 L5,U5,WBA
_DBGMCU_IDCODE_ADDRS = (0xE0042000, 0x40015800, 0x5C001000, 0xE0044000)


class PyocdRttBackend:
    """ST-Link / CMSIS-DAP via pyOCD 0.45。实现 RttBackend 协议。"""

    def __init__(self, log: LogCallback) -> None:
        self._log = log
        self._logger = get_logger()
        self._session = None
        self._target = None
        self._rtt = None  # RTTControlBlock
        self._rtt_cb_addr: int | None = None  # 缓存 RTT 控制块地址，重连时跳过全 RAM 扫描
        self._params: RttConnectParams | None = None

    # ============================================================
    # 连接
    # ============================================================
    def connect(self, params: RttConnectParams) -> RttConnectResult:
        import pyocd.core.session as _sess
        from pyocd.core.helpers import ConnectHelper

        self._params = params

        # CMSIS-DAP prefer_v1（Windows libusb 坑；ST-Link 无害，设了不影响枚举）
        _opts = _sess.Session.get_current().options
        _opts["cmsis_dap.prefer_v1"] = True

        # 解析 target_override：UI 传的 device_name（pyOCD target name 或 part_number）
        target_override = self._resolve_target_type(params.target)
        if target_override is None and self._ensure_pack_installed(params.target):
            target_override = self._resolve_target_type(params.target)
        if target_override is None:
            # 自动检测兜底：连一次读 IDCODE，反查 builtin target
            target_override = self._autodetect_target(params)
        if target_override is None:
            raise ProbeNotConnected(
                f"未知 target：{params.target}\n"
                f"pyOCD 库无此型号。请确认 device 与实际芯片一致，或装 pack：\n"
                f"pyocd pack install {params.target}"
            )

        options = {
            "transport": "swd" if params.iface == "SWD" else "jtag",
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
            self._log("warning", "未检测到所选烧录器，请检查 USB 连接或刷新设备列表")
            raise ProbeNotConnected("no probe")
        try:
            self._session.open()
        except Exception as e:
            msg = _swd_err_hint(str(e))
            self._log("warning", f"target open failed: {msg}")
            with contextlib.suppress(Exception):
                self._session.close()
            self._session = None
            raise ProbeNotConnected(msg) from e
        self._target = self._session.target

        # SWD 通信校验（open 不一定在 SWD 失败时抛，ST-Link 实测 false-OK）
        dp = getattr(self._target, "dp", None)
        read_reg = getattr(dp, "read_reg", None) if dp is not None else None
        if read_reg is not None:
            try:
                read_reg(0)  # DP IDCODE：触发一次 SWD 读
            except AttributeError:
                pass
            except Exception as e:
                msg = _swd_err_hint(f"SWD 校验失败：{e}")
                self._log("warning", msg)
                with contextlib.suppress(Exception):
                    self._session.close()
                self._session = None
                self._target = None
                raise ProbeNotConnected(msg) from e

        # pyOCD open 默认 halt CPU -- 必须 resume 否则 WrOff 不前进，RTT 读永远空
        # （最隐蔽的坑：连接成功 + 控制块找到但读 0 字节）
        with contextlib.suppress(Exception):
            self._target.resume()

        probe = self._session.probe
        self._log("info", f"Probe: {probe.product_name} (S/N: {probe.unique_id})")
        self._log("info", f"Target connected: {params.target}")

        # RTT 控制块（优先用缓存的地址跳过全 RAM 扫描；首次或缓存失效时全扫描）
        try:
            self._rtt = self._start_rtt_with_cache()
        except Exception as e:
            with contextlib.suppress(Exception):
                self._session.close()
            self._session = None
            self._target = None
            raise ProbeNotConnected(f"RTT 控制块未找到（固件是否调了 SEGGER_RTT_Init？）：{e}") from e

        num_up = self._count_allocated_up_channels()
        actual_serial = probe.unique_id or ""
        device_info = self._collect_device_info(params)
        return RttConnectResult(
            actual_serial=actual_serial,
            num_up_channels=num_up,
            device_info=device_info,
        )

    def _start_rtt_with_cache(self):
        """启动 RTT 控制块：优先用缓存的地址（size=0 直接定位），失败回退全 RAM 扫描。

        首次连接全扫描 RAM 找 "SEGGER RTT" 标识（慢，~256ms/128KB）；找到后缓存地址，
        重连时直接定位（快，~2ms）。固件重定位控制块（罕见）时缓存失效，自动回退扫描。
        """
        from pyocd.debug.rtt import SEGGER_RTT_CB, RTTControlBlock, sizeof

        if self._rtt_cb_addr is not None:
            try:
                rtt = RTTControlBlock.from_target(self._target, address=self._rtt_cb_addr, size=0)
                rtt.start()
                return rtt
            except Exception:
                self._logger.info("RTT 缓存地址失效，回退全 RAM 扫描")
        rtt = RTTControlBlock.from_target(self._target)
        rtt.start()
        # 缓存控制块地址：up_channels[0]._desc_addr = cb_addr + sizeof(SEGGER_RTT_CB)
        if rtt.up_channels:
            self._rtt_cb_addr = rtt.up_channels[0]._desc_addr - sizeof(SEGGER_RTT_CB)
        return rtt

    def _count_allocated_up_channels(self) -> int:
        """数已分配的上行通道（SizeOfBuffer>0，从 0 起连续）-- 与 PylinkRttBackend 一致。

        pyOCD 的 up_channels 含声明但未初始化的空槽（SizeOfBuffer=0）。直接 len()
        会返回声明数（含空槽），与 J-Link 路径不一致。按 SEGGER 惯例从 0 连续分配，遇空槽即停。
        """
        if not self._rtt or not self._rtt.up_channels:
            return 1
        allocated = 0
        for ch in self._rtt.up_channels:
            if getattr(ch, "size", 0) > 0:
                allocated += 1
            else:
                break
        return allocated if allocated >= 1 else 1

    def _autodetect_target(self, params: RttConnectParams) -> str | None:
        """target_override 解析失败时的兜底：连一次（无 target_override）读 IDCODE，
        反查 pyOCD builtin target。失败返回 None。

        通用 cortex_m target 内存映射错，但读 IDCODE 只需 DP 访问，不依赖 RAM 映射。
        """
        from pyocd.core.helpers import ConnectHelper

        try:
            sess = ConnectHelper.session_with_chosen_probe(
                unique_id=(params.serial or None),
                options={
                    "transport": "swd" if params.iface == "SWD" else "jtag",
                    "frequency": int(params.speed_khz) * 1000,
                },
            )
            if sess is None:
                return None
            sess.open()
            target = sess.target
            idcode = None
            for addr in _DBGMCU_IDCODE_ADDRS:
                try:
                    idcode = target.read32(addr)
                    break
                except Exception:
                    continue
            sess.close()
        except Exception as e:
            self._logger.warning(f"自动检测 target 失败：{e}")
            return None
        if idcode is None:
            return None
        # 反查 builtin target：pyOCD TARGET dict 的 target 类有 idcode 属性
        return self._lookup_target_by_idcode(idcode)

    @staticmethod
    def _lookup_target_by_idcode(idcode: int) -> str | None:
        """IDCODE -> pyOCD builtin target name（反查 TARGET dict）。找不到返回 None。"""
        try:
            from pyocd.target import TARGET

            for name, target_cls in TARGET.items():
                # builtin target 类通常有 idcode 类属性（部分无则跳过）
                tid = getattr(target_cls, "idcode", None) or getattr(
                    target_cls, "part_number", None
                )
                if tid is None:
                    continue
                # idcode 匹配：有些 target 存的是完整 32-bit IDCODE
                if isinstance(tid, int) and tid == idcode:
                    return name
            # F103/F030 等 DBGMCU_IDCODE 低 16 位是 chip id，pyOCD 可能用部分匹配
            idcode_low = idcode & 0xFFF
            for name, target_cls in TARGET.items():
                tid = getattr(target_cls, "idcode", None)
                if isinstance(tid, int) and (tid & 0xFFF) == idcode_low:
                    return name
        except Exception:
            pass
        return None

    def _collect_device_info(self, params: RttConnectParams) -> dict:
        info: dict = {
            "target_device": params.target,
            "interface": params.iface,
            "speed_khz": params.speed_khz,
            "remote_addr": "",
        }
        try:
            if self._session and self._session.probe:
                probe = self._session.probe
                info["jlink_serial"] = probe.unique_id or ""
                info["jlink_firmware"] = getattr(probe, "firmware_version", "") or ""
                info["jlink_hardware"] = probe.product_name or ""
        except Exception as e:
            self._logger.warning(f"获取 probe 信息失败：{e}")
        try:
            if self._target is not None:
                # core_name / core_cpu 对 pyOCD 无直接等价，留空（UI 容忍缺字段）
                info["core_name"] = getattr(self._target, "part_number", "") or ""
        except Exception:
            pass
        return info

    # ============================================================
    # 断开（幂等）
    # ============================================================
    def disconnect(self) -> None:
        if self._session is None:
            return
        try:
            self._session.close()
        except Exception as e:
            self._logger.warning(f"session close 失败：{e}")
        self._session = None
        self._target = None
        self._rtt = None

    # ============================================================
    # RTT 读写
    # ============================================================
    def read_rtt(self, channel: int, max_bytes: int) -> bytes:
        if self._rtt is None or channel >= len(self._rtt.up_channels):
            return b""
        try:
            return bytes(self._rtt.up_channels[channel].read())
        except Exception:
            # up.read() 抛 RTTError（Invalid up buffer）等--向上抛让 read_loop 计入意外断开
            raise

    def write_rtt(self, channel: int, data: bytes) -> int:
        if self._rtt is None or channel >= len(self._rtt.down_channels):
            return 0
        return int(self._rtt.down_channels[channel].write(data))

    # ============================================================
    # 复位（pyOCD RTT 控制块在 RAM，reset 后固件重新初始化；pyOCD 每次 poll 重读
    # offset，不需 rtt_stop/start）
    # ============================================================
    def reset(self, mode: str) -> None:
        if self._target is None:
            return
        if mode == RTT_RESET_HALT:
            self._target.reset_and_halt()
        elif mode in (RTT_RESET_NORMAL, RTT_RESET_PRE_RECONNECT):
            self._target.reset()
        else:
            raise ValueError(f"未知 reset mode: {mode}")

    # ============================================================
    # 内存读写
    # ============================================================
    def read_memory(self, addr: int, size: int) -> bytes:
        if self._target is None:
            return b""
        return bytes(self._target.read_memory_block8(addr, size))

    def write_memory(self, addr: int, data: bytes) -> int:
        if self._target is None:
            return 0
        self._target.write_memory_block8(addr, list(data))
        return len(data)

    # ============================================================
    # 状态 / 电源（ST-Link/CMSIS-DAP 无 J-Link 式电源输出）
    # ============================================================
    def is_connected(self) -> bool:
        return self._session is not None and self._target is not None

    def supports_power(self) -> bool:
        return False

    def power_on(self) -> None:
        """ST-Link/CMSIS-DAP 无 J-Link 式目标电源输出。worker 应先查 supports_power。"""

    def power_off(self) -> None:
        """同 power_on：no-op。"""

    # ============================================================
    # target 解析（复用 flash PyOCDBackend 的逻辑，保持单点真源）
    # ============================================================
    @staticmethod
    def _resolve_target_type(device_name: str) -> str | None:
        from .pyocd_backend import PyOCDBackend

        return PyOCDBackend._resolve_target_type(device_name)

    def _ensure_pack_installed(self, device_name: str) -> bool:
        from core.pack_service import download_pack

        return download_pack(device_name, log=self._log)
