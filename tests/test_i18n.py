"""i18n：JsonTranslator 行为 + ColorDialog 第三方控件翻译回归。

- ColorDialog 内部用英文源文本调 self.tr('OK'/'Cancel'/'Edit Color'/'Red'/'Green'/'Blue'/'Opacity')，
  历史上 zh_CN 作为默认语言不安装翻译器，导致中文界面下颜色选择窗口全是英文按钮。
  现在所有语言（含 zh_CN）都装 JsonTranslator，zh_CN.json 里收了这些英文→中文映射。
- 校验 JsonTranslator 未命中返回 source（既不空白也不报错）。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget


@pytest.fixture(autouse=True)
def reset_translator_after_test(qapp):
    """init_translator 会改模块级全局 _current_translator 并装到 QApplication，
    跨测试泄漏会让后续用例的 self.tr('中文') 走到遗留 translator（如 'en' 返回英文）。
    每个用例跑完强制重置回默认 zh_CN。"""
    yield
    from core.i18n_service import init_translator
    init_translator("zh_CN")


@pytest.fixture
def dummy_parent(qapp):
    return QWidget()


def test_json_translator_miss_returns_source():
    """未命中的键返回 source 自身，绝不返回空串（Qt 把非空当有效译文采用）。"""
    from core.i18n_service import JsonTranslator
    t = JsonTranslator("en")
    assert t.translate("ctx", "不存在的键XYZ") == "不存在的键XYZ"


def _color_dialog_texts(lang, dummy_parent):
    from core.i18n_service import init_translator
    from qfluentwidgets import ColorDialog
    init_translator(lang)
    d = ColorDialog(QColor("#28afe9"), "title", dummy_parent, enableAlpha=False)
    texts = {
        "OK": d.yesButton.text(),
        "Cancel": d.cancelButton.text(),
        "Edit": d.editLabel.text(),
        "Red": d.redLabel.text(),
        "Green": d.greenLabel.text(),
        "Blue": d.blueLabel.text(),
        "Opacity": d.opacityLabel.text(),
    }
    d.deleteLater()
    return texts


def test_color_dialog_translates_in_zh_cn(dummy_parent):
    """zh_CN 下 ColorDialog 按钮应是中文（曾全是英文，CLAUDE.md 经验条目的回归点）。"""
    t = _color_dialog_texts("zh_CN", dummy_parent)
    assert t["OK"] == "确定"
    assert t["Cancel"] == "取消"
    assert t["Edit"] == "编辑颜色"
    assert t["Red"] == "红"
    assert t["Green"] == "绿"
    assert t["Blue"] == "蓝"
    assert t["Opacity"] == "不透明度"


def test_color_dialog_translates_in_fr(dummy_parent):
    """fr 下 ColorDialog 按钮应是法文。"""
    t = _color_dialog_texts("fr", dummy_parent)
    assert t["Cancel"] == "Annuler"
    assert t["Red"] == "Rouge"
    assert t["Green"] == "Vert"
    assert t["Blue"] == "Bleu"


def test_color_dialog_translates_in_ja(dummy_parent):
    """ja 下 ColorDialog 按钮应是日文。"""
    t = _color_dialog_texts("ja", dummy_parent)
    assert t["Cancel"] == "キャンセル"
    assert t["Red"] == "赤"


def test_own_zh_tr_unchanged_under_zh_translator(dummy_parent):
    """装上 zh_CN 翻译器后，项目自身 self.tr('外观') 仍返回中文原文（未命中返回 source）。"""
    from core.i18n_service import init_translator
    init_translator("zh_CN")
    w = QWidget()
    assert w.tr("外观") == "外观"
    w.deleteLater()


def test_own_en_tr_translates(dummy_parent):
    """装上 en 翻译器后，项目自身 self.tr('外观') 返回英文。"""
    from core.i18n_service import init_translator
    init_translator("en")
    w = QWidget()
    assert w.tr("外观") == "Appearance"
    w.deleteLater()