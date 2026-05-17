"""InfoBar 包装函数：统一 parent + position + 默认 duration。

为什么独立模块：项目里 InfoBar.{warning,error,success} 跨 5+ 个文件
重复 30+ 处，每处都带 ``parent=self, position=InfoBarPosition.TOP, duration=N``
的样板。这里抽 3 个 1 行函数，调用方写 ``infobar.warn(self, "标题", "正文")``
即可，可读性 + 一致性 ↑。

注意：parent 必须显式传，**永远在 main thread 调用**。worker 线程不要
直接调（违反 CLAUDE.md 跨线程规则）—— worker emit log_message 信号
让 UI 槽再用本模块弹出。
"""
from __future__ import annotations

from qfluentwidgets import InfoBar, InfoBarPosition


def warn(parent, title: str, msg: str = "", *, duration: int = 2000) -> None:
    InfoBar.warning(title, msg, parent=parent,
                    position=InfoBarPosition.TOP, duration=duration)


def err(parent, title: str, msg: str = "", *, duration: int = 3000) -> None:
    InfoBar.error(title, msg, parent=parent,
                  position=InfoBarPosition.TOP, duration=duration)


def ok(parent, title: str, msg: str = "", *, duration: int = 2000) -> None:
    InfoBar.success(title, msg, parent=parent,
                    position=InfoBarPosition.TOP, duration=duration)


# Aliases for semantic clarity
error = err
info = ok
success = ok
