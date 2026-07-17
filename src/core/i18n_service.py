"""标准 Qt 国际化方案：

1. JsonTranslator(QTranslator) — 从 JSON 文件加载 Chinese → Translation 映射
2. 所有 UI 文本用 self.tr("中文源文本") — Qt 的 QEvent.LanguageChange 自动刷新
3. 语言切换：removeTranslator + installTranslator + 发送 LanguageChange 事件
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QLocale, QTranslator
from PySide6.QtWidgets import QApplication

_HERE = Path(__file__).resolve().parent
# 开发模式：src/i18n/；Nuitka standalone：exe 同目录下的 i18n/
_I18N_DIR_DEV = _HERE.parent / "i18n"
_I18N_DIR_BUNDLE = Path(sys.executable).resolve().parent / "i18n"
_I18N_DIR = _I18N_DIR_DEV if _I18N_DIR_DEV.exists() else _I18N_DIR_BUNDLE

_SUPPORTED_LANGS: list[str] = ["zh_CN", "zh_TW", "ja", "ko", "en", "fr"]
_DEFAULT_LANG: str = "zh_CN"

_LANG_NAMES: dict[str, str] = {
    "zh_CN": "简体中文",
    "zh_TW": "繁體中文",
    "ja": "日本語",
    "ko": "한국어",
    "en": "English",
    "fr": "Français",
}

# Qt locale → our lang code
_QLOCALE_TO_LANG: dict[str, str] = {
    "zh_CN": "zh_CN",
    "zh_TW": "zh_TW",
    "zh_HK": "zh_TW",
    "zh_SG": "zh_CN",
    "ja_JP": "ja",
    "ko_KR": "ko",
    "en_US": "en",
    "en_GB": "en",
    "en_AU": "en",
    "en_CA": "en",
    "fr_FR": "fr",
    "fr_CA": "fr",
    "fr_BE": "fr",
    "fr_CH": "fr",
}


class JsonTranslator(QTranslator):
    """从 src/i18n/<lang>.json 加载 Chinese→Translation 映射的 QTranslator。"""

    def __init__(self, lang: str, parent=None):
        super().__init__(parent)
        self._dict: dict[str, str] = {}
        self._lang = lang
        self._load(lang)

    def translate(self, context: str, source: str, disambiguation=None, n: int = -1) -> str:
        """查表返回翻译；未命中返回 source 原文。

        Qt 将非空返回值视为有效译文直接采用：返回空串会使控件显示空白，
        而非回退到 source 原文。因此未命中时必须返回 source 自身，
        保证翻译表缺失任意键时控件仍显示源文本。
        """
        return self._dict.get(source, source)

    @property
    def lang(self) -> str:
        return self._lang

    def _load(self, lang: str) -> None:
        path = _I18N_DIR / f"{lang}.json"
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._dict = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._dict = {}


def detect_system_language() -> str:
    """根据 QLocale.system().name() 返回支持的 lang code，否则 _DEFAULT_LANG。"""
    locale = QLocale.system().name()
    return _QLOCALE_TO_LANG.get(locale, _DEFAULT_LANG)


_translator_lock = threading.Lock()
_current_translator: JsonTranslator | None = None
_current_lang: str = _DEFAULT_LANG


def init_translator(lang: str | None = None) -> str:
    """初始化翻译器并安装到 QApplication。返回实际使用的 lang。

    所有语言（含 zh_CN 默认）都安装 JsonTranslator：
    - 非中文语言：把 UI 里的中文源文本翻成对应本地语言；
    - zh_CN：唯一作用是把 qfluentwidgets 内部用英文源文本调
      self.tr('OK'/'Cancel'/'Edit Color'/...) 的第三方控件（如 ColorDialog）
      翻成中文。项目自身的 self.tr('中文') 调用在 zh_CN.json 里不存在，
      未命中返回 source（中文本身），行为与不装翻译器一致。
    """
    global _current_lang, _current_translator
    if lang is None or lang not in _SUPPORTED_LANGS:
        lang = detect_system_language()
    if lang not in _SUPPORTED_LANGS:
        lang = _DEFAULT_LANG

    with _translator_lock:
        _current_lang = lang
        app = QApplication.instance()
        if _current_translator is not None:
            app.removeTranslator(_current_translator)
            _current_translator = None
        _current_translator = JsonTranslator(lang)
        app.installTranslator(_current_translator)
    return lang


def switch_language(lang: str) -> None:
    """切换语言：替换 translator + 发送 LanguageChange 事件自动刷新所有控件。

    所有语言都安装 JsonTranslator（见 init_translator 注释：zh_CN 也装，
    仅为翻译 ColorDialog 等第三方英文源控件）。"""
    global _current_lang, _current_translator
    if lang not in _SUPPORTED_LANGS:
        return
    if _current_lang == lang:
        return

    with _translator_lock:
        _current_lang = lang
        app = QApplication.instance()
        if _current_translator is not None:
            app.removeTranslator(_current_translator)
            _current_translator = None
        _current_translator = JsonTranslator(lang)
        app.installTranslator(_current_translator)

    # 发送 LanguageChange 事件到所有 widget，触发 changeEvent → _retranslate_ui
    if app is not None:
        for w in QApplication.allWidgets():
            QApplication.sendEvent(w, QEvent(QEvent.Type.LanguageChange))


def current_lang() -> str:
    return _current_lang


def lang_display_name(lang: str) -> str:
    return _LANG_NAMES.get(lang, lang)


def supported_langs() -> list[str]:
    return list(_SUPPORTED_LANGS)
