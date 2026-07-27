"""J-Link RTT backend：pylink-square 1.6.0。

从 JLinkWorker 抽出的 probe-specific 调用。连接序列严格按 CLAUDE.md
'pylink 1.6.0 连接顺序'：open -> close -> open(serial) -> rtt_start ->
set_tif -> set_speed -> connect。

包装 worker 持久拥有的 pylink.JLink 实例（enum + 连接复用，不自建--worker
在 initialize() 内建 self.jlink，本 backend 拿引用）。
"""

from __future__ import annotations

import time

import pylink

from ..logger import get_logger
from .base import LogCallback, ProbeNotConnected
from .rtt_backend import (
    RTT_RESET_HALT,
    RTT_RESET_NORMAL,
    RTT_RESET_PRE_RECONNECT,
    RttConnectParams,
    RttConnectResult,
)


class PylinkRttBackend:
    """J-Link via pylink-square 1.6.0。实现 RttBackend 协议。"""

    def __init__(self, jlink: pylink.JLink, log: LogCallback) -> None:
        self._jlink = jlink
        self._log = log
        self._logger = get_logger()

    # ============================================================
    # 连接
    # ============================================================
    def connect(self, params: RttConnectParams) -> RttConnectResult:
        j = self._jlink
        serial = params.serial
        remote = params.remote_addr
        actual_serial = ""

        if remote:
            # 远程模式：跳过 USB 枚举与 serial 校验，按 ip:port 双开。
            # J-Link DLL 不做 DNS，remote_addr 已由 UI 解析为 IPv4 字面量。
            if not j.opened():
                j.open(ip_addr=remote)
                ser = j.serial_number
                j.close()
                j.open(ip_addr=remote)
                j.rtt_start()
                self._log("info", f"J-Link SN: {ser} (远程 {remote})")
                actual_serial = str(int(ser)) if ser is not None else ""
        else:
            # 本地模式：前置校验 + serial 双开
            # 预查 J-Link 是否接入：connected_emulators() 用 JLINKARM_EMU_GetList 纯枚举，
            # 不弹 DLL 原生选择窗。空则提示 + 抛（worker catch 后 _do_disconnect emit 状态 False
            # 让 UI 按钮回正），避免无设备时 jlink.open() 弹出只能鼠标关闭的 DLL 弹窗。
            try:
                emus = j.connected_emulators()
            except Exception as e:
                self._log("warning", f"未检测到 J-Link 设备，请检查 USB 连接 ({e})")
                raise ProbeNotConnected("no jlink") from e
            if not emus:
                self._log("warning", "未检测到 J-Link 设备，请检查 USB 连接")
                raise ProbeNotConnected("no jlink")
            # serial 校验："0"/空 = 未指定（跳过）；真实 serial 校验「这台还在接入」--
            # 否则 open(serial) 对不存在的 serial 会直接抛，且 auto_reconnect 会误连 B。
            if (
                serial
                and serial != "0"
                and not any(
                    str(int(getattr(e, "SerialNumber", 0) or 0)) == serial for e in emus
                )
            ):
                self._log(
                    "warning",
                    f"选中的 J-Link（S/N: {serial}）不在线，请刷新设备列表或重新选择",
                )
                raise ProbeNotConnected("jlink offline")

            if not j.opened():
                # 参考项目的双开模式（pylink 1.6.0 稳定工作的关键模式）：
                # 先 open() 一次取 serial，close，再 open(serial)，然后 rtt_start
                # 注意：rtt_start 必须在 connect(target) 之前调用
                if serial and serial != "0":
                    # 指定了具体 J-Link：双开都按 serial（open() 空参在多设备下
                    # 可能弹 DLL 选择窗或抢到另一台）
                    j.open(serial_no=int(serial))
                    ser = j.serial_number
                    j.close()
                    j.open(serial_no=ser)
                    j.rtt_start()
                else:
                    # 未指定（"0" / 空串）：open() 空参，pylink 自己挑唯一接入的设备
                    j.open()
                    ser = j.serial_number
                    j.close()
                    j.open(str(ser))
                    j.rtt_start()
                self._log("info", f"J-Link SN: {ser}")
                actual_serial = str(int(ser)) if ser is not None else ""

        # SWD / JTAG 二选一（CLAUDE.md 'set_tif 是错的'：不可 OR 起来）
        tif = (
            pylink.enums.JLinkInterfaces.SWD
            if params.iface == "SWD"
            else pylink.enums.JLinkInterfaces.JTAG
        )
        j.set_tif(tif)
        j.set_speed(int(params.speed_khz))
        j.connect(params.target)

        if not j.connected():
            self._log("error", "连接目标失败")
            raise ProbeNotConnected("connect(target) 后 connected() 仍为 False")

        num_up = self._detect_num_up_channels()
        info = self._collect_device_info(
            params.target, params.iface, params.speed_khz, remote
        )
        if not actual_serial:
            sn = j.serial_number
            actual_serial = str(int(sn)) if sn is not None else ""
        return RttConnectResult(
            actual_serial=actual_serial,
            num_up_channels=num_up,
            device_info=info,
        )

    def _detect_num_up_channels(self) -> int:
        """连接后探测 MCU 端实际【已分配】的 RTT 上行通道数。

        关键：不用 rtt_get_num_up_buffers() 的返回值当通道数--它返回的是固件声明的
        MaxNumUpBuffers（描述符数组大小），含「声明了但没初始化的空槽」。实测某
        STM32xx 固件声明 3 个上行缓冲，但只有 ch0 真正分配了缓冲（SizeOfBuffer=1024），
        ch1/ch2 的 SizeOfBuffer=0（空槽，永远没数据）。若用声明数 3，SpinBox 会显示
        0/1/2 且选 4 拉回到 2（空槽无数据）--正是用户报的 bug。

        正确口径：遍历各通道 buf descriptor，数 SizeOfBuffer>0 的（从 0 起连续）。
        空槽即停（SEGGER RTT 通道按惯例从 0 连续分配）。

        RTT 控制块定位是异步的--紧凑重连（断开立即重连 / auto_reconnect）时
        rtt_get_num_up_buffers 会抛 "The RTT Control Block has not yet been found"。
        故失败/返回 0 时短间隔重试，仍失败回退 1（只读 ch0，行为同旧版）。
        """
        last_err: str = ""
        for attempt in range(4):  # 0ms / 150ms / 300ms / 450ms，覆盖典型定位窗口
            try:
                declared = int(self._jlink.rtt_get_num_up_buffers())
                if declared < 1:
                    raise RuntimeError(f"declared={declared}")
                allocated = 0
                for ch in range(declared):
                    desc = self._jlink.rtt_get_buf_descriptor(ch, up=True)
                    if getattr(desc, "SizeOfBuffer", 0) > 0:
                        allocated += 1
                    else:
                        break  # 从 0 起连续，遇空槽即停
                if allocated >= 1:
                    self._logger.info(
                        f"RTT 通道数探测：声明 {declared} / 实际分配 {allocated}（第 {attempt + 1} 次尝试）"
                    )
                    return allocated
                last_err = f"声明{declared}但无已分配缓冲"
            except Exception as e:
                last_err = str(e)
            if attempt < 3:
                time.sleep(0.15)
        self._logger.warning(f"探测 RTT 上行通道数失败（重试 4 次），回退 1：{last_err}")
        return 1

    def _collect_device_info(
        self, target: str, iface: str, speed: int, remote_addr: str = ""
    ) -> dict:
        try:
            return {
                "jlink_firmware": self._jlink.firmware_version,
                "jlink_hardware": str(self._jlink.hardware_version),
                "jlink_serial": str(self._jlink.serial_number),
                "core_name": self._jlink.core_name(),
                "core_id": hex(self._jlink.core_id()),
                "core_cpu": self._jlink.core_cpu(),
                "target_device": target,
                "interface": iface,
                "speed_khz": speed,
                "remote_addr": remote_addr,
            }
        except Exception as e:
            self._logger.warning(f"获取设备信息失败：{e}")
            return {
                "target_device": target,
                "interface": iface,
                "speed_khz": speed,
                "remote_addr": remote_addr,
            }

    # ============================================================
    # 断开（幂等；close 抛 JLinkException 不致命，参考 CLAUDE.md）
    # ============================================================
    def disconnect(self) -> None:
        try:
            self._jlink.rtt_stop()
        except Exception as e:
            self._logger.warning(f"rtt_stop 失败：{e}")
        try:
            self._jlink.close()
        except Exception as e:
            self._logger.warning(f"close 失败：{e}")

    # ============================================================
    # RTT 读写
    # ============================================================
    def read_rtt(self, channel: int, max_bytes: int) -> bytes:
        return bytes(self._jlink.rtt_read(channel, max_bytes))

    def write_rtt(self, channel: int, data: bytes) -> int:
        return self._jlink.rtt_write(channel, data)

    # ============================================================
    # 复位（mode 区分是否需 RTT 重启--pylink 缓存控制块地址在 reset 后过期）
    # ============================================================
    def reset(self, mode: str) -> None:
        if mode == RTT_RESET_HALT:
            # halt=True：CPU 复位后停在复位向量，不执行启动代码
            self._jlink.reset(0, True)
        elif mode == RTT_RESET_NORMAL:
            # normal：复位 + 运行 + RTT 重启（清 pylink 缓存的控制块地址）
            self._jlink.reset(1, False)
            time.sleep(0.1)  # 等 MCU 重新初始化 _SEGGER_RTT 控制块
            self._jlink.rtt_stop()
            self._jlink.rtt_start()
        elif mode == RTT_RESET_PRE_RECONNECT:
            # auto_reconnect 前置复位：复位 + 运行，不重启 RTT（随后 disconnect）
            self._jlink.reset(1, False)
        else:
            raise ValueError(f"未知 reset mode: {mode}")

    # ============================================================
    # 内存读写（复用 memory_service，保持与旧 worker 完全一致）
    # ============================================================
    def read_memory(self, addr: int, size: int) -> bytes:
        from .. import memory_service

        return memory_service.read_memory(self._jlink, addr, size)

    def write_memory(self, addr: int, data: bytes) -> int:
        from .. import memory_service

        return memory_service.write_memory(self._jlink, addr, data)

    # ============================================================
    # 状态 / 电源（J-Link 独有）
    # ============================================================
    def is_connected(self) -> bool:
        return self._jlink.connected()

    def supports_power(self) -> bool:
        return True

    def power_on(self) -> None:
        self._jlink.power_on(default=False)

    def power_off(self) -> None:
        self._jlink.power_off(default=False)
