"""UI 测试：SymbolTableView 的 chip 过滤 / 名称搜索 / 列排序。

固件 blink_sym.axf 含 4 个符号：
- blink.c    FILE   LOCAL
- local_helper FUNC LOCAL
- main       FUNC   GLOBAL
- g_counter  OBJECT GLOBAL

默认 chip：Functions + Variables 亮 → 显示 3 个（main, local_helper, g_counter）。
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def symbol_view(qtbot, fixtures_dir):
    from ui.symbol_table_view import SymbolTableView
    w = SymbolTableView()
    qtbot.addWidget(w)
    w.load(str(fixtures_dir / "blink_sym.axf"))
    return w


def _row_names(view) -> list[str]:
    return [view.table.item(r, 0).text() for r in range(view.table.rowCount())]


def test_default_chips_show_func_and_var(symbol_view):
    """默认 chip 状态下应隐藏 FILE 符号，只显示 FUNC + OBJECT。"""
    names = _row_names(symbol_view)
    assert "main" in names
    assert "local_helper" in names
    assert "g_counter" in names
    assert "blink.c" not in names           # FILE 默认不显示
    assert symbol_view.table.rowCount() == 3


def test_toggle_file_chip_shows_file_marker(symbol_view, qtbot):
    """点亮 File markers chip 后应包含 blink.c。"""
    chip = symbol_view._cat_chips["file"]
    assert not chip.isChecked()
    chip.setChecked(True)                   # 触发 toggled → _apply_filter
    qtbot.wait(20)
    names = _row_names(symbol_view)
    assert "blink.c" in names
    assert symbol_view.table.rowCount() == 4


def test_toggle_off_local_binding_hides_local_symbols(symbol_view, qtbot):
    """关掉 Local 绑定 → local_helper 与 blink.c 都应隐藏；只剩 GLOBAL。"""
    symbol_view._bind_chips["LOCAL"].setChecked(False)
    qtbot.wait(20)
    names = _row_names(symbol_view)
    assert "main" in names                  # GLOBAL FUNC
    assert "g_counter" in names             # GLOBAL OBJECT
    assert "local_helper" not in names      # LOCAL


def test_search_filters_by_substring_case_insensitive(symbol_view, qtbot):
    """搜索框按子串过滤，且与 chip 条件 AND 叠加。"""
    symbol_view.search.setText("MAIN")
    qtbot.wait(20)
    names = _row_names(symbol_view)
    assert names == ["main"]


def test_only_functions_then_sort_by_size_desc(symbol_view, qtbot):
    """只亮 Functions，按 Size 列降序——main(32) 应排在 local_helper(0) 前面。"""
    symbol_view._cat_chips["var"].setChecked(False)
    qtbot.wait(20)
    from PySide6.QtCore import Qt
    symbol_view.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
    qtbot.wait(20)
    names = _row_names(symbol_view)
    # main 大小 32，local_helper 大小 0 → 降序后 main 在 [0]
    assert names[0] == "main"


def test_clear_resets_state(symbol_view):
    """clear() 应清空所有行 + 内部缓存。"""
    symbol_view.clear()
    assert symbol_view.table.rowCount() == 0
    assert symbol_view._symbols == []
    assert symbol_view._section_sizes == {}


def test_load_corrupt_elf_does_not_crash(qtbot, tmp_path):
    """读非 ELF / 损坏文件应捕获 FileParseError，不抛到测试层。

    现实场景：QFileDialog 不会选到不存在的路径，但有可能选到
    扩展名是 axf 但内容损坏的文件。SymbolTableView.load 应消化此异常。
    """
    from ui.symbol_table_view import SymbolTableView
    fake = tmp_path / "bad.axf"
    fake.write_bytes(b"not an elf file at all")
    w = SymbolTableView()
    qtbot.addWidget(w)
    w.load(str(fake))
    assert w.table.rowCount() == 0          # 解析失败 → 空表
