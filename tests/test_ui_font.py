"""core._ui_font：系统 family 解析 + QSS `font:` 锁定控件（RadioButton 等）覆盖。

背景：qfluentwidgets 部分控件（RadioButton / 右键菜单 / InfoBar / 对话框按钮）的
qss 里硬编码 `font: Npx --FontFamilies;`，QSS 的 font 规则优先级高于 setFont()，
导致全局界面字体（family+字号）对它们失效。修复方式是往控件 styleSheet 追加一条
font-family+font-size 规则覆盖（哨兵区间保证幂等）。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget


@pytest.fixture
def rb(qapp):
    from qfluentwidgets import RadioButton
    parent = QWidget()
    QHBoxLayout(parent)
    w = RadioButton("SWD", parent)
    yield w
    parent.deleteLater()


def test_resolve_ui_family_empty_returns_system(qapp):
    from core._ui_font import resolve_ui_family
    sysfam = resolve_ui_family("")
    assert sysfam  # 非空：解析成了某个系统 family
    # 非空 family 原样返回
    assert resolve_ui_family("Consolas") == "Consolas"


def test_sync_qss_font_locked_widgets_overrides_size_and_family(qapp, rb):
    """RadioButton（QSS font 锁定）应能被套上 family+size 覆盖并生效。"""
    from core._ui_font import sync_qss_font_locked_widgets
    sync_qss_font_locked_widgets(QApplication.instance(), "Consolas", 18)
    rb.show()
    QApplication.instance().processEvents()
    assert rb.font().family() == "Consolas"
    assert rb.font().pointSize() == 18


def test_qss_font_override_is_idempotent(qapp, rb):
    """重复应用不叠加哨兵段（styleSheet 里哨兵只出现一次）。"""
    from core._ui_font import (
        _QSS_FONT_OVERRIDE_BEGIN,
        sync_qss_font_locked_widgets,
    )
    app = QApplication.instance()
    sync_qss_font_locked_widgets(app, "Consolas", 18)
    sync_qss_font_locked_widgets(app, "Consolas", 9)
    sync_qss_font_locked_widgets(app, "Consolas", 12)
    assert rb.styleSheet().count(_QSS_FONT_OVERRIDE_BEGIN) == 1


def test_qss_font_override_only_touches_locked_classes(qapp):
    """不在锁定名单里的控件（如 BodyLabel）不应被 setStyleSheet 改写。"""
    from qfluentwidgets import BodyLabel
    from core._ui_font import sync_qss_font_locked_widgets
    lbl = BodyLabel("x")
    before = lbl.styleSheet()
    sync_qss_font_locked_widgets(QApplication.instance(), "Consolas", 18)
    assert lbl.styleSheet() == before  # 未被改
    lbl.deleteLater()


def test_apply_ui_font_family_empty_maps_to_system(qapp):
    """_apply_ui_font('', N) 应把 family 解析成系统 family 而非空串。"""
    from core._ui_font import resolve_ui_family, system_ui_family
    assert resolve_ui_family("") == system_ui_family()
    assert resolve_ui_family("   ") == system_ui_family()  # 全空白也视为空
