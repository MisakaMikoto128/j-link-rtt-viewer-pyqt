"""ConfigService：bundled config.json + 用户偏好 user_prefs.json。

设计要点：
1. set() 节流落盘（默认 200ms 单次 timer），避免高频值（窗口几何/字体大小）拖死 SSD
2. flush() 强制立即落盘，closeEvent 必须调用
3. 写入用 atomic replace（写 .tmp + os.replace）
4. theme/theme_color/font 改动 emit 信号，UI 热应用
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .logger import get_logger


class ConfigService(QObject):
    theme_changed = Signal(str)             # "light" / "dark" / "auto"
    theme_color_changed = Signal(str)       # hex e.g. "#28afe9"
    font_changed = Signal(str, int)         # (family, size)
    max_display_lines_changed = Signal(int) # new max block count for QPlainTextEdit
    rtt_poll_interval_changed = Signal(int) # poll timer interval in ms

    DEFAULTS: dict[str, Any] = {
        "target_mcu": "",
        "interface": "SWD",
        "speed_khz": 4000,
        "rtt_channel": 0,
        "send_history": [],
        "theme": "auto",            # light / dark / auto
        "theme_color": "#28afe9",
        "font_family": "Consolas",
        "font_size": 13,
        "max_display_lines": 10000,
        "rx_timeout_ms": 0,
        "log_dir": "",              # 空 → 用默认 %APPDATA%/JLinkRTTViewer/logs
        "window_geometry": "",      # base64 of QByteArray
        "hex_send_mode": False,
        "auto_scroll": True,
        "power_output": False,
        "log_recording": False,
    }

    SEND_HISTORY_MAX = 50

    def __init__(self, bundled_config_path: Path | None = None, throttle_ms: int = 200, parent=None):
        super().__init__(parent)
        self._logger = get_logger()
        self._bundled_path = bundled_config_path or (
            Path(__file__).resolve().parent.parent / "config.json"
        )
        self._user_prefs_path = self._compute_user_prefs_path()
        self._data: dict[str, Any] = dict(self.DEFAULTS)
        self._bundled: dict[str, Any] = {}
        self._load_bundled()
        self._load_user_prefs()

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(throttle_ms)
        self._flush_timer.timeout.connect(self._do_flush)
        self._dirty = False

    @staticmethod
    def _compute_user_prefs_path() -> Path:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "JLinkRTTViewer" / "user_prefs.json"

    def _load_bundled(self) -> None:
        try:
            with open(self._bundled_path, "r", encoding="utf-8") as f:
                self._bundled = json.load(f)
        except Exception as e:
            self._logger.warning(f"读取 bundled config.json 失败：{e}")
            self._bundled = {}

    def _load_user_prefs(self) -> None:
        path = self._user_prefs_path
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                disk = json.load(f)
        except Exception as e:
            self._logger.warning(f"读取 user_prefs.json 失败：{e}")
            return
        if not isinstance(disk, dict):
            return
        for key, default in self.DEFAULTS.items():
            if key not in disk:
                continue
            v = disk[key]
            expected = type(default)
            if not isinstance(v, expected) or (expected is int and isinstance(v, bool)):
                continue
            self._data[key] = v

    def get(self, key: str) -> Any:
        return self._data.get(key, self.DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        if key not in self.DEFAULTS:
            self._logger.warning(f"忽略未知偏好键：{key}")
            return
        expected = type(self.DEFAULTS[key])
        # bool/int 隔离：isinstance(True, int) == True 是 Python 历史负债
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            self._logger.warning(
                f"偏好 {key} 类型不匹配，期望 {expected.__name__}，收到 {type(value).__name__}"
            )
            return
        if key == "send_history":
            value = [str(x) for x in value][-self.SEND_HISTORY_MAX:]

        if self._data.get(key) == value:
            return  # 值未变：什么都不做（既不写盘也不发信号，避免双向绑定无限递归）

        self._data[key] = value
        self._dirty = True
        self._flush_timer.start()

        if key == "theme":
            self.theme_changed.emit(value)
        elif key == "theme_color":
            self.theme_color_changed.emit(value)
        elif key in ("font_family", "font_size"):
            self.font_changed.emit(self._data["font_family"], self._data["font_size"])
        elif key == "max_display_lines":
            self.max_display_lines_changed.emit(value)
        elif key == "rx_timeout_ms":
            self.rtt_poll_interval_changed.emit(value)

    def flush(self) -> None:
        self._flush_timer.stop()
        self._do_flush()

    def _do_flush(self) -> None:
        if not self._dirty:
            return
        path = self._user_prefs_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            self._dirty = False
        except Exception as e:
            self._logger.error(f"保存 user_prefs.json 失败：{e}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def get_chip_list(self) -> list[str]:
        return list(self._bundled.get("chip_models", []))

    def get_default_speeds(self) -> list[int]:
        return list(self._bundled.get("speed_options_khz", []))

    def get_default_interface(self) -> str:
        return self._bundled.get("default_interface", "SWD")

    def get_default_speed(self) -> int:
        return self._bundled.get("default_speed_khz", 4000)
