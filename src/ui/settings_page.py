"""设置页（占位，由 Task 14 实现）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import SubtitleLabel


class SettingsPage(QWidget):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setObjectName("settings")
        self._cfg = cfg
        layout = QVBoxLayout(self)
        label = SubtitleLabel("设置（占位）", self)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
