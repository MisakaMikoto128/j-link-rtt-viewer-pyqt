"""共享 pytest fixtures。"""
import sys
from pathlib import Path

import pytest

# 防止某些子进程测试找不到 src
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def qapp():
    """整个测试会话共用一个 QApplication，避免多次创建。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
