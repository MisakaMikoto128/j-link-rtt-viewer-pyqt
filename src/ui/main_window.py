"""主窗口：FluentWindow + 左侧导航 + JLinkWorker（外部 QThread）生命周期。"""
from __future__ import annotations

import base64

from PySide6.QtCore import QByteArray, QThread
from PySide6.QtGui import QCloseEvent, QFont, QIcon, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, NavigationItemPosition

from core.config_service import ConfigService
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
        # 初始编码在 thread.start() 前同步设置——不能用 set_encoding_requested.emit，
        # 那会和 initialize() 内 connect 形成竞态导致信号被丢弃。
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

        # 3. 导航
        self.addSubInterface(self.rtt_page, FIF.SPEED_HIGH, "RTT 监控")
        self.addSubInterface(self.memory_page, FIF.CODE, "内存查看")
        self.addSubInterface(self.flash_page, FIF.SEND_FILL, "固件烧录")
        self.navigationInterface.addSeparator()
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(
            self.about_page, FIF.INFO, "关于", NavigationItemPosition.BOTTOM
        )

        # 4. ConfigService 的 rtt_poll_interval_changed / rtt_encoding_changed 信号连到 worker
        self._cfg.rtt_poll_interval_changed.connect(self.worker.set_poll_interval_requested)
        self._cfg.rtt_encoding_changed.connect(self.worker.set_encoding_requested)

        # 5. UI 界面字体（应用到 QApplication，影响侧边栏/按钮/标签等所有 fluent 控件）
        self._cfg.ui_font_changed.connect(self._apply_ui_font)
        self._apply_ui_font(self._cfg.get("ui_font_family"), self._cfg.get("ui_font_size"))

        # 6. 全局快捷键 —— 不依赖当前页面焦点，F2/F3/F4 在任意子页都生效。
        # 路由到 rtt_page 的方法，由方法自己根据按钮状态判断是否执行（幂等）。
        QShortcut(QKeySequence("F2"), self, self.rtt_page.on_shortcut_connect)
        QShortcut(QKeySequence("F3"), self, self.rtt_page.on_shortcut_disconnect)
        QShortcut(QKeySequence("F4"), self, self.rtt_page.on_shortcut_reset)
        QShortcut(QKeySequence("Ctrl+F"), self, self.rtt_page.on_shortcut_find)
        QShortcut(QKeySequence("Ctrl+H"), self, self.rtt_page.on_shortcut_replace)

        # 7. 窗口属性
        self.setWindowTitle("J-Link RTT Viewer")
        # 直接从文件加载 icon —— 不依赖 app.setWindowIcon 调用顺序，
        # 避免 caller 顺序变就静默丢图标。setWindowIcon 触发 FluentTitleBar
        # 的 windowIconChanged 信号，标题栏左上角才会刷新。
        from core._paths import find_app_icon
        icon_path = find_app_icon()
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))
        # 显式约束最小尺寸，让窗口在 Windows 任务栏占据底部时仍能完整显示底部
        # 控件（搜索栏/发送栏）。子控件 sizeHint 累积过大会让 Qt 计算出
        # 1500+ px 的 mintrack，结果窗口被 Windows 强制压扁导致底部被任务栏遮挡。
        # 最小宽度需小于 _COLLAPSE_WIDTH(900)，否则收窄模式永远无法触发
        self.setMinimumSize(480, 540)
        self._restore_geometry()

    def _apply_ui_font(self, family: str, size: int) -> None:
        """应用 UI 全局字体。

        * family="" 且 size<=0 → 恢复 fluent 默认
        * 仅 family 有值时 size 自动补 9pt
        * 仅 size 有值时 family 留空（系统默认字体 + 指定字号）
        """
        app = QApplication.instance()
        if app is None:
            return
        family = (family or "").strip()
        if not family and size <= 0:
            # 完全默认
            app.setFont(QFont("", 9))
            return
        if size <= 0:
            size = 9
        app.setFont(QFont(family, size))

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
