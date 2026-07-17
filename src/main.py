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
from PySide6.QtGui import QIcon
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

    # 冻结系统 UI 字体 family（必须在 setFont 之前）：用于「跟随系统」时还原。
    # 一旦 setFont 了具体 family，QApplication.font() 就回不去系统默认了，得靠这里捕获。
    from core._ui_font import capture_system_ui_family
    capture_system_ui_family()

    # 应用级 icon：影响任务栏 / Alt+Tab。MainWindow 自己也会 setWindowIcon
    # 触发 FluentTitleBar 更新，两者互不依赖（不存在调用顺序问题）。
    from core._paths import find_app_icon
    icon_path = find_app_icon()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

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

    from core.screen_keeper import apply_keep_screen_on
    if cfg.get("keep_screen_on"):
        apply_keep_screen_on(True)

    from core.i18n_service import init_translator
    init_translator(cfg.get("language"))

    from qfluentwidgets import Theme, setTheme, setThemeColor
    theme_str = cfg.get("theme")
    if theme_str == "dark":
        setTheme(Theme.DARK)
    elif theme_str == "light":
        setTheme(Theme.LIGHT)
    else:
        setTheme(Theme.AUTO)
    setThemeColor(cfg.get("theme_color"))

    # 全局界面字体 / 字号：QApplication.setFont 设默认字体，所有 widget 继承。
    # RTT 显示区 / 内存页 hex dump 有各自字体覆盖（_apply_font 单独 setFont）。
    from core._ui_font import resolve_ui_family
    _app_font = app.font()
    _ui_family = resolve_ui_family(cfg.get("ui_font_family") or "")
    if _ui_family:
        _app_font.setFamily(_ui_family)
    _app_font.setPointSize(int(cfg.get("ui_font_size") or 9))
    app.setFont(_app_font)

    # 同步 qfluentwidgets 的 fontFamilies（气泡 ToolTip/TeachingTip 的 family 来源）。
    # 在 MainWindow 构造前设，首次悬停气泡就用对 family。
    from core._ui_font import _sync_fluent_font_families
    _sync_fluent_font_families(_ui_family)

    from ui.main_window import MainWindow
    win = MainWindow(cfg)
    win.show()

    rc = app.exec()
    apply_keep_screen_on(False)
    cfg.flush()
    logger.info(f"应用退出，rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
