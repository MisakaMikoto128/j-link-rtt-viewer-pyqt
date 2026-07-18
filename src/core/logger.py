"""统一日志模块。

模块级单例：首次 get_logger() 时初始化 console + RotatingFileHandler，
后续调用返回同一 Logger。日志目录 Windows 下为 %APPDATA%/JLinkRTTViewer/logs，
Linux/macOS 下为 XDG_STATE_HOME（默认 ~/.local/state）/JLinkRTTViewer/logs，
测试时可通过 _log_dir_override 注入。
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "jlink_rtt_viewer"
_FORMAT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"

_logger: logging.Logger | None = None
_log_dir_override: Path | None = None  # 测试注入 / log_dir 用户偏好


def set_log_dir(path: Path | str) -> None:
    """设置应用日志目录偏好（cfg 的 log_dir 键非空时由 main 在 get_logger 前调用）。
    与 _log_dir_override 共用同一通道；main 设置后不要再反复调用。"""
    global _log_dir_override
    _log_dir_override = Path(path)


def get_log_dir() -> Path:
    """日志目录：Windows → %APPDATA%/JLinkRTTViewer/logs；
    Linux/macOS → XDG_STATE_HOME（默认 ~/.local/state）/JLinkRTTViewer/logs。"""
    if _log_dir_override is not None:
        return Path(_log_dir_override)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME")
        if xdg_state:
            base = Path(xdg_state)
        else:
            home = os.environ.get("HOME")  # Windows 上 Path.home() 忽略 HOME，测试用
            base = (Path(home) if home else Path.home()) / ".local" / "state"
    return base / "JLinkRTTViewer" / "logs"


def get_logger() -> logging.Logger:
    """获取应用全局 logger（单例）。"""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 尝试添加文件 handler，失败则只保留 console
    try:
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.warning(f"无法创建日志文件 handler，将仅输出到控制台：{e}")

    _logger = logger
    return logger
