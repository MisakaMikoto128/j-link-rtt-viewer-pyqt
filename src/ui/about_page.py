"""关于页（占位，由 Task 15 实现）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import SubtitleLabel


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("about")
        layout = QVBoxLayout(self)
        label = SubtitleLabel("关于（占位）", self)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
