"""设置页：外观（主题/字体）+ RTT 行为（最大行数/Rx Timeout/日志目录）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCompleter,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    EditableComboBox,
    PushButton,
    SpinBox,
    SubtitleLabel,
    Theme,
    setTheme,
    setThemeColor,
)
from PySide6.QtGui import QColor, QFont

from . import _infobar
from ._scroll_helpers import make_transparent_scroll

from core.config_service import ConfigService
from core.jlink_worker import RESET_MODE_AUTO_RECONNECT, RESET_MODE_NORMAL
from core.logger import get_log_dir


_RESET_MODE_LABELS = [
    (RESET_MODE_NORMAL, "正常（仅重置目标 MCU）"),
    (RESET_MODE_AUTO_RECONNECT, "自动重连（断开+重连）"),
]


class _SettingRow(QWidget):
    """通用：左标题 + 右控件 的一行。"""

    def __init__(self, title: str, widget: QWidget, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.addWidget(BodyLabel(title), 1)
        lay.addWidget(widget)


class SettingsPage(QWidget):
    def __init__(self, cfg: ConfigService, parent=None):
        super().__init__(parent)
        self.setObjectName("settings")
        self._cfg = cfg
        self._build_ui()

    def _build_ui(self) -> None:
        # 整页透明 ScrollArea —— 窗口压扁时纵向滚，控件不再被挤压重叠。
        # 套路同 RTT / 内存 / 关于页，复用 make_transparent_scroll helper。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll, inner = make_transparent_scroll(self, "settings")
        outer.addWidget(self._scroll)

        root = QVBoxLayout(inner)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # ---- 外观 ----
        appearance = CardWidget(self)
        app_lay = QVBoxLayout(appearance)
        app_lay.addWidget(SubtitleLabel("外观"))

        # 主题模式
        self.cb_theme = ComboBox(self)
        self.cb_theme.addItems(["跟随系统", "浅色", "深色"])
        theme_str = self._cfg.get("theme")
        self.cb_theme.setCurrentIndex({"auto": 0, "light": 1, "dark": 2}.get(theme_str, 0))
        self.cb_theme.currentIndexChanged.connect(self._on_theme_changed)
        app_lay.addWidget(_SettingRow("主题模式", self.cb_theme))

        # 主题色
        color_row = QHBoxLayout()
        color_row.addWidget(BodyLabel("主题色"), 1)
        self.lbl_color = QLabel(self._cfg.get("theme_color"))
        self.lbl_color.setStyleSheet(
            f"background: {self._cfg.get('theme_color')}; padding: 2px 8px; color: white; border-radius: 4px;"
        )
        color_row.addWidget(self.lbl_color)
        self.btn_color = PushButton("选择…", self)
        self.btn_color.clicked.connect(self._on_pick_color)
        color_row.addWidget(self.btn_color)
        wrap = QWidget(self)
        wrap.setLayout(color_row)
        app_lay.addWidget(wrap)

        # 系统字体列表（推荐字体置顶）
        families = self._build_font_family_list()

        # RTT 显示字体：EditableComboBox（family） + SpinBox（size）
        # 改用内嵌选择而非 QFontDialog——后者在 fluent 主题下会因 "MS Sans Serif"
        # DirectWrite 失败、字号返回 -1 等问题卡死/污染配置
        rtt_font_row = QHBoxLayout()
        rtt_font_row.addWidget(BodyLabel("RTT 显示字体"), 1)
        self.cb_rtt_font = EditableComboBox(self)
        self.cb_rtt_font.addItems(families)
        self.cb_rtt_font.setMinimumWidth(220)
        self.cb_rtt_font.setCurrentText(self._cfg.get("font_family") or "Consolas")
        # 自动补全：不区分大小写、子串匹配（输入"yahei"能找到"Microsoft YaHei"）
        completer_rtt = QCompleter(families, self)
        completer_rtt.setCaseSensitivity(Qt.CaseInsensitive)
        completer_rtt.setFilterMode(Qt.MatchContains)
        self.cb_rtt_font.setCompleter(completer_rtt)
        self.cb_rtt_font.currentTextChanged.connect(self._on_rtt_family_changed)
        rtt_font_row.addWidget(self.cb_rtt_font)
        self.sp_font_size = SpinBox(self)
        self.sp_font_size.setRange(8, 32)
        self.sp_font_size.setValue(max(8, int(self._cfg.get("font_size") or 13)))
        self.sp_font_size.setSuffix(" pt")
        self.sp_font_size.valueChanged.connect(self._on_font_size_changed)
        rtt_font_row.addWidget(self.sp_font_size)
        wrap2 = QWidget(self)
        wrap2.setLayout(rtt_font_row)
        app_lay.addWidget(wrap2)

        # UI 界面字体：EditableComboBox + SpinBox + 恢复默认
        ui_font_row = QHBoxLayout()
        ui_font_row.addWidget(BodyLabel("UI 界面字体"), 1)
        self.cb_ui_font = EditableComboBox(self)
        self.cb_ui_font.addItems(["（系统默认）"] + families)
        self.cb_ui_font.setMinimumWidth(220)
        cur_ui_family = self._cfg.get("ui_font_family")
        self.cb_ui_font.setCurrentText(cur_ui_family if cur_ui_family else "（系统默认）")
        # 自动补全（只补全 families，不含"系统默认"占位符）
        completer_ui = QCompleter(families, self)
        completer_ui.setCaseSensitivity(Qt.CaseInsensitive)
        completer_ui.setFilterMode(Qt.MatchContains)
        self.cb_ui_font.setCompleter(completer_ui)
        self.cb_ui_font.currentTextChanged.connect(self._on_ui_family_changed)
        ui_font_row.addWidget(self.cb_ui_font)
        self.sp_ui_font_size = SpinBox(self)
        self.sp_ui_font_size.setRange(8, 24)
        self.sp_ui_font_size.setValue(max(8, int(self._cfg.get("ui_font_size") or 9)))
        self.sp_ui_font_size.setSuffix(" pt")
        self.sp_ui_font_size.valueChanged.connect(self._on_ui_font_size_changed)
        ui_font_row.addWidget(self.sp_ui_font_size)
        self.btn_ui_font_reset = PushButton("恢复默认", self)
        self.btn_ui_font_reset.clicked.connect(self._on_reset_ui_font)
        ui_font_row.addWidget(self.btn_ui_font_reset)
        wrap_ui = QWidget(self)
        wrap_ui.setLayout(ui_font_row)
        app_lay.addWidget(wrap_ui)

        root.addWidget(appearance)

        # ---- RTT 行为 ----
        rtt_card = CardWidget(self)
        rtt_lay = QVBoxLayout(rtt_card)
        rtt_lay.addWidget(SubtitleLabel("RTT 行为"))

        self.sp_max_lines = SpinBox(self)
        self.sp_max_lines.setRange(1000, 100000)
        self.sp_max_lines.setSingleStep(1000)
        self.sp_max_lines.setValue(self._cfg.get("max_display_lines"))
        self.sp_max_lines.valueChanged.connect(lambda v: self._cfg.set("max_display_lines", v))
        rtt_lay.addWidget(_SettingRow("显示区最大行数", self.sp_max_lines))

        self.sp_poll = SpinBox(self)
        self.sp_poll.setRange(5, 1000)   # 5ms - 1s
        self.sp_poll.setSuffix(" ms")
        self.sp_poll.setValue(max(20, self._cfg.get("rtt_poll_interval_ms") or 100))
        self.sp_poll.valueChanged.connect(lambda v: self._cfg.set("rtt_poll_interval_ms", v))
        rtt_lay.addWidget(_SettingRow("RTT 轮询间隔", self.sp_poll))

        # RTT 解码编码：默认 utf-8，可切换 gbk/utf-16-le/latin-1/ascii
        self.cb_encoding = ComboBox(self)
        self.cb_encoding.addItems(["utf-8", "gbk", "utf-16-le", "latin-1", "ascii"])
        cur_enc = (self._cfg.get("rtt_encoding") or "utf-8").lower()
        if self.cb_encoding.findText(cur_enc) < 0:
            self.cb_encoding.addItem(cur_enc)
        self.cb_encoding.setCurrentText(cur_enc)
        self.cb_encoding.currentTextChanged.connect(self._on_encoding_changed)
        rtt_lay.addWidget(_SettingRow("RTT 解码编码", self.cb_encoding))

        log_row = QHBoxLayout()
        log_row.addWidget(BodyLabel("日志保存目录"), 1)
        self.lbl_log_dir = QLabel(self._cfg.get("log_dir") or str(get_log_dir()))
        log_row.addWidget(self.lbl_log_dir)
        self.btn_log_dir = PushButton("选择…", self)
        self.btn_log_dir.clicked.connect(self._on_pick_log_dir)
        log_row.addWidget(self.btn_log_dir)
        self.btn_open_log = PushButton("打开日志目录", self)
        self.btn_open_log.clicked.connect(self._on_open_log_dir)
        log_row.addWidget(self.btn_open_log)
        wrap3 = QWidget(self)
        wrap3.setLayout(log_row)
        rtt_lay.addWidget(wrap3)

        root.addWidget(rtt_card)

        # ---- 标记与重置 ----
        mark_card = CardWidget(self)
        mark_lay = QVBoxLayout(mark_card)
        mark_lay.addWidget(SubtitleLabel("标记与重置"))

        # 标记颜色（用户插入标记 + 自动标记都用）
        mark_color_row = QHBoxLayout()
        mark_color_row.addWidget(BodyLabel("标记颜色"), 1)
        self.lbl_mark_color = QLabel(self._cfg.get("mark_color"))
        self.lbl_mark_color.setStyleSheet(
            f"background: {self._cfg.get('mark_color')}; padding: 2px 8px; color: #222; border-radius: 4px;"
        )
        mark_color_row.addWidget(self.lbl_mark_color)
        self.btn_mark_color = PushButton("选择…", self)
        self.btn_mark_color.clicked.connect(self._on_pick_mark_color)
        mark_color_row.addWidget(self.btn_mark_color)
        wrap_mc = QWidget(self)
        wrap_mc.setLayout(mark_color_row)
        mark_lay.addWidget(wrap_mc)

        # 自动标记开关
        self.chk_auto_mark_connect = CheckBox("连接时自动插入标记")
        self.chk_auto_mark_connect.setChecked(self._cfg.get("auto_mark_on_connect"))
        self.chk_auto_mark_connect.toggled.connect(
            lambda v: self._cfg.set("auto_mark_on_connect", v)
        )
        mark_lay.addWidget(self.chk_auto_mark_connect)

        self.chk_auto_mark_disconnect = CheckBox("断开时自动插入标记")
        self.chk_auto_mark_disconnect.setChecked(self._cfg.get("auto_mark_on_disconnect"))
        self.chk_auto_mark_disconnect.toggled.connect(
            lambda v: self._cfg.set("auto_mark_on_disconnect", v)
        )
        mark_lay.addWidget(self.chk_auto_mark_disconnect)

        # 重置模式：数据驱动 — 加 / 调序模式只动 _RESET_MODE_LABELS 一处
        self.cb_reset_mode = ComboBox(self)
        for _, label in _RESET_MODE_LABELS:
            self.cb_reset_mode.addItem(label)
        cur_mode = self._cfg.get("reset_mode")
        cur_idx = next((i for i, (m, _) in enumerate(_RESET_MODE_LABELS) if m == cur_mode), 0)
        self.cb_reset_mode.setCurrentIndex(cur_idx)
        self.cb_reset_mode.currentIndexChanged.connect(
            lambda i: self._cfg.set("reset_mode", _RESET_MODE_LABELS[i][0])
        )
        mark_lay.addWidget(_SettingRow("重置按钮行为", self.cb_reset_mode))

        root.addWidget(mark_card)
        root.addStretch(1)

    def _on_theme_changed(self, idx: int) -> None:
        mapping = ["auto", "light", "dark"]
        theme_str = mapping[idx]
        self._cfg.set("theme", theme_str)
        if theme_str == "dark":
            setTheme(Theme.DARK)
        elif theme_str == "light":
            setTheme(Theme.LIGHT)
        else:
            setTheme(Theme.AUTO)

    def _on_pick_color(self) -> None:
        from qfluentwidgets import ColorDialog
        cur = QColor(self._cfg.get("theme_color"))
        dlg = ColorDialog(cur, "选择主题色", self, enableAlpha=False)
        dlg.colorChanged.connect(self._apply_color)
        dlg.exec()

    def _apply_color(self, color: QColor) -> None:
        hex_str = color.name()
        self._cfg.set("theme_color", hex_str)
        setThemeColor(hex_str)
        self.lbl_color.setText(hex_str)
        self.lbl_color.setStyleSheet(
            f"background: {hex_str}; padding: 2px 8px; color: white; border-radius: 4px;"
        )

    def _on_pick_mark_color(self) -> None:
        from qfluentwidgets import ColorDialog
        cur = QColor(self._cfg.get("mark_color"))
        dlg = ColorDialog(cur, "选择标记颜色", self, enableAlpha=False)
        dlg.colorChanged.connect(self._apply_mark_color)
        dlg.exec()

    def _apply_mark_color(self, color: QColor) -> None:
        hex_str = color.name()
        self._cfg.set("mark_color", hex_str)
        self.lbl_mark_color.setText(hex_str)
        # 文字深色（标记颜色通常很亮，深字看得清）
        self.lbl_mark_color.setStyleSheet(
            f"background: {hex_str}; padding: 2px 8px; color: #222; border-radius: 4px;"
        )

    @staticmethod
    def _build_font_family_list() -> list[str]:
        """系统字体列表，常用编程/中文字体置顶。"""
        all_families = sorted({f for f in QFontDatabase.families() if f and not f.startswith("@")})
        preferred = [
            "Consolas", "Cascadia Code", "Cascadia Mono", "JetBrains Mono",
            "Source Code Pro", "Fira Code", "Courier New",
            "Microsoft YaHei", "Microsoft YaHei UI", "Source Han Sans CN",
            "Noto Sans CJK SC", "Segoe UI", "Arial", "Times New Roman",
        ]
        head = [f for f in preferred if f in all_families]
        tail = [f for f in all_families if f not in head]
        return head + tail

    def _on_rtt_family_changed(self, family: str) -> None:
        family = (family or "").strip()
        if not family:
            return
        self._cfg.set("font_family", family)

    def _on_font_size_changed(self, v: int) -> None:
        if v <= 0:
            return
        self._cfg.set("font_size", v)

    def _on_ui_family_changed(self, family: str) -> None:
        family = (family or "").strip()
        if family in ("", "（系统默认）"):
            self._cfg.set("ui_font_family", "")
        else:
            self._cfg.set("ui_font_family", family)

    def _on_ui_font_size_changed(self, v: int) -> None:
        if v <= 0:
            return
        self._cfg.set("ui_font_size", v)

    def _on_encoding_changed(self, enc: str) -> None:
        enc = (enc or "utf-8").strip().lower()
        if not enc:
            return
        self._cfg.set("rtt_encoding", enc)
        _infobar.ok(self, "已切换 RTT 编码", f"新编码：{enc}（立即生效）")

    def _on_reset_ui_font(self) -> None:
        self._cfg.set("ui_font_family", "")
        self._cfg.set("ui_font_size", 0)
        # 同步控件显示（block signals 避免再次触发 cfg.set）
        self.cb_ui_font.blockSignals(True)
        self.cb_ui_font.setCurrentText("（系统默认）")
        self.cb_ui_font.blockSignals(False)
        self.sp_ui_font_size.blockSignals(True)
        self.sp_ui_font_size.setValue(9)
        self.sp_ui_font_size.blockSignals(False)
        _infobar.ok(self, "已恢复默认", "UI 界面字体已重置")

    def _on_pick_log_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择日志目录", self.lbl_log_dir.text())
        if path:
            self._cfg.set("log_dir", path)
            self.lbl_log_dir.setText(path)

    def _on_open_log_dir(self) -> None:
        path = self._cfg.get("log_dir") or str(get_log_dir())
        Path(path).mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            _infobar.err(self, "打开失败", str(e))
