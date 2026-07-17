"""Demo 5: 完整 app + 真实硬件，复现用户精确场景。

用户场景：首次打开 -> 故意选通道 4 -> 点连接 -> （预期）应只显示真实可用通道。
旧实现：num_up=3 -> 显示 0,1,2 且落在 2（空槽无数据）。
新实现（SizeOfBuffer 计数）：allocated=1 -> 只剩 ch0 + 全部，落在 0 有数据。
"""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
sys.path.insert(0, "src")
import time
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QThread

app = QApplication([])

from core import jlink_worker as jw_mod
from core.config_service import ConfigService
from ui.rtt_monitor_page import RTTMonitorPage

worker = jw_mod.JLinkWorker()
th = QThread()
worker.moveToThread(th)
th.started.connect(worker.initialize)
th.start()
t0 = time.time()
while not worker._ready and time.time() - t0 < 2:
    QCoreApplication.processEvents()
    time.sleep(0.01)


def pump(sec=0.8):
    t = time.time()
    while time.time() - t < sec:
        QCoreApplication.processEvents()
        time.sleep(0.01)


cfg = ConfigService()
page = RTTMonitorPage(worker, cfg)
page.show()


def snapshot(tag):
    print(f"[{tag}] spin={page.sp_channel.value()} max={page.sp_channel.maximum()} "
          f"view={page._view_channel} num_up={worker.get_num_up_channels()} "
          f"display='{page.display.toPlainText()[:40]!r}'")


# 用户故意选通道 4
page.sp_channel.setValue(4)
pump(0.1)
snapshot("选4-连接前")

# 首次连接（用户点的连接，channel 取 sp_channel.value()=4）
page._on_connect_clicked()
pump(1.2)
snapshot("首次连接后")

# 断开
page._on_connect_clicked()
pump(0.8)
snapshot("断开后")

# 立即重连（紧凑）
page._on_connect_clicked()
pump(1.2)
snapshot("紧凑重连后")

worker.stop_requested.emit()
pump(0.3)
th.quit()
th.wait(2000)
