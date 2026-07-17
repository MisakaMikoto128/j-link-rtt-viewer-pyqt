"""全局界面字体的系统默认 family 解析。

`ui_font_family` 用户偏好里空串 = 「跟随系统」。但「系统」具体是哪个 family
不能写死（不同 Windows 版本 / 不同用户 DPI 设置会变），而且 Qt 一旦
`QApplication.setFont(具体 family)` 后，「再 setFamily(空串)」并不是回到系统
默认——Qt 会沿用上一次的 family。所以要显式解析一个「系统 UI family」来落 family。

机制：`main.py` 启动时（在任何 `setFont` 之前）调 `capture_system_ui_family()`
冻结一份 QApplication 初始 family；之后 `_apply_ui_font("")` 时用它还原。

注意 `QFontDatabase.systemFont(GeneralFont)` 在 Windows 上返回 Segoe UI，与
QApplication 默认一致；我们**两者择其先捕获的**——优先用 `main.py` 启动期捕获的
`QApplication.font().family()`（最贴近 Qt 实际渲染默认），次选 `systemFont`。
"""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

_CACHED_SYSTEM_FAMILY: str | None = None


def capture_system_ui_family() -> str:
    """冻结 QApplication 当前 family（必须在任何 setFont 之前调用）。"""
    global _CACHED_SYSTEM_FAMILY
    app = QApplication.instance()
    fam = ""
    if app is not None:
        fam = app.font().family() or ""
    if not fam:
        fam = QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()
    _CACHED_SYSTEM_FAMILY = fam
    return fam


def system_ui_family() -> str:
    """返回系统 UI family：优先 captured（启动期冻结），否则现取 systemFont。"""
    if _CACHED_SYSTEM_FAMILY:
        return _CACHED_SYSTEM_FAMILY
    app = QApplication.instance()
    if app is not None:
        fam = app.font().family()
        if fam:
            return fam
    return QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()


def resolve_ui_family(family: str | None) -> str:
    """偏好 family 为空 → 系统 family；否则原样返回。"""
    fam = (family or "").strip()
    return fam if fam else system_ui_family()


# qfluentwidgets 自带的中/日文兜底（用于气泡 font 列表里保证非拉丁字符能渲染）。
# ui_family 单一字体在西方字体里覆盖不到中文，Qt 会按列表顺序回退，故兜底要保留。
_CJK_FALLBACK_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei",
                          "PingFang SC", "Noto Sans CJK SC"]


def _build_fluent_font_families(ui_family: str) -> list[str]:
    """以 ui_family 为首选，拼上中/日文兜底（去重、过滤空白）。"""
    fams: list[str] = []
    for fam in [ui_family, *_CJK_FALLBACK_FAMILIES]:
        fam = (fam or "").strip()
        if fam and fam not in fams:
            fams.append(fam)
    return fams


def _sync_fluent_font_families(ui_family: str) -> None:
    """把 qfluentwidgets 的 qconfig.fontFamilies 设成 [ui_family + CJK 兜底]。

    qfluentwidgets 的 ToolTip / TeachingTip / Flyout 气泡用 QSS `font: 12px
    --FontFamilies`，--FontFamilies 由 fontFamilies 决定。设它之后，**后续构造**的
    气泡（ToolTipFilter 每次悬停重建气泡；TeachingTipView 每次点击重建）自动应用新
    family；气泡的 12px 字号由 QSS 锁死不变（用户要的就是「字号不变但 family 跟随」）。
    已存在的同类控件不会刷新——但气泡本身是短生命周期的，下次悬停/点击就会新构造。
    """
    try:
        from qfluentwidgets import qconfig
    except Exception:
        return
    fams = _build_fluent_font_families(ui_family)
    if not fams:
        return
    cur = list(qconfig.get(qconfig.fontFamilies) or [])
    if cur == fams:
        return
    qconfig.set(qconfig.fontFamilies, fams, save=False, copy=False)


# ------------------------------------------------------------------
# QSS `font:` 锁定控件（RadioButton 等）的字号覆盖
# ------------------------------------------------------------------
# 部分 qfluentwidgets 控件的 qss 里硬编码了 `font: Npx --FontFamilies;`（如
# RadioButton=14px、右键菜单=14px、InfoBar=14px、对话框按钮=15px 等）。Qt 里
# 控件自身 styleSheet 的 `font:` 规则**优先级高于 setFont()**——全局界面字号热
# 更新遍历 setFont 对这类控件无效，表现为「字号怎么都不变」。
# 修复：往控件自己的 styleSheet 上**追加**一条 font 规则（更高 specificity），
# 覆盖 qss 里的固定值。本模块用哨兵注释标记本功能的追加段，重复调用时替换哨兵
# 区间而不是无限追加，保证幂等。family 用「偏好+系统兜底+原 --FontFamilies」三段
# 拼，保证 CJK 不缺字。
#
# 哨兵区间：BEGIN..END 之间是本功能写入的内容；其余 styleSheet 原样保留。
_QSS_FONT_OVERRIDE_BEGIN = "/* UI_FONT_OVERRIDE_BEGIN */"
_QSS_FONT_OVERRIDE_END = "/* UI_FONT_OVERRIDE_END */"

# 哪些控件（按类名）有 QSS `font:` 锁定、需要追加覆盖。key=控件类名（QObject
# metaObject 的 className，对 qfluentwidgets 类即 Python 类名）。
# 只收项目实际用到的（右键菜单/TimePicker/InfoBar 等虽也有 qss font 锁定，
# 但项目没用到，不纳入；用到时再加）。发现新的锁定控件就加进来。
_QSS_FONT_LOCKED_CLASS_NAMES: tuple[str, ...] = (
    "RadioButton",              # BUTTON qss: 14px；烧录页 SWD/JTAG
)


def _build_qss_font_rule(family: str, pt_size: int) -> str:
    """生成一条覆盖 qss `font:` 的 styleSheet 规则体（不含选择器）。

    同时覆盖 family+size：
    - size 必须覆盖——qss `font: Npx --FontFamilies` 把字号 px 锁死，setFont 改不动；
    - family 也要覆盖——已存在的 RadioButton 实例不会随 qconfig.fontFamilies 刷新
      （qss 的 --FontFamilies 是构造/应用样式时解析的模板变量，老控件仍是旧值），
      实测只有显式写进 styleSheet 的 font-family 才能让已有实例跟着变。
    family 直接写 ui_family（已是 resolve 后的具体字体名）；CJK 兜底靠
    _sync_fluent_font_families 里 fontFamilies 的列表，二者互不冲突。
    """
    return (
        f"{_QSS_FONT_OVERRIDE_BEGIN}\n"
        f"font-family: '{family}';\n"
        f"font-size: {pt_size}pt;\n"
        f"{_QSS_FONT_OVERRIDE_END}"
    )


def _strip_qss_font_override(style_sheet: str) -> str:
    """移除 styleSheet 里我们之前用哨兵追加的 font 覆盖段（幂等清理）。"""
    begin = style_sheet.find(_QSS_FONT_OVERRIDE_BEGIN)
    end = style_sheet.find(_QSS_FONT_OVERRIDE_END)
    if begin < 0 or end < 0 or end < begin:
        return style_sheet
    return (style_sheet[:begin] + style_sheet[end + len(_QSS_FONT_OVERRIDE_END):]).rstrip()


def _apply_qss_font_override(widget, family: str, pt_size: int) -> None:
    """给「QSS font 锁定」控件追加 font-family+font-size 覆盖，让全局界面字体对它生效。

    实测（scratch/probe_rb2.py / probe_rb_family.py）：
    - RadioButton setStyleSheet 追加 font 规则后 show() 即生效；
    - qconfig.fontFamilies 改 + repolish 对已存在控件无效（--FontFamilies 是构造时
      解析的模板变量）；
    - 只 setFont() 对 qss `font:` 锁定的控件完全无效（QSS font 优先级高于 setFont）。
    所以唯一可靠的覆盖方式是 setStyleSheet 里显式写 font-family + font-size。
    """
    base = _strip_qss_font_override(widget.styleSheet() or "")
    rule = _build_qss_font_rule(family, pt_size)
    # 规则选择器必须写成具体类名，否则 Qt 无法把 font 规则落到这个控件上
    cls = widget.metaObject().className()
    widget.setStyleSheet(f"{base}\n{cls} {{\n{rule}\n}}" if base else f"{cls} {{\n{rule}\n}}")


def sync_qss_font_locked_widgets(root, family: str, pt_size: int) -> None:
    """遍历 root 及其后代，给 QSS `font:` 锁定控件应用字体覆盖。

    只处理 `_QSS_FONT_LOCKED_CLASS_NAMES` 里的类（当前仅 RadioButton）。`root`
    传 QApplication（用 allWidgets）或任意父 widget。RadioButton 在烧录页构造时
    创建，MainWindow 构造末尾的 _apply_ui_font 会覆盖到；此后字号/字体变更也会
    再调一次，故不需要额外的 show 时补调机制。
    """
    from PySide6.QtWidgets import QWidget
    widgets: list[QWidget] = []
    if isinstance(root, QWidget):
        widgets.append(root)
        widgets.extend(root.findChildren(QWidget))
    else:
        # root 可能是 QApplication：用 allWidgets
        try:
            widgets = list(root.allWidgets())
        except Exception:
            widgets = []
    for w in widgets:
        if w.metaObject().className() in _QSS_FONT_LOCKED_CLASS_NAMES:
            _apply_qss_font_override(w, family, pt_size)