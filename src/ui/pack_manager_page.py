"""CMSIS-Pack 管理页面：存储路径 + 已装 pack 管理 + 在线搜索下载。

布局（三 CardWidget，透明 ScrollArea 整页包裹）：
1. Pack 存储 - 存储目录配置（默认 user_prefs 同级 packs/）
2. 已安装 pack - 文件名/厂商/版本/大小表格，子串过滤 + 删除
3. 下载 pack - 按 part_number 搜索 CMSIS-Pack 索引，分页 12/页，选中下载

延迟加载：构造时不 import pack_service（避免启动时加载 cmsis_pack_manager 链），
首次 showEvent 才 import + 枚举已装 pack。搜索/下载用独立线程池/QThread，
不阻塞 UI。控件用 qfluentwidgets，跟随全局 UI 字体 + i18n 重翻译。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, QThread, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
)

from . import _infobar
from ._scroll_helpers import make_transparent_scroll

_PAGE_SIZE = 12  # 在线搜索结果每页条数


class _PackSearchProbe(QObject):
    """搜索结果回传载体（QRunnable 无法直接持 Qt 信号）。"""

    done = Signal(list)


class _PackSearchRunnable(QRunnable):
    """在线 pack 搜索任务（QThreadPool 调度，首次需下载索引可能数秒）。"""

    def __init__(self, query: str, probe: _PackSearchProbe) -> None:
        super().__init__()
        self._query = query
        self._probe = probe

    def run(self) -> None:
        from core.pack_service import search_packs
        try:
            results = search_packs(self._query)
        except Exception:
            results = []
        try:
            self._probe.done.emit(results)
        except RuntimeError:
            # 页面已销毁（关窗/teardown）时 probe 随之删除，池化线程后到的 emit 静默丢弃。
            pass


class _PackMigrateProbe(QObject):
    """迁移结果回传载体。"""

    done = Signal(int)


class _PackMigrateRunnable(QRunnable):
    """旧 pack 迁移任务（QThreadPool 调度，复制文件可能数秒）。"""

    def __init__(self, probe: _PackMigrateProbe) -> None:
        super().__init__()
        self._probe = probe

    def run(self) -> None:
        from core.pack_service import migrate_legacy_packs
        try:
            count = migrate_legacy_packs()
        except Exception:
            count = -1
        try:
            self._probe.done.emit(count)
        except RuntimeError:
            pass


class _PackDownloadWorker(QObject):
    """pack 下载任务（moveToThread 到独立 QThread 执行）。"""

    log_message = Signal(str, str)  # (level, msg)
    finished = Signal(str)  # "downloaded" / "skipped" / "failed"

    def __init__(self, part_number: str) -> None:
        super().__init__()
        self._part = part_number

    @Slot()
    def run(self) -> None:
        from core.pack_service import download_pack
        ok = download_pack(
            self._part,
            log=lambda lv, msg: self.log_message.emit(lv, msg),
        )
        self.finished.emit(ok)


class PackManagerPage(QWidget):
    """CMSIS-Pack 管理页面。延迟加载，构造零 IO。"""

    packs_changed = Signal()  # 下载/删除/迁移后 emit，通知 FlashPage 刷新目标设备下拉

    def __init__(self, cfg, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pack-manager")
        self._cfg = cfg
        self._loaded = False  # 首次 showEvent 后置 True
        # 下载 worker 线程
        self._dl_thread: QThread | None = None
        self._dl_worker: _PackDownloadWorker | None = None
        # 搜索结果分页状态
        self._search_results: list[str] = []
        self._page_index = 0
        # 搜索结果回传
        self._search_probe = _PackSearchProbe()
        self._search_probe.done.connect(self._on_search_done, Qt.QueuedConnection)
        # 迁移结果回传
        self._migrate_probe = _PackMigrateProbe()
        self._migrate_probe.done.connect(self._on_migrate_done, Qt.QueuedConnection)
        self._build_ui()
        self._connect_signals()
        # 不在构造时加载（避免 import pack_service 链 + 读盘拖慢启动）

    # ------------------------------------------------------------------
    # UI 构造（零 pack_service 依赖）
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll, inner = make_transparent_scroll(self, "pack-manager")
        outer.addWidget(self._scroll)

        v = QVBoxLayout(inner)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(12)

        v.addWidget(self._build_path_card())
        # 已安装 + 下载 两卡片左右并排，等分水平宽度（stretch=1 各占一半）；
        # cards_row stretch=1 撑满垂直剩余空间，卡片 SizePolicy 垂直 Expanding
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        cards_row.addWidget(self._build_installed_card(), 1)
        cards_row.addWidget(self._build_download_card(), 1)
        v.addLayout(cards_row, 1)

    def _build_path_card(self) -> CardWidget:
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        self._lbl_path_title = SubtitleLabel(self.tr("CMSIS-Pack 存储"))
        lay.addWidget(self._lbl_path_title)

        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 4)
        self._lbl_path = BodyLabel(self.tr("存储路径:"))
        row.addWidget(self._lbl_path)
        self.le_path = LineEdit(self)
        self.le_path.setReadOnly(True)
        self.le_path.setFixedHeight(33)
        self.le_path.setPlaceholderText(self.tr("首次打开页面时加载"))
        row.addWidget(self.le_path, 1)
        self.btn_browse = PushButton(self.tr("更改…"), self)
        self.btn_browse.setFixedHeight(33)
        row.addWidget(self.btn_browse)
        self.btn_migrate = PushButton(self.tr("迁移旧 CMSIS-Pack"), self)
        self.btn_migrate.setFixedHeight(33)
        self.btn_migrate.setToolTip(
            self.tr("把 cmsis-pack-manager 全局目录的旧 CMSIS-Pack 复制到当前路径")
        )
        row.addWidget(self.btn_migrate)
        lay.addLayout(row)
        return card

    def _build_installed_card(self) -> CardWidget:
        card = CardWidget(self)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        lay = QVBoxLayout(card)
        self._lbl_installed_title = SubtitleLabel(self.tr("已安装 CMSIS-Pack"))
        lay.addWidget(self._lbl_installed_title)

        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 4)
        self._lbl_filter = BodyLabel(self.tr("过滤:"))
        row.addWidget(self._lbl_filter)
        self.le_filter = LineEdit(self)
        self.le_filter.setFixedHeight(33)
        self.le_filter.setPlaceholderText(self.tr("按文件名子串过滤（大小写无关）"))
        row.addWidget(self.le_filter, 1)
        self.btn_refresh = PushButton(self.tr("刷新"), self)
        self.btn_refresh.setFixedHeight(33)
        row.addWidget(self.btn_refresh)
        self.btn_delete = PushButton(self.tr("删除选中"), self)
        self.btn_delete.setFixedHeight(33)
        row.addWidget(self.btn_delete)
        lay.addLayout(row)

        self.tbl_installed = TableWidget(self)
        self.tbl_installed.setColumnCount(4)
        self.tbl_installed.setRowCount(0)
        self.tbl_installed.setHorizontalHeaderLabels([
            self.tr("文件名"), self.tr("厂商"), self.tr("版本"), self.tr("大小"),
        ])
        self.tbl_installed.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_installed.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_installed.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_installed.setAlternatingRowColors(True)
        self.tbl_installed.verticalHeader().setVisible(False)
        self.tbl_installed.setMinimumHeight(180)
        lay.addWidget(self.tbl_installed, 1)
        return card

    def _build_download_card(self) -> CardWidget:
        card = CardWidget(self)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        lay = QVBoxLayout(card)
        self._lbl_download_title = SubtitleLabel(self.tr("下载 CMSIS-Pack"))
        lay.addWidget(self._lbl_download_title)

        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 4)
        self._lbl_search = BodyLabel(self.tr("搜索:"))
        row.addWidget(self._lbl_search)
        self.le_search = LineEdit(self)
        self.le_search.setFixedHeight(33)
        self.le_search.setPlaceholderText(self.tr("输入 part_number（如 STM32F103C8）"))
        row.addWidget(self.le_search, 1)
        self.btn_search = PushButton(self.tr("搜索"), self)
        self.btn_search.setFixedHeight(33)
        row.addWidget(self.btn_search)
        lay.addLayout(row)

        self.tbl_search = TableWidget(self)
        self.tbl_search.setColumnCount(1)
        self.tbl_search.setRowCount(0)
        self.tbl_search.setHorizontalHeaderLabels([self.tr("Part Number")])
        self.tbl_search.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_search.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_search.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_search.setAlternatingRowColors(True)
        self.tbl_search.verticalHeader().setVisible(False)
        self.tbl_search.setMinimumHeight(180)
        lay.addWidget(self.tbl_search, 1)

        row_page = QHBoxLayout()
        row_page.setContentsMargins(0, 4, 0, 4)
        self.btn_prev = PushButton(self.tr("上一页"), self)
        self.btn_prev.setFixedHeight(33)
        row_page.addWidget(self.btn_prev)
        self.lbl_page = BodyLabel(self.tr("0 / 0"))
        row_page.addWidget(self.lbl_page)
        self.btn_next = PushButton(self.tr("下一页"), self)
        self.btn_next.setFixedHeight(33)
        row_page.addWidget(self.btn_next)
        row_page.addStretch(1)
        self.btn_download = PrimaryPushButton(self.tr("下载"), self)
        self.btn_download.setFixedHeight(33)
        row_page.addWidget(self.btn_download)
        lay.addLayout(row_page)
        return card

    def _connect_signals(self) -> None:
        self.btn_browse.clicked.connect(self._on_browse)
        self.btn_migrate.clicked.connect(self._on_migrate)
        self.btn_refresh.clicked.connect(self._reload_installed)
        self.btn_delete.clicked.connect(self._on_delete)
        self.le_filter.textChanged.connect(self._on_filter_changed)
        self.btn_search.clicked.connect(self._on_search)
        self.le_search.returnPressed.connect(self._on_search)
        self.btn_prev.clicked.connect(self._prev_page)
        self.btn_next.clicked.connect(self._next_page)
        self.btn_download.clicked.connect(self._on_download)

    # ------------------------------------------------------------------
    # 延迟加载
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self._lazy_load()

    def _lazy_load(self) -> None:
        from core.pack_service import get_pack_data_path
        self.le_path.setText(get_pack_data_path())
        self._reload_installed()

    # ------------------------------------------------------------------
    # 已安装 pack 列表 / 过滤
    # ------------------------------------------------------------------
    def _reload_installed(self) -> None:
        from core.pack_service import list_installed_packs
        packs = list_installed_packs()
        self.tbl_installed.setRowCount(0)
        for p in packs:
            self._append_installed_row(p)

    def _append_installed_row(self, p) -> None:
        row = self.tbl_installed.rowCount()
        self.tbl_installed.insertRow(row)
        self.tbl_installed.setItem(row, 0, QTableWidgetItem(p.file_name))
        self.tbl_installed.setItem(row, 1, QTableWidgetItem(p.vendor))
        self.tbl_installed.setItem(row, 2, QTableWidgetItem(p.version))
        self.tbl_installed.setItem(row, 3, QTableWidgetItem(self._format_size(p.size_bytes)))

    def _on_filter_changed(self, text: str) -> None:
        # setRowHidden 不重建行，O(n) 过滤，保性能
        q = text.strip().upper()
        for row in range(self.tbl_installed.rowCount()):
            file_name = self.tbl_installed.item(row, 0).text().upper()
            self.tbl_installed.setRowHidden(row, bool(q) and q not in file_name)

    @staticmethod
    def _format_size(size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    # ------------------------------------------------------------------
    # 路径 / 删除
    # ------------------------------------------------------------------
    def _on_browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from core.pack_service import get_pack_data_path, set_pack_data_path
        path = QFileDialog.getExistingDirectory(
            self, self.tr("选择 CMSIS-Pack 存储目录"), get_pack_data_path()
        )
        if not path:
            return
        set_pack_data_path(path)
        self.le_path.setText(path)
        self._reload_installed()
        # 路径变更后已下载的搜索索引失效，清空搜索结果
        self._search_results = []
        self._page_index = 0
        self._refresh_search_table()

    def _on_migrate(self) -> None:
        """异步迁移全局目录旧 pack 到当前 pack_data_path。"""
        self.btn_migrate.setEnabled(False)
        self.btn_migrate.setText(self.tr("迁移中…"))
        runnable = _PackMigrateRunnable(self._migrate_probe)
        QThreadPool.globalInstance().start(runnable)

    @Slot(int)
    def _on_migrate_done(self, count: int) -> None:
        self.btn_migrate.setEnabled(True)
        self.btn_migrate.setText(self.tr("迁移旧 CMSIS-Pack"))
        self._reload_installed()
        if count > 0:
            _infobar.ok(self, self.tr("迁移完成"), self.tr("已迁移 {n} 个 CMSIS-Pack").format(n=count))
            self.packs_changed.emit()
        elif count == 0:
            _infobar.info(self, self.tr("迁移"), self.tr("无旧 CMSIS-Pack 可迁移"))
        else:
            _infobar.error(self, self.tr("迁移失败"), "")

    def _on_delete(self) -> None:
        from core.pack_service import delete_pack
        row = self.tbl_installed.currentRow()
        if row < 0:
            _infobar.warn(self, self.tr("提示"), self.tr("请先选中要删除的 CMSIS-Pack"))
            return
        file_name = self.tbl_installed.item(row, 0).text()
        if delete_pack(file_name):
            self._reload_installed()
            _infobar.ok(self, self.tr("已删除"), file_name)
            self.packs_changed.emit()
        else:
            _infobar.error(self, self.tr("删除失败"), file_name)

    # ------------------------------------------------------------------
    # 在线搜索 / 分页
    # ------------------------------------------------------------------
    def _on_search(self) -> None:
        query = self.le_search.text().strip()
        if not query:
            _infobar.warn(self, self.tr("提示"), self.tr("请输入 part_number"))
            return
        self.btn_search.setEnabled(False)
        self.btn_search.setText(self.tr("搜索中…"))
        runnable = _PackSearchRunnable(query, self._search_probe)
        QThreadPool.globalInstance().start(runnable)

    @Slot(list)
    def _on_search_done(self, results: list) -> None:
        self.btn_search.setEnabled(True)
        self.btn_search.setText(self.tr("搜索"))
        self._search_results = list(results)
        self._page_index = 0
        self._refresh_search_table()
        n = len(self._search_results)
        if n:
            _infobar.info(self, self.tr("搜索完成"), self.tr("找到 {n} 个匹配").format(n=n))
        else:
            _infobar.warn(self, self.tr("无匹配"), "")

    def _refresh_search_table(self) -> None:
        self.tbl_search.setRowCount(0)
        total = len(self._search_results)
        if total == 0:
            self.lbl_page.setText(self.tr("0 / 0"))
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
        start = self._page_index * _PAGE_SIZE
        end = min(start + _PAGE_SIZE, total)
        for part in self._search_results[start:end]:
            row = self.tbl_search.rowCount()
            self.tbl_search.insertRow(row)
            self.tbl_search.setItem(row, 0, QTableWidgetItem(part))
        total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
        self.lbl_page.setText(
            self.tr("{cur} / {total}").format(cur=self._page_index + 1, total=total_pages)
        )
        self.btn_prev.setEnabled(self._page_index > 0)
        self.btn_next.setEnabled(self._page_index < total_pages - 1)

    def _prev_page(self) -> None:
        if self._page_index > 0:
            self._page_index -= 1
            self._refresh_search_table()

    def _next_page(self) -> None:
        total = len(self._search_results)
        total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
        if self._page_index < total_pages - 1:
            self._page_index += 1
            self._refresh_search_table()

    # ------------------------------------------------------------------
    # 下载（QThread + QObject worker）
    # ------------------------------------------------------------------
    def _on_download(self) -> None:
        # 优先取搜索结果表格选中行的 part_number；未选中则回退搜索框文本
        row = self.tbl_search.currentRow()
        if row >= 0:
            part = self.tbl_search.item(row, 0).text().strip()
        else:
            part = self.le_search.text().strip()
        if not part:
            _infobar.warn(
                self, self.tr("提示"),
                self.tr("请先搜索并选中 CMSIS-Pack，或在搜索框输入 part_number"),
            )
            return
        if self._dl_thread is not None:
            _infobar.warn(self, self.tr("提示"), self.tr("下载进行中，请等待"))
            return
        self.btn_download.setEnabled(False)
        self.btn_download.setText(self.tr("下载中…"))
        self._dl_thread = QThread()
        self._dl_worker = _PackDownloadWorker(part)
        self._dl_worker.moveToThread(self._dl_thread)
        self._dl_thread.started.connect(self._dl_worker.run)
        self._dl_worker.finished.connect(self._on_download_finished, Qt.QueuedConnection)
        self._dl_thread.start()

    @Slot(str)
    def _on_download_finished(self, status: str) -> None:
        self.btn_download.setEnabled(True)
        self.btn_download.setText(self.tr("下载"))
        if self._dl_worker is not None:
            self._dl_worker.deleteLater()
            self._dl_worker = None
        if self._dl_thread is not None:
            self._dl_thread.quit()
            self._dl_thread.wait()
            self._dl_thread.deleteLater()
            self._dl_thread = None
        self._reload_installed()
        if status == "downloaded":
            _infobar.ok(self, self.tr("下载完成"), "")
            self.packs_changed.emit()
        elif status == "skipped":
            _infobar.info(self, self.tr("已安装"), self.tr("已安装，跳过下载"))
        else:
            _infobar.error(self, self.tr("下载失败"), "")

    # ------------------------------------------------------------------
    # i18n
    # ------------------------------------------------------------------
    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()
        super().changeEvent(event)

    def _retranslate_ui(self) -> None:
        # 卡片标题
        self._lbl_path_title.setText(self.tr("CMSIS-Pack 存储"))
        self._lbl_installed_title.setText(self.tr("已安装 CMSIS-Pack"))
        self._lbl_download_title.setText(self.tr("下载 CMSIS-Pack"))
        # 行标签
        self._lbl_path.setText(self.tr("存储路径:"))
        self._lbl_filter.setText(self.tr("过滤:"))
        self._lbl_search.setText(self.tr("搜索:"))
        # 按钮
        self.btn_browse.setText(self.tr("更改…"))
        self.btn_migrate.setText(self.tr("迁移旧 CMSIS-Pack"))
        self.btn_migrate.setToolTip(
            self.tr("把 cmsis-pack-manager 全局目录的旧 CMSIS-Pack 复制到当前路径")
        )
        self.btn_refresh.setText(self.tr("刷新"))
        self.btn_delete.setText(self.tr("删除选中"))
        self.btn_search.setText(self.tr("搜索"))
        self.btn_prev.setText(self.tr("上一页"))
        self.btn_next.setText(self.tr("下一页"))
        self.btn_download.setText(self.tr("下载"))
        # 占位
        self.le_path.setPlaceholderText(self.tr("首次打开页面时加载"))
        self.le_filter.setPlaceholderText(self.tr("按文件名子串过滤（大小写无关）"))
        self.le_search.setPlaceholderText(self.tr("输入 part_number（如 STM32F103C8）"))
        # 表格 header
        self.tbl_installed.setHorizontalHeaderLabels([
            self.tr("文件名"), self.tr("厂商"), self.tr("版本"), self.tr("大小"),
        ])
        self.tbl_search.setHorizontalHeaderLabels([self.tr("Part Number")])
        # 分页标签（按当前状态重算）
        self._refresh_search_table()
        # 按钮文字：按当前运行态重设（禁用态 = 进行中）
        self.btn_search.setText(
            self.tr("搜索中…") if not self.btn_search.isEnabled() else self.tr("搜索")
        )
        self.btn_download.setText(
            self.tr("下载中…") if not self.btn_download.isEnabled() else self.tr("下载")
        )
        self.btn_migrate.setText(
            self.tr("迁移中…") if not self.btn_migrate.isEnabled() else self.tr("迁移旧 CMSIS-Pack")
        )

    # ------------------------------------------------------------------
    # 关窗清理
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """主窗口 closeEvent 调；清理下载 worker 线程 + 搜索线程池。"""
        if self._dl_thread is not None:
            self._dl_thread.quit()
            self._dl_thread.wait()
            self._dl_thread = None
        QThreadPool.globalInstance().waitForDone(500)
