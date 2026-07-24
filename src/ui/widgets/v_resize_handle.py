"""显示区可拖高度 handle：6px 命中区 + 主题色细线，hover/拖动时高亮。

为什么不用 QSplitter：splitter 在 QScrollArea 里只能在 viewport 内分配
children，display 永远拖不到比 viewport 大 -- 无法触发整页滚。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QEnterEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QWidget
from qfluentwidgets import themeColor


class VResizeHandle(QFrame):
    """display 下方的水平拖动条 -- 极简观感，跟随主题色。

    6px 命中区（够大好抓）+ 1px 中央细灰线（默认几乎不可见）；hover/拖动
    时变 2px 主题色线（用 qfluentwidgets.themeColor()，自动跟用户偏好）。
    """

    heightChanged = Signal(int)  # 拖动结束 emit 最终高度（持久化用）

    _MIN_TARGET_H = 120

    def __init__(self, target: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = target
        self._dragging = False
        self._hover = False
        self._start_y = 0.0
        self._start_h = 0
        self.setFixedHeight(6)
        self.setCursor(Qt.SizeVerCursor)
        # 用 paintEvent 自绘，stylesheet 留空避免 QSS 引擎干扰 paint

    def enterEvent(self, e: QEnterEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e: QEvent) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        if self._dragging or self._hover:
            color = QColor(themeColor())
            color.setAlpha(220 if self._dragging else 150)
            thickness = 2
        else:
            color = QColor(128, 128, 128, 45)
            thickness = 1
        y = (h - thickness) // 2
        p.fillRect(0, y, w, thickness, color)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._start_y = e.globalPosition().y()
            self._start_h = self._target.height()
            self.update()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._dragging:
            dy = int(e.globalPosition().y() - self._start_y)
            new_h = max(self._MIN_TARGET_H, self._start_h + dy)
            self._target.setFixedHeight(new_h)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self.heightChanged.emit(self._target.height())
            self.update()
            e.accept()
