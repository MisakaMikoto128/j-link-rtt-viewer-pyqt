"""RTT 显示区：QPlainTextEdit + 多通道历史缓冲 + ANSI/HEX 渲染 + 自动滚动/断帧/标记。

从 rtt_monitor_page.py 拆出（Step 4）。持有显示区相关状态与方法：
- _channel_buffers / _all_rtt_buffer / _view_channel：多通道历史缓冲 + 当前视图通道
- _programmatic_scroll：区分程序性 setValue 与用户拖动（同步 chk_auto_scroll）
- _last_rx_time：自动断帧的时间间隙判定
- _mark_history：会话标记下拉历史（最多 10 条）

不持有：连接状态 / 发送通道 / 设备信息 / 工具栏编排 -- 这些留主类。
主类通过 get_view_channel()/set_view_channel() 读写视图通道（_update_stats /
_on_channel_changed / _update_channel_range_from_worker 等需读取）。

字体：显示区用 font_family/font_size（等宽），独立于全局 UI 字体（hex dump 列
对齐依赖等宽）；apply_font 标 _custom_font 属性挡住全局 setFont。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QColor, QFont, QFontDatabase, QTextCharFormat, QTextCursor
from qfluentwidgets import BodyLabel, CheckBox, EditableComboBox, PlainTextEdit

from core.ansi_parser import AnsiAttrs, parse_ansi
from core.config_service import ConfigService
from core.jlink_worker import CHANNEL_ALL

from ._rtt_colors import (
    ANSI_QCOLORS,
    DEFAULT_BG_QCOLOR,
    DEFAULT_FG_QCOLOR,
    DEFAULT_SEND_ECHO_COLOR,
)

if TYPE_CHECKING:
    from ._send_bar import SendBar

_FONT_SIZE_MIN = 8
_FONT_SIZE_MAX = 32


@dataclass
class DisplayAreaControls:
    """主类 _build_ui 构造的显示区控件引用，传给 DisplayArea 持有。"""

    display: PlainTextEdit
    chk_auto_scroll: CheckBox
    chk_auto_frame: CheckBox
    chk_hex_display: CheckBox
    le_mark: EditableComboBox
    lbl_font_size: BodyLabel


class DisplayArea(QObject):
    """RTT 显示区：接收数据渲染 + 通道历史缓冲 + 自动滚动/断帧/标记/字号。

    纯被动：worker.rtt_data_received -> on_rtt_data；按钮 clicked -> on_*。
    主类编排方法（_on_channel_changed / _update_stats 等）通过
    get_view_channel()/set_view_channel() 读写视图通道，render_view() 触发重渲染。
    """

    def __init__(
        self,
        controls: DisplayAreaControls,
        cfg: ConfigService,
        send_bar: SendBar,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._display = controls.display
        self._chk_auto_scroll = controls.chk_auto_scroll
        self._chk_auto_frame = controls.chk_auto_frame
        self._chk_hex_display = controls.chk_hex_display
        self._le_mark = controls.le_mark
        self._lbl_font_size = controls.lbl_font_size
        self._cfg = cfg
        self._send_bar = send_bar

        # 多通道历史缓冲：_channel_buffers[ch] = 该通道纯文本；
        # _all_rtt_buffer = 全部通道按到达顺序合并；上限 rtt_channel_history_chars。
        # _view_channel：当前视图通道（-1=全部）。切通道只换视图，缓冲不变。
        self._channel_buffers: dict[int, str] = {}
        self._all_rtt_buffer: str = ""
        self._view_channel: int = cfg.get("rtt_channel")

        # 自动滚动状态：True=程序性 setValue（autoscroll），False=用户手动滚动。
        # 用法：with self.programmatic_scroll_guard(): sb.setValue(...)
        self._programmatic_scroll = False

        # 自动断帧：上次接收数据的时间戳
        self._last_rx_time: float = 0.0

        # 会话标记下拉历史（最多 10 条，最近在前）
        self._mark_history: list[str] = []

    # ------------------------------------------------------------------
    # 视图通道（主类读写入口）
    # ------------------------------------------------------------------
    def get_view_channel(self) -> int:
        return self._view_channel

    def set_view_channel(self, ch: int) -> None:
        self._view_channel = ch

    @contextmanager
    def programmatic_scroll_guard(self):
        """围栏：with 块内的 sb.setValue 不会触发 on_display_scrolled 取消勾选。"""
        self._programmatic_scroll = True
        try:
            yield
        finally:
            self._programmatic_scroll = False

    # ------------------------------------------------------------------
    # 数据接收 + 渲染
    # ------------------------------------------------------------------
    @Slot(int, str)
    def on_rtt_data(self, channel: int, text: str) -> None:
        """worker 50ms 合并后按通道推来的数据：先按通道入历史缓冲，再决定渲染。

        - 缓冲：_channel_buffers[ch] 存该通道历史，_all_rtt_buffer 存合并历史，
          上限 rtt_channel_history_chars（默认 200k，超出丢最旧）。
        - 渲染：仅当数据通道与当前视图匹配才实时插入显示区
          （全部通道视图 = 任何通道都渲染）。
        """
        if not text:
            return

        # 1) 按通道入历史缓冲（无论当前是否查看，保证切通道后历史完整）
        limit = int(self._cfg.get("rtt_channel_history_chars") or 200000)
        buf = self._channel_buffers.get(channel, "") + text
        if len(buf) > limit:
            buf = buf[-limit:]
        self._channel_buffers[channel] = buf
        self._all_rtt_buffer += text
        if len(self._all_rtt_buffer) > limit:
            self._all_rtt_buffer = self._all_rtt_buffer[-limit:]

        # 2) 视图不匹配：只入缓冲，不渲染（数据已由 worker 按通道合并，此处是纯显示分支）
        if self._view_channel != CHANNEL_ALL and channel != self._view_channel:
            return

        # 3) 实时渲染（当前视图通道的数据）
        # 自动断帧：仅「非全部通道」视图启用--多通道合并视图里时间间隙没有帧语义
        now = time.time()
        if (
            self._view_channel != CHANNEL_ALL
            and self._chk_auto_frame.isChecked()
            and self._last_rx_time > 0
            and (now - self._last_rx_time) * 1000 > self._send_bar._get_frame_timeout_ms()
        ):
            # 插入换行分隔不同帧
            sb_pre = self._display.verticalScrollBar()
            at_b = sb_pre.value() >= sb_pre.maximum() - 4
            tc = self._display.textCursor()
            tc.movePosition(QTextCursor.End)
            if tc.columnNumber() != 0:
                tc.insertText("\n")
            if at_b and self._chk_auto_scroll.isChecked():
                with self.programmatic_scroll_guard():
                    sb_pre.setValue(sb_pre.maximum())
        self._last_rx_time = now

        # 自动滚动判断必须在插入文本前
        sb = self._display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        cursor = self._display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        try:
            if self._chk_hex_display.isChecked():
                # HEX 显示：将文本编码为字节，每字节大写 HEX + 空格
                try:
                    raw = text.encode(self._cfg.get("rtt_encoding") or "utf-8", errors="replace")
                except LookupError:
                    raw = text.encode("utf-8", errors="replace")
                hex_str = " ".join(f"{b:02X}" for b in raw)
                cursor.insertText(hex_str + " ")
            else:
                for seg, attrs in parse_ansi(text):
                    cursor.insertText(seg, self._fmt(attrs))
        finally:
            cursor.endEditBlock()

        if at_bottom and self._chk_auto_scroll.isChecked():
            with self.programmatic_scroll_guard():
                sb.setValue(sb.maximum())

    def render_view(self) -> None:
        """切通道后重建显示区：把当前视图通道的历史缓冲重新 ANSI 解析 + 渲染。

        重建是一次性的（切通道动作），渲染后滚到底。搜索高亮 / 匹配计数属于旧文档，
        重建后清空（搜索栏本身保留，用户可重新搜索）。
        """
        if self._view_channel == CHANNEL_ALL:
            text = self._all_rtt_buffer
        else:
            text = self._channel_buffers.get(self._view_channel, "")
        self._display.setExtraSelections([])
        self._display.clear()
        if not text:
            return
        cursor = self._display.textCursor()
        if self._chk_hex_display.isChecked():
            try:
                raw = text.encode(self._cfg.get("rtt_encoding") or "utf-8", errors="replace")
            except LookupError:
                raw = text.encode("utf-8", errors="replace")
            cursor.insertText(" ".join(f"{b:02X}" for b in raw) + " ")
        else:
            for seg, attrs in parse_ansi(text):
                cursor.insertText(seg, self._fmt(attrs))
        with self.programmatic_scroll_guard():
            self._display.verticalScrollBar().setValue(
                self._display.verticalScrollBar().maximum()
            )

    @Slot()
    def on_clear_clicked(self) -> None:
        """清除按钮：显示区 + 所有通道历史缓冲一并清空（明确丢弃历史）。"""
        self._display.clear()
        self._channel_buffers.clear()
        self._all_rtt_buffer = ""

    # ------------------------------------------------------------------
    # 自动滚动双向同步
    # ------------------------------------------------------------------
    @Slot(bool)
    def on_auto_scroll_toggled(self, checked: bool) -> None:
        """checkbox 勾选/取消：持久化 + 勾选时立即跳到底并恢复跟踪。"""
        self._cfg.set("auto_scroll", checked)
        if checked:
            sb = self._display.verticalScrollBar()
            with self.programmatic_scroll_guard():
                sb.setValue(sb.maximum())

    @Slot(int)
    def on_display_scrolled(self, _value: int) -> None:
        """display 滚动条 valueChanged：双向同步 chk_auto_scroll。
        - 已勾选 + 用户上滚离开底部 -> 取消勾选（停止自动滚动）
        - 未勾选 + 用户滚回底部 -> 重新勾选（恢复自动滚动）
        程序性 sb.setValue() 不触发（programmatic_scroll_guard 过滤）。
        """
        if self._programmatic_scroll:
            return
        sb = self._display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        is_checked = self._chk_auto_scroll.isChecked()
        if is_checked and not at_bottom:
            self._set_auto_scroll_silent(False)
        elif not is_checked and at_bottom:
            self._set_auto_scroll_silent(True)

    def _set_auto_scroll_silent(self, checked: bool) -> None:
        """改 checkbox + 落 cfg，但不触发 on_auto_scroll_toggled 回调
        （避免它再发起一次程序性 setValue 形成回环）。"""
        self._chk_auto_scroll.blockSignals(True)
        self._chk_auto_scroll.setChecked(checked)
        self._chk_auto_scroll.blockSignals(False)
        self._cfg.set("auto_scroll", checked)

    # ------------------------------------------------------------------
    # 染色行追加（发送回显 / 会话标记 / 意外断开提示 共用）
    # ------------------------------------------------------------------
    def append_styled_line(
        self, text: str, color: str, *, bold: bool = False, force_scroll: bool = False
    ) -> None:
        """在显示区末尾追加一行染色文本（自动滚动判断 + 程序性滚动围栏）。

        发送回显 / 会话标记 / 意外断开红字提示 三处同形态：都是把一段带颜色的
        文本追加到显示区末尾。统一在此处理 at_bottom 判断 + 换行 + cursor +
        QTextCharFormat，避免每处各写一份而漏 reset 程序性滚动标志。

        force_scroll=True 时不看 chk_auto_scroll（会话标记：用户主动插入，
        即使关闭自动滚动也跟到末尾）；其余按 chk_auto_scroll 决定。

        注意：这类「非 RTT 数据」的染色行只入显示区，**不写入 _channel_buffers /
        _all_rtt_buffer**--它们不是通道数据，切通道重建视图时不应复现。
        """
        sb = self._display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        cursor = self._display.textCursor()
        cursor.movePosition(QTextCursor.End)
        if cursor.columnNumber() != 0:
            cursor.insertText("\n")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Bold)
        cursor.insertText(text, fmt)
        if at_bottom and (force_scroll or self._chk_auto_scroll.isChecked()):
            with self.programmatic_scroll_guard():
                sb.setValue(sb.maximum())

    def echo_sent_text(self, text: str) -> None:
        """在显示区追加一行 » 开头的回显文本，颜色由 send_text_color 决定。

        插入与自动滚动由 append_styled_line 统一处理。
        """
        self.append_styled_line(
            f"» {text}\n",
            self._cfg.get("send_text_color") or DEFAULT_SEND_ECHO_COLOR,
        )

    # ------------------------------------------------------------------
    # 会话标记
    # ------------------------------------------------------------------
    def insert_mark_text(self, text: str) -> None:
        """在显示区追加一行视觉分隔的标记。颜色由 cfg.mark_color 决定。

        text="" -> 插入纯分隔线 ──────。
        被用户点 "插入标记" + 连接/断开自动标记共用。
        """
        line = f"──── {text} ────" if text else "─" * 50
        self.append_styled_line(
            line + "\n",
            self._cfg.get("mark_color") or "#ffff55",
            bold=True,
            force_scroll=True,
        )

    @Slot()
    def on_insert_mark(self) -> None:
        text = self._le_mark.currentText().strip()
        if text:
            if text in self._mark_history:
                self._mark_history.remove(text)
            self._mark_history.append(text)
            self._mark_history = self._mark_history[-10:]
            self._le_mark.clear()
            self._le_mark.addItems(reversed(self._mark_history))
        self.insert_mark_text(text)
        # qfluentwidgets EditableComboBox 没有 clearEditText()--用 setCurrentText 替代
        self._le_mark.setCurrentText("")

    # ------------------------------------------------------------------
    # ANSI 格式 + 字体
    # ------------------------------------------------------------------
    def _fmt(self, attrs: AnsiAttrs) -> QTextCharFormat:
        # 注意：QColor 必须从预构造表查（ANSI_QCOLORS），不要 QColor(hex_string)。
        # RTT 高吞吐时本函数每段都调，每次构造 QColor 是不必要的 syscall + alloc。
        fmt = QTextCharFormat()
        if attrs.fg:
            fmt.setForeground(ANSI_QCOLORS.get(attrs.fg, DEFAULT_FG_QCOLOR))
        if attrs.bg:
            fmt.setBackground(ANSI_QCOLORS.get(attrs.bg, DEFAULT_BG_QCOLOR))
        if attrs.bold:
            # 用 setFontWeight 而非 setFont(fmt.font())--后者会把字号也设回
            # QTextCharFormat 默认值（通常远小于 widget 字号），导致 bold
            # 段落字号被缩水。setFontWeight 只改 weight，字号继承 widget。
            fmt.setFontWeight(QFont.Bold)
        return fmt

    @Slot(str, int)
    def apply_font(self, family: str, size: int) -> None:
        font = QFont(family, size)
        if font.family() != family:
            # 字体回落到等宽字体
            font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
            font.setPointSize(size)
        self._display.setFont(font)
        # 标记专属字体：全局界面字号热更新时跳过，保持等宽 + RTT 专用字号
        self._display.setProperty("_custom_font", True)
        # 同步右上角字号显示
        self._lbl_font_size.setText(str(size))

    def adjust_font_size(self, delta: int) -> None:
        cur = int(self._cfg.get("font_size"))
        new = max(_FONT_SIZE_MIN, min(_FONT_SIZE_MAX, cur + delta))
        if new != cur:
            self._cfg.set("font_size", new)
