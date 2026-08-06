"""Tests for the JSON-driven option bytes module.

Runtime reads only ``data/ob_profiles/{device_id}.json`` (built by
``tools/build_ob_database.py``).  These tests verify the profile schema and the
ops executor with a mock backend (no hardware).  Hardware-verified paths
(F030+J-Link, F103+DAPLink) are cross-checked against the register sequences in
``scratch/agent1/agent2_findings.md``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.option_bytes import (
    ObProfile,
    RdpLevel,
    WrpInfo,
    available_device_ids,
    load_profile,
    read_rdp_level,
    read_wrp_status,
    set_rdp_level,
    set_wrp,
)


# ---------------------------------------------------------------------------
# Mock backend -- records writes, returns canned reads
# ---------------------------------------------------------------------------
class MockBackend:
    def __init__(self, reads: dict[int, int] | None = None):
        self.reads = dict(reads or {})
        self.writes: list[tuple[str, int, int]] = []
        self.resets = 0

    def mem_read32(self, addr: int) -> int:
        return self.reads.get(addr, 0)

    def mem_write32(self, addr: int, value: int) -> None:
        self.writes.append(("32", addr, value))

    def mem_write16(self, addr: int, value: int) -> None:
        self.writes.append(("16", addr, value))

    def reset(self) -> None:
        self.resets += 1

    def write32s(self) -> list[tuple[int, int]]:
        return [(a, v) for k, a, v in self.writes if k == "32"]

    def write16s(self) -> list[tuple[int, int]]:
        return [(a, v) for k, a, v in self.writes if k == "16"]


# ---------------------------------------------------------------------------
# Profile loading (JSON schema)
# ---------------------------------------------------------------------------
class TestObProfile:
    def test_f103_profile_loaded(self):
        p = load_profile("0x410")
        assert p.device_id == "0x410"
        assert p.family == "F1"
        assert p.verified is True
        # read-view: OBR @ 0x4002201C bit1 (boolean R)
        assert p.rdp_read_view is not None
        assert p.rdp_read_view.address == 0x4002201C
        assert p.rdp_read_view.bit_offset == 1 and p.rdp_read_view.bit_width == 1
        assert p.rdp_read_view.access == "R"
        # write-view: physical OB @ 0x1FFFF800 byte (W)
        assert p.rdp_write_view.address == 0x1FFFF800
        assert p.rdp_write_view.bit_width == 8 and p.rdp_write_view.access == "W"
        # F1 RDP L0 = 0xA5 (unique)
        assert 0xA5 in p.rdp_write_view.values

    def test_f030_profile_single_rw_view(self):
        p = load_profile("0x440")
        assert p.family == "F0"
        # F030: single RW field (no separate read-view)
        assert p.rdp_write_view.address == 0x1FFFF800
        assert p.rdp_write_view.access in ("RW", "W")
        assert 0xAA in p.rdp_write_view.values   # F0 L0 = 0xAA (NOT 0xA5)
        assert 0xCC in p.rdp_write_view.values   # L2

    def test_h7_dual_register_views(self):
        p = load_profile("0x483")
        assert p.family == "H7"
        # H7: OPTSR_CUR (R) + OPTSR_PRG (W), byte[8]
        assert p.rdp_read_view is not None
        assert p.rdp_read_view.address == 0x5200201C and p.rdp_read_view.access == "R"
        assert p.rdp_write_view.address == 0x52002020 and p.rdp_write_view.access == "W"
        assert p.rdp_read_view.bit_offset == 8

    def test_flash_regs_and_bits_from_svd(self):
        p = load_profile("0x410")
        # F1 FLASH register addresses (from SVD)
        assert p.flash_regs.keyr == 0x40022004
        assert p.flash_regs.optkeyr == 0x40022008
        assert p.flash_regs.cr == 0x40022010
        assert p.flash_regs.sr == 0x4002200C
        # F1 CR bit offsets
        assert p.cr_bits["LOCK"] == 7
        assert p.cr_bits["STRT"] == 6
        assert p.cr_bits["OPTER"] == 5
        assert p.cr_bits["OPTPG"] == 4
        assert p.cr_bits["OPTWRE"] == 9   # bit 9, not bit 2

    def test_l4_register_offsets_differ_from_f1(self):
        """L4 shares base 0x40022000 but different offsets -- proves SVD data is real."""
        p = load_profile("0x462")
        assert p.family == "L4"
        assert p.flash_regs.keyr == 0x40022008   # +0x08 not +0x04
        assert "OBL_LAUNCH" in p.cr_bits          # L4-only bit
        assert p.cr_bits["OBL_LAUNCH"] == 27

    def test_procedure_fields(self):
        f1 = load_profile("0x410")
        assert f1.procedure.ob_programming == "halfword"
        assert f1.procedure.obl_trigger == "reset"
        assert f1.procedure.obl_requires_power_cycle is False
        # F1 OPTKEY == FLASH key (legacy)
        assert f1.procedure.optkey_seq == f1.procedure.flash_key_seq == (0x45670123, 0xCDEF89AB)

        f4 = load_profile("0x413")
        assert f4.procedure.ob_programming == "optcr32"
        assert f4.procedure.obl_trigger == "opt_start"
        # F4 OPTKEY != FLASH key (modern split)
        assert f4.procedure.optkey_seq == (0x08192A3B, 0x4C5D6E7F)
        assert f4.procedure.flash_key_seq == (0x45670123, 0xCDEF89AB)

    def test_available_device_ids_includes_verified(self):
        ids = available_device_ids()
        for did in ["0x410", "0x440", "0x413", "0x462", "0x483"]:
            assert did in ids, f"missing verified chip {did}"


# ---------------------------------------------------------------------------
# ops: read_rdp_level
# ---------------------------------------------------------------------------
class TestReadRdpLevel:
    def test_f103_l0_via_obr_boolean(self):
        backend = MockBackend(reads={0x4002201C: 0x03FFFFFC})  # bit1=0
        assert read_rdp_level("0x410", backend) == RdpLevel.L0

    def test_f103_l1_via_obr_boolean(self):
        backend = MockBackend(reads={0x4002201C: 0x03FFFFFE})  # bit1=1
        assert read_rdp_level("0x410", backend) == RdpLevel.L1

    def test_f030_l0_via_byte(self):
        backend = MockBackend(reads={0x1FFFF800: 0xAA})
        assert read_rdp_level("0x440", backend) == RdpLevel.L0

    def test_f030_l2_via_byte(self):
        backend = MockBackend(reads={0x1FFFF800: 0xCC})
        assert read_rdp_level("0x440", backend) == RdpLevel.L2


# ---------------------------------------------------------------------------
# ops: set_rdp_level -- halfword path (F0/F1, HARDWARE-VERIFIED sequence)
# ---------------------------------------------------------------------------
class TestSetRdpHalfword:
    def test_refuse_l2(self):
        backend = MockBackend()
        with pytest.raises(ValueError, match="permanent"):
            set_rdp_level("0x440", RdpLevel.L2, backend)

    def test_f030_l0_to_l1_halfword_sequence(self):
        """Verify the exact register sequence from agent1 findings (F030 L0->L1)."""
        backend = MockBackend()
        result = set_rdp_level("0x440", RdpLevel.L1, backend)
        w32 = backend.write32s()
        w16 = backend.write16s()
        KEYR, OPTKEYR, CR, OB = 0x40022004, 0x40022008, 0x40022010, 0x1FFFF800

        # 1. unlock KEYR + OPTKEYR (F0 legacy: OPTKEY == FLASH key)
        assert (KEYR, 0x45670123) in w32
        assert (KEYR, 0xCDEF89AB) in w32
        assert (OPTKEYR, 0x45670123) in w32
        assert (OPTKEYR, 0xCDEF89AB) in w32

        # 2. erase OB: two CR writes (OPTER|OPTWRE then OPTER|STRT|OPTWRE)
        cr_vals = [v for a, v in w32 if a == CR]
        OPTER, STRT, OPTWRE, OPTPG, LOCK = (1 << 5), (1 << 6), (1 << 9), (1 << 4), (1 << 7)
        assert (OPTER | OPTWRE) in cr_vals
        assert (OPTER | STRT | OPTWRE) in cr_vals

        # 3. program halfword 0x44BB (RDP=0xBB, comp=0x44)
        assert (OB, 0x44BB) in w16
        assert (OPTPG | OPTWRE) in cr_vals

        # 4. lock
        assert LOCK in cr_vals

        # F0 OBL = power_cycle (no reset fired)
        assert result.obl_status == "needs_power_cycle"
        assert backend.resets == 0

    def test_f103_l0_to_l1_uses_legacy_optkey(self):
        """F1 OPTKEY == FLASH key (0x45670123), NOT 0x08192A3B."""
        backend = MockBackend()
        set_rdp_level("0x410", RdpLevel.L1, backend)
        w32 = backend.write32s()
        assert (0x40022008, 0x45670123) in w32   # OPTKEYR = FLASH key (legacy)
        assert (0x40022008, 0x08192A3B) not in w32


    def test_f103_l0_to_l1_resets_for_obl(self):
        """F1 OBL = reset (not power_cycle); backend.reset() fires."""
        backend = MockBackend()
        result = set_rdp_level("0x410", RdpLevel.L1, backend)
        assert result.obl_status == "applied"
        assert backend.resets == 1


# ---------------------------------------------------------------------------
# ops: set_wrp -- WRP (write protection) tests
# ---------------------------------------------------------------------------
# WRP profiles are synthesized in-test (data-layer subagent builds the JSON
# separately).  Each test loads the real chip profile for register/bits info,
# then monkeypatches `wrp` with a synthetic WrpInfo to exercise each mode.
# ---------------------------------------------------------------------------
def _load_real_profile(device_id: str) -> ObProfile:
    """Load a real profile from JSON (used for register addresses / bit maps)."""
    return load_profile(device_id)


def _with_wrp(profile: ObProfile, wrp: WrpInfo) -> ObProfile:
    """Return a copy of ``profile`` with its ``wrp`` field replaced.

    ObProfile is frozen; we use dataclasses.replace to keep all other fields.
    """
    from dataclasses import replace
    return replace(profile, wrp=wrp)


class TestSetWrp:
    """set_wrp: dispatch by wrp.model + procedure.ob_programming."""

    def test_none_model_raises_runtime_error(self, monkeypatch):
        """Chip with wrp.present=False -> RuntimeError."""
        profile = _load_real_profile("0x440")  # F030
        # Force wrp.present=False to test the guard (F030 actually has WRP now)
        from core.option_bytes import ops as ops_mod
        monkeypatch.setattr(
            ops_mod, "load_profile",
            lambda device_id: _with_wrp(profile, WrpInfo(present=False, model="none"))
        )
        backend = MockBackend()
        with pytest.raises(RuntimeError, match="WRP not supported"):
            set_wrp("0x440", backend)

    def test_bit_halfword_f030_full_protect_preserves_rdp(self, monkeypatch):
        """F030 bit+halfword: WRP halfword written to 0xFF00 and RDP preserved.

        Critical sequence under test: erasing OB wipes RDP, so the executor
        must (a) read RDP first, (b) erase OB, (c) write WRP=0xFF00, (d) write
        RDP back at its original value.

        Mock backend returns 0xAA at the RDP OB address (0x1FFFF800), so we
        expect RDP halfword 0x55AA (= 0xAA | (~0xAA & 0xFF) << 8 = 0xAA | 0x5500)
        to be re-written after the WRP write.

        WRP all-protect halfword for active_low=True: byte=0x00, complement=0xFF,
        halfword = 0x00 | (0xFF << 8) = 0xFF00.

        F030 (0x440) WRP fields: WRP_bit0 @ 0x1FFFF808 bit_off=0 (hw 0x1FFFF808),
        nWRP_bit8 @ 0x1FFFF808 bit_off=16 (hw 0x1FFFF80A).
        """
        backend = MockBackend(reads={0x1FFFF800: 0xAA})  # RDP currently = 0xAA (L0)
        result = set_wrp("0x440", backend)
        w32 = backend.write32s()
        w16 = backend.write16s()

        KEYR, OPTKEYR, CR, OB_RDP = (
            0x40022004, 0x40022008, 0x40022010, 0x1FFFF800,
        )
        OPTER, STRT, OPTWRE, OPTPG, LOCK = (1 << 5), (1 << 6), (1 << 9), (1 << 4), (1 << 7)

        # 1. unlock KEYR + OPTKEYR
        assert (KEYR, 0x45670123) in w32
        assert (KEYR, 0xCDEF89AB) in w32
        assert (OPTKEYR, 0x45670123) in w32
        assert (OPTKEYR, 0xCDEF89AB) in w32

        # 2. erase OB: two CR writes
        cr_vals = [v for a, v in w32 if a == CR]
        assert (OPTER | OPTWRE) in cr_vals
        assert (OPTER | STRT | OPTWRE) in cr_vals

        # 3. WRP halfwords written to 0xFF00 (all-protect, active_low)
        #    WRP_bit0 @ 0x1FFFF808 bit_off=0 -> hw_addr = 0x1FFFF808
        #    nWRP_bit8 @ 0x1FFFF808 bit_off=16 -> hw_addr = 0x1FFFF80A
        assert (0x1FFFF808, 0xFF00) in w16
        assert (0x1FFFF80A, 0xFF00) in w16

        # 4. RDP rewritten at original value 0xAA -> halfword 0x55AA
        assert (OB_RDP, 0x55AA) in w16

        # 5. OPTPG|OPTWRE was set before each program
        assert (OPTPG | OPTWRE) in cr_vals

        # 6. lock
        assert LOCK in cr_vals

        # F0 OBL = power_cycle (no reset)
        assert result == "needs_power_cycle"
        assert backend.resets == 0

    def test_bit_halfword_f1_preserves_rdp_via_obr_read(self, monkeypatch):
        """F1 bit+halfword: RDP read via OBR boolean -> defaults to 0xA5 (L0).

        F1 has a boolean read-view (OBR bit1) that cannot recover the raw OB
        byte; the executor assumes L0=0xA5 and re-writes that.  This is the
        documented contract: auto-add-wrp runs right after auto-unlock-rdp
        has set L0, so the chip is at L0 when WRP is added.

        F1 (0x410) WRP fields: WRP0/WRP8 @ 0x1FFFF808, WRP16/WRP24 @ 0x1FFFF80C.
        Halfword addresses: 0x1FFFF808, 0x1FFFF80A, 0x1FFFF80C, 0x1FFFF80E.
        """
        backend = MockBackend(reads={0x4002201C: 0x03FFFFFC})  # OBR bit1=0 -> L0
        result = set_wrp("0x410", backend)
        w16 = backend.write16s()

        # Each WRP halfword -> 0xFF00 (active_low all-protect)
        for hw_addr in (0x1FFFF808, 0x1FFFF80A, 0x1FFFF80C, 0x1FFFF80E):
            assert (hw_addr, 0xFF00) in w16, f"WRP halfword not written at 0x{hw_addr:X}"

        # RDP -> 0xA5 halfword (0xA5 | (~0xA5 & 0xFF) << 8 = 0xA5 | 0x5A00 = 0x5AA5)
        assert (0x1FFFF800, 0x5AA5) in w16

        # F1 OBL = reset (applied)
        assert result == "applied"
        assert backend.resets == 1

    def test_bit_optcr32_f407_clears_nwrp_bits(self, monkeypatch):
        """F4 bit+optcr32: nWRP bits in OPTCR cleared; RDP untouched.

        F4 (0x413) OPTCR layout: WRP0 at bit_offset=16, bit_width=12 (nWRP[11:0],
        active_low).  Starting from OPTCR with nWRP=0xFFF (unprotected) and
        RDP=0xAA (L0): expect nWRP bits cleared, RDP bits unchanged.
        """
        # WRP field: WRP0 @ 0x40023C14 bit_off=16 bit_w=12 active_low=True

        # Initial OPTCR: nWRP=0xFFF (unprotected), RDP=0xAA (L0), OPTLOCK=0
        initial_optcr = (0xFFF << 16) | (0xAA << 8)
        backend = MockBackend(reads={0x40023C14: initial_optcr})
        result = set_wrp("0x413", backend)
        w32 = backend.write32s()

        OPTCR = 0x40023C14
        OPTLOCK = 1 << 0
        OPTSTRT = 1 << 1

        # Expected: nWRP bits cleared (12 bits at offset 16), RDP bits preserved
        expected = initial_optcr & ~(0xFFF << 16)   # clear nWRP, keep RDP=0xAA

        # Writes to OPTCR: plain value, value|OPTSTRT, value|OPTLOCK
        optcr_writes = [v for a, v in w32 if a == OPTCR]
        assert expected in optcr_writes                      # initial write
        assert (expected | OPTSTRT) in optcr_writes          # trigger
        assert (expected | OPTLOCK) in optcr_writes          # lock

        # RDP bits in the written value must equal the original RDP (0xAA << 8)
        written = optcr_writes[0]
        rdp_mask = 0xFF << 8
        assert (written & rdp_mask) == (initial_optcr & rdp_mask)

        # F4 OBL = opt_start (applied, no reset)
        assert result == "applied"
        assert backend.resets == 0

    def test_edge_ob_register_l4_full_protect(self, monkeypatch):
        """L4 edge+ob_register_word: STRT=0, END=max in WRP1A/WRP1B registers.

        L4 (0x462) WRP fields: WRP1A_STRT/END @ 0x4002202C, WRP1B_STRT/END @ 0x40022030.
        STRT at bit_offset=0 (8 bits), END at bit_offset=16 (8 bits).
        Full protect = STRT=0, END=0xFF in each register.
        """
        # Initial WRP1A: STRT=0x10, END=0x20 -> 0x00200010
        # Initial WRP1B: STRT=0x05, END=0x08 -> 0x00080005
        backend = MockBackend(reads={
            0x4002202C: 0x00200010,
            0x40022030: 0x00080005,
        })
        result = set_wrp("0x462", backend)
        w32 = backend.write32s()

        WRP1A = 0x4002202C
        WRP1B = 0x40022030
        CR = 0x40022014
        OPTSTRT = 1 << 17
        LOCK = 1 << 31

        # Expected: STRT=0, END=0xFF -> 0x00FF0000 in each register
        expected_wrp = 0x00FF0000
        wrp1a_writes = [v for a, v in w32 if a == WRP1A]
        wrp1b_writes = [v for a, v in w32 if a == WRP1B]
        assert expected_wrp in wrp1a_writes
        assert expected_wrp in wrp1b_writes

        # CR.OPTSTRT trigger fired, then LOCK
        cr_writes = [v for a, v in w32 if a == CR]
        assert OPTSTRT in cr_writes
        assert LOCK in cr_writes

        # L4 OBL = cr_obl_launch (applied via _apply_obl writing OBL_LAUNCH)
        assert result == "applied"

    def test_bit_ob_register_h7_full_protect(self, monkeypatch):
        """H7 bit+ob_register_word: WPSN register bits cleared, OPTSTART fired.

        H7 (0x483) WRP field: WRPS @ 0x5200203C bit_off=0 bit_w=8 active_low=True.
        Full protect = clear all 8 bits (0=protected).  Trigger via OPTCR.OPTSTART.
        """
        # Initial WPSN register: 0x000000FF (all unprotected)
        backend = MockBackend(reads={0x5200203C: 0x000000FF})
        result = set_wrp("0x483", backend)
        w32 = backend.write32s()

        WPSN = 0x5200203C
        OPTCR = 0x52002018
        OPTSTART = 1 << 1
        OPTLOCK = 1 << 0

        # Expected: bits 0-7 cleared -> 0x00000000
        wpsn_writes = [v for a, v in w32 if a == WPSN]
        assert 0x00000000 in wpsn_writes

        # OPTCR.OPTSTART fired, then OPTLOCK
        optcr_writes = [v for a, v in w32 if a == OPTCR]
        assert OPTSTART in optcr_writes
        assert OPTLOCK in optcr_writes

        # H7 OBL = optcr_optstart (applied, no extra reset)
        assert result == "applied"

    def test_unknown_model_raises(self, monkeypatch):
        """Unknown WRP model -> NotImplementedError."""
        profile = _load_real_profile("0x440")
        wrp = WrpInfo(present=True, model="bogus", fields=())
        patched = _with_wrp(profile, wrp)
        from core.option_bytes import ops as ops_mod
        monkeypatch.setattr(ops_mod, "load_profile", lambda device_id: patched)
        with pytest.raises(NotImplementedError, match="bogus"):
            set_wrp("0x440", MockBackend())


# ---------------------------------------------------------------------------
# ops: read_wrp_status -- read WRP status (全保护/未保护/部分保护)
# ---------------------------------------------------------------------------
class TestReadWrpStatus:
    """read_wrp_status: returns 全保护/未保护/部分保护 by model."""

    def test_none_model_raises_runtime_error(self, monkeypatch):
        """Chip with wrp.present=False -> RuntimeError."""
        profile = _load_real_profile("0x440")
        from core.option_bytes import ops as ops_mod
        monkeypatch.setattr(
            ops_mod, "load_profile",
            lambda device_id: _with_wrp(profile, WrpInfo(present=False, model="none"))
        )
        with pytest.raises(RuntimeError, match="WRP not supported"):
            read_wrp_status("0x440", MockBackend())

    def test_bit_halfword_f030_all_protected(self):
        """F030 bit+halfword: all WRP fields = 0x00 (active_low) -> 全保护.

        F030 WRP fields: WRP_bit0 @ 0x1FFFF808 bit_off=0, nWRP_bit8 @ 0x1FFFF808 bit_off=16.
        Both active_low=True: 0x00 = protected.  Read 32-bit at 0x1FFFF808 = 0x00000000
        -> both fields 0x00 -> 全保护.
        """
        backend = MockBackend(reads={0x1FFFF808: 0x00000000})
        assert read_wrp_status("0x440", backend) == "全片写保护"

    def test_bit_halfword_f030_no_protect(self):
        """F030 bit+halfword: all WRP fields = 0xFF (active_low) -> 未保护.

        32-bit at 0x1FFFF808: low byte = 0xFF (WRP_bit0), bits[23:16] = 0xFF (nWRP_bit8).
        Note: halfword layout has value + complement; for unprotected, value=0xFF,
        complement=0x00.  32-bit read = 0x00FF00FF.
        """
        backend = MockBackend(reads={0x1FFFF808: 0x00FF00FF})
        assert read_wrp_status("0x440", backend) == "无写保护"

    def test_bit_halfword_f030_partial(self):
        """F030 bit+halfword: one field protected, one not -> 部分保护.

        WRP_bit0 = 0x00 (protected), nWRP_bit8 = 0xFF (unprotected).
        32-bit at 0x1FFFF808: low byte = 0x00, bits[23:16] = 0xFF -> 0x00FF0000.
        """
        backend = MockBackend(reads={0x1FFFF808: 0x00FF0000})
        assert read_wrp_status("0x440", backend) == "部分写保护"

    def test_bit_optcr32_f407_all_protected(self):
        """F4 bit+optcr32: nWRP=0x000 (12 bits cleared) -> 全保护.

        F4 WRP field: WRP0 @ 0x40023C14 bit_off=16 bit_w=12 active_low=True.
        All-protect = nWRP bits cleared.  OPTCR value with nWRP=0: (0x000 << 16).
        """
        backend = MockBackend(reads={0x40023C14: 0x00000000})
        assert read_wrp_status("0x413", backend) == "全片写保护"

    def test_bit_optcr32_f407_no_protect(self):
        """F4 bit+optcr32: nWRP=0xFFF (12 bits all set) -> 未保护."""
        backend = MockBackend(reads={0x40023C14: 0x0FFF0000})
        assert read_wrp_status("0x413", backend) == "无写保护"

    def test_bit_optcr32_f407_partial(self):
        """F4 bit+optcr32: nWRP=0x00F (some bits set, some clear) -> 部分保护."""
        backend = MockBackend(reads={0x40023C14: 0x000F0000})
        assert read_wrp_status("0x413", backend) == "部分写保护"

    def test_bit_ob_register_h7_all_protected(self):
        """H7 bit+ob_register: WPSN=0x00 (all 8 bits cleared, active_low) -> 全保护.

        H7 WRP field: WRPS @ 0x5200203C bit_off=0 bit_w=8 active_low=True.
        """
        backend = MockBackend(reads={0x5200203C: 0x00000000})
        assert read_wrp_status("0x483", backend) == "全片写保护"

    def test_bit_ob_register_h7_no_protect(self):
        """H7 bit+ob_register: WPSN=0xFF (all 8 bits set, active_low) -> 未保护."""
        backend = MockBackend(reads={0x5200203C: 0x000000FF})
        assert read_wrp_status("0x483", backend) == "无写保护"

    def test_edge_ob_register_l4_all_protected(self):
        """L4 edge+ob_register: STRT=0 & END=0xFF in both areas -> 全保护.

        L4 WRP1A/1B @ 0x4002202C/0x40022030, STRT bit_off=0 bit_w=8, END bit_off=16 bit_w=8.
        All-protect: STRT=0, END=0xFF -> register value = 0x00FF0000.
        """
        backend = MockBackend(reads={
            0x4002202C: 0x00FF0000,
            0x40022030: 0x00FF0000,
        })
        assert read_wrp_status("0x462", backend) == "全片写保护"

    def test_edge_ob_register_l4_no_protect(self):
        """L4 edge+ob_register: STRT>END in both areas -> 未保护.

        No-protect: STRT=0xFF, END=0x00 -> register value = 0x000000FF.
        """
        backend = MockBackend(reads={
            0x4002202C: 0x000000FF,
            0x40022030: 0x000000FF,
        })
        assert read_wrp_status("0x462", backend) == "无写保护"

    def test_edge_ob_register_l4_partial(self):
        """L4 edge+ob_register: 1A all-protect, 1B no-protect -> 部分保护."""
        backend = MockBackend(reads={
            0x4002202C: 0x00FF0000,  # 1A: STRT=0, END=0xFF -> all-protect
            0x40022030: 0x000000FF,  # 1B: STRT=0xFF, END=0 -> no-protect
        })
        assert read_wrp_status("0x462", backend) == "部分写保护"

    def test_edge_ob_register_l4_partial_range(self):
        """L4 edge+ob_register: STRT=0x10, END=0x20 (valid range) -> 部分保护."""
        backend = MockBackend(reads={
            0x4002202C: 0x00200010,  # 1A: STRT=0x10, END=0x20
            0x40022030: 0x00200010,  # 1B: STRT=0x10, END=0x20
        })
        assert read_wrp_status("0x462", backend) == "部分写保护"


# ---------------------------------------------------------------------------
# ops: set_wrp(protect_all=False) -- clear WRP
# ---------------------------------------------------------------------------
class TestSetWrpClear:
    """set_wrp(protect_all=False): clears all WRP bits / reverses edge range."""

    def test_bit_halfword_f030_clear_writes_0x00FF(self):
        """F030 bit+halfword protect_all=False: WRP halfword = 0x00FF, RDP preserved.

        active_low=True, protect_all=False -> byte = 0xFF (unprotected).
        Halfword = 0xFF | (~0xFF & 0xFF) << 8 = 0xFF | 0x00 << 8 = 0x00FF.

        F030 (0x440) WRP fields: WRP_bit0 @ 0x1FFFF808 bit_off=0 (hw 0x1FFFF808),
        nWRP_bit8 @ 0x1FFFF808 bit_off=16 (hw 0x1FFFF80A).
        """
        backend = MockBackend(reads={0x1FFFF800: 0xAA})  # RDP currently = 0xAA (L0)
        result = set_wrp("0x440", backend, protect_all=False)
        w16 = backend.write16s()

        # WRP halfwords = 0x00FF (unprotected, active_low)
        assert (0x1FFFF808, 0x00FF) in w16
        assert (0x1FFFF80A, 0x00FF) in w16

        # RDP rewritten at original value 0xAA -> halfword 0x55AA
        assert (0x1FFFF800, 0x55AA) in w16

        # F0 OBL = power_cycle (no reset)
        assert result == "needs_power_cycle"
        assert backend.resets == 0

    def test_bit_halfword_f1_clear_preserves_rdp(self):
        """F1 bit+halfword protect_all=False: each WRP halfword = 0x00FF, RDP=0x5AA5.

        F1 (0x410) WRP fields: WRP0/WRP8 @ 0x1FFFF808, WRP16/WRP24 @ 0x1FFFF80C.
        Halfword addresses: 0x1FFFF808, 0x1FFFF80A, 0x1FFFF80C, 0x1FFFF80E.
        """
        backend = MockBackend(reads={0x4002201C: 0x03FFFFFC})  # OBR bit1=0 -> L0
        result = set_wrp("0x410", backend, protect_all=False)
        w16 = backend.write16s()

        # Each WRP halfword -> 0x00FF (active_low, unprotected)
        for hw_addr in (0x1FFFF808, 0x1FFFF80A, 0x1FFFF80C, 0x1FFFF80E):
            assert (hw_addr, 0x00FF) in w16, f"WRP halfword not 0x00FF at 0x{hw_addr:X}"

        # RDP -> 0xA5 halfword (0xA5 | (~0xA5 & 0xFF) << 8 = 0x5AA5)
        assert (0x1FFFF800, 0x5AA5) in w16

        # F1 OBL = reset (applied)
        assert result == "applied"
        assert backend.resets == 1

    def test_bit_optcr32_f407_clear_sets_nwrp_bits(self):
        """F4 bit+optcr32 protect_all=False: nWRP bits set to 0xFFF (unprotected).

        F4 WRP field: WRP0 @ 0x40023C14 bit_off=16 bit_w=12 active_low=True.
        protect_all=False & active_low=True -> set bits (1 = unprotected).
        """
        # Initial OPTCR: nWRP=0x000 (protected), RDP=0xAA (L0)
        initial_optcr = (0x000 << 16) | (0xAA << 8)
        backend = MockBackend(reads={0x40023C14: initial_optcr})
        result = set_wrp("0x413", backend, protect_all=False)
        w32 = backend.write32s()

        OPTCR = 0x40023C14
        OPTLOCK = 1 << 0
        OPTSTRT = 1 << 1

        # Expected: nWRP bits set to 0xFFF, RDP preserved
        expected = initial_optcr | (0xFFF << 16)

        optcr_writes = [v for a, v in w32 if a == OPTCR]
        assert expected in optcr_writes
        assert (expected | OPTSTRT) in optcr_writes
        assert (expected | OPTLOCK) in optcr_writes

        # RDP bits preserved
        rdp_mask = 0xFF << 8
        assert (optcr_writes[0] & rdp_mask) == (initial_optcr & rdp_mask)

        assert result == "applied"

    def test_edge_ob_register_l4_clear_writes_strt_max_end_zero(self):
        """L4 edge+ob_register protect_all=False: STRT=max, END=0 -> 0x000000FF.

        L4 WRP1A/1B: STRT bit_off=0 bit_w=8, END bit_off=16 bit_w=8.
        protect_all=False -> STRT=0xFF, END=0x00 -> register = 0x000000FF.
        """
        backend = MockBackend(reads={
            0x4002202C: 0x00FF0000,  # currently all-protect
            0x40022030: 0x00FF0000,
        })
        result = set_wrp("0x462", backend, protect_all=False)
        w32 = backend.write32s()

        WRP1A = 0x4002202C
        WRP1B = 0x40022030

        # Expected: STRT=0xFF, END=0x00 -> 0x000000FF
        expected_clear = 0x000000FF
        wrp1a_writes = [v for a, v in w32 if a == WRP1A]
        wrp1b_writes = [v for a, v in w32 if a == WRP1B]
        assert expected_clear in wrp1a_writes
        assert expected_clear in wrp1b_writes

        assert result == "applied"

    def test_bit_ob_register_h7_clear_sets_wpsn_bits(self):
        """H7 bit+ob_register protect_all=False: WPSN bits set to 0xFF (unprotected).

        H7 WRP field: WRPS @ 0x5200203C bit_off=0 bit_w=8 active_low=True.
        protect_all=False & active_low=True -> set bits (1 = unprotected).
        """
        backend = MockBackend(reads={0x5200203C: 0x00000000})  # currently all-protect
        result = set_wrp("0x483", backend, protect_all=False)
        w32 = backend.write32s()

        WPSN = 0x5200203C
        OPTCR = 0x52002018
        OPTSTART = 1 << 1
        OPTLOCK = 1 << 0

        # Expected: bits 0-7 set -> 0x000000FF
        wpsn_writes = [v for a, v in w32 if a == WPSN]
        assert 0x000000FF in wpsn_writes

        optcr_writes = [v for a, v in w32 if a == OPTCR]
        assert OPTSTART in optcr_writes
        assert OPTLOCK in optcr_writes

        assert result == "applied"

