"""搜索/替换浮动工具栏 —— QFluentWidgets 风格，对齐 VSCode 交互。

Ctrl+F → 打开/聚焦查找（收起替换行）
Ctrl+H → 打开并展开替换行
Esc    → 关闭

组件以浮动方式叠加在父容器右上角，不占用布局流。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon,
    LineEdit,
    ToolTipFilter,
    TransparentToolButton,
    isDarkTheme,
)


def _tip(widget: QWidget, text: str, duration: int = 300) -> None:
    """给控件安装 QFluentWidgets 风格的 tooltip（圆角 + 阴影）。"""
    widget.setToolTip(text)
    widget.installEventFilter(ToolTipFilter(widget, duration))


# ---------------------------------------------------------------------------
# 内联 SVG 图标（VSCode 风格 replace / replace-all）
# ---------------------------------------------------------------------------
_REPLACE_ONE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
    'viewBox="0 0 16 16"><path fill="{c}" d="M11.5 1H5.7L1 5.7v.6l5 5h.6'
    'l4.9-5zM6 10.2L2.1 6.3 6 2.4v7.8zM14 11H9.2l-.7.7L9.2 12.4H14V11z"/>'
    '</svg>'
)
_REPLACE_ALL_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
    'viewBox="0 0 16 16"><path fill="{c}" d="M11.5 1H5.7L1 5.7v.6l5 5h.6'
    'l4.9-5zM6 10.2L2.1 6.3 6 2.4v7.8zM7 15h2v-2H7v2zm4 0h2v-2h-2v2zm-8'
    ' 0h2v-2H3v2z"/></svg>'
)


def _svg_icon(svg_template: str, size: int = 16) -> QIcon:
    """从 SVG 模板创建 QIcon，自动跟随深浅色主题。"""
    color = "#c5c5c5" if isDarkTheme() else "#424242"
    svg = svg_template.replace("{c}", color)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    pm.loadFromData(svg.encode("utf-8"), "SVG")
    return QIcon(pm)


# ---------------------------------------------------------------------------
# 可切换的 Toggle 按钮（Aa / ab / .*）
# ---------------------------------------------------------------------------
class _ToggleButton(TransparentToolButton):
    """带文本的 toggle 按钮。QFluentWidgets 的 ToolButton 默认不渲染
    setText 文字（只画 icon），所以这里自绘文字 + checkable 高亮。"""

    def __init__(self, text: str, tip: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._text = text
        _tip(self, tip)
        self.setFixedSize(28, 24)
        self.setCheckable(True)

    def paintEvent(self, e) -> None:
        from PySide6.QtGui import QPainter, QColor, QFont
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # checked 时画高亮背景
        if self.isChecked():
            bg = QColor(0, 120, 212, 50) if not isDarkTheme() else QColor(0, 120, 212, 80)
            p.setBrush(bg)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(self.rect(), 4, 4)
        # 画文字
        fg = QColor("#1a1a1a") if not isDarkTheme() else QColor("#e0e0e0")
        if self.isChecked():
            fg = QColor(0, 120, 212) if not isDarkTheme() else QColor(80, 180, 255)
        p.setPen(fg)
        font = QFont()
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignCenter, self._text)


class SearchBar(QWidget):
    """浮动搜索/替换栏，对齐 VSCode 行为。

    Signals
    -------
    search_requested(text, backward, case_sensitive, whole_word, regex)
    replace_requested(text, replacement, replace_all, case_sensitive, whole_word, regex)
    options_changed()
    closed()
    """

    search_requested = Signal(str, bool, bool, bool, bool)
    replace_requested = Signal(str, str, bool, bool, bool, bool)
    options_changed = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("searchBar")
        self._build_ui()
        self._wire_signals()
        self.setVisible(False)
        self._set_replace_visible(False)
        # 浮动层：不被父布局管理
        self.setParent(parent)
        self.raise_()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setFixedWidth(460)
        self.setAttribute(Qt.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(2)

        # ---- 搜索行 ----
        row1 = QHBoxLayout()
        row1.setSpacing(3)

        self.le_search = LineEdit(self)
        self.le_search.setPlaceholderText("查找…")
        self.le_search.setClearButtonEnabled(True)
        self.le_search.setFixedHeight(28)

        self.btn_case = _ToggleButton("Aa", "区分大小写", self)
        self.btn_word = _ToggleButton("ab", "全词匹配", self)
        self.btn_regex = _ToggleButton(".*", "正则表达式", self)

        self.lbl_match = QLabel("")
        self.lbl_match.setMinimumWidth(80)
        self.lbl_match.setAlignment(Qt.AlignCenter)

        self.btn_prev = TransparentToolButton(FluentIcon.UP, self)
        _tip(self.btn_prev, "上一个 (Shift+Enter)")
        self.btn_prev.setFixedSize(26, 24)

        self.btn_next = TransparentToolButton(FluentIcon.DOWN, self)
        _tip(self.btn_next, "下一个 (Enter)")
        self.btn_next.setFixedSize(26, 24)

        self.btn_toggle_replace = TransparentToolButton(FluentIcon.CHEVRON_DOWN_MED, self)
        self.btn_toggle_replace.setFixedSize(26, 24)
        _tip(self.btn_toggle_replace, "展开/收起替换 (Ctrl+H)")
        self.btn_toggle_replace.setCheckable(True)

        self.btn_close = TransparentToolButton(FluentIcon.CLOSE, self)
        self.btn_close.setFixedSize(26, 24)
        _tip(self.btn_close, "关闭 (Esc)")

        row1.addWidget(self.le_search, 1)
        row1.addWidget(self.btn_case)
        row1.addWidget(self.btn_word)
        row1.addWidget(self.btn_regex)
        row1.addWidget(self.lbl_match)
        row1.addWidget(self.btn_prev)
        row1.addWidget(self.btn_next)
        row1.addWidget(self.btn_toggle_replace)
        row1.addWidget(self.btn_close)
        outer.addLayout(row1)

        # ---- 替换行 ----
        self._row2 = QHBoxLayout()
        self._row2.setSpacing(3)

        self.le_replace = LineEdit(self)
        self.le_replace.setPlaceholderText("替换…")
        self.le_replace.setClearButtonEnabled(True)
        self.le_replace.setFixedHeight(28)

        self.btn_replace = TransparentToolButton(_svg_icon(_REPLACE_ONE_SVG), self)
        self.btn_replace.setFixedSize(26, 24)
        _tip(self.btn_replace, "替换 (Enter 在替换框中)")

        self.btn_replace_all = TransparentToolButton(_svg_icon(_REPLACE_ALL_SVG), self)
        self.btn_replace_all.setFixedSize(26, 24)
        _tip(self.btn_replace_all, "全部替换")

        self._row2.addWidget(self.le_replace, 1)
        self._row2.addWidget(self.btn_replace)
        self._row2.addWidget(self.btn_replace_all)
        outer.addLayout(self._row2)

        self._apply_style()

    def _wire_signals(self) -> None:
        self.le_search.returnPressed.connect(self._on_search_next)
        self.le_replace.returnPressed.connect(self._on_replace_one)
        self.btn_prev.clicked.connect(self._on_search_prev)
        self.btn_next.clicked.connect(self._on_search_next)
        self.btn_close.clicked.connect(self.close_bar)
        self.btn_toggle_replace.toggled.connect(self._on_toggle_replace)
        self.le_search.textChanged.connect(lambda _: self.options_changed.emit())
        self.btn_case.toggled.connect(lambda _: self.options_changed.emit())
        self.btn_word.toggled.connect(lambda _: self.options_changed.emit())
        self.btn_regex.toggled.connect(lambda _: self.options_changed.emit())
        self.btn_replace.clicked.connect(self._on_replace_one)
        self.btn_replace_all.clicked.connect(self._on_replace_all)

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------
    def show_search(self, initial_text: str = "") -> None:
        """Ctrl+F：显示查找框，**始终收起替换行**（对齐 VSCode）。

        initial_text: 非空时自动填入搜索框（用于选中文本场景）。
        """
        self._set_replace_visible(False)
        self.setVisible(True)
        if initial_text:
            self.le_search.setText(initial_text)
        self.le_search.setFocus()
        self.le_search.selectAll()
        self._reposition()

    def show_replace(self, initial_text: str = "") -> None:
        """Ctrl+H：显示查找框 + **展开替换行**。

        initial_text: 非空时自动填入搜索框（用于选中文本场景）。
        """
        self.setVisible(True)
        self._set_replace_visible(True)
        if initial_text:
            self.le_search.setText(initial_text)
        self.le_search.setFocus()
        self.le_search.selectAll()
        self._reposition()

    def close_bar(self) -> None:
        self.setVisible(False)
        self.closed.emit()

    def set_match_label(self, text: str) -> None:
        self.lbl_match.setText(text)

    def search_text(self) -> str:
        return self.le_search.text()

    def replace_text(self) -> str:
        return self.le_replace.text()

    def case_sensitive(self) -> bool:
        return self.btn_case.isChecked()

    def whole_word(self) -> bool:
        return self.btn_word.isChecked()

    def regex_enabled(self) -> bool:
        return self.btn_regex.isChecked()

    def is_replace_visible(self) -> bool:
        return self._replace_visible

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _on_search_next(self) -> None:
        t = self.le_search.text()
        if t:
            self.search_requested.emit(t, False, self.case_sensitive(),
                                       self.whole_word(), self.regex_enabled())

    def _on_search_prev(self) -> None:
        t = self.le_search.text()
        if t:
            self.search_requested.emit(t, True, self.case_sensitive(),
                                       self.whole_word(), self.regex_enabled())

    def _on_replace_one(self) -> None:
        kw = self.le_search.text()
        if kw:
            self.replace_requested.emit(
                kw, self.le_replace.text(), False,
                self.case_sensitive(), self.whole_word(), self.regex_enabled())

    def _on_replace_all(self) -> None:
        kw = self.le_search.text()
        if kw:
            self.replace_requested.emit(
                kw, self.le_replace.text(), True,
                self.case_sensitive(), self.whole_word(), self.regex_enabled())

    def _on_toggle_replace(self, checked: bool) -> None:
        self._set_replace_visible(checked)
        self._reposition()

    def _set_replace_visible(self, visible: bool) -> None:
        self._replace_visible = visible
        self.le_replace.setVisible(visible)
        self.btn_replace.setVisible(visible)
        self.btn_replace_all.setVisible(visible)
        self.btn_toggle_replace.blockSignals(True)
        self.btn_toggle_replace.setChecked(visible)
        self.btn_toggle_replace.blockSignals(False)
        self.btn_toggle_replace.setIcon(
            FluentIcon.UP if visible else FluentIcon.CHEVRON_DOWN_MED
        )
        if visible:
            self.le_replace.setFocus()

    def _reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        self.adjustSize()
        margin = 8
        x = p.width() - self.width() - margin
        y = margin
        self.move(max(0, x), max(0, y))

    def _apply_style(self) -> None:
        dark = isDarkTheme()
        if dark:
            bg = "rgba(37, 37, 38, 240)"
            border = "#3c3c3c"
            match_fg = "#999999"
        else:
            bg = "rgba(250, 250, 250, 245)"
            border = "#d0d0d0"
            match_fg = "#666666"

        self.setStyleSheet(f"""
            SearchBar {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            SearchBar QLabel {{
                color: {match_fg};
                font-size: 12px;
            }}
        """)

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Escape:
            self.close_bar()
            return
        super().keyPressEvent(e)

    def eventFilter(self, obj, event: QEvent) -> bool:
        if event.type() == QEvent.Resize:
            self._reposition()
        return super().eventFilter(obj, event)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        p = self.parentWidget()
        if p is not None:
            p.installEventFilter(self)
        self._reposition()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        p = self.parentWidget()
        if p is not None:
            p.removeEventFilter(self)
