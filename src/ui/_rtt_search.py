"""RTT 显示区搜索/替换：正则/全词/大小写 + 匹配高亮 + 计数。

从 RTTMonitorPage 拆出，依赖 display（QPlainTextEdit）+ SearchBar 组件，
通过信号通信。_match_count_timer 内部持有（200ms 节流计数，避免每按键
全 buffer 扫描）。
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
        # 搜索匹配数节流：textChanged 每按键全 buffer 扫描太重，200ms 单次延迟
        self._match_count_timer = QTimer(self)
        self._match_count_timer.setSingleShot(True)
        self._match_count_timer.setInterval(200)
        self._match_count_timer.timeout.connect(self._do_update_match_count)
        # 连接 search_bar 信号
        search_bar.search_requested.connect(self._do_search)
        search_bar.options_changed.connect(self._on_search_options_changed)
        search_bar.replace_requested.connect(self._do_replace)
        search_bar.closed.connect(self._on_bar_closed)

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

    def _do_search(
        self, text: str, backward: bool, case_sensitive: bool, whole_word: bool, regex: bool
    ) -> None:
        pat = self._build_regex(text, whole_word, regex, case_sensitive)
        if pat is None:
            self._search_bar.set_match_label(self.tr("无效正则"))
            return
        full = self._display.toPlainText()
        matches = list(pat.finditer(full))
        if not matches:
            self._search_bar.set_match_label("0/0")
            self._display.setExtraSelections([])
            return
        # 找当前光标后/前的下一个匹配
        cursor = self._display.textCursor()
        cur_pos = cursor.selectionEnd() if not backward else cursor.selectionStart()
        if backward:
            target = None
            for m in reversed(matches):
                if m.start() < cur_pos:
                    target = m
                    break
            if target is None:
                target = matches[-1]  # 回卷到最后一个
        else:
            target = None
            for m in matches:
                if m.start() >= cur_pos:
                    target = m
                    break
            if target is None:
                target = matches[0]  # 回卷到第一个
        # 移动光标到匹配位置
        tc = self._display.textCursor()
        tc.setPosition(target.start())
        tc.setPosition(target.end(), QTextCursor.KeepAnchor)
        self._display.setTextCursor(tc)
        self._display.ensureCursorVisible()
        self._update_match_position(text)

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
            matches = list(pat.finditer(full))
            if not matches:
                self._update_match_position(text)
                return
            doc = self._display.document()
            for m in reversed(matches):
                tc = QTextCursor(doc)
                tc.setPosition(m.start())
                tc.setPosition(m.end(), QTextCursor.KeepAnchor)
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

    def _update_match_position(self, text: str) -> None:
        """显示 "第 N 项，共 M 项"，并把全部匹配位置叠黄色 ExtraSelection。"""
        if not text:
            self._search_bar.set_match_label("")
            self._display.setExtraSelections([])
            return
        pat = self._build_regex(
            text,
            self._search_bar.whole_word(),
            self._search_bar.regex_enabled(),
            self._search_bar.case_sensitive(),
        )
        if pat is None:
            self._search_bar.set_match_label(self.tr("无效正则"))
            self._display.setExtraSelections([])
            return
        full = self._display.toPlainText()
        matches = list(pat.finditer(full))
        cnt = len(matches)
        if cnt == 0:
            self._search_bar.set_match_label("0/0")
            self._display.setExtraSelections([])
            return
        # 当前光标在哪个匹配中
        cursor = self._display.textCursor()
        cur_pos = cursor.selectionStart()
        idx = 0
        for i, m in enumerate(matches):
            if m.start() <= cur_pos <= m.end():
                idx = i + 1
                break
        else:
            # 光标不在任何匹配中，找最近的
            for i, m in enumerate(matches):
                if m.start() >= cur_pos:
                    idx = i + 1
                    break
            else:
                idx = cnt
        self._search_bar.set_match_label(f"{idx}/{cnt}")
        self._highlight_matches(matches, limit=500)

    def _highlight_matches(self, matches: list, limit: int = 500) -> None:
        """匹配位置叠浅黄色背景。超过 limit 截断。"""
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 235, 100, 140))
        selections: list = []
        for m in matches[:limit]:
            c = QTextCursor(self._display.document())
            c.setPosition(m.start())
            c.setPosition(m.end(), QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format = fmt
            selections.append(sel)
        self._display.setExtraSelections(selections)
