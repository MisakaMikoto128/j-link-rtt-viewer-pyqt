"""共享 pytest fixtures。

所有 UI 测试默认走 offscreen 平台插件：不弹窗、不占焦点、可在无显示器
的 CI 环境跑。`QT_QPA_PLATFORM=offscreen` 必须在 QApplication 创建前设好，
所以放在 conftest 模块加载阶段，而不是 fixture 里。
"""
import os
import sys
from pathlib import Path

import pytest

# 必须在任何 PySide6 import 之前；pytest-qt 的 qtbot 也依赖它
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 防止某些子进程测试找不到 src
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def qapp():
    """整个测试会话共用一个 QApplication，避免多次创建。

    pytest-qt 也会自动创建 qapp；显式 fixture 用于 mock/无 qtbot 的老测试。
    """
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    """把 ConfigService 的落盘根目录指到临时目录，避免污染真实 user_prefs.json。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def fixtures_dir():
    """tests/fixtures 路径（含 blink.bin / blink.hex / blink_sym.axf 等）。"""
    return FIXTURES


@pytest.fixture
def screenshot_dir(tmp_path):
    """每个测试一个目录，方便 grab() 落盘观察。失败时通过 pytest -s 看路径。"""
    d = tmp_path / "screenshots"
    d.mkdir()
    return d


def grab_widget(widget, path):
    """把控件渲染到 PNG，返回 (width, height, bytes_len)，供回归断言用。

    offscreen 平台下 QWidget.grab() 仍能拿到正确的像素栅格。
    """
    widget.adjustSize()
    pm = widget.grab()
    pm.save(str(path), "PNG")
    return pm.width(), pm.height(), Path(path).stat().st_size
