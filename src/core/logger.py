"""统一日志模块。

模块级单例：首次 get_logger() 时初始化 console + RotatingFileHandler，
后续调用返回同一 Logger。日志目录默认 %APPDATA%/JLinkRTTViewer/logs，
测试时可通过 _log_dir_override 注入。
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "jlink_rtt_viewer"
_FORMAT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"

_initialized: bool = False
_logger: logging.Logger | None = None
_log_dir_override: Path | None = None  # 测试注入用


def get_log_dir() -> Path:
    """日志目录：%APPDATA%/JLinkRTTViewer/logs，缺失则 ~/AppData/Roaming/...。"""
    if _log_dir_override is not None:
        return Path(_log_dir_override)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "JLinkRTTViewer" / "logs"


def get_logger() -> logging.Logger:
    """获取应用全局 logger（单例）。"""
    global _initialized, _logger
    if _initialized and _logger is not None:
        return _logger

    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _initialized = True
    _logger = logger
    return logger
