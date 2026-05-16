"""关于页：应用信息 + 功能介绍 + 第三方致谢。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    HyperlinkButton,
    SubtitleLabel,
    TitleLabel,
)

APP_VERSION = "0.1.0"
AUTHOR_NAME = "MisakaMikoto128"
AUTHOR_GITHUB = "https://github.com/MisakaMikoto128"
PROJECT_URL = "https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt"


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("about")
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(20)

        # 标题
        title = TitleLabel("J-Link RTT Viewer", self)
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        ver = BodyLabel(f"版本 {APP_VERSION}", self)
        ver.setAlignment(Qt.AlignCenter)
        root.addWidget(ver)

        # 功能介绍
        feat_row = QHBoxLayout()
        feat_row.addWidget(self._feature_card(
            "📊 RTT 监控",
            "实时显示 MCU 通过 SEGGER RTT 输出的日志，支持 UTF-8 中文与 ANSI 颜色，"
            "16 个通道任意切换，可向 MCU 发送文本/十六进制数据。"
        ), 1)
        feat_row.addWidget(self._feature_card(
            "🔍 内存查看",
            "读取目标设备任意地址内存并以 Hex Dump 形式展示，"
            "支持按区间将固件分块导出为 .bin 文件。"
        ), 1)
        root.addLayout(feat_row)

        # 作者
        author = CardWidget(self)
        a_lay = QVBoxLayout(author)
        a_lay.addWidget(SubtitleLabel("作者", self))
        a_lay.addWidget(BodyLabel(AUTHOR_NAME, self))
        a_lay.addWidget(HyperlinkButton(AUTHOR_GITHUB, f"GitHub: {AUTHOR_NAME}"))
        a_lay.addWidget(HyperlinkButton(PROJECT_URL, "📦 项目仓库"))
        root.addWidget(author)

        # 致谢
        ack = CardWidget(self)
        ack_lay = QVBoxLayout(ack)
        ack_lay.addWidget(SubtitleLabel("第三方依赖致谢", self))
        ack_lay.addWidget(BodyLabel("• pylink-square — SEGGER J-Link Python 封装", self))
        ack_lay.addWidget(BodyLabel("• PySide6 / Qt — Qt for Python", self))
        ack_lay.addWidget(BodyLabel("• PyQt-Fluent-Widgets — Fluent 设计组件库", self))
        root.addWidget(ack)

        root.addStretch(1)

    def _feature_card(self, title: str, desc: str) -> CardWidget:
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        lay.addWidget(SubtitleLabel(title, self))
        body = BodyLabel(desc, self)
        body.setWordWrap(True)
        lay.addWidget(body)
        return card
