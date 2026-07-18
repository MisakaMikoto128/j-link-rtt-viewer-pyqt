"""ConfigService：默认值、节流、flush、atomic write。"""
import json
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from core.config_service import ConfigService


@pytest.fixture
def cfg(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # 把 bundled config.json 放进 tmp 临时 src
    bundled = tmp_path / "bundled_config.json"
    bundled.write_text(json.dumps({
        "chip_models": ["X", "Y"],
        "default_interface": "SWD",
        "default_speed_khz": 4000,
        "speed_options_khz": [100, 4000, 8000],
        "default_font_family": "Consolas",
        "default_font_size": 13,
        "default_rtt_channel": 0,
    }), encoding="utf-8")
    return ConfigService(bundled_config_path=bundled, throttle_ms=50)


def test_default_values(cfg):
    assert cfg.get("target_mcu") == ""
    assert cfg.get("rtt_channel") == 0
    assert cfg.get("send_history") == []
    assert cfg.get("theme") == "auto"
    assert cfg.get_chip_list() == ["X", "Y"]
    assert cfg.get_default_speeds() == [100, 4000, 8000]


def test_set_emits_signals(cfg, qapp):
    received = []
    cfg.theme_changed.connect(lambda v: received.append(("theme", v)))
    cfg.theme_color_changed.connect(lambda v: received.append(("color", v)))
    cfg.font_changed.connect(lambda f, s: received.append(("font", f, s)))

    cfg.set("theme", "dark")
    cfg.set("theme_color", "#ff0000")  # 非 DEFAULT 值，确保信号触发
    cfg.set("font_family", "Cascadia Mono")
    cfg.set("font_size", 16)

    QCoreApplication.processEvents()
    assert ("theme", "dark") in received
    assert ("color", "#ff0000") in received
    assert any(r[0] == "font" for r in received)


def test_set_throttled(cfg, qapp, tmp_path):
    user_prefs = tmp_path / "JLinkRTTViewer" / "user_prefs.json"

    cfg.set("target_mcu", "STM32G070CB")
    # 立即落盘 → 应该尚未写入
    assert not user_prefs.exists()

    # 等节流 timer + Qt 事件循环
    deadline = time.time() + 1.0
    while not user_prefs.exists() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    assert user_prefs.exists()
    data = json.loads(user_prefs.read_text(encoding="utf-8"))
    assert data["target_mcu"] == "STM32G070CB"


def test_flush_writes_immediately(cfg, qapp, tmp_path):
    user_prefs = tmp_path / "JLinkRTTViewer" / "user_prefs.json"

    cfg.set("rtt_channel", 5)
    cfg.flush()
    assert user_prefs.exists()
    data = json.loads(user_prefs.read_text(encoding="utf-8"))
    assert data["rtt_channel"] == 5


def test_send_history_truncated_to_50(cfg, qapp):
    long_hist = [f"cmd-{i}" for i in range(80)]
    cfg.set("send_history", long_hist)
    cfg.flush()
    assert cfg.get("send_history") == long_hist[-50:]


def test_reload_from_disk(cfg, qapp, tmp_path):
    cfg.set("theme", "dark")
    cfg.set("target_mcu", "STM32H750VB")
    cfg.flush()

    cfg2 = ConfigService(bundled_config_path=cfg._bundled_path, throttle_ms=50)
    assert cfg2.get("theme") == "dark"
    assert cfg2.get("target_mcu") == "STM32H750VB"


def test_user_prefs_path_uses_xdg_config_home_on_linux(monkeypatch, tmp_path):
    """Linux 下优先使用 XDG_CONFIG_HOME。"""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    path = ConfigService._compute_user_prefs_path()
    assert path == tmp_path / "xdg_config" / "JLinkRTTViewer" / "user_prefs.json"


def test_user_prefs_path_falls_back_to_dot_config_on_linux(monkeypatch, tmp_path):
    """Linux 下 XDG_CONFIG_HOME 未设置时回退到 ~/.config。"""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = ConfigService._compute_user_prefs_path()
    assert path == tmp_path / ".config" / "JLinkRTTViewer" / "user_prefs.json"


def test_user_prefs_path_uses_appdata_on_windows(monkeypatch, tmp_path):
    """Windows 下使用 APPDATA。"""
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = ConfigService._compute_user_prefs_path()
    assert path == tmp_path / "JLinkRTTViewer" / "user_prefs.json"

    cfg.set("speed_khz", True)
    assert cfg.get("speed_khz") == 4000  # 未被修改，保持 default


def test_atomic_write_on_crash(cfg, qapp, tmp_path, monkeypatch):
    """模拟写入时崩溃：tmp 文件即使存在，最终原文件不损坏。"""
    user_prefs = tmp_path / "JLinkRTTViewer" / "user_prefs.json"
    cfg.set("rtt_channel", 3)
    cfg.flush()

    original = user_prefs.read_text(encoding="utf-8")

    # mock os.replace 抛异常
    import os as _os
    original_replace = _os.replace

    def fake_replace(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr(_os, "replace", fake_replace)
    cfg.set("rtt_channel", 99)
    cfg.flush()  # 不应抛
    monkeypatch.setattr(_os, "replace", original_replace)

    # 原文件未被破坏
    assert user_prefs.read_text(encoding="utf-8") == original


def test_flash_defaults_present(tmp_path, monkeypatch):
    """新增的 flash_* 偏好键必须出现在 DEFAULTS 里，并有正确默认值。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    bundled = tmp_path / "bundled_config.json"
    bundled.write_text(json.dumps({
        "chip_models": ["X", "Y"],
        "default_interface": "SWD",
        "default_speed_khz": 4000,
        "speed_options_khz": [100, 4000, 8000],
        "default_font_family": "Consolas",
        "default_font_size": 13,
        "default_rtt_channel": 0,
    }), encoding="utf-8")
    cfg = ConfigService(bundled_config_path=bundled, throttle_ms=50)
    assert cfg.get("flash_device_name") == "STM32H750VB"
    assert cfg.get("flash_interface") == "SWD"
    assert cfg.get("flash_speed") == 4000
    assert cfg.get("flash_bin_address") == 0x08000000
    assert cfg.get("flash_erase_mode") == "sector"
    assert cfg.get("flash_post_action") == "reset_run"
    assert cfg.get("flash_verify") is False
    assert cfg.get("flash_recent_files") == []
    assert cfg.get("flash_recent_files_mtime") == {}


def test_flash_set_persists_recent_files(tmp_path, monkeypatch):
    """flash_recent_files 是 list，set 进去要保留。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    bundled = tmp_path / "bundled_config.json"
    bundled.write_text(json.dumps({
        "chip_models": ["X", "Y"],
        "default_interface": "SWD",
        "default_speed_khz": 4000,
        "speed_options_khz": [100, 4000, 8000],
        "default_font_family": "Consolas",
        "default_font_size": 13,
        "default_rtt_channel": 0,
    }), encoding="utf-8")
    cfg = ConfigService(bundled_config_path=bundled, throttle_ms=50)
    cfg.set("flash_recent_files", ["C:/a.axf", "C:/b.hex"])
    cfg.flush()
    cfg2 = ConfigService(bundled_config_path=bundled, throttle_ms=50)
    assert cfg2.get("flash_recent_files") == ["C:/a.axf", "C:/b.hex"]
