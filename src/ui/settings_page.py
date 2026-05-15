"""设置页：外观（主题/字体）+ RTT 行为（最大行数/Rx Timeout/日志目录）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    PushButton,
    SpinBox,
    SubtitleLabel,
    Theme,
    setTheme,
    setThemeColor,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFontDialog

from core.config_service import ConfigService
from core.logger import get_log_dir


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
        root = QVBoxLayout(self)
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

        # 显示字体
        font_row = QHBoxLayout()
        font_row.addWidget(BodyLabel("显示字体"), 1)
        self.lbl_font = QLabel(f"{self._cfg.get('font_family')} {self._cfg.get('font_size')}pt")
        font_row.addWidget(self.lbl_font)
        self.btn_font = PushButton("选择…", self)
        self.btn_font.clicked.connect(self._on_pick_font)
        font_row.addWidget(self.btn_font)
        wrap2 = QWidget(self)
        wrap2.setLayout(font_row)
        app_lay.addWidget(wrap2)

        # 字体大小（仍提供 SpinBox 快速调整）
        self.sp_font_size = SpinBox(self)
        self.sp_font_size.setRange(8, 32)
        self.sp_font_size.setValue(self._cfg.get("font_size"))
        self.sp_font_size.valueChanged.connect(self._on_font_size_changed)
        app_lay.addWidget(_SettingRow("字体大小", self.sp_font_size))

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

        self.sp_rx_to = SpinBox(self)
        self.sp_rx_to.setRange(0, 5000)
        self.sp_rx_to.setSuffix(" ms")
        self.sp_rx_to.setValue(self._cfg.get("rx_timeout_ms"))
        self.sp_rx_to.valueChanged.connect(lambda v: self._cfg.set("rx_timeout_ms", v))
        rtt_lay.addWidget(_SettingRow("Rx Timeout", self.sp_rx_to))

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

    def _on_pick_font(self) -> None:
        cur = QFont(self._cfg.get("font_family"), self._cfg.get("font_size"))
        ok, font = QFontDialog.getFont(cur, self, "选择字体")
        if not ok:
            return
        self._cfg.set("font_family", font.family())
        self._cfg.set("font_size", font.pointSize())
        self.sp_font_size.setValue(font.pointSize())
        self.lbl_font.setText(f"{font.family()} {font.pointSize()}pt")

    def _on_font_size_changed(self, v: int) -> None:
        self._cfg.set("font_size", v)
        self.lbl_font.setText(f"{self._cfg.get('font_family')} {v}pt")

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
            InfoBar.error("打开失败", str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
