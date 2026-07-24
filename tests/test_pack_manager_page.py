"""PackManagerPage UI 测试：已装列表/过滤/删除 + 在线搜索分页。"""

from __future__ import annotations


def test_pack_page_lists_packs(qtbot, isolated_appdata, monkeypatch, tmp_path):
    from core import pack_service

    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    (tmp_path / "Keil.STM32F0_DFP.1.0.0.pack").write_bytes(b"\x00" * 1024)
    (tmp_path / "Keil.STM32F1_DFP.4.0.0.pack").write_bytes(b"\x00" * 2048)
    from core.config_service import ConfigService
    from ui.pack_manager_page import PackManagerPage

    page = PackManagerPage(ConfigService())
    qtbot.addWidget(page)
    page._lazy_load()
    try:
        assert page.tbl_installed.rowCount() == 2
        assert page.tbl_installed.item(0, 0).text() == "Keil.STM32F0_DFP.1.0.0.pack"
        assert page.tbl_installed.item(0, 1).text() == "Keil"
        assert page.tbl_installed.item(0, 2).text() == "1.0.0"
    finally:
        page.shutdown()


def test_pack_page_filter(qtbot, isolated_appdata, monkeypatch, tmp_path):
    from core import pack_service

    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    (tmp_path / "Keil.STM32F0_DFP.1.0.0.pack").write_bytes(b"\x00")
    (tmp_path / "Keil.STM32F1_DFP.4.0.0.pack").write_bytes(b"\x00")
    from core.config_service import ConfigService
    from ui.pack_manager_page import PackManagerPage

    page = PackManagerPage(ConfigService())
    qtbot.addWidget(page)
    page._lazy_load()
    try:
        assert page.tbl_installed.rowCount() == 2
        page.le_filter.setText("F1")
        assert page.tbl_installed.isRowHidden(0) is True  # F0
        assert page.tbl_installed.isRowHidden(1) is False  # F1
    finally:
        page.shutdown()


def test_pack_page_delete_selected(qtbot, isolated_appdata, monkeypatch, tmp_path):
    from core import pack_service

    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    f = tmp_path / "Keil.STM32F0_DFP.1.0.0.pack"
    f.write_bytes(b"\x00")
    from core.config_service import ConfigService
    from ui.pack_manager_page import PackManagerPage

    page = PackManagerPage(ConfigService())
    qtbot.addWidget(page)
    page._lazy_load()
    try:
        page.tbl_installed.selectRow(0)
        page._on_delete()
        assert not f.exists()
        assert page.tbl_installed.rowCount() == 0
    finally:
        page.shutdown()


def test_pack_page_search_pagination(qtbot, isolated_appdata, monkeypatch, tmp_path):
    """在线搜索：mock search_packs 返回 30 条，分页 12/页 -> 1/12 + 2/12 + 3/6。"""
    from core import pack_service

    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    monkeypatch.setattr(
        pack_service,
        "search_packs",
        lambda q, limit=500: [f"STM32F{i:02d}" for i in range(30)],
    )
    from core.config_service import ConfigService
    from ui.pack_manager_page import PackManagerPage

    page = PackManagerPage(ConfigService())
    qtbot.addWidget(page)
    page._lazy_load()
    try:
        page.le_search.setText("STM32")
        page._on_search()
        qtbot.waitUntil(lambda: page.tbl_search.rowCount() == 12, timeout=2000)
        assert page.tbl_search.rowCount() == 12  # 第一页满 12
        assert page.lbl_page.text() == "1 / 3"
        assert page.btn_next.isEnabled() and not page.btn_prev.isEnabled()

        page._next_page()
        assert page.tbl_search.rowCount() == 12  # 第二页满 12
        assert page.lbl_page.text() == "2 / 3"
        assert page.btn_prev.isEnabled()

        page._next_page()
        assert page.tbl_search.rowCount() == 6  # 第三页余 6
        assert page.lbl_page.text() == "3 / 3"
        assert not page.btn_next.isEnabled()

        page._prev_page()
        assert page.tbl_search.rowCount() == 12
        assert page.lbl_page.text() == "2 / 3"
    finally:
        page.shutdown()


def test_pack_page_download_uses_selected_row(qtbot, isolated_appdata, monkeypatch, tmp_path):
    """选中搜索结果行后点下载，worker 收到该 part_number（mock download_pack）。"""
    from core import pack_service

    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    monkeypatch.setattr(
        pack_service,
        "search_packs",
        lambda q, limit=500: ["STM32F103C8Tx"],
    )
    captured = {}

    def _fake_download(part, log=None):
        captured["part"] = part
        return "downloaded"

    monkeypatch.setattr(pack_service, "download_pack", _fake_download)

    from core.config_service import ConfigService
    from ui.pack_manager_page import PackManagerPage

    page = PackManagerPage(ConfigService())
    qtbot.addWidget(page)
    page._lazy_load()
    try:
        page.le_search.setText("STM32F103")
        page._on_search()
        qtbot.waitUntil(lambda: page.tbl_search.rowCount() == 1, timeout=2000)
        page.tbl_search.selectRow(0)
        page._on_download()
        qtbot.waitUntil(lambda: page._dl_thread is None, timeout=3000)
        assert captured.get("part") == "STM32F103C8Tx"
    finally:
        page.shutdown()
