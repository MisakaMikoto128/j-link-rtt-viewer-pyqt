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


@pytest.fixture(autouse=True)
def stub_make_backend(request, monkeypatch):
    """UI 级测试（FlashPage 等真实 FlashWorker + QThread 的 fixture）默认 stub 掉
    make_backend -> MagicMock backend，烧录流程立即走通并 emit
    flash_finished(True, "烧录成功")。

    防 pytestqt segfault：真实 PylinkBackend/PyOCDBackend 会碰 J-Link/pyOCD DLL
    （ctypes），远程 open 对不可达主机约 3s 才返回。测试 teardown 时 worker 线程
    若仍卡在 DLL 调用里，Python 解释器退出阶段卸载 DLL 状态 -> access violation。

    直接测 _run_flash 的单元测试（test_flash_worker.py）需要真实 make_backend
    走自己注入的 mock pylink/pyOCD，用 @pytest.mark.real_make_backend 退出本 stub。
    """
    if request.node.get_closest_marker("real_make_backend"):
        return None
    from unittest.mock import MagicMock

    backend = MagicMock()
    backend.connected_serial.return_value = ""
    monkeypatch.setattr(
        "core.flash_worker.make_backend", lambda kind, log: backend)
    return backend


@pytest.fixture(autouse=True)
def stub_pyocd_enumerator(monkeypatch):
    """隔离测试与真机 + 防 pytestqt segfault：

    1. 设 JLINK_RTT_TEST_MODE=1 -> FlashWorker.initialize 跳过 1s pyOCD 枚举
       timer。否则 timer 在 worker 线程 fire + 跨线程 emit pyocd_probes_enumerated，
       在测试 teardown 后投递到已销毁的 FlashPage，触发 pytestqt _process_events
       segfault（Qt 线程 assertion）。
    2. stub enumerate_pyocd_probes -> []：即使 timer 误触发也不扫真机 USB
       （开发机连着 H7-TOOL 时会 emit 真 probe 重建 combo，干扰测试状态）。
    """
    monkeypatch.setenv("JLINK_RTT_TEST_MODE", "1")
    from core.probe import enumerator
    monkeypatch.setattr(enumerator, "enumerate_pyocd_probes", lambda: [])


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
