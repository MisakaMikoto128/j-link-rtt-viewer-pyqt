"""RTT 监控页（占位，由 Task 11/12 实现）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import SubtitleLabel


class RTTMonitorPage(QWidget):
    def __init__(self, worker, cfg, parent=None):
        super().__init__(parent)
        self.setObjectName("rtt-monitor")
        self._worker = worker
        self._cfg = cfg
        layout = QVBoxLayout(self)
        label = SubtitleLabel("RTT 监控（占位）", self)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
