"""pack_service 单元测试：通配匹配、文件名解析、枚举、删除、下载（mock Cache）。

不依赖网络/真实 pack：list/delete 用 tmp 目录，download/search 用 MagicMock 替
cmsis_pack_manager.Cache。
"""
from __future__ import annotations

from unittest.mock import MagicMock


def test_wildcard_eq():
    from core.pack_service import wildcard_eq
    assert wildcard_eq("stm32f030c8tx", "stm32f030c8t6") is True
    assert wildcard_eq("stm32f030c8tx", "stm32f030c8") is False  # 长度不同
    assert wildcard_eq("stm32f030c8tx", "stm32f030c8ab") is False  # 非 x 位严格


def test_parse_pack_file_name():
    from core.pack_service import _parse_pack_file_name
    assert _parse_pack_file_name("Keil.STM32F0_DFP.1.0.0.pack") == ("Keil", "STM32F0_DFP", "1.0.0")
    assert _parse_pack_file_name("Vendor.Name.pack") == ("Vendor", "Name", "")
    assert _parse_pack_file_name("nodelimiter.pack") == ("", "nodelimiter", "")


def test_list_installed_packs(tmp_path, monkeypatch):
    from core import pack_service
    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    (tmp_path / "Keil.STM32F0_DFP.1.0.0.pack").write_bytes(b"\x00" * 1024)
    (tmp_path / "Keil.STM32F1_DFP.4.0.0.pack").write_bytes(b"\x00" * 2048)
    (tmp_path / "not_a_pack.txt").write_text("x")
    packs = pack_service.list_installed_packs()
    assert len(packs) == 2
    assert packs[0].file_name == "Keil.STM32F0_DFP.1.0.0.pack"
    assert packs[0].vendor == "Keil"
    assert packs[0].name == "STM32F0_DFP"
    assert packs[0].version == "1.0.0"
    assert packs[0].size_bytes == 1024


def test_delete_pack(tmp_path, monkeypatch):
    from core import pack_service
    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    f = tmp_path / "Keil.STM32F0_DFP.1.0.0.pack"
    f.write_bytes(b"\x00")
    assert pack_service.delete_pack("Keil.STM32F0_DFP.1.0.0.pack") is True
    assert not f.exists()
    # 拒绝路径穿越与非 .pack 扩展名
    assert pack_service.delete_pack("../evil.pack") is False
    assert pack_service.delete_pack("not.pack.txt") is False


def test_get_pack_data_path_default(isolated_appdata):
    from core.pack_service import get_pack_data_path
    path = get_pack_data_path()
    # 默认 cmsis-pack-manager 全局目录（用户旧 pack 在此，无需迁移）
    assert "cmsis-pack-manager" in path


def test_get_pack_data_path_custom(isolated_appdata):
    from core import pack_service
    from core.config_service import ConfigService
    pack_service.invalidate_cache()
    pack_service.set_pack_data_path("/custom/pack/path")
    assert pack_service.get_pack_data_path() == "/custom/pack/path"
    pack_service.invalidate_cache()
    ConfigService().set("pack_data_path", "")


def test_download_pack_mock(monkeypatch):
    """download_pack 委托 Cache，mock 验证匹配+下载调用。"""
    from core import pack_service

    cache = MagicMock()
    cache.index = {"STM32F103C8Tx": MagicMock()}
    packs = [MagicMock()]
    cache.packs_for_devices.return_value = packs
    monkeypatch.setattr(pack_service, "get_pack_cache", lambda: cache)

    logs = []
    ok = pack_service.download_pack("STM32F103C8T6", log=lambda lv, msg: logs.append((lv, msg)))
    assert ok == "downloaded"
    cache.download_pack_list.assert_called_once_with(packs)
    assert any("下载完成" in m for _, m in logs)


def test_download_pack_no_match(monkeypatch):
    from core import pack_service
    cache = MagicMock()
    cache.index = {}
    monkeypatch.setattr(pack_service, "get_pack_cache", lambda: cache)
    assert pack_service.download_pack("STM32F999XY") == "failed"


def test_download_pack_skipped_when_installed(monkeypatch, tmp_path):
    """已安装的 pack 查重跳过，返回 'skipped'，不调 download_pack_list。"""
    from core import pack_service
    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    cache = MagicMock()
    cache.index = {"STM32F103C8Tx": MagicMock()}
    pack_ref = MagicMock()
    pack_ref.vendor = "Keil"
    pack_ref.pack = "STM32F1xx_DFP"
    pack_ref.version = "2.4.1"
    cache.packs_for_devices.return_value = [pack_ref]
    monkeypatch.setattr(pack_service, "get_pack_cache", lambda: cache)
    # 预置 pack 文件已存在
    (tmp_path / "Keil" / "STM32F1xx_DFP").mkdir(parents=True)
    (tmp_path / "Keil" / "STM32F1xx_DFP" / "2.4.1.pack").write_bytes(b"\x00")
    pack_service.invalidate_cache()
    result = pack_service.download_pack("STM32F103C8T6")
    assert result == "skipped"
    cache.download_pack_list.assert_not_called()


def test_list_installed_packs_subdir(tmp_path, monkeypatch):
    """CMSIS-Pack 下载结构 Vendor/Pack/Version.pack，list_installed_packs 递归扫描。"""
    from core import pack_service
    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    (tmp_path / "Keil" / "STM32F0xx_DFP").mkdir(parents=True)
    (tmp_path / "Keil" / "STM32F0xx_DFP" / "3.1.1.pack").write_bytes(b"\x00" * 1024)
    packs = pack_service.list_installed_packs()
    assert len(packs) == 1
    p = packs[0]
    assert p.vendor == "Keil"
    assert p.name == "STM32F0xx_DFP"
    assert p.version == "3.1.1"
    assert p.file_name == "Keil/STM32F0xx_DFP/3.1.1.pack"


def test_delete_pack_subdir(tmp_path, monkeypatch):
    """删除允许子目录相对路径，拒路径穿越。"""
    from core import pack_service
    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(tmp_path))
    f = tmp_path / "Keil" / "STM32F0xx_DFP" / "3.1.1.pack"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x00")
    assert pack_service.delete_pack("Keil/STM32F0xx_DFP/3.1.1.pack") is True
    assert not f.exists()
    # 路径穿越拒绝
    assert pack_service.delete_pack("../evil.pack") is False
    assert pack_service.delete_pack("Keil/../../escape.pack") is False


def test_migrate_legacy_packs(tmp_path, monkeypatch):
    """迁移全局目录旧 pack 到 pack_data_path，保留子目录结构，已存在则跳过。"""
    from core import pack_service
    legacy = tmp_path / "legacy"
    (legacy / "Keil" / "STM32F0xx_DFP").mkdir(parents=True)
    (legacy / "Keil" / "STM32F0xx_DFP" / "3.1.1.pack").write_bytes(b"\x00" * 100)
    dest = tmp_path / "packs"
    dest.mkdir()
    monkeypatch.setattr(pack_service, "get_pack_data_path", lambda: str(dest))
    monkeypatch.setattr(pack_service, "get_legacy_packs_dir", lambda: str(legacy))
    pack_service.invalidate_cache()

    count = pack_service.migrate_legacy_packs()
    assert count == 1
    assert (dest / "Keil" / "STM32F0xx_DFP" / "3.1.1.pack").exists()
    # 再次迁移：已存在，跳过
    assert pack_service.migrate_legacy_packs() == 0
