"""目标设备选择组件：下拉候选 8 条 + 完整库 QCompleter 补全 + 历史记录。

设计约束（来自 CLAUDE.md 踩坑经验）：
- 不要把 3700+ 条设备名直接 addItems 到 EditableComboBox，否则打开下拉会卡死。
- 下拉只显示“最近使用 8 条”（无输入）或“当前输入匹配的前 8 条”。
- 完整设备库交给 QCompleter，由 Qt 内部过滤并弹出补全提示，不污染下拉 items。
- 用户确认输入后（editingFinished / activated）加入页面历史，RTT 与 Flash 历史键分开。
- 保留用户手动输入任意名称的能力（如 STM32F030C8T6x），不一定非在下拉 items 里。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QWidget
from qfluentwidgets import EditableComboBox

from core.config_service import ConfigService


class TargetComboBox(EditableComboBox):
    """目标设备选择框。

    - 无输入时，下拉显示该页面最近使用的 8 个目标。
    - 有输入时，下拉从 names_provider 返回的列表中模糊匹配前 8 条。
    - 完整候选库通过 QCompleter 提供输入补全，不进入下拉 items。
    - 用户确认输入后自动加入页面历史（cfg 中 history_key 指定的列表）。
    """

    MAX_DROPDOWN_ITEMS = 8
    MAX_HISTORY = 8

    def __init__(
        self,
        cfg: ConfigService,
        history_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._history_key = history_key
        self._names_provider: Callable[[], Sequence[str]] | None = None
        self._updating = False

        # 初始化历史（保证是列表）
        self._history: list[str] = self._load_history()

        # 补全器 model：数据在 set_names_provider / refresh 时更新
        self._completer_model = QStringListModel(self)
        completer = QCompleter(self._completer_model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.setCompleter(completer)

        # 信号
        self.textChanged.connect(self._on_text_changed)
        self.activated.connect(self._on_activated)
        self.editingFinished.connect(self._on_editing_finished)

    def restore_text(self, text: str) -> None:
        """外部恢复上次保存的文本；若 saved 为空但有历史，默认显示历史第一项。"""
        t = text.strip().upper()
        if not t and self._history:
            t = self._history[0]
        self.setText(t)
        self._refresh_dropdown(t)
        # 恢复文本不记录历史（避免把旧值重复写入队首）
    def set_names_provider(self, provider: Callable[[], Sequence[str]]) -> None:
        """设置完整设备名来源。Flash 页切烧录器 kind 时调用。"""
        self._names_provider = provider
        self._refresh_completer()
        self._refresh_dropdown(self.currentText())

    # ------------------------------------------------------------------
    # 历史读写
    # ------------------------------------------------------------------
    def _load_history(self) -> list[str]:
        raw = self._cfg.get(self._history_key)
        if not isinstance(raw, list):
            return []
        # 过滤非字符串、去重、限制长度
        seen: set[str] = set()
        result: list[str] = []
        for item in raw:
            s = str(item).strip().upper()
            if s and s not in seen:
                seen.add(s)
                result.append(s)
                if len(result) >= self.MAX_HISTORY:
                    break
        return result

    def _save_history(self) -> None:
        self._cfg.set(self._history_key, list(self._history))

    def _record_use(self, text: str) -> None:
        """用户确认了一个目标名：加入历史并持久化。"""
        name = text.strip().upper()
        if not name:
            return
        if name in self._history:
            self._history.remove(name)
        self._history.insert(0, name)
        self._history = self._history[: self.MAX_HISTORY]
        self._save_history()

    # ------------------------------------------------------------------
    # 候选刷新
    # ------------------------------------------------------------------
    def _refresh_completer(self) -> None:
        """把完整设备库刷新到 QCompleter model。"""
        names = list(self._names_provider() if self._names_provider else ())
        self._completer_model.setStringList(names)

    def _dropdown_candidates(self, text: str) -> list[str]:
        """生成下拉 items：最多 8 条。"""
        query = text.strip().upper()
        if not query:
            return list(self._history)[: self.MAX_DROPDOWN_ITEMS]

        names = self._names_provider() if self._names_provider else ()
        matches: list[str] = []
        for n in names:
            if query in n.upper():
                matches.append(n)
                if len(matches) >= self.MAX_DROPDOWN_ITEMS:
                    break
        return matches

    def _refresh_dropdown(self, text: str) -> None:
        """刷新下拉 items，同时保持当前输入文本和 cursor 位置。"""
        self._updating = True
        try:
            candidates = self._dropdown_candidates(text)
            cursor = self.cursorPosition()
            self.blockSignals(True)
            self.clear()
            self.addItems(candidates)
            self.setText(text)
            self.setCursorPosition(cursor)
            self.blockSignals(False)
        finally:
            self._updating = False

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _on_text_changed(self, text: str) -> None:
        """用户正在输入：刷新下拉候选，保持输入文本。"""
        if self._updating:
            return
        self._refresh_dropdown(text)

    def _on_activated(self, _index: int) -> None:
        """用户从下拉选了一项。"""
        self._record_use(self.currentText())

    def _on_editing_finished(self) -> None:
        """用户按回车或失焦：记录历史。"""
        self._record_use(self.currentText())
