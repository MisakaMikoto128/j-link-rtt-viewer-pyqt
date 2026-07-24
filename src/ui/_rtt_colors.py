"""RTT 监控页颜色常量：ANSI 调色板 + 标记色 + 编码/重连映射。

预构造 QColor 避免 RTT 高吞吐热路径（_fmt 每个 ANSI 段都调）重复解析 hex
字符串 + 申请对象。模块加载时一次性建好，热路径只查 dict。
"""

from __future__ import annotations

from PySide6.QtGui import QColor

# ANSI 16 色调色板（映射到 hex）
ANSI_COLOR_MAP: dict[str, str] = {
    "black": "#000000",
    "red": "#cc0000",
    "green": "#00aa00",
    "yellow": "#cc9900",
    "blue": "#3366cc",
    "magenta": "#aa00aa",
    "cyan": "#00aaaa",
    "white": "#dddddd",
    "bright_black": "#666666",
    "bright_red": "#ff5555",
    "bright_green": "#55ff55",
    "bright_yellow": "#ffff55",
    "bright_blue": "#5599ff",
    "bright_magenta": "#ff55ff",
    "bright_cyan": "#55ffff",
    "bright_white": "#ffffff",
}

# 预构造 QColor：热路径只查 dict，不重复 QColor(hex) 解析
ANSI_QCOLORS: dict[str, QColor] = {k: QColor(v) for k, v in ANSI_COLOR_MAP.items()}
DEFAULT_FG_QCOLOR = QColor("#dddddd")
DEFAULT_BG_QCOLOR = QColor("#222222")

# 标记色（发送回显 / 远程连接 / 意外断开 / 等待重连）
DEFAULT_SEND_ECHO_COLOR = "#FFA500"  # 发送回显默认色（橙色）
REMOTE_MARK_COLOR = "#5599ff"  # 远程连接标记色（蓝）
DISCONNECT_ALERT_COLOR = "#cc0000"  # 意外断开红字提示色（与 ANSI red 一致）
PENDING_RECONNECT_COLOR = "#ff8c00"  # 等待自动重连提示色（橙）

# 自动重连各阶段提示色：disconnect=橙红（告警）、attempt=琥珀（进行中）、
# success=绿、failed=红、cancelled=灰。
RECONNECT_COLORS: dict[str, str] = {
    "disconnect_reconnecting": "#cc6600",
    "attempt": "#b8860b",
    "success": "#2e7d32",
    "failed": "#cc0000",
    "cancelled": "#888888",
}

# 编码显示名映射（权威定义在 settings_page._ENCODING_DISPLAY，此处为本地副本）
ENCODING_LABEL_MAP: dict[str, str] = {
    "utf-8": "UTF-8",
    "gbk": "GBK",
    "utf-16-le": "UTF-16-LE",
    "latin-1": "Latin-1",
    "ascii": "ASCII",
}
