"""固件分析视图：把「符号 / 段 / 占用汇总」三个视图用 SegmentedWidget 切换，
共用同一个已选 axf/elf。作为烧录页底部的附属信息面板，不独立成页。

- 符号 Symbols：SymbolTableView（名称/地址/大小/类型/绑定/段/占段%）
- 段 Sections：内存相关段(SHF_ALLOC) 的地址/大小/RWX/对齐
- 占用汇总 Summary：text/data/bss + Flash/RAM 总量 + 入口/初始 SP/Reset_Handler
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHeaderView,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    SegmentedWidget,
    StrongBodyLabel,
    TableWidget,
)

from core.flash_file_parser import (
    FileParseError,
    read_elf_meta,
    read_memory_summary,
    read_sections,
)

from .symbol_table_view import _NumericItem, SymbolTableView

_SEC_COLUMNS = ["Name", "Address", "Size", "Flags", "Align"]


def _human(n: int) -> str:
    """字节数 → '1234 B (1.2 KiB)' 风格。"""
    if n < 1024:
        return f"{n} B"
    return f"{n} B ({n / 1024:.1f} KiB)"


class _SectionsView(QWidget):
    """内存相关段（SHF_ALLOC）表。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.lbl_title = StrongBodyLabel("段表 Sections")
        layout.addWidget(self.lbl_title)

        self.table = TableWidget()
        self.table.setColumnCount(len(_SEC_COLUMNS))
        self.table.setHorizontalHeaderLabels(_SEC_COLUMNS)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(_SEC_COLUMNS)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        for col, w in ((1, 120), (2, 120), (3, 110), (4, 70)):
            self.table.setColumnWidth(col, w)
        layout.addWidget(self.table, 1)

        self.lbl_hint = CaptionLabel("仅列出占用内存的段（SHF_ALLOC）。")
        self.lbl_hint.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.lbl_hint)

    def load(self, path: str) -> None:
        try:
            secs = read_sections(path)
        except FileParseError:
            secs = []
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(secs))
        for r, s in enumerate(secs):
            name_item = QTableWidgetItem(s.name)
            addr_item = _NumericItem(f"0x{s.addr:08X}")
            addr_item.setData(Qt.ItemDataRole.UserRole, s.addr)
            size_item = _NumericItem(_human(s.size))
            size_item.setData(Qt.ItemDataRole.UserRole, s.size)
            flags_item = QTableWidgetItem(s.flags)
            align_item = _NumericItem(str(s.align))
            align_item.setData(Qt.ItemDataRole.UserRole, s.align)
            for c, item in enumerate(
                    (name_item, addr_item, size_item, flags_item, align_item)):
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        self.table.setUpdatesEnabled(True)
        self.lbl_title.setText(f"段表 Sections（{len(secs)}）")

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.lbl_title.setText("段表 Sections")


class _SummaryView(QWidget):
    """内存占用汇总 + ELF 元信息。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        layout.addWidget(StrongBodyLabel("内存占用 Memory usage"))
        form = QFormLayout()
        form.setSpacing(8)
        self.lbl_flash = BodyLabel("-")
        self.lbl_ram = BodyLabel("-")
        self.lbl_text = BodyLabel("-")
        self.lbl_data = BodyLabel("-")
        self.lbl_bss = BodyLabel("-")
        form.addRow(StrongBodyLabel("Flash（text+data）"), self.lbl_flash)
        form.addRow(StrongBodyLabel("RAM（data+bss）"), self.lbl_ram)
        form.addRow(BodyLabel("text（代码 + 只读）"), self.lbl_text)
        form.addRow(BodyLabel("data（已初始化）"), self.lbl_data)
        form.addRow(BodyLabel("bss（未初始化）"), self.lbl_bss)
        layout.addLayout(form)

        layout.addWidget(StrongBodyLabel("入口与向量 Entry & vectors"))
        form2 = QFormLayout()
        form2.setSpacing(8)
        self.lbl_entry = BodyLabel("-")
        self.lbl_sp = BodyLabel("-")
        self.lbl_reset = BodyLabel("-")
        form2.addRow(BodyLabel("Entry point"), self.lbl_entry)
        form2.addRow(BodyLabel("初始 SP（向量表[0]）"), self.lbl_sp)
        form2.addRow(BodyLabel("Reset_Handler（向量表[1]）"), self.lbl_reset)
        layout.addLayout(form2)

        self.lbl_hint = CaptionLabel(
            "内存占用采用 arm-none-eabi-size 的 Berkeley 统计方式："
            "text = 已加载的可执行/只读段（.text/.rodata/.isr_vector），"
            "data = 已初始化可写段（.data），bss = 未初始化段（.bss）；"
            "Flash = text + data，RAM = data + bss。"
            "初始 SP / Reset_Handler 按 Cortex-M 约定，从最低 LOAD 段头 8 字节"
            "读取（向量表第 0、1 个字），非 Cortex-M 架构无意义。")
        self.lbl_hint.setStyleSheet("color: #6b7280;")
        self.lbl_hint.setWordWrap(True)
        layout.addWidget(self.lbl_hint)
        layout.addStretch(1)

    def load(self, path: str) -> None:
        try:
            s = read_memory_summary(path)
            m = read_elf_meta(path)
        except FileParseError:
            self.clear()
            return
        self.lbl_flash.setText(_human(s.flash))
        self.lbl_ram.setText(_human(s.ram))
        self.lbl_text.setText(_human(s.text))
        self.lbl_data.setText(_human(s.data))
        self.lbl_bss.setText(_human(s.bss))
        self.lbl_entry.setText(f"0x{m.entry:08X}")
        self.lbl_sp.setText(
            f"0x{m.initial_sp:08X}" if m.initial_sp is not None else "—")
        self.lbl_reset.setText(
            f"0x{m.reset_handler:08X}" if m.reset_handler is not None else "—")

    def clear(self) -> None:
        for lbl in (self.lbl_flash, self.lbl_ram, self.lbl_text, self.lbl_data,
                    self.lbl_bss, self.lbl_entry, self.lbl_sp, self.lbl_reset):
            lbl.setText("-")


class FirmwareAnalysisView(QWidget):
    """SegmentedWidget 切换的「符号 / 段 / 占用汇总」复合视图。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.pivot = SegmentedWidget()
        self.stack = QStackedWidget()
        layout.addWidget(self.pivot)
        layout.addWidget(self.stack, 1)

        self.symbols = SymbolTableView()
        self.sections = _SectionsView()
        self.summary = _SummaryView()
        self._add(self.symbols, "symbols", "符号 Symbols")
        self._add(self.sections, "sections", "段 Sections")
        self._add(self.summary, "summary", "占用汇总 Summary")

        self.stack.currentChanged.connect(self._sync_pivot)
        self.pivot.setCurrentItem("symbols")
        self.stack.setCurrentWidget(self.symbols)

    def _add(self, w: QWidget, key: str, text: str) -> None:
        w.setObjectName(key)
        self.stack.addWidget(w)
        self.pivot.addItem(
            routeKey=key, text=text,
            onClick=lambda: self.stack.setCurrentWidget(w))

    def _sync_pivot(self, idx: int) -> None:
        self.pivot.setCurrentItem(self.stack.widget(idx).objectName())

    # ---- 公开 API：三视图共用同一路径 ----
    def load(self, path: str) -> None:
        self.symbols.load(path)
        self.sections.load(path)
        self.summary.load(path)

    def clear(self) -> None:
        self.symbols.clear()
        self.sections.clear()
        self.summary.clear()
