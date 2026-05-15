"""ANSI 转义序列解析。

只支持 SGR（Select Graphic Rendition）参数子集：
  0=reset, 1=bold, 22=normal,
  30-37 / 90-97 = 前景色, 40-47 / 100-107 = 背景色
更复杂的 38;5;N（256 色）/ 38;2;R;G;B（真彩）参数会被静默吞掉，但不抛错。
返回 list[(text, AnsiAttrs)]，AnsiAttrs 是纯 dataclass，不依赖 QtGui。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CSI_RE = re.compile(r"\x1b\[([0-9;]*)m")

_BASE = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
_BRIGHT = ["bright_" + c for c in _BASE]


@dataclass(frozen=True)
class AnsiAttrs:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False


@dataclass
class _MutAttrs:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False

    def freeze(self) -> AnsiAttrs:
        return AnsiAttrs(fg=self.fg, bg=self.bg, bold=self.bold)


def _apply_sgr(state: _MutAttrs, params: list[int]) -> None:
    """对一组 SGR 参数依次套用，识别不了的跳过。"""
    i = 0
    while i < len(params):
        p = params[i]
        if p == 0:
            state.fg = None
            state.bg = None
            state.bold = False
        elif p == 1:
            state.bold = True
        elif p == 22:
            state.bold = False
        elif 30 <= p <= 37:
            state.fg = _BASE[p - 30]
        elif 40 <= p <= 47:
            state.bg = _BASE[p - 40]
        elif 90 <= p <= 97:
            state.fg = _BRIGHT[p - 90]
        elif 100 <= p <= 107:
            state.bg = _BRIGHT[p - 100]
        elif p == 38 or p == 48:
            # 38;5;N or 38;2;R;G;B — skip subsequent params
            if i + 1 < len(params):
                mode = params[i + 1]
                if mode == 5 and i + 2 < len(params):
                    i += 2          # 消费 5;N
                elif mode == 2 and i + 4 < len(params):
                    i += 4          # 消费 2;R;G;B
                elif mode in (2, 5):
                    # 不完整：消费剩余所有参数，避免泄漏
                    i = len(params) - 1
                else:
                    i += 1
            # else: 只有 38/48，下一次循环 i+1 自然超出
        # 其他参数（粗体/斜体/下划线之外）忽略
        i += 1


def parse_ansi(text: str) -> list[tuple[str, AnsiAttrs]]:
    """把含 ANSI 序列的字符串切成有色段。"""
    if not text:
        return []

    segments: list[tuple[str, AnsiAttrs]] = []
    state = _MutAttrs()
    pos = 0

    for m in CSI_RE.finditer(text):
        # 截取前一段普通文本
        if m.start() > pos:
            segments.append((text[pos:m.start()], state.freeze()))

        params_str = m.group(1)
        # 含字母字符的 CSI 序列（如 \x1b[abcm）不会被正则匹配，自然作为文本流过
        params = [int(p) for p in params_str.split(";") if p != ""]
        if not params:
            params = [0]
        _apply_sgr(state, params)
        pos = m.end()

    # 剩余文本（如果末尾有未匹配的 \x1b[ 不会被 finditer 命中，会留在这里）
    if pos < len(text):
        segments.append((text[pos:], state.freeze()))

    # 合并相邻同 attrs 段，方便 UI 渲染
    if not segments:
        return []
    merged: list[tuple[str, AnsiAttrs]] = [segments[0]]
    for seg, attrs in segments[1:]:
        prev_seg, prev_attrs = merged[-1]
        if prev_attrs == attrs:
            merged[-1] = (prev_seg + seg, attrs)
        else:
            merged.append((seg, attrs))
    return merged
