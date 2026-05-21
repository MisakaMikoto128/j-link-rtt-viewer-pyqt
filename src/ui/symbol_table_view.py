"""ELF/axf 符号表查看控件：类别/绑定 chip 多选过滤 + 名称搜索 + 列排序 + 复制。

设计要点（对齐参考稿）：
- 一次性读入全部符号，过滤全部在显示层完成——没有「先决定读多少再过滤」的隐藏层级。
- 类别（Functions/Variables/File markers/Sections/Other）和绑定（Global/Local/Weak）
  都是同一层的 chip toggle：勾了就显示、不勾就隐藏，逻辑一致。
- chip 文字中英并列，hover 有 tooltip 说明对应的 ELF 符号类型/绑定。
- 默认只亮 Functions + Variables；底部一行说明告诉用户其余类别是什么。
- Type 列用淡色底 + 强调色文字做成 pill，区分函数/变量等。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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
    CaptionLabel,
    FluentIcon as FIF,
    PillPushButton,
    PushButton,
    SearchLineEdit,
    StrongBodyLabel,
    TableWidget,
)

from core.flash_file_parser import (
    FileParseError,
    Symbol,
    read_sections,
    read_symbols,
)

_COLUMNS = ["Name", "Address", "Size", "Type", "Binding", "Section", "% 段"]

# 类别 chip：key, 英文, 中文, 图标, tooltip, 默认是否亮
_CATEGORIES = [
    ("func",    "Functions",    "函数",     FIF.CODE,
     "代码函数 (STT_FUNC)"),
    ("var",     "Variables",    "变量",     FIF.TAG,
     "全局 / 静态变量 (STT_OBJECT)"),
    ("file",    "File markers", "文件标记", FIF.DOCUMENT,
     "源文件名标记，编译器生成 (STT_FILE)"),
    ("section", "Sections",     "段",       FIF.TILES,
     "段符号，编译器生成 (STT_SECTION)"),
    ("other",   "Other",        "其它",     FIF.MORE,
     "无类型 / 其它符号 (STT_NOTYPE 等)"),
]
_DEFAULT_CATEGORIES = {"func", "var"}

# 绑定 chip：key(=Symbol.bind), 英文, 中文, tooltip
_BINDINGS = [
    ("GLOBAL", "Global", "全局", "全局符号 (STB_GLOBAL)"),
    ("LOCAL",  "Local",  "局部", "局部符号 (STB_LOCAL)"),
    ("WEAK",   "Weak",   "弱",   "弱符号 (STB_WEAK)"),
]

# Type 列 pill 配色：(底色, 文字色)。QColor 在模块加载时构造一次，
# 避免给上万行符号逐行 new QColor。
_TYPE_QCOLORS = {
    "FUNC":    (QColor("#ede9fe"), QColor("#6d28d9")),   # 紫
    "OBJECT":  (QColor("#dbeafe"), QColor("#1d4ed8")),   # 蓝
    "FILE":    (QColor("#f1f5f9"), QColor("#475569")),   # 灰
    "SECTION": (QColor("#ccfbf1"), QColor("#0f766e")),   # 青
}
_TYPE_QCOLOR_DEFAULT = (QColor("#f1f5f9"), QColor("#475569"))


def _category_of(sym_type: str) -> str:
    if sym_type == "FUNC":
        return "func"
    if sym_type == "OBJECT":
        return "var"
    if sym_type == "FILE":
        return "file"
    if sym_type == "SECTION":
        return "section"
    return "other"


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
        self._symbols: list[Symbol] = []  # 当前文件的全部符号
        self._section_sizes: dict[str, int] = {}  # {段名: 段大小}，算占比用
        self._current_path: str | None = None
        self._cat_chips: dict[str, PillPushButton] = {}
        self._bind_chips: dict[str, PillPushButton] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ---- 标题行：标题 + 统计 + 复制 ----
        top = QHBoxLayout()
        self.lbl_title = StrongBodyLabel("符号表 Symbol Table")
        top.addWidget(self.lbl_title)
        self.lbl_count = CaptionLabel("")
        self.lbl_count.setStyleSheet("color: #6b7280;")
        top.addWidget(self.lbl_count)
        top.addStretch(1)
        self.btn_copy = PushButton("复制选中 Copy")
        self.btn_copy.setToolTip("复制选中行：名称 + 地址 + 大小")
        top.addWidget(self.btn_copy)
        layout.addLayout(top)

        # ---- 搜索框 ----
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("按名称过滤  Filter by name…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        # ---- 类别 chip 行 ----
        cat_row = QHBoxLayout()
        cat_row.setSpacing(8)
        cat_row.addWidget(BodyLabel("显示 Show"))
        for key, en, zh, icon, tip in _CATEGORIES:
            chip = PillPushButton(icon, f"{en} {zh}")
            chip.setCheckable(True)
            chip.setChecked(key in _DEFAULT_CATEGORIES)
            chip.setToolTip(tip)
            chip.toggled.connect(self._apply_filter)
            self._cat_chips[key] = chip
            cat_row.addWidget(chip)
        cat_row.addStretch(1)
        layout.addLayout(cat_row)

        # ---- 绑定 chip 行 ----
        bind_row = QHBoxLayout()
        bind_row.setSpacing(8)
        bind_row.addWidget(BodyLabel("绑定 Binding"))
        for key, en, zh, tip in _BINDINGS:
            chip = PillPushButton(f"{en} {zh}")
            chip.setCheckable(True)
            chip.setChecked(True)  # 默认三种绑定都显示
            chip.setToolTip(tip)
            chip.toggled.connect(self._apply_filter)
            self._bind_chips[key] = chip
            bind_row.addWidget(chip)
        bind_row.addStretch(1)
        layout.addLayout(bind_row)

        # ---- 表格 ----
        self.table = TableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        # 列宽用固定/可拖动而非 ResizeToContents：后者每次模型变更都要扫描
        # 全表所有行重算列宽，大符号表(上万行)切换 chip 时会明显卡顿。
        # Name 拉伸占满，其余给定合理初始宽度、用户可拖。
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(_COLUMNS)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        for col, w in ((1, 110), (2, 70), (3, 90), (4, 90), (5, 140), (6, 70)):
            self.table.setColumnWidth(col, w)
        layout.addWidget(self.table, 1)

        # ---- 底部说明 ----
        self.lbl_hint = CaptionLabel(
            "默认仅显示 Functions 函数 与 Variables 变量。"
            "File markers / Sections / Other 是编译器生成的辅助符号"
            "（源文件名标记、段符号、无类型符号等），需要时点亮对应类别查看。")
        self.lbl_hint.setStyleSheet("color: #6b7280;")
        self.lbl_hint.setWordWrap(True)
        layout.addWidget(self.lbl_hint)

        self.search.textChanged.connect(self._apply_filter)
        self.btn_copy.clicked.connect(self._copy_selected)

    # ---- 公开 API ----
    def load(self, path: str) -> None:
        """读取并显示某 ELF/axf 的符号表。解析失败时清空。"""
        self._current_path = path
        try:
            self._symbols = read_symbols(path, func_and_data_only=False)
        except FileParseError:
            self._symbols = []
        try:
            self._section_sizes = {s.name: s.size for s in read_sections(path)}
        except FileParseError:
            self._section_sizes = {}
        self._apply_filter()

    def clear(self) -> None:
        self._current_path = None
        self._symbols = []
        self._section_sizes = {}
        self.table.setRowCount(0)
        self.lbl_count.setText("")

    # ---- 内部 ----
    def _apply_filter(self) -> None:
        kw = self.search.text().strip().lower()
        active_cats = {k for k, c in self._cat_chips.items() if c.isChecked()}
        active_binds = {k for k, c in self._bind_chips.items() if c.isChecked()}
        rows = [
            s for s in self._symbols
            if (not kw or kw in s.name.lower())
            and _category_of(s.type) in active_cats
            and s.bind in active_binds
        ]

        # 重填期间关刷新 + 关排序：避免逐行触发重绘/边插边排乱序
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, s in enumerate(rows):
            name_item = QTableWidgetItem(s.name)
            addr_item = _NumericItem(f"0x{s.address:08X}")
            addr_item.setData(Qt.ItemDataRole.UserRole, s.address)
            size_item = _NumericItem(str(s.size))
            size_item.setData(Qt.ItemDataRole.UserRole, s.size)
            type_item = QTableWidgetItem(s.type)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            bg, fg = _TYPE_QCOLORS.get(s.type, _TYPE_QCOLOR_DEFAULT)
            type_item.setBackground(bg)
            type_item.setForeground(fg)
            bind_item = QTableWidgetItem(s.bind)
            sec_item = QTableWidgetItem(s.section)
            sec_size = self._section_sizes.get(s.section, 0)
            if sec_size > 0:
                pct = s.size * 100.0 / sec_size
                pct_item = _NumericItem(f"{pct:.1f}%")
                pct_item.setData(Qt.ItemDataRole.UserRole, pct)
            else:
                pct_item = _NumericItem("-")
                pct_item.setData(Qt.ItemDataRole.UserRole, -1.0)
            pct_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for c, item in enumerate(
                    (name_item, addr_item, size_item, type_item,
                     bind_item, sec_item, pct_item)):
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        self.table.setUpdatesEnabled(True)

        total = len(self._symbols)
        shown = len(rows)
        if total == 0:
            self.lbl_count.setText("")
        elif shown == total:
            self.lbl_count.setText(f"{total} 符号 symbols")
        else:
            self.lbl_count.setText(f"显示 {shown} / 共 {total}")

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
