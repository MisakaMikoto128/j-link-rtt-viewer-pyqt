"""发送栏逻辑：输入 + HEX + CRC 脚本 + 定时发送 + 自动断帧 + 回显 + 统计。

从 RTTMonitorPage 拆出，持有发送栏控件引用（dataclass 传入）+ cfg。
通过信号通信：send_requested（-> worker）/ echo_requested（-> 显示区回显）/
stats_changed（-> 状态栏 lbl_status_tx）/ warn_requested（-> InfoBar）。
_append_styled_line 留主类（显示区逻辑，操作 display + scroll guard）。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from core.crc_utils import CRC_ALGORITHMS, compute_crc
from core.jlink_worker import encode_send_payload


@dataclass
class SendBarControls:
    """发送栏控件引用（RTTMonitorPage._build 创建后传入）。"""

    te_send: object
    btn_send: object
    btn_hex_tx_down: object
    chk_crc_script: object
    cb_crc_algo: object
    chk_show_send_text: object
    btn_frame_help: object
    chk_auto_frame: object
    le_frame_timeout: object
    chk_timed_send: object
    le_timed_interval: object
    btn_timed_unit: object


class SendBar(QObject):
    """发送栏逻辑。控件由 RTTMonitorPage 创建，本类持有引用 + 连接信号。"""

    send_requested = Signal(str, bool)  # text, is_hex -> worker.send_data_requested
    echo_requested = Signal(str)  # orig_text -> RTTMonitorPage._echo_sent_text
    stats_changed = Signal(int, int)  # total, last -> lbl_status_tx
    warn_requested = Signal(str, str)  # title, msg -> _infobar.warn

    def __init__(self, controls: SendBarControls, cfg, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._c = controls
        self._cfg = cfg
        self._is_connected = False
        self._send_total_bytes = 0
        self._send_last_bytes = 0
        self._timed_send_pending = False
        self._timed_send_timer = QTimer(self)
        self._timed_send_timer.timeout.connect(self._on_timed_send_fire)
        self._te_send_orig_ss: str | None = None
        self._frame_tip = None
        self._frame_help_title = self.tr("自动断帧")
        self._frame_help_content = self._build_frame_help_content()
        # 连接控件信号
        controls.btn_send.clicked.connect(self._on_send_clicked)
        controls.chk_crc_script.toggled.connect(self._on_crc_script_toggled)
        controls.btn_hex_tx_down.toggled.connect(self._on_hex_send_toggled)
        controls.btn_frame_help.clicked.connect(self._on_frame_help_clicked)
        controls.chk_auto_frame.toggled.connect(self._on_auto_frame_toggled)
        controls.chk_timed_send.toggled.connect(self._on_timed_send_toggled)

    # ---- 公开接口 ----

    def set_connected(self, connected: bool) -> None:
        """连接状态变化（_on_state_changed 时主类调）。"""
        self._is_connected = connected
        if not connected:
            self._timed_send_timer.stop()
            self._timed_send_pending = False
        else:
            # 连接后自动恢复定时发送（checkbox 仍勾选且 pending）
            if self._timed_send_pending and self._c.chk_timed_send.isChecked():
                self._start_timed_send_timer()

    def reset_send_stats(self) -> None:
        """清零发送统计（_on_reset_stats_clicked 时主类调）。"""
        self._send_total_bytes = 0
        self._send_last_bytes = 0
        self.stats_changed.emit(0, 0)

    def retranslate(self) -> None:
        """语言切换时刷新 _frame_help_title/content。"""
        self._frame_help_title = self.tr("自动断帧")
        self._frame_help_content = self._build_frame_help_content()

    def _build_frame_help_content(self) -> str:
        return (
            self.tr("接收超时设置（1~200 毫秒），默认 20ms。")
            + "\n\n"
            + self.tr("在接收连续数据流时，如果相邻两批数据的接收时间间隔")
            + "\n"
            + self.tr("超过设定值，则判定为一帧数据结束，自动插入换行。")
            + "\n\n"
            + self.tr("自动断帧：启用后，每个数据帧显示后自动添加换行符，")
            + "\n"
            + self.tr("便于区分不同帧。")
        )

    # ---- 发送 ----

    def _on_send_clicked(self) -> None:
        if not self._is_connected:
            self.warn_requested.emit(
                self.tr("未连接目标"), self.tr("请先连接 J-Link 和目标设备后再发送")
            )
            return
        text = self._c.te_send.toPlainText().strip()
        if not text:
            return
        is_hex = self._c.btn_hex_tx_down.isChecked()

        # 脚本：勾选后按 cb_crc_algo 所选追加后缀 -- CRC 算法 或 自动换行
        if self._c.chk_crc_script.isChecked():
            script_idx = self._c.cb_crc_algo.currentIndex()
            if script_idx >= len(CRC_ALGORITHMS):
                # 「自动换行」：非 HEX 模式追加换行符（字符取自设置页「换行符」）
                if not is_hex:
                    text += self._cfg.get("send_line_ending")
            else:
                try:
                    _, algo_key = CRC_ALGORITHMS[script_idx]
                    payload = encode_send_payload(text, is_hex)
                    crc_bytes = compute_crc(algo_key, payload)
                    full_payload = payload + crc_bytes
                    # 追加 CRC 后以 HEX 方式发送
                    text = " ".join(f"{b:02X}" for b in full_payload)
                    is_hex = True
                except Exception as exc:
                    self.warn_requested.emit(self.tr("CRC 错误"), str(exc))
                    return

        self.send_requested.emit(text, is_hex)
        # 发送字节统计：发送时即时刷新（不走 1s 轮询，避免延迟）
        sent_bytes = len(encode_send_payload(text, is_hex))
        self._send_total_bytes += sent_bytes
        self._send_last_bytes = sent_bytes
        self.stats_changed.emit(self._send_total_bytes, self._send_last_bytes)
        # 加入历史（去重 + 末尾追加）-- 存用户原始输入，不存换行符和 CRC 追加后的
        orig_text = self._c.te_send.toPlainText().strip()
        hist = list(self._cfg.get("send_history") or [])
        if orig_text in hist:
            hist.remove(orig_text)
        hist.append(orig_text)
        self._cfg.set("send_history", hist)

        # 发送回显：勾选"显示发送字符串"后每次发送在显示区追加一行染色文本
        if self._c.chk_show_send_text.isChecked():
            self.echo_requested.emit(orig_text)

    def _on_crc_script_toggled(self, checked: bool) -> None:
        """CRC 脚本 checkbox 切换：顶部边框上色 + 由上而下的红色渐变背景。"""
        if checked:
            self._te_send_orig_ss = self._c.te_send.styleSheet()
            _crc_css = (
                "\nQPlainTextEdit {"
                "  border-top: 2px solid #cc3300;"
                "  background: qlineargradient("
                "    x1:0, y1:0, x2:0, y2:1,"
                "    stop:0 rgba(204,51,0,0.14),"
                "    stop:0.05 rgba(204,51,0,0.06),"
                "    stop:0.1 rgba(204,51,0,0.02),"
                "    stop:0.15 rgba(204,51,0,0));"
                "}"
                "\nQPlainTextEdit:hover {"
                "  border-top: 2px solid #cc3300;"
                "}"
                "\nQPlainTextEdit:focus {"
                "  border-top: 2px solid #cc3300;"
                "}"
            )
            self._c.te_send.setStyleSheet(self._te_send_orig_ss + _crc_css)
        else:
            orig = self._te_send_orig_ss
            if orig is not None:
                self._c.te_send.setStyleSheet(orig)
                self._te_send_orig_ss = None

    def _on_hex_send_toggled(self, checked: bool) -> None:
        """HEX 发送模式切换：双向转换发送框内容。

        checked=True  -> 文本 -> HEX："hello" -> "68 65 6C 6C 6F"
        checked=False -> HEX -> 文本："68 65 6C 6C 6F" -> "hello"
        转换失败（非法 HEX）则保留原文。
        """
        self._cfg.set("hex_send_mode", checked)
        cur = self._c.te_send.toPlainText()
        if not cur:
            return
        if checked:
            try:
                raw = cur.encode("utf-8")
                hex_str = " ".join(f"{b:02X}" for b in raw)
                self._c.te_send.setPlainText(hex_str)
            except Exception:
                pass
        else:
            try:
                cleaned = cur.replace(" ", "").replace("\n", "").replace("\r", "")
                if len(cleaned) % 2 != 0:
                    cleaned += "0"
                raw = bytes.fromhex(cleaned)
                self._c.te_send.setPlainText(raw.decode("utf-8", errors="replace"))
            except ValueError:
                pass  # 非法 HEX，保留原文

    # ---- 自动断帧 ----

    def _on_frame_help_clicked(self) -> None:
        """? 按钮点击：弹出 PopupTeachingTip，点击外部自动关闭。"""
        from qfluentwidgets import (
            PopupTeachingTip,
            TeachingTipTailPosition,
            TeachingTipView,
        )

        view = TeachingTipView(
            title=self._frame_help_title,
            content=self._frame_help_content,
            isClosable=True,
            tailPosition=TeachingTipTailPosition.TOP,
        )
        self._frame_tip = PopupTeachingTip.make(
            view,
            target=self._c.btn_frame_help,
            duration=-1,
            tailPosition=TeachingTipTailPosition.TOP,
            parent=self.parent(),
        )
        view.closed.connect(self._frame_tip.close)

    def _on_auto_frame_toggled(self, checked: bool) -> None:
        """自动断帧 checkbox 切换：选中 = 功能启用，参数锁定（禁用编辑）。"""
        self._c.le_frame_timeout.setEnabled(not checked)
        self._c.btn_frame_help.setEnabled(not checked)

    def _get_frame_timeout_ms(self) -> int:
        """从 LineEdit 解析自动断帧超时值，夹到 [1, 200]。"""
        try:
            return max(1, min(200, int(self._c.le_frame_timeout.text())))
        except (ValueError, AttributeError):
            return 20

    # ---- 定时发送 ----

    def _on_timed_send_toggled(self, checked: bool) -> None:
        """定时发送 checkbox 切换：选中 = 功能启用，参数锁定（禁用编辑）。"""
        self._c.le_timed_interval.setEnabled(not checked)
        self._c.btn_timed_unit.setEnabled(not checked)
        if checked:
            if not self._is_connected:
                self.warn_requested.emit(
                    self.tr("提示"), self.tr("未连接目标，定时发送将在连接后自动启动")
                )
                self._timed_send_pending = True
                return
            self._start_timed_send_timer()
        else:
            self._timed_send_timer.stop()
            self._timed_send_pending = False

    def _get_timed_interval_sec(self) -> float:
        """从 LineEdit 解析定时发送间隔，夹到 [0.001, 999]。"""
        try:
            v = float(self._c.le_timed_interval.text())
            return max(0.001, min(999.0, v))
        except (ValueError, AttributeError):
            return 1.0

    def _start_timed_send_timer(self) -> None:
        """按当前 interval 启动/重启定时器。"""
        self._timed_send_timer.stop()
        interval_ms = max(1, int(self._get_timed_interval_sec() * 1000))
        self._timed_send_timer.setInterval(interval_ms)
        self._timed_send_timer.start()
        self._timed_send_pending = False

    def _on_timed_send_fire(self) -> None:
        """定时器回调：自动触发发送。"""
        if not self._is_connected:
            self._timed_send_timer.stop()
            self._timed_send_pending = True
            return
        # 如果用户改了间隔，实时生效
        interval_ms = max(1, int(self._get_timed_interval_sec() * 1000))
        if self._timed_send_timer.interval() != interval_ms:
            self._timed_send_timer.setInterval(interval_ms)
        self._on_send_clicked()
