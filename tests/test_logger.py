"""logger 单例 + 文件 handler 行为。"""
import logging
import tempfile
from pathlib import Path

from core import logger as logger_mod


def test_get_logger_returns_same_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(logger_mod, "_logger", None)
    monkeypatch.setattr(logger_mod, "_log_dir_override", tmp_path)
    log1 = logger_mod.get_logger()
    log2 = logger_mod.get_logger()
    assert log1 is log2


def test_get_logger_has_console_and_file_handler(monkeypatch, tmp_path):
    # 重置模块状态
    monkeypatch.setattr(logger_mod, "_logger", None)
    monkeypatch.setattr(logger_mod, "_log_dir_override", tmp_path)

    log = logger_mod.get_logger()
    handler_types = {type(h).__name__ for h in log.handlers}
    assert "StreamHandler" in handler_types
    assert "RotatingFileHandler" in handler_types

    log.info("hello logger")
    log_files = list(tmp_path.glob("*.log"))
    assert len(log_files) == 1
    assert "hello logger" in log_files[0].read_text(encoding="utf-8")


def test_log_dir_default_under_appdata(monkeypatch):
    monkeypatch.setattr(logger_mod, "_logger", None)
    monkeypatch.setattr(logger_mod, "_log_dir_override", None)

    path = logger_mod.get_log_dir()
    assert "JLinkRTTViewer" in str(path)
    assert path.name == "logs"


def test_log_dir_uses_xdg_state_home_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    monkeypatch.setattr(logger_mod, "_log_dir_override", None)

    path = logger_mod.get_log_dir()
    assert path == tmp_path / "xdg_state" / "JLinkRTTViewer" / "logs"


def test_log_dir_falls_back_to_dot_local_state_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(logger_mod, "_log_dir_override", None)

    path = logger_mod.get_log_dir()
    assert path == tmp_path / ".local" / "state" / "JLinkRTTViewer" / "logs"


def test_log_dir_uses_appdata_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(logger_mod, "_log_dir_override", None)

    path = logger_mod.get_log_dir()
    assert path == tmp_path / "JLinkRTTViewer" / "logs"


def test_logger_falls_back_to_console_when_file_handler_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(logger_mod, "_logger", None)
    # 用一个非法路径让 RotatingFileHandler 构造失败
    bad_path = tmp_path / "nonexistent_drive_or_path"
    # 让 get_log_dir 返回这个路径，但通过 monkeypatch mkdir 失败
    def fail_mkdir(*args, **kwargs):
        raise PermissionError("simulated permission denied")
    monkeypatch.setattr(logger_mod, "_log_dir_override", bad_path)
    monkeypatch.setattr("pathlib.Path.mkdir", fail_mkdir)

    log = logger_mod.get_logger()
    handler_types = {type(h).__name__ for h in log.handlers}
    assert "StreamHandler" in handler_types
    # 文件 handler 应该没添加进去
    assert "RotatingFileHandler" not in handler_types
