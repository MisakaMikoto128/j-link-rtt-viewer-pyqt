"""logger 单例 + 文件 handler 行为。"""
import logging
import tempfile
from pathlib import Path

from core import logger as logger_mod


def test_get_logger_returns_same_instance():
    log1 = logger_mod.get_logger()
    log2 = logger_mod.get_logger()
    assert log1 is log2


def test_get_logger_has_console_and_file_handler(monkeypatch, tmp_path):
    # 重置模块状态
    monkeypatch.setattr(logger_mod, "_initialized", False)
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
    monkeypatch.setattr(logger_mod, "_initialized", False)
    monkeypatch.setattr(logger_mod, "_logger", None)
    monkeypatch.setattr(logger_mod, "_log_dir_override", None)

    path = logger_mod.get_log_dir()
    assert "JLinkRTTViewer" in str(path)
    assert path.name == "logs"
