"""截图烟雾测试：用 QWidget.grab() 把界面落 PNG，做最朴素的回归。

设计取舍：
- 不做严格的像素 diff（字体反走样、主题色随系统、qfluentwidgets 动画都会扰动），
  只断言「截图非空 + 状态变化前后字节数有差异」这类弱不变量。
- PNG 落到 pytest 提供的 tmp_path 下，失败时通过 `pytest -s` 看路径。
- 真要做严格视觉回归再上 pixelmatch / pillow.ImageChops，先把脚手架跑通。
"""
from __future__ import annotations

import pytest


def _grab(widget, path):
    """grab() 返回 (w, h, file_size)；offscreen 平台同样能拿到像素。"""
    from PySide6.QtCore import QSize
    widget.resize(QSize(900, 600))
    widget.adjustSize()
    pm = widget.grab()
    pm.save(str(path), "PNG")
    return pm.width(), pm.height(), path.stat().st_size


def test_symbol_table_screenshot_non_empty(qtbot, fixtures_dir, screenshot_dir):
    """SymbolTableView 加载后截图应非空，且像素数与窗口尺寸吻合。"""
    from ui.symbol_table_view import SymbolTableView
    w = SymbolTableView()
    qtbot.addWidget(w)
    w.load(str(fixtures_dir / "blink_sym.axf"))
    w.show()                              # offscreen 下 show() 仍触发 layout
    qtbot.waitExposed(w)
    width, height, size = _grab(w, screenshot_dir / "symbol_default.png")
    assert width >= 400 and height >= 200
    assert size > 1000                    # 任何渲染的 PNG 都会超过这个


def test_chip_toggle_changes_screenshot(qtbot, fixtures_dir, screenshot_dir):
    """切换 chip 后截图字节数应与默认状态有差异——证明 UI 确实重绘了。

    弱断言：只比文件大小，不比像素。文件大小不变并不必然说明没重绘，
    但本测试只是确认「过滤逻辑跑通且触发了渲染管线」。
    """
    from ui.symbol_table_view import SymbolTableView
    w = SymbolTableView()
    qtbot.addWidget(w)
    w.load(str(fixtures_dir / "blink_sym.axf"))
    w.show()
    qtbot.waitExposed(w)

    _, _, size_default = _grab(w, screenshot_dir / "symbol_default.png")
    # 点亮 File markers + Sections + Other → 行数从 3 → 4
    for k in ("file", "section", "other"):
        w._cat_chips[k].setChecked(True)
    qtbot.wait(50)
    _, _, size_all = _grab(w, screenshot_dir / "symbol_all.png")

    # 文件存在、字节数都非零
    assert size_default > 0 and size_all > 0
    # 内容应该有变化（多了一行符号）
    assert size_default != size_all


def test_flash_page_screenshot_axf_vs_bin(
        qtbot, isolated_appdata, fixtures_dir, screenshot_dir):
    """FlashPage 选 axf 与 bin 的截图应不同——分析面板显隐变化。"""
    from core.config_service import ConfigService
    from ui.flash_page import FlashPage
    cfg = ConfigService()
    page = FlashPage(cfg)
    qtbot.addWidget(page)
    try:
        page.show()
        qtbot.waitExposed(page)

        page._select_file(str(fixtures_dir / "blink_sym.axf"))
        qtbot.wait(50)
        _, _, size_axf = _grab(page, screenshot_dir / "flash_axf.png")

        page._select_file(str(fixtures_dir / "blink.bin"))
        qtbot.wait(50)
        _, _, size_bin = _grab(page, screenshot_dir / "flash_bin.png")

        assert size_axf > 0 and size_bin > 0
        assert size_axf != size_bin       # 面板显隐 → 字节数显著变化
    finally:
        page.shutdown()
