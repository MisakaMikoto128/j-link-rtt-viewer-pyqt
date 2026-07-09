"""UI 测试：FlashPage 文件选择链路。

锁住 commit a068fd0 修的根因 bug：`EditableComboBox.setCurrentText` 对不在 items
里的路径是 no-op，导致浏览/拖放选的文件不显示、烧录提示「未选择文件」。
现在走 `_select_file → _rebuild_file_combo → setCurrentIndex` 路径，路径必须立刻出现在 combo 文本里。

另：选 axf 应显示分析面板，选 bin/hex 应隐藏。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def flash_page(qtbot, isolated_appdata):
    """新建 FlashPage 实例；APPDATA 已被 monkeypatch 到 tmp 目录，互不污染。"""
    from core.config_service import ConfigService
    from ui.flash_page import FlashPage
    cfg = ConfigService()
    page = FlashPage(cfg)
    qtbot.addWidget(page)
    yield page
    # 干净关 worker 线程，避免下个测试 dangling QThread
    page.shutdown()


def test_select_axf_shows_path_in_combo(flash_page, fixtures_dir):
    """选 axf 后 cmb_file 应立即显示完整路径——回归 setCurrentText no-op bug。"""
    axf = str(fixtures_dir / "blink_sym.axf")
    flash_page._select_file(axf)
    assert flash_page.cmb_file.currentText() == axf
    assert flash_page.cmb_file.count() >= 1


def test_select_axf_persists_to_recent_files(flash_page, fixtures_dir):
    """选完文件后 cfg 的 flash_recent_files 应记录该路径，重启也能恢复。"""
    axf = str(fixtures_dir / "blink_sym.axf")
    flash_page._select_file(axf)
    recent = flash_page._cfg.get("flash_recent_files")
    assert recent[0] == axf


def test_select_axf_shows_analysis_panel(flash_page, fixtures_dir):
    """axf 文件应显示固件分析面板（符号 / 段 / 占用汇总）。"""
    axf = str(fixtures_dir / "blink_sym.axf")
    flash_page._select_file(axf)
    assert flash_page.symbol_card.isVisibleTo(flash_page) or \
        flash_page.symbol_card.isVisible() is False  # 父未 show 时 isVisible=False 正常
    # 关键断言：底层 setVisible 状态本身
    assert not flash_page.symbol_card.isHidden()


def test_select_bin_hides_analysis_panel(flash_page, fixtures_dir):
    """bin 文件无符号表，分析面板必须隐藏。"""
    flash_page._select_file(str(fixtures_dir / "blink_sym.axf"))  # 先 show
    assert not flash_page.symbol_card.isHidden()
    flash_page._select_file(str(fixtures_dir / "blink.bin"))      # 切到 bin
    assert flash_page.symbol_card.isHidden()


def test_select_same_file_twice_no_duplicate_in_recent(flash_page, fixtures_dir):
    """重复选同一文件不应在 recent 里产生重复条目，应置顶。"""
    p = str(fixtures_dir / "blink_sym.axf")
    flash_page._select_file(p)
    flash_page._select_file(p)
    recent = flash_page._cfg.get("flash_recent_files")
    assert recent.count(p) == 1


def test_select_nonexistent_path_does_not_crash_or_persist(flash_page, tmp_path):
    """选不存在的路径：warn infobar + 不修改 recent。"""
    before = list(flash_page._cfg.get("flash_recent_files") or [])
    flash_page._select_file(str(tmp_path / "ghost.axf"))
    after = list(flash_page._cfg.get("flash_recent_files") or [])
    assert before == after


def test_recent_files_capped_at_10(flash_page, tmp_path):
    """最近文件列表上限 10——继续添加最旧的应被淘汰。"""
    # 造 12 个真实存在的文件
    paths = []
    for i in range(12):
        p = tmp_path / f"f{i}.bin"
        p.write_bytes(b"\x00" * 4)
        paths.append(str(p))
    for p in paths:
        flash_page._select_file(p)
    recent = flash_page._cfg.get("flash_recent_files")
    assert len(recent) == 10
    # 最后选的应在最前，最旧的两个被淘汰
    assert recent[0] == paths[-1]
    assert paths[0] not in recent
    assert paths[1] not in recent


def test_device_combo_has_completer(flash_page):
    """cmb_device 应配备 QCompleter，支持子串匹配。"""
    from PySide6.QtCore import Qt
    completer = flash_page.cmb_device.completer()
    assert completer is not None
    assert completer.caseSensitivity() == Qt.CaseInsensitive
    assert completer.filterMode() == Qt.MatchContains
