"""收窄模式悬浮卡片：承载控制栏，fade + slide 动画显隐。

窗口缩窄时左侧配置面板 reparent 进悬浮卡片，浮在右侧数据区之上
（不挤压布局）。btn_panel_toggle 控制显隐，fade + slide 动画。

为什么不用 QSplitter / 布局：收窄模式下左侧面板需要"浮"在右侧数据区
之上，而不是挤压右侧布局--这样弹出卡片时应用仍处于收窄模式，
右侧显示区宽度不会因卡片弹出而变化。
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect, QVBoxLayout, QWidget
from qfluentwidgets import isDarkTheme


class FloatingPanel(QObject):
    """悬浮卡片容器（不参与布局流，move() 定位）。

    持有卡片 widget + 透明度/位移动画。RTTMonitorPage 负责 reparent
    _config_panel 进 content_layout + 决定何时 show/hide。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        card = QWidget(parent)
        card.setObjectName("floatingCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setFixedWidth(280)
        card.setVisible(False)
        self._card = card

        # 卡片内部布局：承载 _config_panel，无内边距让面板填满卡片
        self._layout = QVBoxLayout(card)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # 透明度效果：用于 fade 动画（初始 0.0 - 隐藏时不可见）
        self._opacity = QGraphicsOpacityEffect(card)
        self._opacity.setOpacity(0.0)
        card.setGraphicsEffect(self._opacity)

        # 位移动画：用于 slide 动画
        self._pos_anim = QPropertyAnimation(card, b"pos", card)
        self._pos_anim.setDuration(220)
        self._pos_anim.setEasingCurve(QEasingCurve.OutCubic)

        # 透明度动画：用于 fade 动画
        self._opacity_anim = QPropertyAnimation(self._opacity, b"opacity", card)
        self._opacity_anim.setDuration(220)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._opacity_anim.finished.connect(self._on_anim_finished)

        self.apply_style()

    def card_widget(self) -> QWidget:
        """卡片 widget（用于 reparent 判断 / setVisible）。"""
        return self._card

    def content_layout(self) -> QVBoxLayout:
        """卡片内部布局（用于 addWidget/removeWidget _config_panel）。"""
        return self._layout

    def apply_style(self) -> None:
        """按当前深浅色主题刷新卡片样式。"""
        dark = isDarkTheme()
        if dark:
            bg = "rgba(45, 45, 48, 250)"
            border = "#3c3c3c"
        else:
            bg = "rgba(252, 252, 252, 250)"
            border = "#d0d0d0"
        self._card.setStyleSheet(f"""
            QWidget#floatingCard {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
        """)

    def stop_animations(self) -> None:
        """停止正在进行的卡片动画（stop 不触发 finished 信号）。"""
        self._pos_anim.stop()
        self._opacity_anim.stop()

    def show_card(self, parent_height: int) -> None:
        """展开：fade 0->1 + slide 从左侧 40px 滑入。

        只有 X 方向位移，Y 始终等于 target.y()，不会出现"先弹 X 再移 Y"。
        """
        self.stop_animations()
        self.apply_style()  # 确保主题正确

        # 计算目标位置
        margin = 8
        target = QPoint(margin, margin)
        self._card.setFixedHeight(max(100, parent_height - 2 * margin))

        was_visible = self._card.isVisible()
        if not was_visible:
            # 首次/隐藏后重新显示：从目标左侧 40px 处滑入，Y 与 target 一致
            start_pos = QPoint(target.x() - 40, target.y())
            start_opacity = 0.0
            self._card.move(start_pos)
            self._opacity.setOpacity(0.0)
        else:
            # 中途反转：从当前位置/透明度继续
            start_pos = self._card.pos()
            start_opacity = self._opacity.opacity()

        self._card.setVisible(True)
        self._card.raise_()

        self._pos_anim.setStartValue(start_pos)
        self._pos_anim.setEndValue(target)
        self._opacity_anim.setStartValue(start_opacity)
        self._opacity_anim.setEndValue(1.0)
        self._pos_anim.start()
        self._opacity_anim.start()

    def hide_card(self) -> None:
        """收起：fade 1->0 + slide 向左滑出 40px。"""
        self.stop_animations()
        start_pos = self._card.pos()
        start_opacity = self._opacity.opacity()
        end_pos = QPoint(start_pos.x() - 40, start_pos.y())

        self._pos_anim.setStartValue(start_pos)
        self._pos_anim.setEndValue(end_pos)
        self._opacity_anim.setStartValue(start_opacity)
        self._opacity_anim.setEndValue(0.0)
        self._pos_anim.start()
        self._opacity_anim.start()

    def _on_anim_finished(self) -> None:
        """动画结束：若是收起方向则隐藏卡片，避免遮挡下层交互。"""
        if self._opacity.opacity() < 0.5:
            self._card.setVisible(False)

    def reposition(self, parent_height: int) -> None:
        """窗口 resize 时重新计算卡片位置/高度。

        动画进行中只更新高度（避免和位移动画打架），位置等动画结束后再校正。
        """
        if not self._card.isVisible():
            return
        margin = 8
        self._card.setFixedHeight(max(100, parent_height - 2 * margin))
        if self._pos_anim.state() != QAbstractAnimation.State.Running:
            self._card.move(margin, margin)

    def is_visible(self) -> bool:
        return self._card.isVisible()

    def set_visible(self, visible: bool) -> None:
        self._card.setVisible(visible)
