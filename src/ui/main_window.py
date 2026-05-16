"""主窗口：FluentWindow + 左侧导航 + JLinkWorker（外部 QThread）生命周期。"""
from __future__ import annotations

import base64

from PySide6.QtCore import QByteArray, QThread
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, NavigationItemPosition

from core.config_service import ConfigService
from core.jlink_worker import JLinkWorker
from core.logger import get_logger

from .about_page import AboutPage
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
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.initialize)
        self.worker_thread.start()

        # 2. 各页面
        self.rtt_page = RTTMonitorPage(self.worker, cfg, self)
        self.memory_page = MemoryViewerPage(self.worker, cfg, self)
        self.settings_page = SettingsPage(cfg, self)
        self.about_page = AboutPage(self)

        # 3. 导航
        self.addSubInterface(self.rtt_page, FIF.SPEED_HIGH, "RTT 监控")
        self.addSubInterface(self.memory_page, FIF.CODE, "内存查看")
        self.navigationInterface.addSeparator()
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(
            self.about_page, FIF.INFO, "关于", NavigationItemPosition.BOTTOM
        )

        # 4. ConfigService 的 rtt_poll_interval_changed 信号连到 worker
        self._cfg.rtt_poll_interval_changed.connect(self.worker.set_poll_interval_requested)

        # 5. UI 界面字体（应用到 QApplication，影响侧边栏/按钮/标签等所有 fluent 控件）
        self._cfg.ui_font_changed.connect(self._apply_ui_font)
        self._apply_ui_font(self._cfg.get("ui_font_family"), self._cfg.get("ui_font_size"))

        # 6. 窗口属性
        self.setWindowTitle("J-Link RTT Viewer")
        self._restore_geometry()

    def _apply_ui_font(self, family: str, size: int) -> None:
        """应用 UI 全局字体。空 family 或 size<=0 → 恢复 fluent 默认（QApplication 出厂 font）。"""
        app = QApplication.instance()
        if app is None:
            return
        if family and size > 0:
            font = QFont(family, size)
            app.setFont(font)
        else:
            # 恢复默认：用一个全新 QApplication 创建时的默认 font 不容易拿到，
            # 这里用空字符串构造 + 默认字号 9（fluent 标准）作为兜底
            app.setFont(QFont("", 9))

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

        event.accept()
