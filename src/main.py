"""程序入口。

启动顺序：
1. 高 DPI 策略
2. QApplication
3. logger（先于业务模块）
4. pylink DLL 致命检测（失败即弹框退出）
5. ConfigService + 主题色
6. MainWindow.show()
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

# 确保 src 加入 path
SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)

    from core.logger import get_logger
    logger = get_logger()
    logger.info("应用启动")

    try:
        import pylink
        pylink.JLink()  # 触发 JLinkARM.dll 加载，构造失败立即抛
    except Exception as e:
        logger.error(f"加载 JLinkARM.dll 失败：{e}")
        QMessageBox.critical(
            None,
            "启动失败",
            f"加载 JLinkARM.dll 失败：\n\n{e}\n\n请确认已安装 SEGGER J-Link 驱动。",
        )
        return 1

    from core.config_service import ConfigService
    cfg = ConfigService()

    from qfluentwidgets import Theme, setTheme, setThemeColor
    theme_str = cfg.get("theme")
    if theme_str == "dark":
        setTheme(Theme.DARK)
    elif theme_str == "light":
        setTheme(Theme.LIGHT)
    else:
        setTheme(Theme.AUTO)
    setThemeColor(cfg.get("theme_color"))

    from ui.main_window import MainWindow
    win = MainWindow(cfg)
    win.show()

    rc = app.exec()
    cfg.flush()
    logger.info(f"应用退出，rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
