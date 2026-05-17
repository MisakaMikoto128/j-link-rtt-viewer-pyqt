"""固件烧录页：独立 FlashWorker + 独立 QThread，不干涉 RTT/Memory。

UI 布局（4 个 Card，透明 ScrollArea 整页包裹）：
1. 连接参数 — device / interface / speed
2. 固件文件 — file picker + 最近 10 + 拖放 + 解析后 format/range/size
3. 烧录选项 — erase_mode / post_action / extra_verify
4. 执行 — 大按钮 + ProgressBar + 阶段文字 + 可折叠详情面板

参数持久化：cfg.flash_*。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    EditableComboBox,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    SpinBox,
    StrongBodyLabel,
)

from core.config_service import ConfigService
from core.flash_worker import (
    ERASE_MODE_CHIP,
    ERASE_MODE_SECTOR,
    FORMAT_BIN,
    POST_ACTION_NONE,
    POST_ACTION_RESET,
    POST_ACTION_RESET_RUN,
    FlashParams,
    FlashWorker,
)

from . import _infobar
from ._scroll_helpers import make_transparent_scroll


_ERASE_LABELS = [
    ("扇区擦除（推荐，快）", ERASE_MODE_SECTOR),
    ("整片擦除（慢，更干净）", ERASE_MODE_CHIP),
]
_POST_LABELS = [
    ("仅烧录", POST_ACTION_NONE),
    ("烧录 + 复位", POST_ACTION_RESET),
    ("烧录 + 复位 + 运行（推荐）", POST_ACTION_RESET_RUN),
]


class FlashPage(QWidget):
    def __init__(self, cfg: ConfigService, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("flashPage")
        self._cfg = cfg
        self._is_running = False

        # 独立 worker + 独立 QThread（和 JLinkWorker 完全无关）
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

        v.addWidget(self._build_conn_card())
        v.addWidget(self._build_file_card())
        v.addWidget(self._build_options_card())
        v.addWidget(self._build_run_card())
        v.addStretch(1)

        self._connect_signals()
        self._load_prefs_into_controls()

    # ---- card builders (占位，下一 Task 填实) ----
    def _build_conn_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.addWidget(StrongBodyLabel("连接参数"))
        row = QHBoxLayout()
        row.addWidget(BodyLabel("Device:"))
        self.cmb_device = EditableComboBox()
        self.cmb_device.addItems(self._cfg.get_chip_list() or ["STM32H750VB"])
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
        self.spin_speed = SpinBox()
        self.spin_speed.setRange(100, 50000)
        self.spin_speed.setSingleStep(100)
        row2.addWidget(self.spin_speed)
        row2.addStretch(1)
        layout.addLayout(row2)
        return card

    def _build_file_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.addWidget(StrongBodyLabel("固件文件"))

        row = QHBoxLayout()
        row.addWidget(BodyLabel("File:"))
        self.cmb_file = EditableComboBox()  # 最近 10 文件下拉
        self.cmb_file.setMinimumWidth(360)
        row.addWidget(self.cmb_file, 1)
        self.btn_browse = PushButton("浏览…")
        row.addWidget(self.btn_browse)
        self.lbl_mtime_flag = BodyLabel("")
        self.lbl_mtime_flag.setStyleSheet("color: #d97706;")  # amber
        row.addWidget(self.lbl_mtime_flag)
        layout.addLayout(row)

        # format + range
        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("Format:"))
        self.lbl_format = BodyLabel("(无)")
        row2.addWidget(self.lbl_format)
        row2.addSpacing(20)
        row2.addWidget(BodyLabel("Range:"))
        self.lbl_range = BodyLabel("(无)")
        row2.addWidget(self.lbl_range, 1)
        layout.addLayout(row2)

        # bin start addr (仅 bin 模式可编辑)
        row3 = QHBoxLayout()
        row3.addWidget(BodyLabel("Bin 起始地址:"))
        self.edit_bin_addr = LineEdit()
        self.edit_bin_addr.setPlaceholderText("0x08000000")
        self.edit_bin_addr.setMaximumWidth(180)
        row3.addWidget(self.edit_bin_addr)
        row3.addStretch(1)
        layout.addLayout(row3)
        return card

    def _build_options_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.addWidget(StrongBodyLabel("烧录选项"))

        row = QHBoxLayout()
        row.addWidget(BodyLabel("擦除模式:"))
        self.cmb_erase = ComboBox()
        for label, _ in _ERASE_LABELS:
            self.cmb_erase.addItem(label)
        row.addWidget(self.cmb_erase, 1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("完成动作:"))
        self.cmb_post = ComboBox()
        for label, _ in _POST_LABELS:
            self.cmb_post.addItem(label)
        row2.addWidget(self.cmb_post, 1)
        layout.addLayout(row2)

        self.chk_verify = CheckBox("额外 byte-by-byte verify（慢一倍）")
        layout.addWidget(self.chk_verify)
        return card

    def _build_run_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)

        self.btn_flash = PrimaryPushButton("开始烧录")
        self.btn_flash.setMinimumHeight(36)
        layout.addWidget(self.btn_flash)

        row = QHBoxLayout()
        self.lbl_stage = BodyLabel("待命")
        row.addWidget(self.lbl_stage)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        row.addWidget(self.progress, 1)
        layout.addLayout(row)

        # 详情面板（折叠）
        row_det = QHBoxLayout()
        self.btn_toggle_log = PushButton("▶ 详情")
        self.btn_toggle_log.setFlat(True)
        row_det.addWidget(self.btn_toggle_log)
        self.btn_copy_log = PushButton("复制日志")
        row_det.addWidget(self.btn_copy_log)
        row_det.addStretch(1)
        layout.addLayout(row_det)

        self.txt_log = PlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(1000)
        self.txt_log.setVisible(False)
        layout.addWidget(self.txt_log)
        return card

    # ---- 占位（下一 Task 填）----
    def _connect_signals(self) -> None:
        pass

    def _load_prefs_into_controls(self) -> None:
        pass

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
            self.cmb_file.setCurrentText(path)
            e.acceptProposedAction()

    def shutdown(self) -> None:
        """主窗口 closeEvent 调；干净关掉 worker 线程。"""
        self._worker.stop_requested.emit()
        if not self._thread.wait(3000):
            self._thread.terminate()
            self._thread.wait(1000)
