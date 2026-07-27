"""固件烧录页：独立 FlashWorker + 独立 QThread，不干涉 RTT/Memory。

UI 布局（4 个 Card，透明 ScrollArea 整页包裹）：
1. 连接参数 — device / interface / speed
2. 固件文件 — file picker + 最近 10 + 拖放 + 解析后 format/range/size
3. 烧录选项 — erase_mode / post_action / extra_verify
4. 执行 — 大按钮 + ProgressBar + 阶段文字 + 可折叠详情面板

参数持久化：cfg.flash_*。
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, Qt, QThread, QThreadPool, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    DotInfoBadge,
    EditableComboBox,
    InfoLevel,
    LineEdit,
    MessageBox,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    StrongBodyLabel,
)

from core.config_service import ConfigService
from core.flash_worker import (
    BURNER_KIND_JLINK,
    ERASE_MODE_CHIP,
    ERASE_MODE_SECTOR,
    FORMAT_BIN,
    FORMAT_ELF,
    POST_ACTION_HALT,
    POST_ACTION_NONE,
    POST_ACTION_RESET_RUN,
    FlashParams,
    FlashWorker,
)
from core.target_discovery import (
    TargetDeviceInfo,
    read_cached_target_infos_for_burner_kind,
    read_cached_target_names_for_burner_kind,
)

from . import _infobar
from ._scroll_helpers import make_transparent_scroll
from ._ui_helpers import tip
from .firmware_analysis_view import FirmwareAnalysisView, FlashOccupancyBar
from .ob_card import OptionBytesCard
from .widgets.remote_host import (
    REMOTE_ITEM_TEXT,
    is_valid_port,
    resolve_remote_host,
)
from .widgets.remote_probe import RemoteProbeHelper, TcpReachableRunnable
from .widgets.target_combo_box import TargetComboBox

_ERASE_LABELS = [
    ("扇区擦除（推荐，快）", ERASE_MODE_SECTOR),
    ("整片擦除（慢，更干净）", ERASE_MODE_CHIP),
]
_POST_LABELS = [
    ("仅烧录", POST_ACTION_NONE),
    ("烧录后暂停", POST_ACTION_HALT),
    ("烧录后复位运行", POST_ACTION_RESET_RUN),
]


class FlashPage(QWidget):
    def __init__(self, cfg: ConfigService, rtt_worker=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("flashPage")
        self._cfg = cfg
        self._rtt_worker = rtt_worker
        self._rtt_page_ref: QWidget | None = None
        self._is_running = False
        self._stage_key = "idle"  # 用于 _retranslate_ui 重置 lbl_stage
        self._parse_state = "empty"  # "empty" | "error" | "ok"

        # 与 RTT 页协调：烧录前先断同一台 J-Link 的 RTT，烧完回连
        self._resume_rtt_after_flash = False
        self._last_post_action = POST_ACTION_NONE
        self._pyocd_targets_enumerated = (
            False  # 切到 CMSIS-DAP/ST-Link 时按需触发 pyOCD target 枚举
        )
        self._rtt_pending_disconnect = False
        self._rtt_disconnect_timeout_timer: QTimer | None = None
        self._rtt_resume_remote_addr = ""

        # 远程 J-Link 模式状态
        self._remote_mode = False
        self._remote_reachable: bool | None = None
        self._remote_probe = RemoteProbeHelper()
        self._remote_probe.probe_done.connect(self._on_remote_probe_done)
        self._remote_probe_in_flight = False

        # burner 下拉两源：J-Link 来自 rtt_worker.devices_enumerated（pylink），
        # ST-Link / CMSIS-DAP 来自 worker.pyocd_probes_enumerated（pyOCD）。
        # _rebuild_burner_combo 合并两源按类型分组重建。
        self._jlink_serials: list[str] = []
        self._jlink_products: dict[str, str] = {}
        self._pyocd_probes: list[tuple[str, str, str]] = []  # (kind, serial, product)
        self._last_burner_enum_state: tuple | None = None
        self._burner_initialized = False
        # 选中烧录器的 programmatic 真源（kind, serial）。
        # qfluentwidgets EditableComboBox 在 clear+addItem+setCurrentIndex 后
        # currentIndex()/currentText() 偶发不同步（CLAUDE.md），仅靠 combo 状态
        # 解析会把整条 label 当 serial -> 红点持续显示 + "烧录器不在线"。这里存真源，
        # _current_burner() 在 combo 状态不可靠时回退到它。
        self._selected_serial: str = ""
        self._selected_kind: str = ""
        self._selected_product: str = ""

        # 固件文件变化监控：QFileSystemWatcher 监听当前选中固件的 mtime 变化（系统级文件
        # 事件，非轮询）。编译器写文件通常先 unlink+rename 原子替换，watcher 会 emit
        # fileChanged；为应对「删旧建新」导致 watcher 失效，辅以 1s 防抖 timer 复查 mtime
        # 并按需重新 addPath。检测到变化时：橙色提示 + （若启用自动烧录）触发烧录。
        # _auto_burn_armed：刚选中/烧录完成后置 False，避免同一变化重复触发自动烧录；
        # 仅下一次 fileChanged 事件才触发。
        self._file_watcher: QFileSystemWatcher | None = None
        self._watched_path: str = ""
        self._watched_mtime: float = 0.0
        self._auto_burn_armed = False
        self._file_recheck_timer = QTimer(self)
        self._file_recheck_timer.setSingleShot(True)
        self._file_recheck_timer.setInterval(1000)
        self._file_recheck_timer.timeout.connect(self._recheck_file_mtime)

        # 独立 worker + 独立 QThread（和 JLinkWorker 完全无关）
        # FlashWorker 启动前确保 pyocd 预热完成（防 aggregator 扫描在 FlashWorker
        # 线程建 pylink.JLink、与 RTT worker 并发打 DLL 全局句柄 -> 0x14 崩，根因
        # 见 core.probe.enumerator docstring）。main.py 启动后台预热通常此时已完成，
        # wait 立即返回；极端情况（启动后立即切烧录页）wait 阻塞 ~1.2s 兜底。
        # FlashPage 懒构造（_LazyPageWrapper），本 __init__ 在用户首次切烧录页时跑。
        from core.probe.enumerator import wait_for_pyocd_prepare

        wait_for_pyocd_prepare()
        self._thread = QThread(self)
        self._worker = FlashWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.initialize)
        self._thread.start()

        # 拖放
        self.setAcceptDrops(True)

        # 外层：透明 scroll
        scroll, inner = make_transparent_scroll(self, "flash")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # inner 主 layout
        v = QVBoxLayout(inner)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(12)

        v.addWidget(self._build_conn_options_row())
        v.addWidget(self._build_file_card())
        v.addWidget(self._build_run_card())
        v.addWidget(self._build_option_bytes_card())
        v.addWidget(self._build_symbol_card())
        v.addStretch(1)

        self._connect_signals()
        self._load_prefs_into_controls()
        # 初始化 Flash 占用条的 device info（prefs 已回填 cmb_device）
        self._on_target_device_changed(self.cmb_device.currentText())

    # ---- card builders (占位，下一 Task 填实) ----
    def _build_conn_options_row(self) -> QWidget:
        """「连接参数」+「烧录选项」两卡片同一行，等高（取较高者），不合并卡片。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        conn = self._build_conn_card()
        opts = self._build_options_card()
        # 内容少的卡片加 stretch 撑高，使两卡片视觉等高
        conn.layout().addStretch(1)
        opts.layout().addStretch(1)
        h.addWidget(conn, 1)
        h.addWidget(opts, 1)
        return row

    def _build_conn_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        self.lbl_conn_title = StrongBodyLabel(self.tr("连接参数"))
        layout.addWidget(self.lbl_conn_title)

        # ---- 烧录器选择（多 J-Link 接入时选哪台）----
        row_jlink = QHBoxLayout()
        row_jlink.setSpacing(6)
        row_jlink.setContentsMargins(0, 0, 0, 0)
        self._lbl_burner = BodyLabel(self.tr("烧录器:"))
        self._lbl_burner.setFixedHeight(33)
        row_jlink.addWidget(self._lbl_burner)

        self._burner_status_dot = DotInfoBadge(card)
        self._burner_status_dot.setLevel(InfoLevel.ERROR)
        self._burner_status_dot.setFixedSize(8, 8)
        self._burner_status_dot.hide()
        row_jlink.addWidget(self._burner_status_dot, alignment=Qt.AlignVCenter)

        self.cmb_burner = EditableComboBox(card)
        self.cmb_burner.setFixedHeight(33)
        self.cmb_burner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.cmb_burner.setMinimumWidth(50)
        row_jlink.addWidget(self.cmb_burner, 1)
        layout.addLayout(row_jlink)

        # ---- 远程主机输入行（选中「远程连接…」时显示）----
        self.remote_row = QWidget(card)
        remote_layout = QHBoxLayout(self.remote_row)
        remote_layout.setSpacing(6)
        remote_layout.setContentsMargins(0, 0, 0, 0)
        self._lbl_remote_host = BodyLabel(self.tr("远程主机:"), self.remote_row)
        self._lbl_remote_host.setFixedHeight(33)
        remote_layout.addWidget(self._lbl_remote_host)
        self.le_remote_host = LineEdit(self.remote_row)
        self.le_remote_host.setFixedHeight(33)
        self.le_remote_host.setPlaceholderText(self.tr("IP 或域名，如 192.168.79.1"))
        remote_layout.addWidget(self.le_remote_host, 1)
        self._lbl_remote_port = BodyLabel(self.tr("端口:"), self.remote_row)
        self._lbl_remote_port.setFixedHeight(33)
        remote_layout.addWidget(self._lbl_remote_port)
        self.le_remote_port = LineEdit(self.remote_row)
        self.le_remote_port.setFixedHeight(33)
        self.le_remote_port.setPlaceholderText("19020")
        self.le_remote_port.setMaximumWidth(80)
        remote_layout.addWidget(self.le_remote_port)
        layout.addWidget(self.remote_row)
        self.remote_row.setVisible(False)

        row = QHBoxLayout()
        self.lbl_device = BodyLabel(self.tr("目标设备:"))
        row.addWidget(self.lbl_device)
        self.cmb_device = TargetComboBox(self._cfg, "flash_device_history")
        # 设备列表：直接读磁盘缓存（零 DLL 调用，主线程安全，永不枚举）。
        # 不依赖 worker 时序：缓存命中即有候选；缓存空时由 _connect_signals 末尾
        # 主动再读一次 + worker 的 target_infos_ready 信号兜底。
        kind = (
            self._selected_kind
            if self._selected_kind and self._selected_kind != "remote"
            else BURNER_KIND_JLINK
        )
        self.cmb_device.set_names_provider(
            lambda: list(read_cached_target_names_for_burner_kind(kind))
        )
        self.cmb_device.set_target_info_lookup(self._lookup_target_info)
        self.cmb_device.restore_text(str(self._cfg.get("flash_device_name") or ""))
        self.cmb_device.textChanged.connect(self._on_target_device_changed)
        row.addWidget(self.cmb_device, 1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("Interface:"))
        self.rb_swd = RadioButton("SWD")
        self.rb_jtag = RadioButton("JTAG")
        row2.addWidget(self.rb_swd)
        row2.addWidget(self.rb_jtag)
        row2.addSpacing(20)
        row2.addWidget(BodyLabel("Speed (kHz):"))
        # 与 RTT 监控页完全一致：非编辑 ComboBox + 默认速度列表
        self.cmb_speed = ComboBox()
        for s in self._cfg.get_default_speeds():
            self.cmb_speed.addItem(str(s))
        row2.addWidget(self.cmb_speed)
        row2.addStretch(1)
        layout.addLayout(row2)
        return card

    def _lookup_target_info(self, name: str) -> TargetDeviceInfo | None:
        """按名称从当前烧录器 kind 对应的设备库查询 TargetDeviceInfo。

        缓存未就绪（worker 线程枚举未完成）时返回 None——此时调
        target_infos_for_burner_kind 会在主线程触发 supported_device 枚举，
        损坏 J-Link DLL TLS → worker connect 崩 0x14。
        """
        name = name.strip().upper()
        if not name:
            return None
        kind = (
            self._selected_kind
            if self._selected_kind and self._selected_kind != "remote"
            else BURNER_KIND_JLINK
        )
        for info in read_cached_target_infos_for_burner_kind(kind):
            if info.name == name:
                return info
        return None

    def _on_target_infos_ready(self) -> None:
        """worker 线程 target_discovery 枚举完成 → 回填设备下拉（命中缓存，不重复枚举）。"""
        self._refresh_device_combo()

    def _refresh_device_combo(self) -> None:
        """按当前烧录器 kind 刷新目标设备下拉的数据源（只读缓存，不枚举）。"""
        kind = (
            self._selected_kind
            if self._selected_kind and self._selected_kind != "remote"
            else BURNER_KIND_JLINK
        )
        self.cmb_device.set_names_provider(
            lambda: list(read_cached_target_names_for_burner_kind(kind))
        )
        self.cmb_device.refresh_tooltip()
        # kind 变化可能换设备库 -> 重查当前设备的 Flash 容量
        self._on_target_device_changed(self.cmb_device.currentText())

    def _build_file_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        self.lbl_file_title = StrongBodyLabel(self.tr("固件文件"))
        layout.addWidget(self.lbl_file_title)

        row = QHBoxLayout()
        row.addWidget(BodyLabel("File:"))
        self.cmb_file = EditableComboBox()  # 最近 8 文件下拉
        self.cmb_file.setMinimumWidth(360)
        row.addWidget(self.cmb_file, 1)
        self.btn_browse = PushButton(self.tr("浏览…"))
        row.addWidget(self.btn_browse)
        self.btn_save_as = PushButton(self.tr("另存为…"))
        self.btn_save_as.setToolTip(self.tr("把当前固件转换为 .bin / .hex 另存"))
        row.addWidget(self.btn_save_as)
        layout.addLayout(row)

        # format + range + 固件变化状态指示（放在 range 行末尾）
        # 状态色：绿=正常（已识别最新）/ 橙=检测到固件已更新（mtime 比对）。
        # 附带文件修改日期（不翻译日期格式）。
        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("Format:"))
        self.lbl_format = BodyLabel(self.tr("(无)"))
        row2.addWidget(self.lbl_format)
        row2.addSpacing(20)
        row2.addWidget(BodyLabel("Range:"))
        self.lbl_range = BodyLabel(self.tr("(无)"))
        row2.addWidget(self.lbl_range, 1)
        self.lbl_mtime_flag = BodyLabel("")
        row2.addWidget(self.lbl_mtime_flag)
        layout.addLayout(row2)

        # row3：bin 起始地址 + 「固件变化后自动烧录」RadioButton
        row3 = QHBoxLayout()
        self.lbl_bin_addr = BodyLabel(self.tr("Bin 起始地址:"))
        row3.addWidget(self.lbl_bin_addr)
        self.edit_bin_addr = LineEdit()
        self.edit_bin_addr.setPlaceholderText("0x08000000")
        self.edit_bin_addr.setMaximumWidth(180)
        row3.addWidget(self.edit_bin_addr)
        row3.addStretch(1)
        self.rb_auto_burn = RadioButton(self.tr("固件变化后自动烧录"))
        tip(self.rb_auto_burn, self.tr("使能后每次固件重新编译发生变化时自动触发烧录"))
        self.rb_auto_burn.setChecked(bool(self._cfg.get("auto_burn_on_change")))
        row3.addWidget(self.rb_auto_burn)
        layout.addLayout(row3)

        # Flash 占用条：加载固件（任意格式）后显示其在目标 Flash 中的位置/占比，
        # 占用区颜色跟随主题色；无固件时无占用区。
        self.flash_bar = FlashOccupancyBar()
        layout.addWidget(self.flash_bar)
        return card

    def _build_options_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        self.lbl_options_title = StrongBodyLabel(self.tr("烧录选项"))
        layout.addWidget(self.lbl_options_title)

        row = QHBoxLayout()
        self.lbl_erase = BodyLabel(self.tr("擦除模式:"))
        row.addWidget(self.lbl_erase)
        self.cmb_erase = ComboBox()
        for label, _ in _ERASE_LABELS:
            self.cmb_erase.addItem(self.tr(label))
        row.addWidget(self.cmb_erase, 1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        self.lbl_post = BodyLabel(self.tr("完成动作:"))
        row2.addWidget(self.lbl_post)
        self.cmb_post = ComboBox()
        for label, _ in _POST_LABELS:
            self.cmb_post.addItem(self.tr(label))
        row2.addWidget(self.cmb_post, 1)
        layout.addLayout(row2)

        self.chk_verify = CheckBox(self.tr("额外 byte-by-byte verify（慢一倍）"))
        layout.addWidget(self.chk_verify)
        return card

    def _build_run_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)

        self.btn_flash = PrimaryPushButton(self.tr("开始烧录"))
        self.btn_flash.setMinimumHeight(36)
        layout.addWidget(self.btn_flash)

        # 全片擦除：复用烧录完整流程（连接→RTT协调→整片擦除→断开），不烧录。
        self.btn_erase_chip = PushButton(self.tr("全片擦除"))
        self.btn_erase_chip.setMinimumHeight(32)
        layout.addWidget(self.btn_erase_chip)

        # 自动保护选项 (水平排布; checkbox 自然宽度, tooltip 居中在 checkbox 上方)
        auto_row = QHBoxLayout()
        self.chk_auto_unlock_rdp = CheckBox(self.tr("自动解除读写保护"))
        tip(self.chk_auto_unlock_rdp, self.tr(
            "烧录前自动解除 RDP 读保护 (L1->L0, 触发 mass erase) + WRP 写保护"
        ))
        auto_row.addWidget(self.chk_auto_unlock_rdp)
        self.chk_auto_add_rdp = CheckBox(self.tr("自动添加读取保护"))
        tip(self.chk_auto_add_rdp, self.tr(
            "烧录完成后自动设置 RDP = L1 (读保护生效; 下次烧录需先解除)"
        ))
        auto_row.addWidget(self.chk_auto_add_rdp)
        self.chk_auto_add_wrp = CheckBox(self.tr("自动添加写入保护"))
        tip(self.chk_auto_add_wrp, self.tr(
            "烧录完成后自动启用写保护 WRP (防误擦写; 需先解除才能再烧录)"
        ))
        auto_row.addWidget(self.chk_auto_add_wrp)
        auto_row.addSpacing(12)
        self.btn_reset = PushButton(self.tr("复位"))
        tip(self.btn_reset, self.tr("复位目标芯片 (兼容所有烧录器; 不烧录不擦除)"))
        self.btn_reset.clicked.connect(lambda: self.ob_card.do_reset())
        auto_row.addWidget(self.btn_reset)
        auto_row.addStretch()
        layout.addLayout(auto_row)

        row = QHBoxLayout()
        self.lbl_stage = BodyLabel(self.tr("待命"))
        row.addWidget(self.lbl_stage)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        row.addWidget(self.progress, 1)
        layout.addLayout(row)

        # 详情面板（折叠）
        row_det = QHBoxLayout()
        self.btn_toggle_log = PushButton(self.tr("▶ 详情"))
        self.btn_toggle_log.setFlat(True)
        row_det.addWidget(self.btn_toggle_log)
        self.btn_copy_log = PushButton(self.tr("复制日志"))
        row_det.addWidget(self.btn_copy_log)
        row_det.addStretch(1)
        layout.addLayout(row_det)

        self.txt_log = PlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(1000)
        # 详情文本框高度翻倍（默认约 6 行 ~100px -> 200px）
        self.txt_log.setMinimumHeight(200)
        self.txt_log.setVisible(False)
        layout.addWidget(self.txt_log)
        return card

    def _build_option_bytes_card(self) -> QWidget:
        """选项字 (RDP) 卡片 -- 数据驱动 (ST XML + SVD + family 表)。"""
        self.ob_card = OptionBytesCard(
            get_burner_params=self._ob_get_burner_params,
            rtt_worker=getattr(self, "_rtt_worker", None),
        )
        return self.ob_card

    def _ob_get_burner_params(self):
        """供 OB 卡片用:返回当前烧录器 + 设备参数 (kind, serial, device, iface, speed)。"""
        kind, serial = self._current_burner()
        if not kind:
            return None
        device = self.cmb_device.currentText().strip()
        if not device:
            return None
        interface = "SWD" if self.rb_swd.isChecked() else "JTAG"
        try:
            speed = int(self.cmb_speed.currentText().strip() or "4000", 10)
        except ValueError:
            speed = 4000
        return (kind, serial, device, interface, speed)

    def _build_symbol_card(self) -> QWidget:
        # 仅 axf/elf 时显示；其它格式 / 无文件时整卡隐藏
        self.symbol_card = CardWidget()
        layout = QVBoxLayout(self.symbol_card)
        self.analysis_view = FirmwareAnalysisView()
        self.analysis_view.setMinimumHeight(760)
        layout.addWidget(self.analysis_view)
        self.symbol_card.setVisible(False)
        return self.symbol_card

    # ---- 加载偏好到控件 ----
    def _load_prefs_into_controls(self) -> None:
        saved_device = str(self._cfg.get("flash_device_name") or "").strip()
        if saved_device and self.cmb_device.findText(saved_device) < 0:
            self.cmb_device.addItem(saved_device)
        self.cmb_device.setCurrentText(saved_device)
        iface = self._cfg.get("flash_interface")
        self.rb_swd.setChecked(iface == "SWD")
        self.rb_jtag.setChecked(iface == "JTAG")

        # speed：与 RTT 页一致——若保存值不在默认列表则补一项再选中
        cur_speed = str(int(self._cfg.get("flash_speed")))
        if self.cmb_speed.findText(cur_speed) < 0:
            self.cmb_speed.addItem(cur_speed)
        self.cmb_speed.setCurrentText(cur_speed)

        # 最近文件：重建下拉并选中第一个（阻塞信号，避免触发 currentTextChanged）
        recent = list(self._cfg.get("flash_recent_files") or [])
        self._rebuild_file_combo(recent)
        if recent:
            self._parse_and_show(recent[0], silent=True)

        # bin addr
        addr = int(self._cfg.get("flash_bin_address"))
        self.edit_bin_addr.setText(f"0x{addr:08X}")

        # erase mode
        em = self._cfg.get("flash_erase_mode")
        for i, (_, v) in enumerate(_ERASE_LABELS):
            if v == em:
                self.cmb_erase.setCurrentIndex(i)
                break

        # post action（旧配置 "reset" 迁移到 "halt"）
        pa = self._cfg.get("flash_post_action")
        if pa == "reset":
            pa = "halt"
            self._cfg.set("flash_post_action", pa)
        for i, (_, v) in enumerate(_POST_LABELS):
            if v == pa:
                self.cmb_post.setCurrentIndex(i)
                break

        self.chk_verify.setChecked(bool(self._cfg.get("flash_verify")))
        self.chk_auto_unlock_rdp.setChecked(bool(self._cfg.get("flash_auto_unlock_rdp")))
        self.chk_auto_add_rdp.setChecked(bool(self._cfg.get("flash_auto_add_rdp")))
        self.chk_auto_add_wrp.setChecked(bool(self._cfg.get("flash_auto_add_wrp")))

        # 远程主机输入（下拉重建时会按 flash_jlink_mode 决定是否显示）
        self.le_remote_host.setText(str(self._cfg.get("flash_remote_host") or ""))
        self.le_remote_port.setText(str(self._cfg.get("flash_remote_port") or ""))

    # ---- 信号连接 ----
    def _connect_signals(self) -> None:
        # 持久化
        self.cmb_device.currentTextChanged.connect(lambda s: self._cfg.set("flash_device_name", s))
        self.rb_swd.toggled.connect(lambda on: on and self._cfg.set("flash_interface", "SWD"))
        self.rb_jtag.toggled.connect(lambda on: on and self._cfg.set("flash_interface", "JTAG"))
        self.cmb_speed.currentTextChanged.connect(
            lambda s: self._cfg.set("flash_speed", int(s)) if s.strip() else None
        )
        self.edit_bin_addr.editingFinished.connect(self._on_bin_addr_changed)
        self.cmb_erase.currentIndexChanged.connect(
            lambda i: self._cfg.set("flash_erase_mode", _ERASE_LABELS[i][1])
        )
        self.cmb_post.currentIndexChanged.connect(
            lambda i: self._cfg.set("flash_post_action", _POST_LABELS[i][1])
        )
        self.chk_verify.toggled.connect(lambda v: self._cfg.set("flash_verify", bool(v)))
        self.chk_auto_unlock_rdp.toggled.connect(lambda v: self._cfg.set("flash_auto_unlock_rdp", bool(v)))
        self.chk_auto_add_rdp.toggled.connect(lambda v: self._cfg.set("flash_auto_add_rdp", bool(v)))
        self.chk_auto_add_wrp.toggled.connect(lambda v: self._cfg.set("flash_auto_add_wrp", bool(v)))
        self.rb_auto_burn.toggled.connect(
            lambda v: self._cfg.set("auto_burn_on_change", bool(v))
        )

        # 文件
        self.btn_browse.clicked.connect(self._on_browse)
        self.btn_save_as.clicked.connect(self._on_save_as)
        # 用户从下拉选择 / 手动输入路径回车 → 仅解析显示
        self.cmb_file.currentTextChanged.connect(self._on_file_text_changed)

        # 烧录器下拉
        _Qt = Qt
        self.cmb_burner.currentTextChanged.connect(self._on_burner_selection_changed)
        self.le_remote_host.textChanged.connect(self._trigger_remote_probe)
        self.le_remote_port.textChanged.connect(self._trigger_remote_probe)
        if self._rtt_worker is not None:
            self._rtt_worker.devices_enumerated.connect(
                self._on_jlink_burners_enumerated, _Qt.QueuedConnection
            )
            self._rtt_worker.connection_state_changed.connect(
                self._on_rtt_state_for_flash, _Qt.QueuedConnection
            )
            # target_discovery 枚举完成（worker 线程跑完）→ 回填设备下拉。
            # functools.cache 保证此时 target_infos_for_burner_kind 直接命中缓存，
            # 不重复枚举（也不会在主线程触发 supported_device → 崩 DLL）。
            # 测试的 FakeRttWorker 可能没这个信号——hasattr 守卫。
            if hasattr(self._rtt_worker, "target_infos_ready"):
                self._rtt_worker.target_infos_ready.connect(
                    self._on_target_infos_ready, _Qt.QueuedConnection
                )
                # connect 后主动再读一次磁盘缓存：worker 可能已跑完（emit 早于
                # connect 信号丢失），但缓存已写，主动读即拿到，摆脱时序竞态。
                self._on_target_infos_ready()
        # pyOCD 烧录器枚举（非 J-Link，FlashWorker worker 线程 1s tick）
        self._worker.pyocd_probes_enumerated.connect(
            self._on_pyocd_burners_enumerated, _Qt.QueuedConnection
        )
        # pyOCD target 库枚举完成（FlashWorker worker 线程）-> 回填设备下拉。
        # 切到 cmsisdap/stlink 时 read_cached_pyocd_* 命中 cache。
        # 测试 fixture 的 FakeFlashWorker 可能没此信号，hasattr 守卫。
        if hasattr(self._worker, "pyocd_target_infos_ready"):
            self._worker.pyocd_target_infos_ready.connect(
                self._on_target_infos_ready, _Qt.QueuedConnection
            )
            self._on_target_infos_ready()  # 主动调：worker 可能已跑完，读磁盘/进程 cache

        # 详情折叠
        self.btn_toggle_log.clicked.connect(self._toggle_log)
        self.btn_copy_log.clicked.connect(self._copy_log)

        # worker → ui（QueuedConnection 显式声明：CLAUDE.md 跨线程信号约定）
        self.btn_flash.clicked.connect(lambda: self._on_start_flash(False))
        self.btn_erase_chip.clicked.connect(self._on_erase_chip_clicked)
        self._worker.flash_started.connect(self._on_flash_started, _Qt.QueuedConnection)
        self._worker.flash_stage_changed.connect(self._on_stage_changed, _Qt.QueuedConnection)
        self._worker.flash_progress.connect(self._on_progress, _Qt.QueuedConnection)
        self._worker.flash_log.connect(self._on_log, _Qt.QueuedConnection)
        self._worker.flash_finished.connect(self._on_flash_finished, _Qt.QueuedConnection)

        # RTT 断开等待兜底 timer（单次 5s）
        self._rtt_disconnect_timeout_timer = QTimer(self)
        self._rtt_disconnect_timeout_timer.setSingleShot(True)
        self._rtt_disconnect_timeout_timer.setInterval(5000)
        self._rtt_disconnect_timeout_timer.timeout.connect(self._on_rtt_disconnect_timeout)

    def _on_rtt_disconnect_timeout(self) -> None:
        """5s 内 RTT 没断干净也继续烧。"""
        if self._rtt_pending_disconnect:
            self._rtt_pending_disconnect = False
            self._worker.flash_requested.emit()
            _infobar.warn(self, self.tr("提示"), self.tr("等待 RTT 断开超时，直接烧录"))

    def _on_bin_addr_changed(self) -> None:
        txt = self.edit_bin_addr.text().strip()
        try:
            v = int(txt, 0) if txt else 0
        except ValueError:
            _infobar.warn(
                self,
                self.tr("Bin 起始地址格式错误"),
                self.tr("无法解析为整数：{txt}").format(txt=txt),
            )
            return
        self._cfg.set("flash_bin_address", int(v))
        # 重解析当前文件以更新 range 显示
        cur = self.cmb_file.currentText().strip()
        if cur:
            self._parse_and_show(cur, silent=True)

    # ------------------------------------------------------------------
    # 烧录器选择（与 RTT 页同形态：下拉 + 红点 + 离线占位）
    # ------------------------------------------------------------------
    def _on_jlink_burners_enumerated(self, data: str) -> None:
        """data: 'serial|product;...'。"""
        serials: list[str] = []
        products: dict[str, str] = {}
        if data:
            for chunk in data.split(";"):
                if not chunk:
                    continue
                serial, _, product = chunk.partition("|")
                serial = serial.strip()
                product = product.strip()
                if serial and serial.isdigit():
                    serials.append(serial)
                    products[serial] = product
                    self._cache_burner(BURNER_KIND_JLINK, serial, product)
        self._jlink_serials = serials
        self._jlink_products = products
        self._rebuild_burner_combo()

    def _on_pyocd_burners_enumerated(self, data: str) -> None:
        """data: 'kind|serial|product;...'。"""
        probes: list[tuple[str, str, str]] = []
        if data:
            for chunk in data.split(";"):
                if not chunk:
                    continue
                parts = chunk.split("|", 2)
                if len(parts) < 2:
                    continue
                kind = parts[0].strip()
                serial = parts[1].strip()
                product = parts[2].strip() if len(parts) > 2 else ""
                if kind and serial:
                    probes.append((kind, serial, product))
                    self._cache_burner(kind, serial, product)
        self._pyocd_probes = probes
        self._rebuild_burner_combo()

    def _cache_burner(self, kind: str, serial: str, product: str) -> None:
        """把 (serial -> kind/product) 写入 cfg 缓存，供离线/重启时生成 label。"""
        if not serial:
            return
        cache: dict[str, dict] = dict(self._cfg.get("flash_burner_cache") or {})
        cache[serial] = {"kind": kind, "product": product}
        self._cfg.set("flash_burner_cache", cache)

    def _get_cached_burner(self, serial: str) -> tuple[str, str]:
        """按 serial 读缓存，返回 (kind, product)；无缓存返回 ('', '')。"""
        cache = self._cfg.get("flash_burner_cache") or {}
        info = cache.get(serial) or {}
        return (str(info.get("kind") or ""), str(info.get("product") or ""))

    def _rebuild_burner_combo(self) -> None:
        """合并 J-Link + pyOCD 两源重建 burner 下拉，按类型分组。"""
        new_state = (tuple(self._jlink_serials), tuple(self._pyocd_probes))
        if self._last_burner_enum_state == new_state and self._burner_initialized:
            self._sync_remote_mode_from_selection()
            self._sync_burner_status_dot()
            return
        self._last_burner_enum_state = new_state

        prev_serial = self._selected_serial
        if not prev_serial and not self._burner_initialized:
            prev_serial = str(self._cfg.get("flash_jlink_serial") or "").strip()
            self._burner_initialized = True

        want_remote = str(self._cfg.get("flash_jlink_mode") or "") == "remote"

        self.cmb_burner.blockSignals(True)
        self.cmb_burner.setReadOnly(False)
        try:
            self.cmb_burner.clear()
            # J-Link 分组
            if self._jlink_serials:
                self._add_separator_item(self.tr("── J-Link ──"))
                for s in self._jlink_serials:
                    label = self._burner_label("jlink", s, self._jlink_products.get(s, ""))
                    self._add_burner_item("jlink", s, label)
            # ST-Link 分组
            stlink_probes = [p for p in self._pyocd_probes if p[0] == "stlink"]
            if stlink_probes:
                self._add_separator_item(self.tr("── ST-Link ──"))
                for kind, serial, product in stlink_probes:
                    self._add_burner_item(kind, serial, self._burner_label(kind, serial, product))
            # CMSIS-DAP 分组
            dap_probes = [p for p in self._pyocd_probes if p[0] == "cmsisdap"]
            if dap_probes:
                self._add_separator_item(self.tr("── CMSIS-DAP ──"))
                for kind, serial, product in dap_probes:
                    self._add_burner_item(kind, serial, self._burner_label(kind, serial, product))
            # 远程连接尾项
            self.cmb_burner.addItem(REMOTE_ITEM_TEXT)

            # 恢复选中 + 写入真源 self._selected_serial/_selected_kind
            if want_remote:
                self.cmb_burner.setCurrentText(REMOTE_ITEM_TEXT)
                self._selected_serial = ""
                self._selected_kind = "remote"
                self._selected_product = ""
            elif prev_serial:
                idx = self._find_burner_index_by_serial(prev_serial)
                if idx >= 0:
                    self.cmb_burner.setCurrentIndex(idx)
                    self.cmb_burner.setText(self.cmb_burner.itemText(idx))
                    self._selected_serial = prev_serial
                    self._selected_kind = self._lookup_burner_kind(prev_serial)
                    self._selected_product = self._lookup_burner_product(prev_serial)
                else:
                    cached_kind, cached_product = self._get_cached_burner(prev_serial)
                    self._selected_kind = cached_kind
                    self._selected_product = cached_product
                    self.cmb_burner.setCurrentIndex(-1)
                    self.cmb_burner.setText(
                        self._burner_label(self._selected_kind, prev_serial, self._selected_product)
                    )
                    self.cmb_burner.setReadOnly(True)
                    self._selected_serial = prev_serial
            elif self._jlink_serials or self._pyocd_probes:
                self.cmb_burner.setCurrentIndex(1)
                self.cmb_burner.setText(self.cmb_burner.itemText(1))
                d = self.cmb_burner.itemData(1)
                if isinstance(d, tuple) and len(d) == 2:
                    self._selected_serial = d[1] or ""
                    self._selected_kind = d[0] or ""
                    self._selected_product = self._lookup_burner_product(d[1] or "")
                else:
                    self._selected_serial = ""
                    self._selected_kind = ""
                    self._selected_product = ""
            else:
                self.cmb_burner.setCurrentText(REMOTE_ITEM_TEXT)
                self._selected_serial = ""
                self._selected_kind = "remote"
                self._selected_product = ""
        finally:
            self.cmb_burner.blockSignals(False)

        self._sync_remote_mode_from_selection()
        self._sync_burner_status_dot()
        self._maybe_enumerate_pyocd_targets()

    def _add_separator_item(self, text: str) -> None:
        """加不可选的分组分隔项。"""
        self.cmb_burner.addItem(text)
        self.cmb_burner.setItemEnabled(self.cmb_burner.count() - 1, False)

    def _add_burner_item(self, kind: str, serial: str, label: str) -> None:
        self.cmb_burner.addItem(label, userData=(kind, serial))

    def _find_burner_index_by_serial(self, serial: str) -> int:
        for i in range(self.cmb_burner.count()):
            data = self.cmb_burner.itemData(i)
            if isinstance(data, tuple) and len(data) == 2 and data[1] == serial:
                return i
        return -1

    def _lookup_burner_kind(self, serial: str) -> str:
        """按 serial 反查 burner kind；离线时回退缓存。"""
        if serial in self._jlink_serials:
            return BURNER_KIND_JLINK
        for kind, s, _product in self._pyocd_probes:
            if s == serial:
                return kind
        kind, _ = self._get_cached_burner(serial)
        return kind

    def _lookup_burner_product(self, serial: str) -> str:
        """按 serial 反查 product；离线时回退缓存。"""
        for _kind, s, product in self._pyocd_probes:
            if s == serial:
                return product
        if serial in self._jlink_products:
            return self._jlink_products[serial]
        _kind, product = self._get_cached_burner(serial)
        return product

    def _burner_label(self, kind: str, serial: str, product: str = "") -> str:
        """生成 burner 显示 label（单点真源：在线 items / 离线占位共用）。"""
        if kind == "jlink":
            name = product if product else "J-Link"
            return f"{name}: {serial}"
        if kind == "cmsisdap":
            prod = f" ({product})" if product else ""
            return f"CMSIS-DAP{prod}: {serial}"
        if kind == "stlink":
            prod = f" ({product})" if product else ""
            return f"ST-Link{prod}: {serial}"
        return serial

    def _current_burner(self) -> tuple[str, str]:
        """返回当前选中烧录器 (kind, serial)。只读真源（_selected_*）。"""
        if self._selected_kind == "remote":
            return ("remote", "")
        if self._selected_serial:
            return (self._selected_kind or "", self._selected_serial)
        return ("", "")

    def _sync_remote_mode_from_selection(self) -> None:
        self._remote_mode = self._current_burner()[0] == "remote"
        self.remote_row.setVisible(self._remote_mode)
        if self._remote_mode:
            self._trigger_remote_probe()

    def _sync_burner_status_dot(self) -> None:
        """当前选中烧录器不在线 -> 显示红点并只读。

        远程模式下：解析失败或探测不可达 -> 红点；可达 -> 隐藏。
        """
        kind, serial = self._current_burner()
        if not serial and kind != "remote":
            self._burner_status_dot.hide()
            return

        if kind == "remote":
            # None = 未知，也显示红点
            self._burner_status_dot.setVisible(self._remote_reachable is not True)
            self.cmb_burner.setReadOnly(False)
            return

        online = serial in self._jlink_serials or any(p[1] == serial for p in self._pyocd_probes)
        self._burner_status_dot.setVisible(not online)
        self.cmb_burner.setReadOnly(not online)

    def _on_burner_selection_changed(self) -> None:
        """combo 文本改变 -> 用 currentText 反查 item 同步真源 + 持久化 + 同步红点。"""
        text = self.cmb_burner.currentText().strip()
        if text == REMOTE_ITEM_TEXT:
            kind, serial = "remote", ""
        else:
            idx = -1
            for i in range(self.cmb_burner.count()):
                if self.cmb_burner.itemText(i).strip() == text:
                    idx = i
                    break
            data = self.cmb_burner.itemData(idx) if idx >= 0 else None
            if not (isinstance(data, tuple) and len(data) == 2 and data[1]):
                return
            kind, serial = data[0] or "", data[1] or ""
        self._selected_kind = kind
        self._selected_serial = serial
        self._selected_product = self._lookup_burner_product(serial) if serial else ""
        self._remote_mode = kind == "remote"
        self.remote_row.setVisible(self._remote_mode)

        if self._remote_mode:
            self._cfg.set("flash_jlink_mode", "remote")
            self._trigger_remote_probe()
        else:
            self._cfg.set("flash_jlink_mode", "usb")
            if serial:
                self._cfg.set("flash_jlink_serial", serial)
        self._refresh_device_combo()
        self.cmb_device.refresh_tooltip()
        self._sync_burner_status_dot()
        self._maybe_enumerate_pyocd_targets()

    def _trigger_remote_probe(self) -> None:
        """异步探测远程主机 TCP 可达性；有在飞探测时跳过。"""
        if self._remote_probe_in_flight:
            return
        host = self.le_remote_host.text().strip()
        port_text = self.le_remote_port.text().strip()
        resolved = resolve_remote_host(host)
        port = int(port_text) if is_valid_port(port_text) else 0
        if resolved and port:
            self._remote_probe_in_flight = True
            runnable = TcpReachableRunnable(resolved, port, self._remote_probe)
            QThreadPool.globalInstance().start(runnable)
        else:
            self._remote_reachable = False
            self._sync_burner_status_dot()

    def _on_remote_probe_done(self, reachable: bool) -> None:
        self._remote_probe_in_flight = False
        self._remote_reachable = reachable
        self._sync_burner_status_dot()

    def _on_target_device_changed(self, _text: str) -> None:
        """目标设备名变化：刷新 Flash 占用图所需 device info。"""
        info = self._lookup_target_info(self.cmb_device.currentText())
        self.analysis_view.set_device_info(info)
        self.flash_bar.set_device_info(info)

    def _on_browse(self) -> None:
        cur = self.cmb_file.currentText().strip()
        start_dir = str(Path(cur).parent) if cur else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("选择固件文件"),
            start_dir,
            self.tr("固件文件 (*.axf *.elf *.hex *.bin);;所有文件 (*.*)"),
        )
        if not path:
            return
        self._select_file(path)

    def _on_save_as(self) -> None:
        """把当前固件转换为 .bin / .hex 另存（目标格式由所选后缀决定）。"""
        src = self.cmb_file.currentText().strip()
        if not src:
            _infobar.warn(self, self.tr("未选择文件"), self.tr("请先选择要转换的固件"))
            return
        if not os.path.exists(src):
            _infobar.warn(self, self.tr("文件不存在"), src)
            return

        stem = Path(src).stem
        start_dir = str(Path(src).with_name(stem + ".bin"))
        dst, sel = QFileDialog.getSaveFileName(
            self, self.tr("固件另存为"), start_dir, "Binary (*.bin);;Intel HEX (*.hex)"
        )
        if not dst:
            return
        # 用户没敲后缀时按所选过滤器补全
        if not os.path.splitext(dst)[1]:
            dst += ".hex" if "hex" in sel.lower() else ".bin"

        try:
            bin_addr = int(self.edit_bin_addr.text().strip(), 0)
        except (ValueError, TypeError):
            bin_addr = int(self._cfg.get("flash_bin_address"))

        from core import flash_file_parser as fp

        try:
            fp.convert_file(src, dst, bin_start_addr=bin_addr)
        except fp.FileParseError as e:
            _infobar.error(self, self.tr("转换失败"), str(e))
            return
        _infobar.success(self, self.tr("已另存"), dst)

    def _rebuild_file_combo(self, recent: list[str], select_index: int = 0) -> None:
        """用最近文件列表重建下拉项并选中 select_index。

        EditableComboBox.setCurrentText 对不在 items 里的文本是 no-op，
        所以新文件必须先 addItem 再用 index 选中。重建期间阻塞信号，
        避免误触发 currentTextChanged → _on_file_text_changed。
        """
        self.cmb_file.blockSignals(True)
        try:
            self.cmb_file.clear()
            for p in recent:
                self.cmb_file.addItem(p)
            if recent and 0 <= select_index < len(recent):
                self.cmb_file.setCurrentIndex(select_index)
        finally:
            self.cmb_file.blockSignals(False)

    def _select_file(self, path: str) -> None:
        """浏览 / 拖放选中新文件：置顶最近文件 + 重建下拉 + 显示 + 解析。"""
        path = path.strip()
        if not path:
            return
        if not os.path.exists(path):
            _infobar.warn(self, self.tr("文件不存在"), path)
            return
        recent = list(self._cfg.get("flash_recent_files") or [])
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        recent = recent[:8]
        self._cfg.set("flash_recent_files", recent)
        self._rebuild_file_combo(recent, select_index=0)
        self._parse_and_show(path, silent=False)

    def _on_file_text_changed(self, text: str) -> None:
        """用户从下拉选择 / 手动输入路径：置顶到 recent + 解析显示。

    置顶后下次重启选中 recent[0] = 上次选的文件。不重建下拉（避免
    lineEdit 抖动）；下拉 items 顺序下次重启时按 cfg 重建。
    """
        text = text.strip()
        if not text or not os.path.exists(text):
            if not text:
                self._parse_state = "empty"
                self.lbl_format.setText(self.tr("(无)"))
                self.lbl_range.setText(self.tr("(无)"))
                self.lbl_mtime_flag.setText("")
                self.analysis_view.clear()
                self.flash_bar.clear()
                self.symbol_card.setVisible(False)
            # 切到空/不存在文件 -> 清旧 watcher，避免监听已丢弃的路径
            self._watch_file("")
            return
        recent = list(self._cfg.get("flash_recent_files") or [])
        if not recent or recent[0] != text:
            if text in recent:
                recent.remove(text)
            recent.insert(0, text)
            recent = recent[:8]
            self._cfg.set("flash_recent_files", recent)
        self._parse_and_show(text, silent=True)

    def _parse_and_show(self, path: str, silent: bool = False) -> None:
        """解析固件 → 填 format/range → bin_addr 可编辑性 + mtime 比对。"""
        path = path.strip()
        if not path:
            return
        if not os.path.exists(path):
            if not silent:
                _infobar.warn(self, self.tr("文件不存在"), path)
            return

        from core import flash_file_parser as fp

        # bin addr 取页面当前值
        try:
            bin_addr = int(self.edit_bin_addr.text().strip(), 0)
        except (ValueError, TypeError):
            bin_addr = int(self._cfg.get("flash_bin_address"))
        try:
            info = fp.parse_file(path, bin_start_addr=bin_addr)
        except fp.FileParseError as e:
            self._parse_state = "error"
            self.lbl_format.setText(self.tr("(解析失败)"))
            self.lbl_range.setText("")
            self.analysis_view.clear()
            self.flash_bar.clear()
            self.symbol_card.setVisible(False)
            if not silent:
                _infobar.error(self, self.tr("文件解析失败"), str(e))
            return

        self._parse_state = "ok"

        self.lbl_format.setText(info.fmt.upper())
        self.lbl_range.setText(
            f"0x{info.addr_start:08X} – 0x{info.addr_end:08X} "
            f"({info.total_bytes} B, {info.notes})"
        )
        # bin 模式才允许编辑 bin_addr
        self.edit_bin_addr.setEnabled(info.fmt == FORMAT_BIN)

        # 更新 Flash 占用图（所有格式）
        self.analysis_view.set_firmware_range(info.addr_start, info.addr_end)
        self.flash_bar.set_firmware_range(info.addr_start, info.addr_end)

        # 符号表：仅 ELF/axf 显示；非 ELF 仍要保留 Flash 占用图，所以只清 symbols/sections
        if info.fmt == FORMAT_ELF:
            self.analysis_view.load(path)
            self.symbol_card.setVisible(True)
        else:
            self.analysis_view.symbols.clear()
            self.analysis_view.sections.clear()
            self.symbol_card.setVisible(False)

        # mtime 比对：留旧 mtime 作为基线，初次选中固件时 prev_mt 有值（历史）
        # 才判定「已更新」，无基线则记为本基线（视为已读完最新，下次变化才算更新）。
        mt_map = dict(self._cfg.get("flash_recent_files_mtime") or {})
        cur_mt = os.path.getmtime(path)
        prev_mt = mt_map.get(path)
        self._watched_mtime = cur_mt
        # 初次选中固件时 _auto_burn_armed=False（不重复触发）；本次 mtime 基线已记录
        self._update_mtime_flag(
            prev_mtime=prev_mt, cur_mtime=cur_mt, armed_initial=False, path=path
        )
        mt_map[path] = cur_mt
        self._cfg.set("flash_recent_files_mtime", mt_map)
        # 挂 watcher 监听后续变化（编译写文件触发 fileChanged）
        self._watch_file(path)

    def _update_mtime_flag(
        self,
        *,
        prev_mtime: float | None,
        cur_mtime: float,
        armed_initial: bool,
        path: str,
    ) -> None:
        """刷新固件变化指示器颜色 + 文案 + 自动烧录触发。

        - prev_mtime 为 None（首次见此文件）-> 绿色（视为已识别最新），不 armed
        - prev_mtime 非空且 cur_mtime > prev + 0.5（文件被改过）-> 橙色「已更新」
          若 _auto_burn_armed 为真则触发自动烧录一次，否则置 armed 等下次
        - armed_initial=True 时不视为变化（_watch_file 初次挂监控用，防止 false 立即触发）
        文案附带文件修改日期（不翻译日期格式）。
        """
        from datetime import datetime

        bright_green = "#2ecc71"
        amber = "#d97706"
        if prev_mtime is None or armed_initial:
            # 初次/刚挂监控视为已识别最新 -> 绿
            self.lbl_mtime_flag.setStyleSheet(f"color: {bright_green};")
            date_str = datetime.fromtimestamp(cur_mtime).strftime("%Y-%m-%d %H:%M:%S")
            self.lbl_mtime_flag.setText(self.tr("● 已识别 {date}").format(date=date_str))
            if not armed_initial:
                self._auto_burn_armed = True
            return
        if cur_mtime > prev_mtime + 0.5:
            # 检测到固件已更新 -> 橙 + 「已更新」
            self.lbl_mtime_flag.setStyleSheet(f"color: {amber};")
            date_str = datetime.fromtimestamp(cur_mtime).strftime("%Y-%m-%d %H:%M:%S")
            self.lbl_mtime_flag.setText(
                self.tr("● 已更新 {date}").format(date=date_str)
            )
            if self._auto_burn_armed and self.rb_auto_burn.isChecked():
                self._auto_burn_armed = False  # 一次变化只触发一次
                self._on_start_flash()
        else:
            # 未变 -> 绿
            self.lbl_mtime_flag.setStyleSheet(f"color: {bright_green};")
            date_str = datetime.fromtimestamp(cur_mtime).strftime("%Y-%m-%d %H:%M:%S")
            self.lbl_mtime_flag.setText(self.tr("● 已识别 {date}").format(date=date_str))

    def _watch_file(self, path: str) -> None:
        """挂 QFileSystemWatcher 监听文件 mtime 变化（编译完成触发 fileChanged）。"""
        if self._file_watcher is not None:
            # 清旧 watcher（含 directory/file），释放信号
            with contextlib.suppress(RuntimeError):
                self._file_watcher.fileChanged.disconnect(self._on_watched_file_changed)
            self._file_watcher.removePaths(
                self._file_watcher.files() + self._file_watcher.directories()
            )
        self._file_watcher = QFileSystemWatcher(self)
        self._watched_path = path
        need_watching = bool(path) and os.path.exists(path)
        if need_watching:
            self._file_watcher.addPath(path)
            # 同时监听父目录 -- 应对「编译写文件用 unlink+rename 原子替换」导致 path 监控失效：
            # 目录的 directoryChanged 会触发，我们据此重新 addPath 并复查 mtime。
            parent = str(Path(path).parent)
            if parent and os.path.isdir(parent):
                self._file_watcher.addPath(parent)
            self._file_watcher.fileChanged.connect(self._on_watched_file_changed)
            self._file_watcher.directoryChanged.connect(self._schedule_recheck)

    def _on_watched_file_changed(self, _path: str) -> None:
        """QFileSystemWatcher fileChanged：文件被写/替换 -> 触发变化处理。

        用 1s 防抖 timer（编译可能多次写），保证最后一次写完再比对 mtime。
        """
        self._file_recheck_timer.start()

    def _schedule_recheck(self, _dir: str) -> None:
        """父目录 directoryChanged（编译 unlink+rename 时触发）：复查文件 mtime。"""
        self._file_recheck_timer.start()

    def _recheck_file_mtime(self) -> None:
        """1s 防抖后复查当前监控文件的 mtime，按变/不变刷新指示器 + 自动烧录。"""
        path = self._watched_path
        if not path or not os.path.exists(path):
            # 文件被删（编译中可能短暂失踪）-> 不立即清，等下一次 directoryChanged 重挂
            self._file_recheck_timer.start()  # 继续等
            return
        try:
            cur_mt = os.path.getmtime(path)
        except OSError:
            return
        prev_mt = self._watched_mtime
        if cur_mt != prev_mt:
            # 真变化 -> 更新指示器（armed_initial=False 允许触发自动烧录）
            self._update_mtime_flag(
                prev_mtime=prev_mt, cur_mtime=cur_mt, armed_initial=False, path=path
            )
            # 记新基线 mtime 到 cfg 缓存
            mt_map = dict(self._cfg.get("flash_recent_files_mtime") or {})
            mt_map[path] = cur_mt
            self._cfg.set("flash_recent_files_mtime", mt_map)
            self._watched_mtime = cur_mt
        # watcher 可能因 unlink+rename 失效，补挂一次（addPath 幂等，已存在不会重复加）
        if self._file_watcher is not None and path not in self._file_watcher.files():
            self._file_watcher.addPath(path)

    def _toggle_log(self) -> None:
        vis = not self.txt_log.isVisible()
        self.txt_log.setVisible(vis)
        self.btn_toggle_log.setText(self.tr("▼ 详情") if vis else self.tr("▶ 详情"))

    def _copy_log(self) -> None:
        import platform

        import PySide6

        from ui.about_page import APP_VERSION

        header = (
            f"J-Link RTT Viewer / Flash log\n"
            f"App version: {APP_VERSION}\n"
            f"OS: {platform.platform()}\n"
            f"pylink-square: 1.6.0\n"
            f"PySide6: {PySide6.__version__}\n"
            f"---\n"
        )
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(header + self.txt_log.toPlainText())
        _infobar.info(self, self.tr("已复制日志到剪贴板"), "")

    def _on_erase_chip_clicked(self) -> None:
        """全片擦除按钮：先弹确认框防误点，确认后走烧录流程（erase_only=True）。"""
        if self._is_running:
            return
        device = self.cmb_device.currentText().strip() or self.tr("（未填设备）")
        box = MessageBox(
            self.tr("确认全片擦除"),
            self.tr("将擦除 {device} 的整个 Flash，此操作不可恢复。\n确定继续吗？").format(
                device=device
            ),
            self.window(),
        )
        box.yesButton.setText(self.tr("擦除"))
        box.cancelButton.setText(self.tr("取消"))
        if box.exec():
            self._on_start_flash(erase_only=True)

    def _maybe_enumerate_pyocd_targets(self) -> None:
        """按需枚举 pyOCD target 库：仅当选中 CMSIS-DAP / ST-Link 烧录器时触发。

        J-Link 模式用 pylink ``supported_device``，不需要 pyOCD target 库（省
        ~1s CMSIS-Pack 索引加载）。用户切到 CMSIS-DAP / ST-Link 时才 emit 信号，
        worker 线程异步枚举，不卡 UI。构造时 ``_rebuild_burner_combo`` 恢复选中
        也会触发（cfg 默认 cmsisdap/stlink 时首次即加载）。
        """
        if self._pyocd_targets_enumerated:
            return
        if self._selected_kind in ("cmsisdap", "stlink"):
            self._pyocd_targets_enumerated = True
            if hasattr(self._worker, "enumerate_pyocd_targets_requested"):
                self._worker.enumerate_pyocd_targets_requested.emit()

    def _on_packs_changed(self) -> None:
        """Pack 管理页下载/删除/迁移后，重新枚举 pyOCD target 刷新设备下拉。

        ``pack_service`` 已失效 ``get_pyocd_target_infos`` 的 functools.cache，
        这里重置枚举标志 + 触发重新枚举，worker 填新 cache 后 emit ready 刷新
        cmb_device。无需重启进程（之前下载新 pack 后设备列表看不到，要重启）。
        """
        self._pyocd_targets_enumerated = False
        self._maybe_enumerate_pyocd_targets()

    def _on_start_flash(self, erase_only: bool = False) -> None:
        """开始烧录 / 全片擦除。

        erase_only=True 时复用完整流程（连接 → RTT 同设备协调 → 整片擦除 → 断开），
        只是固定 chip 擦除且不烧录。无需固件文件。
        """
        if self._is_running:
            return

        path = self.cmb_file.currentText().strip()
        if not erase_only:
            if not path:
                _infobar.warn(
                    self, self.tr("未选择文件"), self.tr("请先选择 .axf/.elf/.hex/.bin 文件")
                )
                return
            if not os.path.exists(path):
                _infobar.warn(self, self.tr("文件不存在"), path)
                return

        from core import flash_file_parser as fp

        try:
            fmt = fp.detect_format(path) if path else FORMAT_BIN
        except fp.FileParseError as e:
            _infobar.error(self, self.tr("格式不支持"), str(e))
            return

        try:
            bin_addr = int(self.edit_bin_addr.text().strip(), 0)
        except (ValueError, TypeError):
            bin_addr = 0

        device = self.cmb_device.currentText().strip()
        if not device:
            _infobar.warn(
                self, self.tr("未填 Device"), self.tr("请填写目标设备名（如 STM32H750VB）")
            )
            return

        iface = "SWD" if self.rb_swd.isChecked() else "JTAG"
        speed = int(self.cmb_speed.currentText())
        erase_mode = _ERASE_LABELS[self.cmb_erase.currentIndex()][1]
        post_action = _POST_LABELS[self.cmb_post.currentIndex()][1]
        self._last_post_action = post_action
        verify = self.chk_verify.isChecked()
        if erase_only:
            # 全片擦除：固定 chip 擦除，不做烧录后动作/校验
            erase_mode = ERASE_MODE_CHIP
            post_action = POST_ACTION_NONE
            verify = False

        # ---- 本地 / 远程 烧录器参数分支 ----
        remote_addr = ""
        burner_serial = ""
        burner_kind = BURNER_KIND_JLINK
        if self._remote_mode:
            host = self.le_remote_host.text().strip()
            port_text = self.le_remote_port.text().strip()
            resolved = resolve_remote_host(host)
            if resolved is None:
                _infobar.warn(
                    self,
                    self.tr("提示"),
                    self.tr('无法解析主机名 "{host}"，请检查输入').format(host=host),
                )
                return
            if not is_valid_port(port_text):
                _infobar.warn(self, self.tr("提示"), self.tr("端口无效（1-65535）"))
                return
            remote_addr = f"{resolved}:{port_text}"
            self._cfg.set("flash_remote_host", host)
            self._cfg.set("flash_remote_port", port_text)
        else:
            burner_kind, burner_serial = self._current_burner()
            if not burner_serial:
                _infobar.warn(self, self.tr("提示"), self.tr("未检测到烧录器，请检查 USB 连接"))
                return
            # 离线占位检查（serial 不在可见列表）
            if not (
                burner_serial in self._jlink_serials
                or any(p[1] == burner_serial for p in self._pyocd_probes)
            ):
                _infobar.warn(
                    self, self.tr("提示"), self.tr("选中的烧录器不在线，请刷新设备列表或重新选择")
                )
                return

        params = FlashParams(
            file_path=path,
            file_format=fmt,
            bin_start_addr=bin_addr,
            device_name=device,
            interface=iface,
            speed_khz=speed,
            erase_mode=erase_mode,
            post_action=post_action,
            extra_verify=verify,
            jlink_serial=burner_serial,
            remote_addr=remote_addr,
            burner_kind=burner_kind,
            erase_only=erase_only,
            auto_unlock_rdp=self.chk_auto_unlock_rdp.isChecked(),
            auto_add_rdp=self.chk_auto_add_rdp.isChecked(),
            auto_add_wrp=self.chk_auto_add_wrp.isChecked(),
        )
        self._worker.set_pending_params(params)

        # 与 RTT 协调：仅 J-Link 烧录器且与 RTT 同一台时才先断 RTT
        # （ST-Link / CMSIS-DAP 跟 J-Link RTT 不抢 probe，不冲突，直接烧）
        self._set_rtt_busy(True)
        self._rtt_resume_remote_addr = ""
        if self._rtt_worker is not None:
            rtt_state = self._rtt_worker.state_name()
            rtt_info = self._rtt_worker.get_device_info()
            rtt_serial = str(rtt_info.get("jlink_serial", "") or "")
            rtt_remote_addr = str(rtt_info.get("remote_addr", "") or "")
            same_device = False
            if self._remote_mode:
                same_device = (
                    rtt_state == "CONNECTED" and rtt_remote_addr == remote_addr and remote_addr
                )
            elif burner_kind == BURNER_KIND_JLINK:
                same_device = (
                    rtt_state == "CONNECTED" and rtt_serial == burner_serial and burner_serial
                )
            if same_device:
                self._resume_rtt_after_flash = True
                self._rtt_pending_disconnect = True
                self._rtt_resume_remote_addr = rtt_remote_addr
                self._rtt_worker.disconnect_requested.emit()
                self._rtt_disconnect_timeout_timer.start()
                return

        self._resume_rtt_after_flash = False
        self._worker.flash_requested.emit()

    def _on_flash_started(self) -> None:
        self._is_running = True
        self._stage_key = "preparing"
        self._set_rtt_busy(True)
        self._set_inputs_enabled(False)
        self.btn_flash.setText(self.tr("烧录中…"))
        self.txt_log.clear()
        self.progress.setValue(0)
        self.lbl_stage.setText(self.tr("准备…"))

    def _on_stage_changed(self, stage: str) -> None:
        self._stage_key = stage
        label_map = {
            "connect": self.tr("连接中…"),
            "erase": self.tr("擦除中…"),
            "program": self.tr("写入中…"),
            "verify": self.tr("校验中…"),
            "reset": self.tr("复位中…"),
            "disconnect": self.tr("断开中…"),
        }
        self.lbl_stage.setText(label_map.get(stage, stage))

    def _on_progress(self, current: int, total: int) -> None:
        if total <= 0:
            self.progress.setValue(0)
            return
        self.progress.setValue(int(current * 100 / total))

    def _on_log(self, level: str, msg: str) -> None:
        from datetime import datetime

        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        prefix = {"info": "", "warn": "⚠ ", "error": "✖ "}.get(level, "")
        self.txt_log.appendPlainText(f"[{ts}] {prefix}{msg}")

    def _on_flash_finished(self, ok: bool, summary: str) -> None:
        self._is_running = False
        self._stage_key = "done" if ok else "failed"
        self._set_inputs_enabled(True)
        self.btn_flash.setText(self.tr("开始烧录"))
        self.btn_erase_chip.setText(self.tr("全片擦除"))
        self._set_rtt_busy(False)

        if self._resume_rtt_after_flash and self._last_post_action != POST_ACTION_HALT:
            self._resume_rtt_after_flash = False
            target = self._cfg.get("target_mcu")
            iface = self._cfg.get("interface")
            speed = int(self._cfg.get("speed_khz") or 0)
            channel = int(self._cfg.get("rtt_channel") or 0)
            if target:
                if self._rtt_resume_remote_addr:
                    self._rtt_worker.connect_remote_requested.emit(
                        target, iface, speed, channel, self._rtt_resume_remote_addr
                    )
                else:
                    burner_serial = self._current_burner()[1]
                    if burner_serial:
                        self._rtt_worker.connect_requested.emit(
                            target, iface, speed, channel, burner_serial
                        )
            self._rtt_resume_remote_addr = ""
        if ok:
            self.lbl_stage.setText(self.tr("完成 ✓"))
            self.progress.setValue(100)
            _infobar.success(self, self.tr("操作成功"), summary)
            # 烧录成功 -> 固件视为"已识别最新"（绿 + 日期），armed 置真等下次变化
            self._auto_burn_armed = True
            path = self._watched_path
            if path and os.path.exists(path):
                try:
                    self._watched_mtime = os.path.getmtime(path)
                    self._update_mtime_flag(
                        prev_mtime=None,
                        cur_mtime=self._watched_mtime,
                        armed_initial=True,
                        path=path,
                    )
                except OSError:
                    pass
        else:
            self.lbl_stage.setText(self.tr("失败 ✖"))
            # 失败时自动展开详情 + 写固定建议文案
            if not self.txt_log.isVisible():
                self._toggle_log()
            self.txt_log.appendPlainText(
                self.tr("⚠ Flash 已部分擦除/写入，建议下次用「整片擦除」重烧")
            )
            _infobar.error(self, self.tr("操作失败"), summary)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for w in (
            self.cmb_device,
            self.rb_swd,
            self.rb_jtag,
            self.cmb_speed,
            self.cmb_file,
            self.btn_browse,
            self.btn_save_as,
            self.edit_bin_addr,
            self.cmb_erase,
            self.cmb_post,
            self.chk_verify,
            self.chk_auto_unlock_rdp,
            self.chk_auto_add_rdp,
            self.chk_auto_add_wrp,
            self.btn_reset,
            self.cmb_burner,
            self.le_remote_host,
            self.le_remote_port,
            self.rb_auto_burn,
        ):
            w.setEnabled(enabled)
        self.btn_flash.setEnabled(enabled)
        self.btn_erase_chip.setEnabled(enabled)

    # ---- i18n ----
    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()
        super().changeEvent(event)

    def _retranslate_ui(self) -> None:
        # 静态标题 / 标签
        self.lbl_conn_title.setText(self.tr("连接参数"))
        self._lbl_burner.setText(self.tr("烧录器:"))
        self._lbl_remote_host.setText(self.tr("远程主机:"))
        self._lbl_remote_port.setText(self.tr("端口:"))
        self.lbl_device.setText(self.tr("目标设备:"))
        self.lbl_file_title.setText(self.tr("固件文件"))
        self.lbl_bin_addr.setText(self.tr("Bin 起始地址:"))
        self.lbl_options_title.setText(self.tr("烧录选项"))
        self.lbl_erase.setText(self.tr("擦除模式:"))
        self.lbl_post.setText(self.tr("完成动作:"))

        # 输入框占位
        self.le_remote_host.setPlaceholderText(self.tr("IP 或域名，如 192.168.79.1"))
        self.le_remote_port.setPlaceholderText("19020")

        # 按钮
        self.btn_browse.setText(self.tr("浏览…"))
        self.btn_save_as.setText(self.tr("另存为…"))
        self.btn_save_as.setToolTip(self.tr("把当前固件转换为 .bin / .hex 另存"))
        self.btn_copy_log.setText(self.tr("复制日志"))
        self.chk_verify.setText(self.tr("额外 byte-by-byte verify（慢一倍）"))
        self.chk_auto_unlock_rdp.setText(self.tr("自动解除读写保护"))
        tip(self.chk_auto_unlock_rdp, self.tr("烧录前自动解除 RDP 读保护 (L1->L0, 触发 mass erase) + WRP 写保护"))
        self.chk_auto_add_rdp.setText(self.tr("自动添加读取保护"))
        tip(self.chk_auto_add_rdp, self.tr("烧录完成后自动设置 RDP = L1 (读保护生效; 下次烧录需先解除)"))
        self.chk_auto_add_wrp.setText(self.tr("自动添加写入保护"))
        tip(self.chk_auto_add_wrp, self.tr("烧录完成后自动启用写保护 WRP (防误擦写; 需先解除才能再烧录)"))
        self.btn_reset.setText(self.tr("复位"))
        tip(self.btn_reset, self.tr("复位目标芯片 (兼容所有烧录器; 不烧录不擦除)"))
        self.rb_auto_burn.setText(self.tr("固件变化后自动烧录"))
        tip(self.rb_auto_burn, self.tr("使能后每次固件重新编译发生变化时自动触发烧录"))

        # 动态按钮文案
        if self._is_running:
            self.btn_flash.setText(self.tr("烧录中…"))
        else:
            self.btn_flash.setText(self.tr("开始烧录"))
        self.btn_erase_chip.setText(self.tr("全片擦除"))
        vis_log = self.txt_log.isVisible()
        self.btn_toggle_log.setText(self.tr("▼ 详情") if vis_log else self.tr("▶ 详情"))

        # 阶段标签（按当前 _stage_key 重置）
        stage_labels = {
            "idle": self.tr("待命"),
            "preparing": self.tr("准备…"),
            "connect": self.tr("连接中…"),
            "erase": self.tr("擦除中…"),
            "program": self.tr("写入中…"),
            "verify": self.tr("校验中…"),
            "reset": self.tr("复位中…"),
            "disconnect": self.tr("断开中…"),
            "done": self.tr("完成 ✓"),
            "failed": self.tr("失败 ✖"),
        }
        self.lbl_stage.setText(stage_labels.get(self._stage_key, self.tr("待命")))

        # format / range（仅在空 / 解析失败时重置；已解析成功的是技术数据不动）
        if self._parse_state == "empty":
            self.lbl_format.setText(self.tr("(无)"))
            self.lbl_range.setText(self.tr("(无)"))
        elif self._parse_state == "error":
            self.lbl_format.setText(self.tr("(解析失败)"))
            self.lbl_range.setText("")

        # ComboBox 项：保存当前 index → 清空 → 用 tr 重新填 → 恢复 index（阻塞信号）
        idx_erase = self.cmb_erase.currentIndex()
        self.cmb_erase.blockSignals(True)
        self.cmb_erase.clear()
        for label, _ in _ERASE_LABELS:
            self.cmb_erase.addItem(self.tr(label))
        self.cmb_erase.setCurrentIndex(idx_erase)
        self.cmb_erase.blockSignals(False)

        idx_post = self.cmb_post.currentIndex()
        self.cmb_post.blockSignals(True)
        self.cmb_post.clear()
        for label, _ in _POST_LABELS:
            self.cmb_post.addItem(self.tr(label))
        self.cmb_post.setCurrentIndex(idx_post)
        self.cmb_post.blockSignals(False)

    # ---- 拖放（下一 Task 完善）----
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        urls = e.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path.lower().endswith((".axf", ".elf", ".hex", ".bin")):
            self._select_file(path)
            e.acceptProposedAction()

    def _on_rtt_state_for_flash(self, connected: bool) -> None:
        """RTT 断开后确认切到断开态，再启动烧录。"""
        if self._rtt_pending_disconnect and not connected:
            self._rtt_pending_disconnect = False
            self._rtt_disconnect_timeout_timer.stop()
            self._worker.flash_requested.emit()

    def _set_rtt_busy(self, busy: bool) -> None:
        """烧录期间锁定/解锁 RTT 页的连接按钮。"""
        if self._rtt_page_ref is not None:
            self._rtt_page_ref.set_flash_busy(busy)

    def shutdown(self) -> None:
        """主窗口 closeEvent 调；干净关掉 worker 线程。

        必须 drain 事件循环：worker._on_stop 里对 timer 做 deleteLater，
        若线程已退出而 deleteLater 事件没来得及处理，主线程析构 timer
        会跨线程 killTimer，触发 Qt assertion/segfault（CLAUDE.md）。
        """
        try:
            self.ob_card.cleanup()
        except Exception:
            pass
        self._worker.stop_requested.emit()
        if not self._thread.wait(3000):
            self._thread.terminate()
            self._thread.wait(1000)
        # 处理 worker 线程 quit 前排入的 deleteLater 事件
        from PySide6.QtCore import QCoreApplication, QEvent

        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
