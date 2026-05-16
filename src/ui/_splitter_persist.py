"""QSplitter 状态持久化到 ConfigService。

为什么独立模块：RTT 页（未来可能内存页也用）需要"接 splitterMoved → cfg.set
base64 编码的 saveState"以及"启动时从 cfg.get → restoreState"两段逻辑。
抽出来调用方写 ``_splitter_persist.wire(self.splitter, self._cfg, "rtt_splitter_state")``
和 ``_splitter_persist.restore(self.splitter, self._cfg, "rtt_splitter_state", self._logger)``
即可，对齐 ``_infobar.py`` 的模块化风格。

注意：cfg.set 已有 200ms 节流（CLAUDE.md "ConfigService.set 高频值要节流"
经验），splitterMoved 拖动期间高频触发也不会拖死 SSD。
"""
from __future__ import annotations

import base64

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QSplitter


def wire(splitter: QSplitter, cfg, key: str) -> None:
    """接 splitterMoved 信号 → 把 saveState() base64 编码后存进 cfg[key]。"""
    def _save(*_args) -> None:
        cfg.set(key, base64.b64encode(bytes(splitter.saveState())).decode("ascii"))
    splitter.splitterMoved.connect(_save)


def restore(splitter: QSplitter, cfg, key: str, logger) -> None:
    """从 cfg[key] 取 base64 → 解码 → restoreState。

    异常路径（跨版本不兼容 / user_prefs 损坏）catch + warning，回落代码默认
    setStretchFactor 比例（即不调 restoreState）。
    """
    state_b64 = cfg.get(key)
    if not state_b64:
        return
    try:
        splitter.restoreState(QByteArray(base64.b64decode(state_b64)))
    except Exception as e:
        logger.warning(f"恢复 splitter state 失败 ({key})：{e}")
