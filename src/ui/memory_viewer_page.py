"""内存查看页（占位，由 Task 13 实现）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import SubtitleLabel


class MemoryViewerPage(QWidget):
    def __init__(self, worker, cfg, parent=None):
        super().__init__(parent)
        self.setObjectName("memory-viewer")
        self._worker = worker
        self._cfg = cfg
        layout = QVBoxLayout(self)
        label = SubtitleLabel("内存查看（占位）", self)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
