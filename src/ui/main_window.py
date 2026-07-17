"""主窗口：FluentWindow + 左侧导航 + JLinkWorker（外部 QThread）生命周期。"""
from __future__ import annotations

import base64

from PySide6.QtCore import QByteArray, QEvent, QThread
from PySide6.QtGui import QCloseEvent, QIcon, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, NavigationItemPosition

from core.config_service import ConfigService
from core._ui_font import (
    _sync_fluent_font_families,
    resolve_ui_family,
    sync_qss_font_locked_widgets,
)
from core.i18n_service import switch_language as _switch_language
from core.jlink_worker import JLinkWorker
from core.logger import get_logger

from .about_page import AboutPage
from .flash_page import FlashPage
from .memory_viewer_page import MemoryViewerPage
from .rtt_monitor_page import RTTMonitorPage
from .settings_page import SettingsPage


class MainWindow(FluentWindow):
    def __init__(self, cfg: ConfigService) -> None:
        super().__init__()
        self._cfg = cfg
        self._logger = get_logger()

        # 1. 创建 worker + 独立 QThread（不是 worker 自己继承 QThread！）
        self.worker_thread = QThread(self)
        self.worker = JLinkWorker()
        self.worker.set_initial_encoding(cfg.get("rtt_encoding"))
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.initialize)
        self.worker_thread.start()

        # 2. 各页面
        self.rtt_page = RTTMonitorPage(self.worker, cfg, self)
        self.memory_page = MemoryViewerPage(self.worker, cfg, self)
        self.flash_page = FlashPage(cfg, self)
        self.settings_page = SettingsPage(cfg, self)
        self.about_page = AboutPage(self)

        # 3. 导航 — 存储 route_key → tr_key 映射，用于语言切换时刷新
        self._nav_items: list[tuple[str, str]] = []
        self._add_nav(self.rtt_page, FIF.SPEED_HIGH, "RTT 监控")
        self._add_nav(self.memory_page, FIF.CODE, "内存查看")
        self._add_nav(self.flash_page, FIF.SEND_FILL, "固件烧录")
        self.navigationInterface.addSeparator()
        self._add_nav(self.settings_page, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM)
        self._add_nav(self.about_page, FIF.INFO, "关于", NavigationItemPosition.BOTTOM)

        # 4. ConfigService 的 rtt_poll_interval_changed / rtt_encoding_changed 信号连到 worker
        self._cfg.rtt_poll_interval_changed.connect(self.worker.set_poll_interval_requested)
        self._cfg.rtt_encoding_changed.connect(self.worker.set_encoding_requested)
        self._cfg.language_changed.connect(_switch_language)
        self._cfg.ui_font_size_changed.connect(self._apply_ui_font_size)
        self._cfg.ui_font_family_changed.connect(self._apply_ui_font_family)

        # 5. 全局快捷键
        QShortcut(QKeySequence("F2"), self, self.rtt_page.on_shortcut_connect)
        QShortcut(QKeySequence("F3"), self, self.rtt_page.on_shortcut_disconnect)
        QShortcut(QKeySequence("F4"), self, self.rtt_page.on_shortcut_reset)
        QShortcut(QKeySequence("Ctrl+F"), self, self.rtt_page.on_shortcut_find)
        QShortcut(QKeySequence("Ctrl+H"), self, self.rtt_page.on_shortcut_replace)

        # 7. 窗口属性
        self.setWindowTitle(self.tr("J-Link RTT Viewer"))
        from core._paths import find_app_icon
        icon_path = find_app_icon()
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(480, 540)
        self._restore_geometry()
        # 首次刷新全局界面字体：qfluentwidgets 控件用自己的默认字号（pixelSize），
        # 不继承 QApplication.setFont，故构造后遍历 setFont 强制覆盖（否则首次显示
        # 用控件默认字号，改字号才生效）。改默认 10pt 时正因此 bug 才被发现。
        self._apply_ui_font(cfg.get("ui_font_family") or "", int(cfg.get("ui_font_size") or 9))

    def _add_nav(self, widget, icon, text_key, position=NavigationItemPosition.TOP) -> None:
        """添加导航项并记录 route_key → tr_key 映射。"""
        self.addSubInterface(widget, icon, self.tr(text_key), position)
        self._nav_items.append((widget.objectName(), text_key))

    def changeEvent(self, event: QEvent) -> None:
        """语言切换时刷新导航项文字 + 窗口标题。"""
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()
        super().changeEvent(event)

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(self.tr("J-Link RTT Viewer"))
        for route_key, text_key in self._nav_items:
            item = self.navigationInterface.widget(route_key)
            if item is not None:
                try:
                    item.setText(self.tr(text_key))
                except Exception:
                    pass  # 某些导航 widget 可能没有 setText

    def _apply_ui_font_size(self, size: int) -> None:
        """size 运行时变更入口（ConfigService.ui_font_size_changed 触发）。

        以当前 cfg 的 family + 新 size 合成字体后应用，保证改字号不会
        把 family 丢回系统默认。"""
        self._apply_ui_font(self._cfg.get("ui_font_family") or "", size)

    def _apply_ui_font_family(self, family: str) -> None:
        """family 运行时变更入口（ConfigService.ui_font_family_changed 触发）。"""
        self._apply_ui_font(family or "", int(self._cfg.get("ui_font_size") or 9))

    def _apply_ui_font(self, family: str, size: int) -> None:
        """运行时改全局界面字体：QApplication.setFont 设新默认 family+字号，遍历已存在
        widget 刷新（跳过有专属字体的 RTT/内存显示区，它们各自 _apply_font 覆盖）。

        QApplication.setFont 只影响新建 widget，已存在的不会自动刷新，故遍历。
        用动态属性 _custom_font 标记专属字体 widget（RTT display / 内存 display），
        避免全局字体覆盖它们的等宽专用字号。family 空串表示「跟随系统」：
        显式解析成系统 UI family 后再 setFamily——否则 Qt 沿用上一次的 family，
        永远切不回系统字体（CLAUDE.md 经验条目）。

        同步设 qfluentwidgets 的 fontFamilies（qconfig）为 [ui_family, 中文/日文兜底]：
        qfluentwidgets 的 ToolTip / TeachingTip / Flyout 等气泡用原生 QLabel + 自己的
        QSS `font: 12px --FontFamilies`，fontFamilies 决定 --FontFamilies 的取值。气泡
        字号被 QSS 锁在 12px（用户要求气泡字号不变），但 family 要跟随 UI 字体，故这里
        把 fontFamilies 设成 UI family（每次悬停/点击重新构造气泡时自动应用新值）。
        """
        resolved = resolve_ui_family(family)
        f = QApplication.font()
        f.setFamily(resolved)
        f.setPointSize(size)
        QApplication.setFont(f)
        for w in QApplication.allWidgets():
            if w.property("_custom_font"):
                continue
            w.setFont(f)
        _sync_fluent_font_families(resolved)
        # QSS `font:` 锁定的控件（RadioButton/右键菜单等）setFont 无效，
        # 单独用 setStyleSheet 追加 font 规则覆盖（见 core._ui_font 注释）。
        # 用 QApplication 全量 widget（右键菜单/对话框是独立顶级窗口，不在主窗口子树）。
        sync_qss_font_locked_widgets(QApplication.instance(), resolved, size)

    def _restore_geometry(self) -> None:
        geom_b64 = self._cfg.get("window_geometry")
        if geom_b64:
            try:
                ba = QByteArray(base64.b64decode(geom_b64))
                self.restoreGeometry(ba)
                return
            except Exception as e:
                self._logger.warning(f"恢复窗口几何失败：{e}")
        self.resize(1200, 800)

    def showEvent(self, event: QShowEvent) -> None:
        """显示后兜底裁剪到可用区域，防止 Windows 任务栏遮挡底部控件。

        FluentWindow 内部 NavigationInterface + StackedWidget 子页累积 sizeHint
        可能让 Qt 算出比屏幕可用区域还大的 mintrack。Windows 此时会让 client
        区扩展到任务栏后方，看上去底部被遮挡。我们在显示后主动检查并夹回
        可用区域。"""
        super().showEvent(event)
        if getattr(self, "_did_initial_fit", False):
            return
        self._did_initial_fit = True
        app = QApplication.instance()
        if app is None:
            return
        screen = app.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        geo = self.frameGeometry()
        # 任意一边超出可用区域 → 重新放进去
        if (geo.bottom() > avail.bottom() or geo.right() > avail.right()
                or geo.top() < avail.top() or geo.left() < avail.left()):
            new_w = min(geo.width(), avail.width())
            new_h = min(geo.height(), avail.height())
            new_x = max(avail.left(), min(geo.left(), avail.right() - new_w))
            new_y = max(avail.top(), min(geo.top(), avail.bottom() - new_h))
            self.setGeometry(new_x, new_y, new_w, new_h)

    def closeEvent(self, event: QCloseEvent) -> None:
        # 保存窗口几何
        geom = self.saveGeometry()
        self._cfg.set("window_geometry", base64.b64encode(bytes(geom)).decode("ascii"))
        self._cfg.flush()

        # 关闭 worker：emit stop → worker._on_stop 在 worker 线程清理 → thread.quit()
        # wait 5 秒：pylink close() 在 STM32H750VB 实测最长 ~3 秒，留余量
        self.worker.stop_requested.emit()
        if not self.worker_thread.wait(5000):
            self._logger.error("worker 退出超时，强制 terminate")
            # terminate 前防御性 close 日志文件，避免最后几秒日志丢失。
            # Python file 对象的 close() 由 GIL 串行化，主线程调安全。
            try:
                self.worker._close_log_file()
            except Exception as e:
                self._logger.warning(f"主线程兜底关日志文件失败：{e}")
            self.worker_thread.terminate()
            self.worker_thread.wait(1000)

        # 关掉烧录页的独立 worker thread
        try:
            self.flash_page.shutdown()
        except Exception as e:
            self._logger.warning(f"FlashPage shutdown failed: {e}")

        event.accept()
