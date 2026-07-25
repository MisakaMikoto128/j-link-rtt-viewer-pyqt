"""RTT 显示区搜索/替换：正则/全词/大小写 + 匹配高亮 + 计数。

从 RTTMonitorPage 拆出，依赖 display（QPlainTextEdit）+ SearchBar 组件，
通过信号通信。_match_count_timer 内部持有（400ms 节流计数，避免每按键
全 buffer 扫描）。display.textChanged 触发同一节流，RTT 流入时保持高亮同步。

位置映射（code point -> UTF-16）：Qt QTextDocument 用 UTF-16 code unit 存储，
emoji/非 BMP 字符（U+10000+，如 😍🍟🍔）占 2 个 code unit；Python re.finditer
返回 code point 索引。直接用 code point 位置调 setPosition 会错位（选中错文本）。
_build_cp_to_utf16_map 构建 code point i -> UTF-16 位置的映射，所有 cursor
操作（高亮 / 跳转 / 替换）都用映射后的 UTF-16 位置。
"""

from __future__ import annotations

import re

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit

from .widgets.search_bar import SearchBar


class SearchHandler(QObject):
    """搜索/替换逻辑：快捷键入口 + 匹配导航 + 高亮 + 计数。"""

    def __init__(
        self, display: QPlainTextEdit, search_bar: SearchBar, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._display = display
        self._search_bar = search_bar
        # 搜索匹配数节流：textChanged 每按键全 buffer 扫描太重，400ms 单次延迟
        # （大文本 + RTT 流入叠加时 200/300ms 仍卡，400ms 更稳）
        self._match_count_timer = QTimer(self)
        self._match_count_timer.setSingleShot(True)
        self._match_count_timer.setInterval(400)
        self._match_count_timer.timeout.connect(self._do_update_match_count)
        # 连接 search_bar 信号
        search_bar.search_requested.connect(self._do_search)
        search_bar.options_changed.connect(self._on_search_options_changed)
        search_bar.replace_requested.connect(self._do_replace)
        search_bar.closed.connect(self._on_bar_closed)
        # RTT 数据实时流入时，搜索高亮会过时（新文本无高亮、计数错位）。
        # 监听 display.textChanged -> 节流重新扫描，保持高亮/计数与文本同步。
        display.textChanged.connect(self._on_display_text_changed)

    def on_shortcut_find(self) -> None:
        """Ctrl+F：切换搜索栏显示/隐藏。已打开时仅聚焦。

        如果显示区有选中文本，自动填入搜索框（VSCode 行为）。
        """
        sel = self._display.textCursor().selectedText().strip()
        if self._search_bar.isVisible():
            if sel:
                self._search_bar.le_search.setText(sel)
            self._search_bar.le_search.setFocus()
            self._search_bar.le_search.selectAll()
        else:
            self._search_bar.show_search(initial_text=sel)

    def on_shortcut_replace(self) -> None:
        """Ctrl+H：切换搜索栏 + 展开替换行。已展开时关闭。

        如果显示区有选中文本，自动填入搜索框。
        """
        sel = self._display.textCursor().selectedText().strip()
        if self._search_bar.isVisible() and self._search_bar.is_replace_visible():
            self._search_bar.close_bar()
        else:
            self._search_bar.show_replace(initial_text=sel)

    def _on_bar_closed(self) -> None:
        """搜索栏关闭时把焦点还给 display，清除高亮。"""
        self._display.setFocus()
        self._display.setExtraSelections([])

    def _on_display_text_changed(self) -> None:
        """显示区文本变化（RTT 流入/清除/替换）时，节流重新扫描高亮 + 计数。

        仅在搜索栏可见且有搜索词时触发，避免无谓扫描。RTT 持续流入时，
        搜索高亮是搜索时刻的快照，不加这步会出现「新文本无高亮 + 计数过时」，
        用户看到半边高亮半边无，感觉搜索「对不上」。
        """
        if self._search_bar.isVisible() and self._search_bar.search_text():
            self._match_count_timer.start()

    def _on_search_options_changed(self) -> None:
        """搜索选项（大小写/全词/正则）变化时重新计数。"""
        self._match_count_timer.start()

    def _build_regex(self, pattern: str, whole_word: bool, regex: bool, case_sensitive: bool):
        """构建编译好的正则表达式。返回 re.Pattern 或 None（模式无效时）。"""
        if regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                expr = f"\\b(?:{pattern})\\b" if whole_word else pattern
                return re.compile(expr, flags)
            except re.error:
                return None
        # 非正则：把 pattern 当字面量
        flags = 0 if case_sensitive else re.IGNORECASE
        expr = re.escape(pattern)
        if whole_word:
            expr = f"\\b{expr}\\b"
        return re.compile(expr, flags)

    def _build_cp_to_utf16_map(self, full: str) -> list[int]:
        """构建 code point 索引 -> UTF-16 code unit 位置映射。

        Qt QTextDocument 用 UTF-16 存储，非 BMP 字符（emoji，U+10000+）占 2 个
        code unit；Python str 按 code point 计数。re.finditer 返回 code point 索引，
        直接喂给 QTextCursor.setPosition 会错位（每个 emoji 偏移 +1）。

        返回 list[int] 长度 len(full)+1：cp_to_utf16[i] = code point i 对应的
        UTF-16 位置；cp_to_utf16[len(full)] = 文档末尾 UTF-16 位置。
        """
        cp_to_utf16 = [0] * (len(full) + 1)
        acc = 0
        for i, ch in enumerate(full):
            acc += 2 if ord(ch) > 0xFFFF else 1
            cp_to_utf16[i + 1] = acc
        return cp_to_utf16

    def _scan_utf16_matches(
        self, text: str, whole_word: bool, regex: bool, case_sensitive: bool
    ) -> tuple[list[tuple[int, int]] | None, list[int] | None]:
        """扫描全文，返回 (UTF-16 位置 matches, cp_to_utf16 映射)。

        matches 为 [(utf16_start, utf16_end), ...]，可直接用于 QTextCursor.setPosition。
        返回 (None, None) 表示正则无效；([], map) 表示无匹配。
        """
        if not text:
            return [], None
        pat = self._build_regex(text, whole_word, regex, case_sensitive)
        if pat is None:
            return None, None
        full = self._display.toPlainText()
        cp_to_utf16 = self._build_cp_to_utf16_map(full)
        matches = [
            (cp_to_utf16[m.start()], cp_to_utf16[m.end()]) for m in pat.finditer(full)
        ]
        return matches, cp_to_utf16

    def _do_search(
        self, text: str, backward: bool, case_sensitive: bool, whole_word: bool, regex: bool
    ) -> None:
        u16_matches, _ = self._scan_utf16_matches(text, whole_word, regex, case_sensitive)
        if u16_matches is None:
            self._search_bar.set_match_label(self.tr("无效正则"))
            return
        if not u16_matches:
            self._search_bar.set_match_label("0/0")
            self._display.setExtraSelections([])
            return
        # cur_pos 是 UTF-16 位置（Qt cursor 内部用 UTF-16），u16_matches 也是 UTF-16
        cursor = self._display.textCursor()
        cur_pos = cursor.selectionEnd() if not backward else cursor.selectionStart()
        if backward:
            target = None
            for s, _e in reversed(u16_matches):
                if s < cur_pos:
                    target = (s, _e)
                    break
            if target is None:
                target = u16_matches[-1]  # 回卷到最后一个
        else:
            target = None
            for s, _e in u16_matches:
                if s >= cur_pos:
                    target = (s, _e)
                    break
            if target is None:
                target = u16_matches[0]  # 回卷到第一个
        # 移动光标到匹配位置（UTF-16 位置，直接用）
        tc = self._display.textCursor()
        tc.setPosition(target[0])
        tc.setPosition(target[1], QTextCursor.KeepAnchor)
        self._display.setTextCursor(tc)
        self._display.ensureCursorVisible()
        # 复用已扫描的 u16_matches，避免 _update_match_position 重复 toPlainText + finditer
        self._update_match_position(text, u16_matches)

    def _do_replace(
        self,
        text: str,
        replacement: str,
        replace_all: bool,
        case_sensitive: bool,
        whole_word: bool,
        regex: bool,
    ) -> None:
        pat = self._build_regex(text, whole_word, regex, case_sensitive)
        if pat is None:
            self._search_bar.set_match_label(self.tr("无效正则"))
            return
        if replace_all:
            # 从后往前逐段替换，保留周围文本的 QTextCharFormat（不用 setPlainText，
            # 后者会清除所有格式导致 ANSI 染色丢失）
            full = self._display.toPlainText()
            cp_to_utf16 = self._build_cp_to_utf16_map(full)
            matches = list(pat.finditer(full))
            if not matches:
                self._update_match_position(text)
                return
            doc = self._display.document()
            for m in reversed(matches):
                tc = QTextCursor(doc)
                tc.setPosition(cp_to_utf16[m.start()])
                tc.setPosition(cp_to_utf16[m.end()], QTextCursor.KeepAnchor)
                tc.insertText(replacement)  # 保留周围文本的格式
            self._update_match_position(text)
        else:
            # 替换当前选中：如果当前选中文本匹配 pattern，替换它
            cursor = self._display.textCursor()
            if cursor.hasSelection():
                sel = cursor.selectedText()
                if pat.fullmatch(sel):
                    cursor.insertText(replacement)
            # 找下一个
            self._do_search(text, False, case_sensitive, whole_word, regex)

    def _do_update_match_count(self) -> None:
        self._update_match_position(self._search_bar.search_text())

    def _update_match_position(
        self, text: str, u16_matches: list[tuple[int, int]] | None = None
    ) -> None:
        """显示 "第 N 项，共 M 项"，并把全部匹配位置叠黄色 ExtraSelection。

        u16_matches 非空时复用（_do_search 已扫描），避免重复 toPlainText + finditer。
        u16_matches 元素为 (utf16_start, utf16_end)，与 cursor 位置（UTF-16）同域。
        """
        if not text:
            self._search_bar.set_match_label("")
            self._display.setExtraSelections([])
            return
        if u16_matches is None:
            u16_matches, _ = self._scan_utf16_matches(
                text,
                self._search_bar.whole_word(),
                self._search_bar.regex_enabled(),
                self._search_bar.case_sensitive(),
            )
            if u16_matches is None:
                self._search_bar.set_match_label(self.tr("无效正则"))
                self._display.setExtraSelections([])
                return
        cnt = len(u16_matches)
        if cnt == 0:
            self._search_bar.set_match_label("0/0")
            self._display.setExtraSelections([])
            return
        # 当前光标在哪个匹配中（cur_pos 是 UTF-16 位置，u16_matches 也是 UTF-16）
        cursor = self._display.textCursor()
        cur_pos = cursor.selectionStart()
        idx = 0
        for i, (s, e) in enumerate(u16_matches):
            # 用 < e 而非 <= e：相邻匹配时 cur_pos==e 不应算入当前匹配，
            # 否则跳到下一个匹配后计数仍显示上一个（"foofoo" 搜 foo，第二个匹配显示 1/2）
            if s <= cur_pos < e:
                idx = i + 1
                break
        else:
            # 光标不在任何匹配中，找最近的
            for i, (s, _e) in enumerate(u16_matches):
                if s >= cur_pos:
                    idx = i + 1
                    break
            else:
                idx = cnt
        self._search_bar.set_match_label(f"{idx}/{cnt}")
        self._highlight_matches(u16_matches, current_idx=idx - 1)

    def _highlight_matches(
        self, u16_matches: list[tuple[int, int]], limit: int = 100, current_idx: int = -1
    ) -> None:
        """匹配位置叠浅黄色背景，当前匹配叠橙色（VSCode 风格）。超过 limit 截断。

        limit 200->100：ExtraSelection 构造 + setExtraSelections 是搜索周期大头，
        100 已足够定位（前 100 个匹配高亮），且显著降低每次扫描的 UI 负担。
        """
        yellow_fmt = QTextCharFormat()
        yellow_fmt.setBackground(QColor(255, 235, 100, 140))
        orange_fmt = QTextCharFormat()
        orange_fmt.setBackground(QColor(255, 165, 0, 180))
        doc = self._display.document()
        selections: list = []
        for i, (s, e) in enumerate(u16_matches[:limit]):
            c = QTextCursor(doc)
            c.setPosition(s)
            c.setPosition(e, QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format = orange_fmt if i == current_idx else yellow_fmt
            selections.append(sel)
        self._display.setExtraSelections(selections)
