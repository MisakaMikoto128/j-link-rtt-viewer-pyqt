"""UI 测试：FlashPage 文件选择链路。

锁住 commit a068fd0 修的根因 bug：`EditableComboBox.setCurrentText` 对不在 items
里的路径是 no-op，导致浏览/拖放选的文件不显示、烧录提示「未选择文件」。
现在走 `_select_file → _rebuild_file_combo → setCurrentIndex` 路径，路径必须立刻出现在 combo 文本里。

另：选 axf 应显示分析面板，选 bin/hex 应隐藏。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject


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
    assert (
        flash_page.symbol_card.isVisibleTo(flash_page)
        or flash_page.symbol_card.isVisible() is False
    )  # 父未 show 时 isVisible=False 正常
    # 关键断言：底层 setVisible 状态本身
    assert not flash_page.symbol_card.isHidden()


def test_select_bin_hides_analysis_panel(flash_page, fixtures_dir):
    """bin 文件无符号表，分析面板必须隐藏。"""
    flash_page._select_file(str(fixtures_dir / "blink_sym.axf"))  # 先 show
    assert not flash_page.symbol_card.isHidden()
    flash_page._select_file(str(fixtures_dir / "blink.bin"))  # 切到 bin
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


# ------------------------------------------------------------------
# 远程 J-Link 模式（FlashPage 部分）
# ------------------------------------------------------------------


class _SignalSpy(QObject):
    """信号计数器。

    信号 spy 用 QObject 的 bound-method 槽连接被测信号（如 flash_requested）：
    bound method 携带 receiver QObject，按其线程亲和性走 AutoConnection 的直连路径，
    在主线程可靠计数。裸 callable 连接到 worker 线程归属的信号时，跨线程 emit 不触发。
    """

    def __init__(self, signal):
        super().__init__()
        self.count = 0
        signal.connect(self._on_signal)

    def _on_signal(self, *args):
        self.count += 1


def _process():
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.processEvents()


def test_remote_item_is_last_combo_item(flash_page):
    """枚举后远程项固定在下拉最末。J-Link label 用 product（无 product 退化为 "J-Link"）。

    下拉：["── J-Link ──", "J-Link: 111", "J-Link OB: 222", "远程连接"]。
    """
    from ui.widgets.remote_host import REMOTE_ITEM_TEXT

    flash_page._on_jlink_burners_enumerated("111|J-Link;222|J-Link OB")
    _process()
    assert flash_page.cmb_burner.count() == 4
    assert flash_page.cmb_burner.itemText(0) == "── J-Link ──"
    assert flash_page.cmb_burner.itemText(1) == "J-Link: 111"
    assert flash_page.cmb_burner.itemText(2) == "J-Link OB: 222"
    assert flash_page.cmb_burner.itemText(3) == REMOTE_ITEM_TEXT


def test_selecting_remote_item_shows_row_and_persists_mode(flash_page):
    """选中「远程连接…」后显示 IP/端口行并持久化 flash_jlink_mode。"""
    from ui.widgets.remote_host import REMOTE_ITEM_TEXT

    flash_page._on_jlink_burners_enumerated("111|A")
    _process()
    idx = flash_page.cmb_burner.findText(REMOTE_ITEM_TEXT)
    flash_page.cmb_burner.setCurrentIndex(idx)
    _process()
    assert not flash_page.remote_row.isHidden()
    assert flash_page._cfg.get("flash_jlink_mode") == "remote"


def test_start_flash_unresolvable_host_warns(flash_page, fixtures_dir):
    """远程模式下主机名无法解析时拦截，不 emit flash_requested。"""
    from ui.widgets.remote_host import REMOTE_ITEM_TEXT

    flash_page._on_jlink_burners_enumerated("")
    idx = flash_page.cmb_burner.findText(REMOTE_ITEM_TEXT)
    flash_page.cmb_burner.setCurrentIndex(idx)
    flash_page.le_remote_host.setText("not a valid host!!")
    flash_page.le_remote_port.setText("19020")
    flash_page._select_file(str(fixtures_dir / "blink.bin"))
    flash_page.cmb_device.setCurrentText("STM32H750VB")
    _process()

    spy = _SignalSpy(flash_page._worker.flash_requested)
    flash_page.btn_flash.click()
    _process()
    assert spy.count == 0


def test_start_flash_valid_localhost_builds_remote_params(flash_page, fixtures_dir):
    """127.0.0.1 不依赖 DNS，FlashParams 应带 remote_addr。"""
    from ui.widgets.remote_host import REMOTE_ITEM_TEXT

    flash_page._on_jlink_burners_enumerated("")
    idx = flash_page.cmb_burner.findText(REMOTE_ITEM_TEXT)
    flash_page.cmb_burner.setCurrentIndex(idx)
    flash_page.le_remote_host.setText("127.0.0.1")
    flash_page.le_remote_port.setText("19020")
    flash_page._select_file(str(fixtures_dir / "blink.bin"))
    flash_page.cmb_device.setCurrentText("STM32H750VB")
    _process()

    params = None

    def capture(p):
        nonlocal params
        params = p

    flash_page._worker.set_pending_params = capture
    flash_page._on_start_flash()
    _process()

    assert params is not None
    assert params.remote_addr == "127.0.0.1:19020"
    assert params.jlink_serial == ""


# ---- remote_host helper 单元 ----


def test_resolve_remote_host_ipv4_literal():
    from ui.widgets.remote_host import resolve_remote_host

    assert resolve_remote_host("127.0.0.1") == "127.0.0.1"


def test_resolve_remote_host_localhost_returns_ipv4():
    from ui.widgets.remote_host import resolve_remote_host

    ip = resolve_remote_host("localhost")
    assert ip is not None
    parts = ip.split(".")
    assert len(parts) == 4


def test_resolve_remote_host_invalid_returns_none():
    from ui.widgets.remote_host import resolve_remote_host

    assert resolve_remote_host("not a host!!") is None
    assert resolve_remote_host("") is None


def test_is_valid_port():
    from ui.widgets.remote_host import is_valid_port

    assert is_valid_port("19020") is True
    assert is_valid_port("1") is True
    assert is_valid_port("65535") is True
    assert is_valid_port("0") is False
    assert is_valid_port("65536") is False
    assert is_valid_port("abc") is False
    assert is_valid_port("") is False


def test_tcp_reachable_localhost_refused_port():
    """本地未监听端口应返回 False；不依赖外部网络。"""
    import socket

    from ui.widgets.remote_host import tcp_reachable

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    assert tcp_reachable("127.0.0.1", port, timeout=0.5) is False


def test_reboot_offline_placeholder_uses_cached_product(flash_page):
    """重启软件后 cfg 残留 serial、设备不在线，缓存应提供 kind/product，
    避免 label 退化为裸 serial。
    """
    SN = "003700393038510C34343436"
    flash_page._cfg.set(
        "flash_burner_cache", {SN: {"kind": "cmsisdap", "product": "H7-TOOL CMSIS-DAP"}}
    )
    flash_page._cfg.set("flash_jlink_serial", SN)
    flash_page._cfg.set("flash_jlink_mode", "usb")
    flash_page._burner_initialized = False
    flash_page._selected_serial = ""
    flash_page._selected_kind = ""
    flash_page._selected_product = ""
    flash_page._last_burner_enum_state = None

    # 重启后首次枚举，设备不在线
    flash_page._on_pyocd_burners_enumerated("")
    _process()

    assert flash_page._current_burner() == ("cmsisdap", SN)
    assert flash_page.cmb_burner.currentText() == "CMSIS-DAP (H7-TOOL CMSIS-DAP): " + SN
    assert flash_page.cmb_burner.isReadOnly()
    assert not flash_page._burner_status_dot.isHidden()


def test_reboot_offline_placeholder_updates_cache_on_replug(flash_page):
    """重启后离线占位用缓存；设备插上后用新 product 更新缓存和 label。"""
    SN = "003700393038510C34343436"
    flash_page._cfg.set("flash_burner_cache", {SN: {"kind": "cmsisdap", "product": "OldProduct"}})
    flash_page._cfg.set("flash_jlink_serial", SN)
    flash_page._cfg.set("flash_jlink_mode", "usb")
    flash_page._burner_initialized = False
    flash_page._selected_serial = ""
    flash_page._selected_kind = ""
    flash_page._selected_product = ""
    flash_page._last_burner_enum_state = None

    # 离线占位显示缓存里的旧名称
    flash_page._on_pyocd_burners_enumerated("")
    _process()
    assert flash_page.cmb_burner.currentText() == "CMSIS-DAP (OldProduct): " + SN

    # 插上，pyOCD 读出最新 product
    flash_page._on_pyocd_burners_enumerated(f"cmsisdap|{SN}|H7-TOOL CMSIS-DAP")
    _process()
    assert flash_page.cmb_burner.currentText() == "CMSIS-DAP (H7-TOOL CMSIS-DAP): " + SN
    assert flash_page.cmb_burner.isReadOnly() is False
    assert flash_page._burner_status_dot.isHidden()
    # 缓存已更新
    assert flash_page._cfg.get("flash_burner_cache")[SN]["product"] == "H7-TOOL CMSIS-DAP"


def test_offline_cfg_serial_then_replug_clears_status(flash_page):
    """cfg 残留 CMSIS-DAP serial 且重启时设备离线（红点 + 占位 + 只读），
    再插上后红点应隐藏、_current_burner 返回正确 (kind, serial)。

    断言：再插上后 _current_burner() 通过 _selected_serial 真源返回裸 serial（而非
    combo label 整串），从而通过 "烧录器在线" 校验。
    """
    SN = "003700393038510C34343436"
    # 模拟重启：cfg 残留上次 serial，且 burner 状态回到首次重建前
    flash_page._cfg.set("flash_jlink_serial", SN)
    flash_page._cfg.set("flash_jlink_mode", "usb")
    flash_page._burner_initialized = False
    flash_page._selected_serial = ""
    flash_page._selected_kind = ""
    flash_page._last_burner_enum_state = None

    # 重启场景：设备离线 -> 占位 + 红点 + 只读
    flash_page._on_pyocd_burners_enumerated("")
    _process()
    assert flash_page._current_burner()[1] == SN  # serial 保留（离线，kind 空）
    assert flash_page.cmb_burner.isReadOnly()  # 离线只读

    # 再插上 CMSIS-DAP
    flash_page._on_pyocd_burners_enumerated(f"cmsisdap|{SN}|DAPLink")
    _process()
    # 在线：_current_burner 返回正确 kind+serial（不是整条 label）
    assert flash_page._current_burner() == ("cmsisdap", SN)
    assert not flash_page.cmb_burner.isReadOnly()  # 在线可写
    # 红点应隐藏（不在线才显示）
    assert not flash_page._burner_status_dot.isVisible()


def test_offline_cfg_holds_placeholder_when_other_devices_online(flash_page):
    """cfg 残留 serial 不在线且存在其它在线设备时，保持 cfg serial 离线占位 + 红点 + 只读，
    不自动切换到在线设备。

    场景：flash_jlink_serial 残留旧 serial，当前接入的是 CMSIS-DAP（serial 不同）。
    期望：保持旧 serial 占位（只读 + 红点），切换由用户在 combo 中主动选择。
    """
    DEVICE_SN = "003700393038510C34343436"
    flash_page._cfg.set("flash_jlink_serial", "STALE_JLINK_99999")
    flash_page._cfg.set("flash_jlink_mode", "usb")
    flash_page._burner_initialized = False
    flash_page._selected_serial = ""
    flash_page._selected_kind = ""
    flash_page._last_burner_enum_state = None

    flash_page._on_pyocd_burners_enumerated(f"cmsisdap|{DEVICE_SN}|DAPLink")
    _process()
    # 保持 cfg serial 占位，不切到在线 CMSIS-DAP
    assert flash_page._current_burner() == ("", "STALE_JLINK_99999")
    assert flash_page.cmb_burner.isReadOnly()  # 离线只读
    assert not flash_page._burner_status_dot.isHidden()  # 红点显示


def test_selected_device_disconnected_holds_placeholder(flash_page):
    """用户主动选中某设备后拔掉它（其它烧录器仍在线），combo 保持原选中占位 + 红点，
    不自动切换到其它在线设备。重插后由在线分支自动选中回来。
    """
    # 三台烧录器同时接入：J-Link + ST-Link + CMSIS-DAP
    flash_page._on_jlink_burners_enumerated("1234567890|JLink")
    flash_page._on_pyocd_burners_enumerated("stlink|ST_SN|STLinkV3;cmsisdap|DAP_SN|DAPLink")
    _process()

    # 用户主动选中 ST-Link
    st_idx = flash_page._find_burner_index_by_serial("ST_SN")
    flash_page.cmb_burner.setCurrentIndex(st_idx)
    flash_page.cmb_burner.setText(flash_page.cmb_burner.itemText(st_idx))
    flash_page._on_burner_selection_changed()
    _process()
    assert flash_page._current_burner() == ("stlink", "ST_SN")
    assert flash_page.cmb_burner.currentText() == "ST-Link (STLinkV3): ST_SN"
    assert flash_page._burner_status_dot.isHidden()  # 在线无红点

    # 拔掉 ST-Link（J-Link + CMSIS-DAP 仍在线）
    flash_page._on_pyocd_burners_enumerated("cmsisdap|DAP_SN|DAPLink")
    _process()
    # 保持 ST-Link 占位（kind/product 保留 -> label 带前缀），不切到别的
    assert flash_page._current_burner() == ("stlink", "ST_SN")
    assert flash_page.cmb_burner.currentText() == "ST-Link (STLinkV3): ST_SN"
    assert flash_page.cmb_burner.isReadOnly()
    assert not flash_page._burner_status_dot.isHidden()  # 红点提示离线

    # 重插 ST-Link -> 自动选中回来
    flash_page._on_pyocd_burners_enumerated("stlink|ST_SN|STLinkV3;cmsisdap|DAP_SN|DAPLink")
    _process()
    assert flash_page._current_burner() == ("stlink", "ST_SN")
    assert flash_page.cmb_burner.currentText() == "ST-Link (STLinkV3): ST_SN"
    assert flash_page._burner_status_dot.isHidden()


def test_burner_selection_changed_uses_current_text_not_index(flash_page):
    """_on_burner_selection_changed 用 currentText 反查 item，不靠 currentIndex。

    模拟 qfluent 偶发：currentText 是 DAPLink label，但 currentIndex 指向 ST-Link。
    旧实现按 currentIndex 取 itemData 会把真源同步成 ST-Link；新实现按文本反查，
    真源应变为 DAPLink。
    """
    flash_page._on_pyocd_burners_enumerated("stlink|ST_SN|STLinkV3;cmsisdap|DAP_SN|DAPLink")
    _process()

    # 先选中 ST-Link（真源归位）
    st_idx = flash_page._find_burner_index_by_serial("ST_SN")
    flash_page.cmb_burner.setCurrentIndex(st_idx)
    flash_page.cmb_burner.setText(flash_page.cmb_burner.itemText(st_idx))
    flash_page._on_burner_selection_changed()
    _process()
    assert flash_page._current_burner() == ("stlink", "ST_SN")

    # 模拟 qfluent desync：currentText 强行写成 DAPLink label，但 currentIndex 保持 ST-Link
    dap_idx = flash_page._find_burner_index_by_serial("DAP_SN")
    dap_text = flash_page.cmb_burner.itemText(dap_idx)
    flash_page.cmb_burner.blockSignals(True)
    flash_page.cmb_burner.setCurrentIndex(st_idx)  # index 仍指 ST-Link
    flash_page.cmb_burner.setText(dap_text)  # 文本是 DAPLink
    flash_page.cmb_burner.blockSignals(False)

    flash_page._on_burner_selection_changed()
    _process()
    assert flash_page._current_burner() == ("cmsisdap", "DAP_SN")


def test_current_burner_reads_truth_source_not_combo(flash_page):
    """_current_burner() 只读真源（_selected_*），不读 combo currentIndex/itemData。

    锁住单向数据流设计：combo 重建后 currentIndex/currentText 偶发不同步（CLAUDE.md），
    读 combo 会引入 stale 命中 -> 偶发"自动切换"。_current_burner() 只读真源根除。
    """
    flash_page._on_pyocd_burners_enumerated("stlink|ST_SN|STLinkV3;cmsisdap|DAP_SN|DAPLink")
    _process()
    st_idx = flash_page._find_burner_index_by_serial("ST_SN")
    flash_page.cmb_burner.setCurrentIndex(st_idx)
    flash_page.cmb_burner.setText(flash_page.cmb_burner.itemText(st_idx))
    flash_page._on_burner_selection_changed()
    _process()
    assert flash_page._current_burner() == ("stlink", "ST_SN")

    # 模拟 qfluent 偶发：combo 状态完全指向 DAPLink（currentIndex + currentText 都是 DAPLink）
    dap_idx = flash_page._find_burner_index_by_serial("DAP_SN")
    flash_page.cmb_burner.blockSignals(True)
    flash_page.cmb_burner.setCurrentIndex(dap_idx)
    flash_page.cmb_burner.setText(flash_page.cmb_burner.itemText(dap_idx))
    flash_page.cmb_burner.blockSignals(False)

    # _current_burner() 只读真源 ST-Link，不读 combo -> 不返回 DAPLink
    assert flash_page._current_burner() == ("stlink", "ST_SN")


def test_start_flash_offline_placeholder_warns_not_online(flash_page, fixtures_dir):
    """离线占位时点烧录应被"不在线"拦截，不 emit flash_requested。"""
    SN = "003700393038510C34343436"
    flash_page._cfg.set("flash_jlink_serial", SN)
    flash_page._cfg.set("flash_jlink_mode", "usb")
    flash_page._burner_initialized = False
    flash_page._selected_serial = ""
    flash_page._selected_kind = ""
    flash_page._last_burner_enum_state = None
    flash_page._on_pyocd_burners_enumerated("")  # 离线占位
    _process()

    flash_page._select_file(str(fixtures_dir / "blink.bin"))
    # STM32F030C8 在 chip_models 里，setCurrentText 对 in-items 值可靠同步
    # （STM32F030C8T6 不在列表 -> setCurrentText 是 no-op，CLAUDE.md）。
    flash_page.cmb_device.setCurrentText("STM32F030C8")
    _process()

    spy = _SignalSpy(flash_page._worker.flash_requested)
    flash_page.btn_flash.click()
    _process()
    assert spy.count == 0  # 离线 -> 不发 flash_requested


def test_start_flash_after_replug_emits_flash_requested(flash_page, fixtures_dir):
    """离线占位 -> 插上 -> 点烧录应通过"在线"检查并 emit flash_requested。"""
    SN = "003700393038510C34343436"
    flash_page._cfg.set("flash_jlink_serial", SN)
    flash_page._cfg.set("flash_jlink_mode", "usb")
    flash_page._burner_initialized = False
    flash_page._selected_serial = ""
    flash_page._selected_kind = ""
    flash_page._last_burner_enum_state = None
    flash_page._on_pyocd_burners_enumerated("")  # 离线
    _process()
    flash_page._on_pyocd_burners_enumerated(f"cmsisdap|{SN}|DAPLink")  # 插上
    _process()

    flash_page._select_file(str(fixtures_dir / "blink.bin"))
    flash_page.cmb_device.setCurrentText("STM32F030C8")  # in-items，可靠同步
    _process()

    spy = _SignalSpy(flash_page._worker.flash_requested)
    flash_page.btn_flash.click()
    _process()
    assert spy.count == 1  # 在线 -> 发 flash_requested
