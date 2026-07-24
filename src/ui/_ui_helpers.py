"""通用 UI helper：Fluent 风格 tooltip + 区域分隔线。

跨页面复用（RTT 监控页 / 搜索栏 / 烧录页等），避免每个文件各自定义 _tip。
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QWidget
from qfluentwidgets import ToolTipFilter


def tip(widget: QWidget, text: str, duration: int = 300) -> None:
    """设置 QFluentWidgets 风格 tooltip：setToolTip 提供文本，ToolTipFilter
    拦截原生 tooltip 事件改用 Fluent 圆角气泡。

    ToolTipFilter 仅安装一次（动态属性 _fluent_tip_installed 标记）。本函数在
    构造与语言重翻译时都会被调用，重复安装会叠加多个 filter，悬停时每个
    filter 各弹一个气泡产生重影。ToolTipFilter 在 showToolTip 时动态读取
    widget.toolTip()，故后续调用只需 setToolTip 即可刷新文本。
    """
    widget.setToolTip(text)
    if not widget.property("_fluent_tip_installed"):
        widget.installEventFilter(ToolTipFilter(widget, duration))
        widget.setProperty("_fluent_tip_installed", True)


def section_separator(parent: QWidget) -> QFrame:
    """创建一条水平分隔线，用于面板区域划分。

    不用 QFrame.HLine + Sunken -- 那种 frame 在 1px 高度下不渲染
    （需要 2px 才能画上下两条线）。直接用背景色填一个 1px 高的 bar。
    """
    line = QFrame(parent)
    line.setFixedHeight(1)
    line.setStyleSheet("QFrame { background-color: rgba(128,128,128,0.3); border: none; }")
    return line
