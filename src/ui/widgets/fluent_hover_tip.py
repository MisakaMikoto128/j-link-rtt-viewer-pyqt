"""Fluent 风格的「跟随鼠标」hover tooltip。

qfluentwidgets 的 `ToolTipFilter` 只支持「相对固定 widget 定位」的静态 tooltip；
但内存查看页 / RTT 页的 hover 提示需要：
  1. 内容随鼠标位置动态计算（逐字节解析、逐行信息）；
  2. 位置跟随鼠标而不是相对某个固定控件。

`QToolTip.showText` 是原生样式，与 Fluent 气泡风格不统一。本模块提供一个
复用 qfluentwidgets `ToolTip`（圆角气泡 + 阴影 + 12px 字号 + `--FontFamilies`
family）的 helper：手动 `move(globalPos)` + `show()`，跟随鼠标定位。

生命周期：单例式复用同一个 `ToolTip` 实例（避免每次 hover 都 new + delete，
高频率 hover 下抖动/闪烁）。内容由调用方 setText 更新，位置由调用方 move。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QWidget

from qfluentwidgets.components.widgets.tool_tip import ToolTip

# 气泡相对鼠标的偏移（x 右移、y 上移显示在光标上方，避免被光标挡住）
_OFFSET = QPoint(12, -18)


class FluentHoverTip:
    """跟随鼠标的 Fluent 气泡 hover 提示。每个页面持有一个实例。"""

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent
        self._tip: ToolTip | None = None
        self._last_text: str = ""

    def show_at(self, global_pos: QPoint, text: str, duration: int = 0) -> None:
        """在 global_pos（全局屏幕坐标）附近显示 text。

        duration<=0 表示不自动消失（由 hide() 显式关闭）——hover 场景需要持续显示
        直到鼠标移走。同一文本重复调用不重建气泡（避免闪烁）。
        """
        if not text:
            self.hide()
            return
        if self._tip is None:
            # parent 传 window 使气泡层级正确；ToolTip 本身是独立 Tool 窗口
            self._tip = ToolTip("", self._parent.window())
        if text != self._last_text:
            self._tip.setText(text)
            self._last_text = text
        self._tip.setDuration(duration)
        # 位置 = 鼠标点 + 偏移；ToolTip 自己会 adjustSize 后按 sizeHint 布局
        self._tip.move(global_pos + _OFFSET)
        if not self._tip.isVisible():
            self._tip.show()

    def hide(self) -> None:
        self._last_text = ""
        if self._tip is not None:
            self._tip.hide()

    def is_showing(self) -> bool:
        return self._tip is not None and self._tip.isVisible()