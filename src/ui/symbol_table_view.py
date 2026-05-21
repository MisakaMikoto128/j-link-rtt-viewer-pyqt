"""ELF/axf 符号表查看控件：搜索过滤 + 列排序 + 复制 + 统计。

纯展示控件，不碰 worker。数据来自 core.flash_file_parser.read_symbols。
- 顶部：标题(含统计) / 搜索框 / 「显示全部符号」勾选 / 复制按钮
- 表格：Name / Address / Size / Type / Section，可点列头排序
  （Address、Size 用 _NumericItem 保证按数值而非字符串排序）
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    PushButton,
    SearchLineEdit,
    StrongBodyLabel,
    TableWidget,
)

from core.flash_file_parser import FileParseError, Symbol, read_symbols

_COLUMNS = ["Name", "Address", "Size", "Type", "Section"]
_ALL_TYPES = "全部类型"
_ALL_BINDS = "全部绑定"


class _NumericItem(QTableWidgetItem):
    """按存入 UserRole 的数值比较，保证 Address/Size 列数值排序。"""

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D105
        a = self.data(Qt.ItemDataRole.UserRole)
        b = other.data(Qt.ItemDataRole.UserRole)
        if a is None or b is None:
            return super().__lt__(other)
        return a < b


class SymbolTableView(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._symbols: list[Symbol] = []  # 当前 func_and_data 范围内的全集
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.lbl_title = StrongBodyLabel("符号表")
        top.addWidget(self.lbl_title)
        top.addSpacing(12)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("按名称过滤…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        top.addWidget(self.search)

        top.addWidget(BodyLabel("类型"))
        self.cmb_type = ComboBox()
        self.cmb_type.setMinimumWidth(110)
        top.addWidget(self.cmb_type)

        top.addWidget(BodyLabel("绑定"))
        self.cmb_bind = ComboBox()
        self.cmb_bind.setMinimumWidth(110)
        top.addWidget(self.cmb_bind)

        self.chk_all = CheckBox("显示全部符号")
        top.addWidget(self.chk_all)
        top.addStretch(1)
        self.btn_copy = PushButton("复制选中")
        top.addWidget(self.btn_copy)
        layout.addLayout(top)

        self.table = TableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(_COLUMNS)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.search.textChanged.connect(self._apply_filter)
        self.cmb_type.currentTextChanged.connect(self._apply_filter)
        self.cmb_bind.currentTextChanged.connect(self._apply_filter)
        self.chk_all.toggled.connect(self._reload)
        self.btn_copy.clicked.connect(self._copy_selected)

        self._current_path: str | None = None

    # ---- 公开 API ----
    def load(self, path: str) -> None:
        """读取并显示某 ELF/axf 的符号表。解析失败时清空并提示。"""
        self._current_path = path
        self._reload()

    def clear(self) -> None:
        self._current_path = None
        self._symbols = []
        self.table.setRowCount(0)
        self.lbl_title.setText("符号表")
        self._repopulate_filter_combos()

    # ---- 内部 ----
    def _reload(self) -> None:
        if not self._current_path:
            self.clear()
            return
        try:
            self._symbols = read_symbols(
                self._current_path,
                func_and_data_only=not self.chk_all.isChecked(),
            )
        except FileParseError:
            self._symbols = []
        self._repopulate_filter_combos()
        self._apply_filter()

    def _repopulate_filter_combos(self) -> None:
        """按当前符号集刷新「类型 / 绑定」下拉，尽量保留原选择。"""
        for combo, attr, all_label in (
                (self.cmb_type, "type", _ALL_TYPES),
                (self.cmb_bind, "bind", _ALL_BINDS)):
            prev = combo.currentText()
            values = sorted({getattr(s, attr) for s in self._symbols})
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label)
            for v in values:
                combo.addItem(v)
            idx = combo.findText(prev)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _apply_filter(self) -> None:
        kw = self.search.text().strip().lower()
        sel_type = self.cmb_type.currentText()
        sel_bind = self.cmb_bind.currentText()
        rows = [
            s for s in self._symbols
            if (not kw or kw in s.name.lower())
            and (sel_type in ("", _ALL_TYPES) or s.type == sel_type)
            and (sel_bind in ("", _ALL_BINDS) or s.bind == sel_bind)
        ]

        # 重填期间关排序，避免边插边排乱序
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, s in enumerate(rows):
            name_item = QTableWidgetItem(s.name)
            addr_item = _NumericItem(f"0x{s.address:08X}")
            addr_item.setData(Qt.ItemDataRole.UserRole, s.address)
            size_item = _NumericItem(str(s.size))
            size_item.setData(Qt.ItemDataRole.UserRole, s.size)
            type_item = QTableWidgetItem(s.type)
            sec_item = QTableWidgetItem(s.section)
            for c, item in enumerate(
                    (name_item, addr_item, size_item, type_item, sec_item)):
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)

        total = len(self._symbols)
        shown = len(rows)
        suffix = f"（{shown}/{total}）" if shown != total else f"（{total}）"
        self.lbl_title.setText(f"符号表{suffix}")

    def _copy_selected(self) -> None:
        from PySide6.QtWidgets import QApplication
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows:
            return
        lines = []
        for r in rows:
            name = self.table.item(r, 0).text()
            addr = self.table.item(r, 1).text()
            size = self.table.item(r, 2).text()
            lines.append(f"{name}\t{addr}\t{size}")
        QApplication.clipboard().setText("\n".join(lines))
