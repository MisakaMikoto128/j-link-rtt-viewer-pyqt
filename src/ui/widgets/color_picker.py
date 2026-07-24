"""颜色选择组件：ComboBox 风格按钮 + 网格色盘弹窗。

ColorComboButton 模仿 ComboBox 外观（实心色块 + 下拉箭头），点击弹出
ColorGridPopup 网格色盘。用 QPushButton + 自绘，避免 QComboBox 的纵向
列表限制（无法直接做网格色盘）。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, isDarkTheme, themeColor

# 色盘弹窗预设色 -- 参照 Office 经典调色板
COLOR_GRID_PRESETS: list[str] = [
    "#FFFFFF",
    "#F2F2F2",
    "#E7E6E6",
    "#BFBFBF",
    "#A6A6A6",
    "#808080",
    "#C00000",
    "#FF0000",
    "#FFC000",
    "#FFFF00",
    "#92D050",
    "#00B050",
    "#00B0F0",
    "#0070C0",
    "#002060",
    "#7030A0",
    "#FF00FF",
    "#FF0066",
    "#FF6600",
    "#FFA500",
    "#FFD700",
    "#948A54",
    "#8B4513",
    "#A0522D",
    "#87CEEB",
    "#4682B4",
    "#2E8B57",
    "#228B22",
    "#808000",
    "#556B2F",
]


class ColorComboButton(QPushButton):
    """模仿 ComboBox 外观的颜色按钮：内部实心色块 + 右侧下拉箭头 ▼。

    点击弹出 ColorGridPopup 网格色盘。外观像标准 ComboBox 但内容是纯色矩形。
    用 QPushButton + 自绘，避免 QComboBox 的弹出列表限制（ComboBox 只能显示
    纵向列表，无法直接做网格色盘）。
    """

    colorChanged = Signal(str)  # hex 色值字符串

    def __init__(self, color_hex: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color_hex)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(84, 30)
        self.clicked.connect(self._show_popup)

    def set_color(self, color_hex: str) -> None:
        """外部设置颜色（不触发 colorChanged）。"""
        self._color = QColor(color_hex)
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # ComboBox 风格背景：白底 + 灰色圆角边框（深浅主题自适应）
        dark = isDarkTheme()
        bg = QColor(56, 56, 56) if dark else QColor(255, 255, 255)
        border_c = QColor(75, 75, 75) if dark else QColor(192, 192, 192)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(0, 0, w, h, 5, 5)

        # 边框线
        p.setPen(border_c)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(0, 0, w - 1, h - 1, 5, 5)

        # 内部色块矩形，高度为可用空间的 3/4，垂直居中
        margin = 5
        swatch_w = w - 28  # 右侧留出箭头 + 间距
        full_h = h - 2 * margin
        swatch_h = full_h * 3 // 4
        swatch_y = margin + (full_h - swatch_h) // 2
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawRoundedRect(margin, swatch_y, swatch_w, swatch_h, 3, 3)

        # 下拉箭头 ▼
        arrow_c = QColor(160, 160, 160) if dark else QColor(100, 100, 100)
        p.setPen(arrow_c)
        p.setBrush(Qt.NoBrush)
        ax = w - 16
        ay = h // 2 - 3
        s = 4
        p.drawLine(ax, ay, ax + s, ay + s)
        p.drawLine(ax + s, ay + s, ax + 2 * s, ay)

    def _show_popup(self) -> None:
        popup = ColorGridPopup(self._color.name(), self)
        popup.colorPicked.connect(self._on_color_picked)
        # 定位在按钮下方左对齐
        pos = self.mapToGlobal(QPoint(0, self.height() + 2))
        popup.move(pos)
        popup.show()

    def _on_color_picked(self, color_hex: str) -> None:
        self._color = QColor(color_hex)
        self.update()
        self.colorChanged.emit(color_hex)


class ColorGridPopup(QWidget):
    """网格色盘浮层：顶部"默认"黑色块 + 下方 5 行 × 6 列预设色块。

    用 Qt.Popup 标志，点击外部自动关闭。字体从 QApplication.font() 继承，
    保证与 fluent 全局字号一致。
    """

    colorPicked = Signal(str)

    _SWATCH = 24
    _GAP = 4
    _COLS = 6

    def __init__(self, current_hex: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup)
        self._current = current_hex
        self.setObjectName("colorGridPopup")
        # 确保字体跟随应用全局（不随 QApplication.font() 变化时自动更新，
        # 但弹出瞬间已是最新值--足够了）
        self.setFont(QApplication.font())
        self._build()

    def _build(self) -> None:
        dark = isDarkTheme()
        bg = "#2d2d30" if dark else "#ffffff"
        border_c = "#3c3c3c" if dark else "#cccccc"
        self.setStyleSheet(f"""
            QWidget#colorGridPopup {{
                background: {bg};
                border: 1px solid {border_c};
                border-radius: 6px;
            }}
        """)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(self._GAP)

        # 顶部："默认" 黑色色块 + 标签
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self._make_swatch("#000000"))
        # 用 BodyLabel 而非 QLabel，保证字体与 qfluentwidgets 全局 UI 字体一致
        lbl = BodyLabel(self.tr("默认"))
        top.addWidget(lbl)
        top.addStretch(1)
        v.addLayout(top)

        # 预设色块网格
        grid = QGridLayout()
        grid.setSpacing(self._GAP)
        for i, c in enumerate(COLOR_GRID_PRESETS):
            row, col = divmod(i, self._COLS)
            grid.addWidget(self._make_swatch(c), row, col)
        v.addLayout(grid)

    def _make_swatch(self, color_hex: str) -> QPushButton:
        """创建单个色块。用 QPushButton + stylesheet，避免自绘。"""
        btn = QPushButton(self)
        btn.setFixedSize(self._SWATCH, self._SWATCH)
        btn.setCursor(Qt.PointingHandCursor)

        is_sel = color_hex.upper() == self._current.upper()
        border = f"2px solid {themeColor()}" if is_sel else "1px solid #cccccc"
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {color_hex};
                border: {border};
                border-radius: 2px;
            }}
            QPushButton:hover {{
                border: 2px solid #888888;
            }}
        """)
        # 默认参数捕获避免闭包循环引用
        btn.clicked.connect(lambda checked=None, c=color_hex: self._pick(c))
        return btn

    def _pick(self, color_hex: str) -> None:
        self.colorPicked.emit(color_hex)
        self.close()
