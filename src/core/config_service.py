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
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .logger import get_logger


class ConfigService(QObject):
    theme_changed = Signal(str)             # "light" / "dark" / "auto"
    theme_color_changed = Signal(str)       # hex e.g. "#28afe9"
    font_changed = Signal(str, int)         # (family, size) — RTT 显示区字体
    memory_font_size_changed = Signal(int)  # 内存页 hex dump 字号（family 与 RTT 共用 font_family）
    ui_font_size_changed = Signal(int)       # 全局界面字号（按钮/标签/发送框等非等宽区；RTT/内存显示区各自覆盖）
    ui_font_family_changed = Signal(str)    # 全局界面字体 family（同上覆盖范围）
    max_display_lines_changed = Signal(int) # new max block count for QPlainTextEdit
    rtt_poll_interval_changed = Signal(int) # poll timer interval in ms
    rtt_encoding_changed = Signal(str)      # RTT 解码编码（utf-8 / gbk / utf-16-le / latin-1 / ascii）
    reset_mode_changed = Signal(str)        # "normal" / "auto_reconnect" — RTT 页用来更新按钮文字
    language_changed = Signal(str)          # "zh_CN" / "zh_TW" / "ja" / "ko" / "en" / "fr"

    DEFAULTS: dict[str, Any] = {
        "target_mcu": "",
        "interface": "SWD",
        "speed_khz": 4000,
        "rtt_channel": 0,            # -1 = 全部通道视图（UI 显示为「全部通道」）；>=0 = 具体通道
        "rtt_channel_history_chars": 200000,  # 每通道历史缓存上限（字符数），超出丢弃最旧
        "send_history": [],
        "theme": "auto",            # light / dark / auto
        "theme_color": "#28afe9",
        "language": "zh_CN",        # zh_CN / zh_TW / ja / ko / en / fr
        "font_family": "Consolas",
        "font_size": 13,
        # 内存页 hex dump 字号（family 沿用 font_family）
        "memory_font_size": 12,
        # 全局界面字号：QApplication.setFont 控制所有 UI 控件（按钮/标签/发送框）。
        # RTT 显示区 / 内存页 hex dump 有各自字号覆盖，不受此项影响。
        "ui_font_size": 9,
        # 全局界面字体 family：空串 = 跟随 QApplication 默认字体（系统 UI 字体）。
        # 覆盖范围同 ui_font_size（_custom_font 标记的 RTT/内存显示区不受影响）。
        "ui_font_family": "",
        "max_display_lines": 10000,
        "rtt_poll_interval_ms": 100,   # RTT 轮询间隔（ms）—— 旧版叫 rx_timeout_ms，已迁移
        "rtt_encoding": "utf-8",       # RTT 解码编码：utf-8 / gbk / utf-16-le / latin-1 / ascii
        # 换行符（系统级：发送「自动换行」追加字符 + 接收断行识别）：\r\n (CRLF) / \n (LF) / \r (CR)
        "send_line_ending": "\r\n",
        "send_script_index": 1,  # 脚本下拉框选中项（cb_crc_algo：CRC 算法索引 或 末项自动换行）
        "keep_screen_on": False,  # 保持屏幕常亮（防息屏）
        "log_dir": "",              # 空 → 用默认 %APPDATA%/JLinkRTTViewer/logs
        "window_geometry": "",      # base64 of QByteArray
        "hex_send_mode": False,
        "auto_scroll": True,
        "power_output": False,
        "log_recording": False,
        # RTT 页 display 的固定高度（px）；用户拖 _VResizeHandle 时持久化更新
        "rtt_display_height": 500,
        # 会话标记颜色（用户插入标记 + 连接/断开自动标记共用）；hex string
        "mark_color": "#ffff55",
        # 发送回显颜色：勾选"显示发送字符串"后在显示区追加的 » 行颜色；hex string
        "send_text_color": "#FFA500",
        # 连接 / 断开时自动在 RTT 显示区插入一条分隔标记（便于会话分段）
        "auto_mark_on_connect": False,
        "auto_mark_on_disconnect": False,
        # 自动重连：物理掉线后轮询 J-Link 是否回来，回来后自动重连同一台（按 serial 区分）。
        # 与 reset_mode 无关：reset_mode 是「重置按钮」的行为，auto_reconnect 是「掉线后」的行为。
        "auto_reconnect": False,
        # 重置按钮行为：
        #   "normal"         → jlink.reset + rtt_stop/start（默认；适合大多数 MCU）
        #   "auto_reconnect" → 重置 = 断开+重连（更可靠，但有 ~1s 延迟）
        "reset_mode": "normal",
        # 上次手动选择/成功连接的 J-Link serial，用于下次启动时自动选中同一台；
        # 空串表示没有历史（首次启动）。不在线时会以「离线占位」形式显示在 combo
        # 中并带红点提示。
        "last_jlink_serial": "",
        # 内存页用户选择持久化（地址/大小/字节序/字节每行/diff/自动刷新间隔/导出/写地址）
        # 不持久化：auto_refresh（断开会清掉）、goto/search（一次性）、write_data（误点高危）
        "mem_read_addr": "0x08000000",
        "mem_read_size": "0x100",
        "mem_bytes_per_row": 16,
        "mem_endian_little": True,
        "mem_diff_highlight": True,
        "mem_refresh_sec": 2,
        "mem_export_addr": "0x08000000",
        "mem_export_preset_idx": 0,
        "mem_export_custom_size": "",
        "mem_write_addr": "0x20000000",
        # hex 区 hover 显示 LE/BE 解析气泡，可关
        "mem_hover_parse": True,
        # 上次跳转地址，空串=无记录
        "mem_goto_addr": "",
        # === 烧录页（v0.3.0 新增）===
        # 独立持久化，不复用 RTT 页的 target_mcu / interface / speed_khz：
        # 让烧录与 RTT 监控目标可以不同（同时维护多个项目）
        "flash_device_name": "STM32H750VB",
        "flash_interface": "SWD",
        "flash_speed": 4000,
        "flash_bin_address": 0x08000000,        # bin 模式的起始地址
        "flash_erase_mode": "sector",           # "sector" | "chip"
        "flash_post_action": "reset_run",       # "none" | "reset" | "reset_run"
        "flash_verify": False,                  # extra byte-by-byte verify
        "flash_recent_files": [],               # 最多 10 个，时间倒序
        "flash_recent_files_mtime": {},         # path → mtime（float），用于变更提示
    }

    SEND_HISTORY_MAX = 50

    def __init__(self, bundled_config_path: Path | None = None, throttle_ms: int = 200, parent=None):
        super().__init__(parent)
        self._logger = get_logger()
        self._bundled_path = bundled_config_path or (
            Path(__file__).resolve().parent.parent / "config.json"
        )
        self._user_prefs_path = self._compute_user_prefs_path()
        # 用户可编辑的 config.json 副本（用于扩展 chip_models / 改默认值等）
        # 优先此路径，回退 bundled。首次启动若不存在则从 bundled seed 一份
        # —— onefile 打包模式下 bundled 在临时解压目录里，每次升级被覆盖，
        # 必须有可写副本才能让用户加自己的 MCU 而不需要重新打包
        self._user_config_path = self._user_prefs_path.parent / "config.json"
        self._data: dict[str, Any] = dict(self.DEFAULTS)
        self._bundled: dict[str, Any] = {}
        self._seed_user_config_if_missing()
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

    def _seed_user_config_if_missing(self) -> None:
        if self._user_config_path.exists():
            return
        if not self._bundled_path.exists():
            return
        try:
            self._user_config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._bundled_path, self._user_config_path)
            self._logger.info(f"已 seed 用户可编辑 config.json → {self._user_config_path}")
        except Exception as e:
            self._logger.warning(f"seed 用户 config.json 失败：{e}")

    def _load_bundled(self) -> None:
        # 优先读用户副本（可编辑），回退 bundled
        path = self._user_config_path if self._user_config_path.exists() else self._bundled_path
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._bundled = json.load(f)
        except Exception as e:
            self._logger.warning(f"读取 config.json 失败：{e}（path={path}）")
            self._bundled = {}

    def get_user_config_path(self) -> Path:
        return self._user_config_path

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
        # 一次性迁移：rx_timeout_ms → rtt_poll_interval_ms（旧名语义混乱，新名与信号匹配）
        if "rx_timeout_ms" in disk and "rtt_poll_interval_ms" not in disk:
            try:
                disk["rtt_poll_interval_ms"] = int(disk["rx_timeout_ms"]) or 100
            except (TypeError, ValueError):
                disk["rtt_poll_interval_ms"] = 100
        # 修复历史脏数据：QFontDialog 返回的 font.pointSize() 在某些情况下为 -1，
        # 会被旧版 _on_pick_font 直接 set 进 cfg → 下次启动 setPointSize 报错
        if disk.get("font_size", 1) <= 0:
            disk["font_size"] = 13
        if disk.get("memory_font_size", 1) <= 0:
            disk["memory_font_size"] = 12
        if disk.get("ui_font_size", 1) <= 0:
            disk["ui_font_size"] = 9
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
        elif key == "memory_font_size":
            self.memory_font_size_changed.emit(self._data["memory_font_size"])
        elif key == "ui_font_size":
            self.ui_font_size_changed.emit(self._data["ui_font_size"])
        elif key == "ui_font_family":
            self.ui_font_family_changed.emit(self._data["ui_font_family"])
        elif key == "rtt_encoding":
            self.rtt_encoding_changed.emit(self._data["rtt_encoding"])
        elif key == "max_display_lines":
            self.max_display_lines_changed.emit(value)
        elif key == "rtt_poll_interval_ms":
            self.rtt_poll_interval_changed.emit(value)
        elif key == "reset_mode":
            self.reset_mode_changed.emit(value)
        elif key == "language":
            self.language_changed.emit(value)

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
