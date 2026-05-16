"""共享的 ScrollArea 工厂：三层透明（scroll/viewport/inner）+ 无 border。

为什么需要：
- QScrollArea 默认 base color 叠在 FluentWindow 上泛 cream，需透明
- 三层 (scroll / viewport / inner) 必须分别设透明，少一层就漏
- objectName 选择器隔离，避免污染 CardWidget / PlainTextEdit 子控件样式

两页（RTT / 内存）调用方式一致，提取避免一处改另一处漏。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget


def make_transparent_scroll(parent: QWidget, name: str) -> tuple[QScrollArea, QWidget]:
    """构建 (scroll_area, inner_widget)，三层透明 + 无 border + 横向滚条禁用。

    name 用作 objectName 前缀（如 "rtt" → "rtt_vp" / "rtt_inner"）。
    把 scroll 放进 parent 的外层 layout，所有内容控件 addWidget 到 inner。
    """
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

    vp = scroll.viewport()
    vp.setObjectName(f"{name}_vp")
    vp.setStyleSheet(f"QWidget#{name}_vp {{ background: transparent; }}")

    inner = QWidget()
    inner.setObjectName(f"{name}_inner")
    inner.setStyleSheet(f"QWidget#{name}_inner {{ background: transparent; }}")
    scroll.setWidget(inner)

    return scroll, inner
