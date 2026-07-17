"""设置页：外观（主题/字体/语言）+ RTT 行为（最大行数/Rx Timeout/日志目录）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
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
from core.i18n_service import lang_display_name, supported_langs
from core.jlink_worker import RESET_MODE_AUTO_RECONNECT, RESET_MODE_NORMAL
from core.logger import get_log_dir


_RESET_MODE_LABELS = [
    (RESET_MODE_NORMAL, "正常（仅重置目标 MCU）"),
    (RESET_MODE_AUTO_RECONNECT, "自动重连（断开+重连）"),
]


# --- RTT 解码编码显示名映射 ---
# 内部存储使用小写标准名（如 utf-8, latin-1），UI 显示使用规范化名称
_ENCODING_DISPLAY: dict[str, str] = {
    "utf-8": "UTF-8",
    "gbk": "GBK",
    "utf-16-le": "UTF-16-LE",
    "latin-1": "Latin-1",
    "ascii": "ASCII",
}
# 反向映射：显示名 → 内部名
_ENCODING_FROM_DISPLAY: dict[str, str] = {v: k for k, v in _ENCODING_DISPLAY.items()}
# ComboBox 显示列表（保持顺序）
_ENCODING_DISPLAY_NAMES: list[str] = [
    "UTF-8", "GBK", "UTF-16-LE", "Latin-1", "ASCII",
]

# --- 换行符选项 ---
_SEND_LINE_ENDING_DISPLAY: dict[str, str] = {
    "\r\n": "CRLF (\\r\\n)",
    "\n": "LF (\\n)",
    "\r": "CR (\\r)",
}
_SEND_LINE_ENDING_FROM_DISPLAY: dict[str, str] = {v: k for k, v in _SEND_LINE_ENDING_DISPLAY.items()}
_SEND_LINE_ENDING_NAMES: list[str] = ["CRLF (\\r\\n)", "LF (\\n)", "CR (\\r)"]


class _SettingRow(QWidget):
    """通用：左标题 + 右控件 的一行。支持 i18n 重翻译。"""

    def __init__(self, title: str, widget: QWidget, parent=None):
        super().__init__(parent)
        self._title_key = title
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        self._label = BodyLabel(self.tr(title))
        lay.addWidget(self._label, 1, Qt.AlignVCenter)
        lay.addWidget(widget, 0, Qt.AlignVCenter)

    def retranslate(self, tr_func) -> None:
        self._label.setText(tr_func(self._title_key))


class SettingsPage(QWidget):
    def __init__(self, cfg: ConfigService, parent=None):
        super().__init__(parent)
        self.setObjectName("settings")
        self._cfg = cfg
        self._setting_rows: list[_SettingRow] = []
        self._build_ui()

    def _build_ui(self) -> None:
        # 整页透明 ScrollArea —— 窗口压扁时纵向滚，控件不再被挤压重叠。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll, inner = make_transparent_scroll(self, "settings")
        outer.addWidget(self._scroll)

        root = QVBoxLayout(inner)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # ---- 外观 ----
        self._appearance_card = CardWidget(self)
        app_lay = QVBoxLayout(self._appearance_card)
        self._lbl_appearance = SubtitleLabel(self.tr("外观"))
        app_lay.addWidget(self._lbl_appearance)

        # 主题模式
        self.cb_theme = ComboBox(self)
        self._theme_labels = [self.tr("跟随系统"), self.tr("浅色"), self.tr("深色")]
        self.cb_theme.addItems(self._theme_labels)
        theme_str = self._cfg.get("theme")
        self.cb_theme.setCurrentIndex({"auto": 0, "light": 1, "dark": 2}.get(theme_str, 0))
        self.cb_theme.currentIndexChanged.connect(self._on_theme_changed)
        row_theme = _SettingRow("主题模式", self.cb_theme)
        self._setting_rows.append(row_theme)
        app_lay.addWidget(row_theme)

        # 语言
        self.cb_language = ComboBox(self)
        self._lang_codes = supported_langs()
        self._lang_labels = [lang_display_name(code) for code in self._lang_codes]
        self.cb_language.addItems(self._lang_labels)
        cur_lang = self._cfg.get("language") or "zh_CN"
        cur_lang_idx = self._lang_codes.index(cur_lang) if cur_lang in self._lang_codes else 0
        self.cb_language.setCurrentIndex(cur_lang_idx)
        self.cb_language.currentIndexChanged.connect(self._on_language_changed)
        row_lang = _SettingRow("语言", self.cb_language)
        self._setting_rows.append(row_lang)
        app_lay.addWidget(row_lang)

        # 主题色
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 4, 0, 4)
        self._lbl_theme_color = BodyLabel(self.tr("主题色"))
        color_row.addWidget(self._lbl_theme_color, 1)
        self.lbl_color = QLabel(self._cfg.get("theme_color"))
        self.lbl_color.setStyleSheet(
            f"background: {self._cfg.get('theme_color')}; padding: 2px 8px; color: white; border-radius: 4px;"
        )
        color_row.addWidget(self.lbl_color)
        self.btn_color = PushButton(self.tr("选择…"), self)
        self.btn_color.clicked.connect(self._on_pick_color)
        color_row.addWidget(self.btn_color)
        wrap = QWidget(self)
        wrap.setLayout(color_row)
        app_lay.addWidget(wrap)

        # 系统字体列表（推荐字体置顶）
        families = self._build_font_family_list()

        # RTT 显示字体：EditableComboBox（family） + SpinBox（size）
        rtt_font_row = QHBoxLayout()
        rtt_font_row.setContentsMargins(0, 4, 0, 4)
        self._lbl_rtt_font = BodyLabel(self.tr("RTT 显示字体"))
        rtt_font_row.addWidget(self._lbl_rtt_font, 1)
        self.cb_rtt_font = EditableComboBox(self)
        self.cb_rtt_font.addItems(families)
        self.cb_rtt_font.setMinimumWidth(220)
        self.cb_rtt_font.setCurrentText(self._cfg.get("font_family") or "Consolas")
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

        root.addWidget(self._appearance_card)

        # ---- RTT 行为 ----
        self._rtt_card = CardWidget(self)
        rtt_lay = QVBoxLayout(self._rtt_card)
        self._lbl_rtt_behavior = SubtitleLabel(self.tr("RTT 行为"))
        rtt_lay.addWidget(self._lbl_rtt_behavior)

        self.sp_max_lines = SpinBox(self)
        self.sp_max_lines.setRange(1000, 100000)
        self.sp_max_lines.setSingleStep(1000)
        self.sp_max_lines.setValue(self._cfg.get("max_display_lines"))
        self.sp_max_lines.valueChanged.connect(lambda v: self._cfg.set("max_display_lines", v))
        row_max = _SettingRow("显示区最大行数", self.sp_max_lines)
        self._setting_rows.append(row_max)
        rtt_lay.addWidget(row_max)

        # 每通道历史缓存：切通道时各通道各自的历史能完整复现的上限（字符数）
        self.sp_channel_hist = SpinBox(self)
        self.sp_channel_hist.setRange(10, 5000)   # × 1000 字符 = 10k ~ 5M
        self.sp_channel_hist.setSingleStep(50)
        self.sp_channel_hist.setSuffix(" k字符")
        self.sp_channel_hist.setValue(
            max(10, int(self._cfg.get("rtt_channel_history_chars") or 200000) // 1000))
        self.sp_channel_hist.valueChanged.connect(
            lambda v: self._cfg.set("rtt_channel_history_chars", v * 1000))
        row_ch_hist = _SettingRow("每通道历史缓存", self.sp_channel_hist)
        self._setting_rows.append(row_ch_hist)
        rtt_lay.addWidget(row_ch_hist)

        self.sp_poll = SpinBox(self)
        self.sp_poll.setRange(5, 1000)   # 5ms - 1s
        self.sp_poll.setSuffix(" ms")
        self.sp_poll.setValue(max(20, self._cfg.get("rtt_poll_interval_ms") or 100))
        self.sp_poll.valueChanged.connect(lambda v: self._cfg.set("rtt_poll_interval_ms", v))
        row_poll = _SettingRow("RTT 轮询间隔", self.sp_poll)
        self._setting_rows.append(row_poll)
        rtt_lay.addWidget(row_poll)

        # RTT 解码编码：默认 utf-8，可切换 gbk/utf-16-le/latin-1/ascii
        self.cb_encoding = ComboBox(self)
        self.cb_encoding.addItems(_ENCODING_DISPLAY_NAMES)
        cur_key: str = (self._cfg.get("rtt_encoding") or "utf-8").strip().lower()
        cur_display: str = _ENCODING_DISPLAY.get(cur_key, _ENCODING_DISPLAY["utf-8"])
        self.cb_encoding.setCurrentText(cur_display)
        self.cb_encoding.currentTextChanged.connect(self._on_encoding_changed)
        row_enc = _SettingRow("RTT 解码编码", self.cb_encoding)
        self._setting_rows.append(row_enc)
        rtt_lay.addWidget(row_enc)

        # 换行符：CRLF / LF / CR
        self.cb_line_ending = ComboBox(self)
        self.cb_line_ending.addItems(_SEND_LINE_ENDING_NAMES)
        cur_le: str = self._cfg.get("send_line_ending") or "\r\n"
        cur_le_display: str = _SEND_LINE_ENDING_DISPLAY.get(cur_le, _SEND_LINE_ENDING_DISPLAY["\r\n"])
        self.cb_line_ending.setCurrentText(cur_le_display)
        self.cb_line_ending.currentTextChanged.connect(self._on_line_ending_changed)
        row_le = _SettingRow("换行符", self.cb_line_ending)
        self._setting_rows.append(row_le)
        rtt_lay.addWidget(row_le)
        # 保持屏幕常亮：勾选后调用系统 API 防止屏幕息屏
        self.chk_keep_screen_on = CheckBox()
        self.chk_keep_screen_on.setChecked(bool(self._cfg.get("keep_screen_on")))
        self.chk_keep_screen_on.toggled.connect(self._on_keep_screen_on_toggled)
        row_screen = _SettingRow("保持屏幕常亮", self.chk_keep_screen_on)
        self._setting_rows.append(row_screen)
        rtt_lay.addWidget(row_screen)

        log_row = QHBoxLayout()
        log_row.setContentsMargins(0, 4, 0, 4)
        self._lbl_log_dir = BodyLabel(self.tr("日志保存目录"))
        log_row.addWidget(self._lbl_log_dir, 1)
        self.lbl_log_dir = QLabel(self._cfg.get("log_dir") or str(get_log_dir()))
        log_row.addWidget(self.lbl_log_dir)
        self.btn_log_dir = PushButton(self.tr("选择…"), self)
        self.btn_log_dir.clicked.connect(self._on_pick_log_dir)
        log_row.addWidget(self.btn_log_dir)
        self.btn_open_log = PushButton(self.tr("打开日志目录"), self)
        self.btn_open_log.clicked.connect(self._on_open_log_dir)
        log_row.addWidget(self.btn_open_log)
        wrap3 = QWidget(self)
        wrap3.setLayout(log_row)
        rtt_lay.addWidget(wrap3)

        root.addWidget(self._rtt_card)

        # ---- 标记与重置 ----
        self._mark_card = CardWidget(self)
        mark_lay = QVBoxLayout(self._mark_card)
        self._lbl_mark_reset = SubtitleLabel(self.tr("标记与重置"))
        mark_lay.addWidget(self._lbl_mark_reset)

        # 标记颜色（用户插入标记 + 自动标记都用）
        mark_color_row = QHBoxLayout()
        mark_color_row.setContentsMargins(0, 4, 0, 4)
        self._lbl_mark_color = BodyLabel(self.tr("标记颜色"))
        mark_color_row.addWidget(self._lbl_mark_color, 1)
        self.lbl_mark_color = QLabel(self._cfg.get("mark_color"))
        self.lbl_mark_color.setStyleSheet(
            f"background: {self._cfg.get('mark_color')}; padding: 2px 8px; color: #222; border-radius: 4px;"
        )
        mark_color_row.addWidget(self.lbl_mark_color)
        self.btn_mark_color = PushButton(self.tr("选择…"), self)
        self.btn_mark_color.clicked.connect(self._on_pick_mark_color)
        mark_color_row.addWidget(self.btn_mark_color)
        wrap_mc = QWidget(self)
        wrap_mc.setLayout(mark_color_row)
        mark_lay.addWidget(wrap_mc)

        # 自动标记开关
        self.chk_auto_mark_connect = CheckBox(self.tr("连接时自动插入标记"))
        self.chk_auto_mark_connect.setChecked(self._cfg.get("auto_mark_on_connect"))
        self.chk_auto_mark_connect.toggled.connect(
            lambda v: self._cfg.set("auto_mark_on_connect", v)
        )
        mark_lay.addWidget(self.chk_auto_mark_connect)

        self.chk_auto_mark_disconnect = CheckBox(self.tr("断开时自动插入标记"))
        self.chk_auto_mark_disconnect.setChecked(self._cfg.get("auto_mark_on_disconnect"))
        self.chk_auto_mark_disconnect.toggled.connect(
            lambda v: self._cfg.set("auto_mark_on_disconnect", v)
        )
        mark_lay.addWidget(self.chk_auto_mark_disconnect)

        # 重置模式：数据驱动
        self.cb_reset_mode = ComboBox(self)
        self._reset_mode_labels_tr = [label for _, label in _RESET_MODE_LABELS]
        for label in self._reset_mode_labels_tr:
            self.cb_reset_mode.addItem(self.tr(label))
        cur_mode = self._cfg.get("reset_mode")
        cur_idx = next((i for i, (m, _) in enumerate(_RESET_MODE_LABELS) if m == cur_mode), 0)
        self.cb_reset_mode.setCurrentIndex(cur_idx)
        self.cb_reset_mode.currentIndexChanged.connect(
            lambda i: self._cfg.set("reset_mode", _RESET_MODE_LABELS[i][0])
        )
        row_reset = _SettingRow("重置按钮行为", self.cb_reset_mode)
        self._setting_rows.append(row_reset)
        mark_lay.addWidget(row_reset)

        root.addWidget(self._mark_card)
        root.addStretch(1)

    # ------------------------------------------------------------------
    # i18n 重翻译
    # ------------------------------------------------------------------
    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()
            super().changeEvent(event)
        else:
            super().changeEvent(event)

    def _retranslate_ui(self) -> None:
        """语言切换后重新设置所有可见文本。"""
        self._lbl_appearance.setText(self.tr("外观"))
        self._lbl_rtt_behavior.setText(self.tr("RTT 行为"))
        self._lbl_mark_reset.setText(self.tr("标记与重置"))
        self._lbl_theme_color.setText(self.tr("主题色"))
        self._lbl_rtt_font.setText(self.tr("RTT 显示字体"))
        self._lbl_log_dir.setText(self.tr("日志保存目录"))
        self._lbl_mark_color.setText(self.tr("标记颜色"))
        self.btn_color.setText(self.tr("选择…"))
        self.btn_log_dir.setText(self.tr("选择…"))
        self.btn_open_log.setText(self.tr("打开日志目录"))
        self.btn_mark_color.setText(self.tr("选择…"))
        self.chk_auto_mark_connect.setText(self.tr("连接时自动插入标记"))
        self.chk_auto_mark_disconnect.setText(self.tr("断开时自动插入标记"))

        # 主题模式 ComboBox：保持选中索引不变，仅刷新文字
        idx = self.cb_theme.currentIndex()
        self._theme_labels = [self.tr("跟随系统"), self.tr("浅色"), self.tr("深色")]
        self.cb_theme.blockSignals(True)
        self.cb_theme.clear()
        self.cb_theme.addItems(self._theme_labels)
        self.cb_theme.setCurrentIndex(idx)
        self.cb_theme.blockSignals(False)

        # 重置模式 ComboBox
        ridx = self.cb_reset_mode.currentIndex()
        self.cb_reset_mode.blockSignals(True)
        self.cb_reset_mode.clear()
        for label in _RESET_MODE_LABELS:
            self.cb_reset_mode.addItem(self.tr(label[1]))
        self.cb_reset_mode.setCurrentIndex(ridx)
        self.cb_reset_mode.blockSignals(False)

        # 所有 _SettingRow 标题
        for row in self._setting_rows:
            row.retranslate(self.tr)

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _on_language_changed(self, idx: int) -> None:
        """语言下拉切换：写入 config → language_changed 信号 → switch_language。"""
        if 0 <= idx < len(self._lang_codes):
            self._cfg.set("language", self._lang_codes[idx])

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
        dlg = ColorDialog(cur, self.tr("选择主题色"), self, enableAlpha=False)
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
        dlg = ColorDialog(cur, self.tr("选择标记颜色"), self, enableAlpha=False)
        dlg.colorChanged.connect(self._apply_mark_color)
        dlg.exec()

    def _apply_mark_color(self, color: QColor) -> None:
        hex_str = color.name()
        self._cfg.set("mark_color", hex_str)
        self.lbl_mark_color.setText(hex_str)
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

    def _on_encoding_changed(self, display_name: str) -> None:
        """编码下拉切换：从显示名映射到内部存储的小写标准名。"""
        key: str = _ENCODING_FROM_DISPLAY.get(display_name, "utf-8")
        self._cfg.set("rtt_encoding", key)
        _infobar.ok(self, self.tr("已切换 RTT 编码"), self.tr("新编码：") + f"{display_name}" + self.tr("（立即生效）"))

    def _on_line_ending_changed(self, display_name: str) -> None:
        """换行符下拉切换：从显示名映射到内部值。"""
        value: str = _SEND_LINE_ENDING_FROM_DISPLAY.get(display_name, "\r\n")
        self._cfg.set("send_line_ending", value)

    def _on_keep_screen_on_toggled(self, checked: bool) -> None:
        self._cfg.set("keep_screen_on", checked)
        from core.screen_keeper import apply_keep_screen_on
        apply_keep_screen_on(checked)

    def _on_pick_log_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr("选择日志目录"), self.lbl_log_dir.text())
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
            _infobar.err(self, self.tr("打开失败"), str(e))
