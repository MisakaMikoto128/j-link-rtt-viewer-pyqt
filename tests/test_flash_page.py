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
    """枚举后远程项固定在下拉最末。

    新分组下拉：["── J-Link ──", "J-Link: 111", "J-Link: 222", "远程连接"]。
    """
    from ui.widgets.remote_host import REMOTE_ITEM_TEXT
    flash_page._on_jlink_burners_enumerated("111|A;222|B")
    _process()
    assert flash_page.cmb_burner.count() == 4
    assert flash_page.cmb_burner.itemText(0) == "── J-Link ──"
    assert flash_page.cmb_burner.itemText(1) == "J-Link: 111"
    assert flash_page.cmb_burner.itemText(2) == "J-Link: 222"
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
    assert flash_page.cmb_burner.isReadOnly()      # 离线只读

    # 再插上 CMSIS-DAP
    flash_page._on_pyocd_burners_enumerated(f"cmsisdap|{SN}|DAPLink")
    _process()
    # 在线：_current_burner 返回正确 kind+serial（不是整条 label）
    assert flash_page._current_burner() == ("cmsisdap", SN)
    assert not flash_page.cmb_burner.isReadOnly()   # 在线可写
    # 红点应隐藏（不在线才显示）
    assert not flash_page._burner_status_dot.isVisible()


def test_stale_cfg_serial_auto_selects_online_device(flash_page):
    """cfg 持有与当前在线设备不同的 serial（用户更换烧录器型号）时，
    设备在线应自动选中在线设备，不停留在离线占位。

    场景：flash_jlink_serial 残留 J-Link serial，当前接入的是 CMSIS-DAP（serial 不同）。
    期望：自动选中在线 CMSIS-DAP，combo 可写、红点隐藏。
    """
    DEVICE_SN = "003700393038510C34343436"
    flash_page._cfg.set("flash_jlink_serial", "STALE_JLINK_99999")  # 旧 J-Link serial
    flash_page._cfg.set("flash_jlink_mode", "usb")
    flash_page._burner_initialized = False
    flash_page._selected_serial = ""
    flash_page._selected_kind = ""
    flash_page._last_burner_enum_state = None

    # CMSIS-DAP 在线（serial 与 cfg 的 J-Link serial 不同）
    flash_page._on_pyocd_burners_enumerated(f"cmsisdap|{DEVICE_SN}|DAPLink")
    _process()
    # 应自动选中在线 CMSIS-DAP，而非卡在离线占位 "STALE_JLINK_99999"
    assert flash_page._current_burner() == ("cmsisdap", DEVICE_SN)
    assert not flash_page.cmb_burner.isReadOnly()  # 在线可写
    assert not flash_page._burner_status_dot.isVisible()  # 红点隐藏


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
