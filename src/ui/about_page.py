"""关于页：Hero header + 功能卡片 + 作者 + 第三方致谢 + 页脚。

布局：QScrollArea 包整页（窗口压扁不挤），内部 5 段：
  1) Hero：logo + 标题/版本/标语 + 动作按钮
  2) 功能特性：3 卡平铺
  3) 作者卡
  4) 第三方依赖致谢
  5) 页脚 caption
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    HeaderCardWidget,
    HyperlinkButton,
    IconWidget,
    ImageLabel,
    PrimaryPushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from core._paths import find_app_logo_png

from ._scroll_helpers import make_transparent_scroll

APP_VERSION = "0.2.1"
APP_TAGLINE = "为嵌入式开发者打造的现代化 SEGGER RTT 调试工具"
AUTHOR_NAME = "MisakaMikoto128"
AUTHOR_BIO = "嵌入式 / Python 开发者"
AUTHOR_GITHUB = "https://github.com/MisakaMikoto128"
PROJECT_URL = "https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt"
ISSUES_URL = f"{PROJECT_URL}/issues"


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("about")
        self._build_ui()

    def _build_ui(self) -> None:
        # 外壳：透明 ScrollArea
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll, inner = make_transparent_scroll(self, "about")
        outer.addWidget(scroll)

        root = QVBoxLayout(inner)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(18)

        root.addWidget(self._build_hero())
        root.addWidget(self._build_feature_grid())
        root.addWidget(self._build_author_card())
        root.addWidget(self._build_acknowledgments())
        root.addWidget(self._build_footer())
        root.addStretch(1)

    # ----------- Hero -----------
    def _build_hero(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("关于本软件")

        body = QWidget(card)
        h = QHBoxLayout(body)
        h.setContentsMargins(4, 4, 4, 4)
        h.setSpacing(20)

        # Logo
        logo_path = find_app_logo_png()
        if logo_path is not None:
            logo = ImageLabel(str(logo_path), body)
            logo.setBorderRadius(16, 16, 16, 16)
            logo.scaledToWidth(96)
        else:
            # PNG 没拷过来时 fallback 用 FluentIcon DEVELOPER_TOOLS
            logo = IconWidget(FluentIcon.DEVELOPER_TOOLS, body)
            logo.setFixedSize(96, 96)
        logo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        h.addWidget(logo, 0, Qt.AlignTop)

        # 标题区
        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        # DisplayLabel 字号过大 (min ≈ 1100px) 会撑爆窄窗口；用 TitleLabel
        title = TitleLabel("J-Link RTT Viewer", body)
        text_col.addWidget(title)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        meta_row.addWidget(SubtitleLabel(f"v{APP_VERSION}", body))
        sep = CaptionLabel("·", body)
        sep.setEnabled(False)
        meta_row.addWidget(sep)
        meta_row.addWidget(CaptionLabel("Python · PySide6 · Fluent Design", body))
        meta_row.addStretch(1)
        text_col.addLayout(meta_row)

        tagline = BodyLabel(APP_TAGLINE, body)
        tagline.setWordWrap(True)
        text_col.addWidget(tagline)

        # 动作按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_repo = PrimaryPushButton(FluentIcon.GITHUB, "项目仓库", body)
        btn_repo.clicked.connect(lambda: self._open_url(PROJECT_URL))
        btn_row.addWidget(btn_repo)
        btn_issue = HyperlinkButton(ISSUES_URL, "反馈 Issue", body, FluentIcon.FEEDBACK)
        btn_row.addWidget(btn_issue)
        btn_row.addStretch(1)
        text_col.addSpacing(6)
        text_col.addLayout(btn_row)

        h.addLayout(text_col, 1)
        card.viewLayout.addWidget(body)
        return card

    # ----------- Features -----------
    def _build_feature_grid(self) -> QWidget:
        wrap = QWidget(self)
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(self._feature_card(
            FluentIcon.COMMAND_PROMPT,
            "RTT 实时监控",
            "16 通道任意切换；UTF-8 中文 + ANSI 颜色解析；"
            "可向 MCU 发送文本 / 十六进制；搜索高亮、会话标记、节流落盘。",
        ), 1)
        row.addWidget(self._feature_card(
            FluentIcon.LIBRARY,
            "内存查看 / 写入",
            "任意地址 Hex Dump，类型解析；hover 显示十进制；"
            "diff 红色高亮；区间分块导出 .bin；高风险写内存（二次确认）。",
        ), 1)
        row.addWidget(self._feature_card(
            FluentIcon.PALETTE,
            "个性化设置",
            "亮 / 暗 / 跟随系统主题；主题色自定义；"
            "UI 与显示区字体独立调节；偏好节流落盘到 %APPDATA%。",
        ), 1)
        return wrap

    def _feature_card(self, icon: FluentIcon, title: str, desc: str) -> HeaderCardWidget:
        card = HeaderCardWidget(self)
        card.setTitle(title)

        # 把 icon 塞进标题左侧
        ic = IconWidget(icon, card)
        ic.setFixedSize(QSize(20, 20))
        card.headerLayout.insertWidget(0, ic)
        card.headerLayout.insertSpacing(1, 8)

        body = BodyLabel(desc, card)
        body.setWordWrap(True)
        card.viewLayout.addWidget(body)
        return card

    # ----------- Author -----------
    def _build_author_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("作者")

        body = QWidget(card)
        h = QHBoxLayout(body)
        h.setContentsMargins(4, 4, 4, 4)
        h.setSpacing(16)

        avatar = IconWidget(FluentIcon.PEOPLE, body)
        avatar.setFixedSize(48, 48)
        h.addWidget(avatar, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(StrongBodyLabel(AUTHOR_NAME, body))
        col.addWidget(CaptionLabel(AUTHOR_BIO, body))
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(HyperlinkButton(AUTHOR_GITHUB, "GitHub 主页", body, FluentIcon.GITHUB))
        btn_row.addStretch(1)
        col.addSpacing(4)
        col.addLayout(btn_row)
        h.addLayout(col, 1)

        card.viewLayout.addWidget(body)
        return card

    # ----------- Acknowledgments -----------
    def _build_acknowledgments(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("第三方依赖")

        # 2 列网格：name | role；role 加 wordWrap，避免单行强行撑宽整页
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        items = [
            ("pylink-square 1.6.0", "SEGGER J-Link Python 封装"),
            ("PySide6 / Qt", "跨平台 GUI 框架"),
            ("PyQt-Fluent-Widgets", "Fluent Design 组件库"),
            ("Nuitka", "Python → 原生可执行打包"),
        ]
        for r, (name, role) in enumerate(items):
            n_lbl = StrongBodyLabel(f"• {name}")
            n_lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            grid.addWidget(n_lbl, r, 0)
            r_lbl = BodyLabel(role)
            r_lbl.setWordWrap(True)
            grid.addWidget(r_lbl, r, 1)
        grid.setColumnStretch(1, 1)

        wrap = QWidget(card)
        wrap.setLayout(grid)
        card.viewLayout.addWidget(wrap)
        return card

    # ----------- Footer -----------
    def _build_footer(self) -> QWidget:
        wrap = QWidget(self)
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(2)
        c1 = CaptionLabel(
            "SEGGER® 与 J-Link® 是 SEGGER Microcontroller GmbH 的注册商标。"
            "本项目与 SEGGER 无任何官方关联。"
        )
        c1.setWordWrap(True)
        c1.setAlignment(Qt.AlignCenter)
        c1.setEnabled(False)
        v.addWidget(c1)

        c2 = CaptionLabel(f"© 2026 {AUTHOR_NAME}  ·  MIT License")
        c2.setAlignment(Qt.AlignCenter)
        c2.setEnabled(False)
        v.addWidget(c2)
        return wrap

    # ----------- helpers -----------
    @staticmethod
    def _open_url(url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))
