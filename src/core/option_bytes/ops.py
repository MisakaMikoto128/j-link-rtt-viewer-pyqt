"""Option-byte operation executor -- consumes :class:`ObProfile` (JSON-loaded).

The profile is built at build-time from ST XML + SVD + RM constants
(see ``tools/build_ob_database.py``).  At runtime this module only needs the
profile + an :class:`ObBackend` (mem_read32 / mem_write32 / mem_write16 / reset)
supplied by the probe adapter.

Hardware-verified paths:
  - halfword (F0 + J-Link, F1 + DAPLink/pyOCD): scratch/agent1/agent2_findings.md
  - optcr32 / ob_register_word: translated from RM, pending HW verify
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .ob_profile import ObProfile, RdpView, WrpField, load_profile


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------
class RdpLevel(Enum):
    L0 = "L0"   # no protection
    L1 = "L1"   # read protection (mass-erase on regression to L0)
    L2 = "L2"   # permanent chip protection (irreversible -- refused)


class ObBackend(Protocol):
    """Memory + reset interface the OB executor needs from a probe backend."""
    def mem_read32(self, addr: int) -> int: ...
    def mem_write32(self, addr: int, value: int) -> None: ...
    def mem_write16(self, addr: int, value: int) -> None: ...
    def reset(self) -> None: ...


@dataclass
class SetRdpResult:
    level_set: RdpLevel
    obl_status: str   # "applied" | "needs_power_cycle"


# ---------------------------------------------------------------------------
# RDP value lookup from profile enum
# ---------------------------------------------------------------------------
def _rdp_value_for_level(profile: ObProfile, level: RdpLevel) -> int:
    """Return the RDP byte value to program for a target level.

    L0 comes from the profile enum (0xA5 for F1, 0xAA for others).
    L1 uses 0xBB (the XML example; any non-L0 non-0xCC value is hardware-accepted).
    """
    enum = profile.rdp_write_view.values
    if level == RdpLevel.L0:
        for val, label in enum.items():
            low = label.lower()
            if "level 0" in low or "no protection" in low:
                return val
        raise ValueError(f"no Level-0 value in profile enum for {profile.device_id}; enum={enum}")
    if level == RdpLevel.L1:
        for val, label in enum.items():
            if "level 1" in label.lower():
                return val
        return 0xBB
    return 0xCC  # L2 -- caller refuses before reaching here


def _decode_rdp(field_val: int, view: RdpView) -> RdpLevel:
    """Decode a raw RDP field value into a level."""
    if view.bit_width == 1:
        # Boolean read-view (F1 OBR bit1): 0 = L0, 1 = protected (L1 or L2).
        # OBR cannot distinguish L1 from L2; treat protected as L1.
        return RdpLevel.L0 if field_val == 0 else RdpLevel.L1
    if field_val == 0xCC:
        return RdpLevel.L2
    for val, label in view.values.items():
        if val == field_val and ("level 0" in label.lower() or "no protection" in label.lower()):
            return RdpLevel.L0
    return RdpLevel.L1


def _extract_field(raw: int, view: RdpView) -> int:
    mask = (1 << view.bit_width) - 1
    return (raw >> view.bit_offset) & mask


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def read_rdp_level(device_id: str, backend: ObBackend) -> RdpLevel:
    """Read the current RDP level via the profile's read-view (L1-safe)."""
    profile = load_profile(device_id)
    view = profile.rdp_read_view or profile.rdp_write_view
    if view is None:
        raise RuntimeError(f"{device_id}: no RDP view in profile")
    raw = backend.mem_read32(view.address)
    return _decode_rdp(_extract_field(raw, view), view)


# ---------------------------------------------------------------------------
# Write: dispatch by programming model
# ---------------------------------------------------------------------------
def set_rdp_level(device_id: str, target: RdpLevel, backend: ObBackend) -> SetRdpResult:
    """Set the RDP level.  Refuses L2.  Attempts the OB write via the backend;
    if the chip rejects it (e.g. L1 blocks debug OB write -> PGSERR), the SR
    error check raises RuntimeError.  Returns the OBL application status.
    """
    if target == RdpLevel.L2:
        raise ValueError("RDP Level 2 is permanent and irreversible; refused")
    profile = load_profile(device_id)

    if profile.rdp_write_view is None:
        raise RuntimeError(f"{device_id}: no RDP write-view in profile")

    mode = profile.procedure.ob_programming
    if mode == "halfword":
        _set_rdp_halfword(profile, target, backend)
    elif mode == "optcr32":
        _set_rdp_optcr32(profile, target, backend)
    elif mode == "ob_register_word":
        _set_rdp_ob_register(profile, target, backend)
    else:
        raise NotImplementedError(f"programming mode {mode!r} not implemented")

    return SetRdpResult(level_set=target, obl_status=_apply_obl(profile, backend))


# ---------------------------------------------------------------------------
# Helpers: bit masks, unlock, busy-wait
# ---------------------------------------------------------------------------
def _bit(bits: dict[str, int], name: str) -> int:
    try:
        return 1 << bits[name]
    except KeyError:
        raise KeyError(f"bit {name!r} not in profile (available: {sorted(bits)})") from None


def _unlock_flash(profile: ObProfile, backend: ObBackend) -> None:
    k1, k2 = profile.procedure.flash_key_seq
    ok1, ok2 = profile.procedure.optkey_seq
    backend.mem_write32(profile.flash_regs.keyr, k1)
    backend.mem_write32(profile.flash_regs.keyr, k2)
    backend.mem_write32(profile.flash_regs.optkeyr, ok1)
    backend.mem_write32(profile.flash_regs.optkeyr, ok2)


def _wait_bsy_sr(profile: ObProfile, backend: ObBackend) -> None:
    bsy = _bit(profile.sr_bits, "BSY")
    while backend.mem_read32(profile.flash_regs.sr) & bsy:
        pass


def _check_sr_errors(profile: ObProfile, backend: ObBackend) -> None:
    """Read FLASH_SR and raise if PGSERR/WRPERR set (chip rejected the write).

    This is how L1->L0 fails on chips that block debug OB writes under L1:
    the write is refused, PGSERR is set.  We detect it and report honestly.
    """
    sr = backend.mem_read32(profile.flash_regs.sr)
    bits = []
    if "PGSERR" in profile.sr_bits and sr & (1 << profile.sr_bits["PGSERR"]):
        bits.append("PGSERR")
    if "WRPERR" in profile.sr_bits and sr & (1 << profile.sr_bits["WRPERR"]):
        bits.append("WRPERR")
    if bits:
        raise RuntimeError(f"OB write rejected (SR={'/'.join(bits)}; chip may block under L1)")


def _wait_bsy_optsr(profile: ObProfile, backend: ObBackend) -> None:
    opt_busy = _bit(profile.optsr_cur_bits, "OPT_BUSY")
    while backend.mem_read32(profile.flash_regs.optsr_cur) & opt_busy:  # type: ignore[arg-type]
        pass


# ---------------------------------------------------------------------------
# halfword (F0, F1) -- HARDWARE-VERIFIED
# ---------------------------------------------------------------------------
def _set_rdp_halfword(profile: ObProfile, target: RdpLevel, backend: ObBackend) -> None:
    """Program RDP via 16-bit halfword writes to OB memory (F0/F1/F3/L0/L1).

    Verified on F030+J-Link and F103+DAPLink.  Critical sequence:
      - erase OB needs TWO CR writes (OPTER|OPTWRE, then OPTER|STRT|OPTWRE)
      - re-unlock after erase (OPT_LOCK re-engages)
      - program via memory_write16 (NOT memory_write nbits=16)
    """
    rdp_val = _rdp_value_for_level(profile, target)
    halfword = rdp_val | ((~rdp_val & 0xFF) << 8)
    cr = profile.flash_regs.cr

    LOCK = _bit(profile.cr_bits, "LOCK")
    STRT = _bit(profile.cr_bits, "STRT")
    OPTER = _bit(profile.cr_bits, "OPTER")
    OPTPG = _bit(profile.cr_bits, "OPTPG")
    OPTWRE = _bit(profile.cr_bits, "OPTWRE")

    _unlock_flash(profile, backend)
    _wait_bsy_sr(profile, backend)

    # erase OB (two CR writes -- STRT before OPTER latches hits wrong target -> PGSERR)
    backend.mem_write32(cr, OPTER | OPTWRE)
    backend.mem_write32(cr, OPTER | STRT | OPTWRE)
    _wait_bsy_sr(profile, backend)

    _unlock_flash(profile, backend)  # re-unlock (OPT_LOCK re-engages after erase)

    backend.mem_write32(cr, OPTPG | OPTWRE)
    backend.mem_write16(profile.rdp_write_view.address, halfword)
    _wait_bsy_sr(profile, backend)
    _check_sr_errors(profile, backend)

    backend.mem_write32(cr, LOCK)


# ---------------------------------------------------------------------------
# optcr32 (F2, F4, F7) -- translated from RM, pending HW verify
# ---------------------------------------------------------------------------
def _set_rdp_optcr32(profile: ObProfile, target: RdpLevel, backend: ObBackend) -> None:
    """Program RDP via 32-bit OPTCR write (OB bits inline).  F4/F7 path."""
    rdp_val = _rdp_value_for_level(profile, target)
    view = profile.rdp_write_view
    mask = (1 << view.bit_width) - 1
    rdp_shifted = (rdp_val & mask) << view.bit_offset
    optcr = profile.flash_regs.optcr

    OPTLOCK = _bit(profile.optcr_bits, "OPTLOCK")
    OPTSTRT = _bit(profile.optcr_bits, "OPTSTRT")

    _unlock_flash(profile, backend)
    _wait_bsy_sr(profile, backend)

    val = backend.mem_read32(optcr)  # type: ignore[arg-type]
    val &= ~(mask << view.bit_offset)
    val |= rdp_shifted
    backend.mem_write32(optcr, val)  # type: ignore[arg-type]
    backend.mem_write32(optcr, val | OPTSTRT)  # type: ignore[arg-type]
    _wait_bsy_sr(profile, backend)
    backend.mem_write32(optcr, val | OPTLOCK)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ob_register_word (L4, L5, G0, G4, WB, WL, H7) -- PENDING HW VERIFY
# ---------------------------------------------------------------------------
def _set_rdp_ob_register(profile: ObProfile, target: RdpLevel, backend: ObBackend) -> None:
    """Program RDP via separate OB register + trigger bit (L4/H7 family)."""
    rdp_val = _rdp_value_for_level(profile, target)
    view = profile.rdp_write_view
    mask = (1 << view.bit_width) - 1
    rdp_shifted = (rdp_val & mask) << view.bit_offset

    _unlock_flash(profile, backend)
    _wait_bsy_sr(profile, backend)

    if profile.procedure.obl_trigger == "optcr_optstart":
        # H7: write OPTSR_PRG, trigger via OPTCR.OPTSTART, poll OPTSR_CUR.OPT_BUSY
        prg = profile.flash_regs.optsr_prg
        optcr = profile.flash_regs.optcr
        val = backend.mem_read32(prg)  # type: ignore[arg-type]
        val &= ~(mask << view.bit_offset)
        val |= rdp_shifted
        backend.mem_write32(prg, val)  # type: ignore[arg-type]
        OPTSTART = _bit(profile.optcr_bits, "OPTSTART")
        OPTLOCK = _bit(profile.optcr_bits, "OPTLOCK")
        backend.mem_write32(optcr, OPTSTART)  # type: ignore[arg-type]
        _wait_bsy_optsr(profile, backend)
        backend.mem_write32(optcr, OPTLOCK)  # type: ignore[arg-type]
    else:
        # L4-style: write OPTR, trigger via CR.OPTSTRT (OBL_LAUNCH applied by _apply_obl)
        optr = profile.flash_regs.optr
        val = backend.mem_read32(optr)  # type: ignore[arg-type]
        val &= ~(mask << view.bit_offset)
        val |= rdp_shifted
        backend.mem_write32(optr, val)  # type: ignore[arg-type]
        OPTSTRT = _bit(profile.cr_bits, "OPTSTRT")
        LOCK = _bit(profile.cr_bits, "LOCK")
        backend.mem_write32(profile.flash_regs.cr, OPTSTRT)
        _wait_bsy_sr(profile, backend)
        backend.mem_write32(profile.flash_regs.cr, LOCK)


# ---------------------------------------------------------------------------
# OBL reload trigger
# ---------------------------------------------------------------------------
def _apply_obl(profile: ObProfile, backend: ObBackend) -> str:
    trigger = profile.procedure.obl_trigger
    if trigger == "power_cycle":
        return "needs_power_cycle"   # F0 <=64KB: only physical POR reloads OBR
    if trigger == "reset":
        backend.reset()              # F1: system reset reloads OB
        return "applied"
    if trigger == "cr_obl_launch":
        OBL_LAUNCH = _bit(profile.cr_bits, "OBL_LAUNCH")
        backend.mem_write32(profile.flash_regs.cr, OBL_LAUNCH)
        return "applied"
    if trigger in ("opt_start", "optcr_optstart"):
        return "applied"   # OPTSTRT/OPTSTART fired during programming; OBL auto-applies
    raise NotImplementedError(f"obl_trigger {trigger!r} not implemented")


# ===========================================================================
# WRP (Write Protection) -- set_wrp
# ===========================================================================
# Schema (per-chip JSON `wrp` section):
#   {
#     "present": true,
#     "model": "bit" | "edge" | "none",
#     "fields": [
#       {"name":"WRP0","address":"0x1FFFF808","bit_offset":0,"bit_width":8,
#        "access":"W","active_low":true,"values":{}}
#     ]
#   }
#
# Modes:
#  - bit  : F0/F1/F3/F4/F7/H7/L0.  Each field is one WRP byte (or nWRP bit
#           group).  active_low=True: write 0 to enable protection.
#           "All protect" = all WRP fields cleared (active_low) or set
#           (active_high) to the all-protect value.
#  - edge : L4/G0/G4.  Fields are STRT/END pairs (WRP1A_STRT, WRP1A_END, ...).
#           "All protect" = STRT=0 (lowest page), END=max (covers entire flash).
#  - none : no WRP support -> RuntimeError.
#
# Dispatch matrix:
#  - bit + halfword        : F0/F1/F3 (OB memory halfword writes)
#  - bit + optcr32         : F2/F4/F7 (32-bit RMW on OPTCR)
#  - bit + ob_register_word: H7/L0   (32-bit RMW on WPSN register + OPTSTART)
#  - edge + ob_register_word: L4/G0/G4 (32-bit RMW on WRP area registers)
#
# CRITICAL (halfword mode): erasing OB wipes the entire OB block (RDP + WRP +
# USER).  Writing WRP therefore requires: read current RDP -> erase OB ->
# rewrite RDP at its original value + new WRP.  Otherwise RDP would become
# 0xFF (=L1) as a side effect.
#
# optcr32 / ob_register_word modes don't share this hazard: WRP and RDP are
# independent bit fields in a register, modified in-place.
# ---------------------------------------------------------------------------
def set_wrp(device_id: str, backend: ObBackend, protect_all: bool = True) -> str:
    """Enable or disable write protection (WRP).

    ``protect_all=True``  -- protect every flash sector ("full WRP").
    ``protect_all=False`` -- clear all WRP (no sector protected).

    Dispatches by ``profile.wrp.model`` (bit/edge/none) and the procedure's
    programming model (halfword/optcr32/ob_register_word).

    Returns the OBL application status (same vocabulary as
    :func:`set_rdp_level`).
    """
    profile = load_profile(device_id)
    wrp = profile.wrp
    if not wrp.present or wrp.model == "none":
        raise RuntimeError(f"{device_id}: WRP not supported (present={wrp.present}, model={wrp.model!r})")

    mode = profile.procedure.ob_programming
    if wrp.model == "bit":
        if mode == "halfword":
            _set_wrp_bit_halfword(profile, backend, protect_all)
        elif mode == "optcr32":
            _set_wrp_bit_optcr32(profile, backend, protect_all)
        elif mode == "ob_register_word":
            _set_wrp_bit_ob_register(profile, backend, protect_all)
        else:
            raise NotImplementedError(f"bit-mode WRP not implemented for ob_programming={mode!r}")
    elif wrp.model == "edge":
        if mode == "ob_register_word":
            _set_wrp_edge_ob_register(profile, backend, protect_all)
        else:
            raise NotImplementedError(f"edge-mode WRP not implemented for ob_programming={mode!r}")
    else:
        raise NotImplementedError(f"WRP model {wrp.model!r} not implemented")

    return _apply_obl(profile, backend)


def read_wrp_status(device_id: str, backend: ObBackend) -> str:
    """Read the current WRP status.

    Returns one of ``"全片写保护"`` (all sectors write-protected),
    ``"无写保护"`` (no sectors protected), or ``"部分写保护"`` (mixed).

    Dispatches by ``profile.wrp.model``:
      - bit  : read each WRP field; active_low=True -> 0x00=protected, 0xFF=unprotected.
      - edge : read STRT/END pairs; STRT=0&END=max=protected, STRT>END=unprotected.
    """
    profile = load_profile(device_id)
    wrp = profile.wrp
    if not wrp.present or wrp.model == "none":
        raise RuntimeError(f"{device_id}: WRP not supported (present={wrp.present}, model={wrp.model!r})")

    if wrp.model == "bit":
        return _read_wrp_status_bit(profile, backend)
    if wrp.model == "edge":
        return _read_wrp_status_edge(profile, backend)
    raise NotImplementedError(f"WRP model {wrp.model!r} not implemented for read")


# ---------------------------------------------------------------------------
# WRP status constants (returned by read_wrp_status)
# ---------------------------------------------------------------------------
WRP_STATUS_ALL = "全片写保护"
WRP_STATUS_NONE = "无写保护"
WRP_STATUS_PARTIAL = "部分写保护"


def _read_wrp_status_bit(profile: ObProfile, backend: ObBackend) -> str:
    """Read WRP bit fields and determine all/partial/none protection.

    For each field (active_low convention):
      - active_low=True : field==0     -> all-protect, field==max -> unprotected
      - active_low=False: field==max   -> all-protect, field==0   -> unprotected

    Overall: all fields all-protect -> "全片写保护"; all fields unprotected -> "无写保护"; mixed -> "部分写保护".
    """
    wrp = profile.wrp
    addr_to_raw: dict[int, int] = {}
    for field in wrp.fields:
        if field.address not in addr_to_raw:
            addr_to_raw[field.address] = backend.mem_read32(field.address)

    all_protect = 0
    none_protect = 0
    total = 0
    for field in wrp.fields:
        raw = addr_to_raw[field.address]
        mask = (1 << field.bit_width) - 1
        field_val = (raw >> field.bit_offset) & mask
        total += 1
        if field.active_low:
            if field_val == 0:
                all_protect += 1
            elif field_val == mask:
                none_protect += 1
        else:
            if field_val == mask:
                all_protect += 1
            elif field_val == 0:
                none_protect += 1

    if total and all_protect == total:
        return WRP_STATUS_ALL
    if total and none_protect == total:
        return WRP_STATUS_NONE
    return WRP_STATUS_PARTIAL


def _read_wrp_status_edge(profile: ObProfile, backend: ObBackend) -> str:
    """Read WRP edge-mode STRT/END pairs.

    For each STRT/END pair (grouped by address):
      - STRT=0 & END=max       -> all-protect (covers entire flash)
      - STRT>END               -> not protected (empty range)
      - otherwise              -> partial

    Overall: all areas all-protect -> "全片写保护"; all areas not-protected -> "无写保护"; mixed -> "部分写保护".
    """
    wrp = profile.wrp

    addr_to_fields: dict[int, dict[str, WrpField]] = {}
    for field in wrp.fields:
        name_up = field.name.upper()
        if "STRT" in name_up:
            key = "STRT"
        elif "END" in name_up:
            key = "END"
        else:
            continue
        addr_to_fields.setdefault(field.address, {})[key] = field

    if not addr_to_fields:
        raise RuntimeError(f"{profile.device_id}: no STRT/END fields in WRP profile (cannot read edge-mode WRP)")

    all_protect = 0
    none_protect = 0
    total = 0
    for addr, fields in addr_to_fields.items():
        strt_field = fields.get("STRT")
        end_field = fields.get("END")
        if strt_field is None or end_field is None:
            continue
        raw = backend.mem_read32(addr)
        strt_mask = (1 << strt_field.bit_width) - 1
        end_mask = (1 << end_field.bit_width) - 1
        strt_val = (raw >> strt_field.bit_offset) & strt_mask
        end_val = (raw >> end_field.bit_offset) & end_mask
        total += 1
        if strt_val == 0 and end_val == end_mask:
            all_protect += 1
        elif strt_val > end_val:
            none_protect += 1

    if total and all_protect == total:
        return WRP_STATUS_ALL
    if total and none_protect == total:
        return WRP_STATUS_NONE
    return WRP_STATUS_PARTIAL


def _wrp_field_byte(field: WrpField, protect_all: bool) -> int:
    """Return the WRP byte value to program for this field.

    active_low=True : protect_all=True  -> 0x00 (all sectors protected)
                     protect_all=False -> 0xFF (no sectors protected)
    active_low=False: protect_all=True  -> 0xFF (all sectors protected)
                     protect_all=False -> 0x00 (no sectors protected)
    """
    if field.active_low:
        return 0x00 if protect_all else 0xFF
    return 0xFF if protect_all else 0x00


def _wrp_all_protect_byte(field: WrpField) -> int:
    """Return the WRP byte value that means "all-protect" for this field.

    active_low=True : 0x00 (all bits cleared = all sectors protected)
    active_low=False: 0xFF (all bits set = all sectors protected)
    """
    return _wrp_field_byte(field, protect_all=True)


def _wrp_halfword_value(field: WrpField, protect_all: bool = True) -> int:
    """Build the 16-bit OB halfword for a WRP byte (value + complement).

    STM32 OB halfword layout: low byte = value, high byte = complement.
    The hardware rejects writes where value != ~complement.
    """
    val = _wrp_field_byte(field, protect_all)
    return val | ((~val & 0xFF) << 8)


def _wrp_halfword_addr(field: WrpField) -> int:
    """Compute the 16-bit halfword address for a WRP field.

    The JSON schema uses bit_offset within a 32-bit aligned read.  Fields at
    bit_offset 0-15 live in the halfword at ``field.address``; fields at
    bit_offset 16-31 live in the halfword at ``field.address + 2``.
    """
    return field.address + (field.bit_offset // 16) * 2


# ---------------------------------------------------------------------------
# bit + halfword (F0/F1/F3) -- full WRP via 16-bit OB writes
# ---------------------------------------------------------------------------
def _set_wrp_bit_halfword(profile: ObProfile, backend: ObBackend, protect_all: bool = True) -> None:
    """Enable/disable full WRP on F0/F1/F3 by writing each WRP halfword.

    ``protect_all=True``  -> each WRP byte = all-protect (0x00 for active_low).
    ``protect_all=False`` -> each WRP byte = no-protect   (0xFF for active_low).

    CRITICAL: erasing the OB block resets RDP to 0xFF (=L1).  We must read
    RDP first, erase OB, then write RDP back at its original value alongside
    the new WRP halfword(s).  Otherwise RDP would become 0xFF
    (L1) as a side effect.

    Sequence mirrors :func:`_set_rdp_halfword` (verified on F030 + F103):
      - unlock KEYR + OPTKEYR
      - erase OB: two CR writes (OPTER|OPTWRE, then OPTER|STRT|OPTWRE)
      - re-unlock (OPT_LOCK re-engages after erase)
      - per WRP field: OPTPG|OPTWRE -> write16(hw_addr, halfword) -> wait -> check
      - rewrite RDP: OPTPG|OPTWRE -> write16(rdp_addr, original RDP halfword) -> wait
      - lock
    """
    wrp = profile.wrp
    cr = profile.flash_regs.cr
    rdp_view = profile.rdp_write_view

    LOCK = _bit(profile.cr_bits, "LOCK")
    STRT = _bit(profile.cr_bits, "STRT")
    OPTER = _bit(profile.cr_bits, "OPTER")
    OPTPG = _bit(profile.cr_bits, "OPTPG")
    OPTWRE = _bit(profile.cr_bits, "OPTWRE")

    # ---- 1. snapshot current RDP before any OB erase ----
    # The OB block stores RDP + WRP0..3 + USER as 16-bit halfwords.  Erasing
    # OB wipes them all; we restore RDP below.
    if rdp_view is None:
        raise RuntimeError(f"{profile.device_id}: cannot preserve RDP during WRP write -- no RDP write-view")
    rdp_read_view = profile.rdp_read_view or rdp_view
    if rdp_read_view.bit_width == 1:
        # F1 boolean read-view (OBR bit1) -- cannot recover the raw OB byte
        # value (0xA5/0xBB/0xCC) from OBR.  Best-effort: assume L0 (0xA5) for
        # F1, since the only legitimate caller is auto-add-wrp right after
        # auto-unlock-rdp has set L0.  A chip under L1 wouldn't accept OB
        # writes anyway (PGSERR fires).
        rdp_byte = 0xA5  # F1 L0
    else:
        raw_ob = backend.mem_read32(rdp_read_view.address)
        mask_rdp = (1 << rdp_read_view.bit_width) - 1
        rdp_byte = (raw_ob >> rdp_read_view.bit_offset) & mask_rdp
    # halfword layout: low byte = value, high byte = complement
    rdp_halfword = rdp_byte | ((~rdp_byte & 0xFF) << 8)

    # ---- 2. unlock + erase OB ----
    _unlock_flash(profile, backend)
    _wait_bsy_sr(profile, backend)

    backend.mem_write32(cr, OPTER | OPTWRE)
    backend.mem_write32(cr, OPTER | STRT | OPTWRE)
    _wait_bsy_sr(profile, backend)

    # ---- 3. re-unlock (OPT_LOCK re-engages after erase) ----
    _unlock_flash(profile, backend)

    # ---- 4. write each WRP field = protect/no-protect halfword ----
    backend.mem_write32(cr, OPTPG | OPTWRE)
    for field in wrp.fields:
        hw_addr = _wrp_halfword_addr(field)
        hw_val = _wrp_halfword_value(field, protect_all)
        backend.mem_write16(hw_addr, hw_val)
        _wait_bsy_sr(profile, backend)
        _check_sr_errors(profile, backend)

    # ---- 5. rewrite RDP at its original value (preserve RDP across OB erase) ----
    backend.mem_write32(cr, OPTPG | OPTWRE)
    backend.mem_write16(rdp_view.address, rdp_halfword)
    _wait_bsy_sr(profile, backend)
    _check_sr_errors(profile, backend)

    # ---- 6. lock ----
    backend.mem_write32(cr, LOCK)


# ---------------------------------------------------------------------------
# bit + optcr32 (F2/F4/F7) -- full WRP via OPTCR nWRP bit clear
# ---------------------------------------------------------------------------
def _set_wrp_bit_optcr32(profile: ObProfile, backend: ObBackend, protect_all: bool = True) -> None:
    """Enable/disable full WRP on F2/F4/F7 by modifying nWRP bits in OPTCR.

    F4 OPTCR layout: nWRP at bit_offset=16 (one bit per 16KB sector,
    active_low: 0 = protected).  ``protect_all=True`` clears all nWRP bits;
    ``protect_all=False`` sets them all.  RDP lives in a different OPTCR bit
    field -- untouched by this operation.

    For chips with two OPTCR registers (e.g. F42x/F43x OPTCR + OPTCR1),
    each WRP field's ``address`` identifies which register to modify.
    OPTSTRT/OPTLOCK are always in the primary OPTCR (profile.flash_regs.optcr).
    """
    wrp = profile.wrp
    optcr = profile.flash_regs.optcr
    if optcr is None:
        raise RuntimeError(f"{profile.device_id}: OPTCR missing -- cannot use optcr32 WRP path")

    OPTLOCK = _bit(profile.optcr_bits, "OPTLOCK")
    OPTSTRT = _bit(profile.optcr_bits, "OPTSTRT")

    _unlock_flash(profile, backend)
    _wait_bsy_sr(profile, backend)

    # Group fields by address; each address gets one RMW.  OPTSTRT/OPTLOCK
    # are only in the primary OPTCR (profile.flash_regs.optcr).
    addrs = []
    for addr in dict.fromkeys(f.address for f in wrp.fields):  # unique, order-preserving
        val = backend.mem_read32(addr)
        for field in wrp.fields:
            if field.address != addr:
                continue
            mask = ((1 << field.bit_width) - 1) << field.bit_offset
            # active_low=True & protect_all=True  -> clear bits (0=protected)
            # active_low=True & protect_all=False -> set bits   (1=unprotected)
            # active_low=False & protect_all=True  -> set bits   (1=protected)
            # active_low=False & protect_all=False -> clear bits (0=unprotected)
            protect = (field.active_low == protect_all)
            if protect:
                val &= ~mask   # clear bits -> protected (active_low) or unprotected (active_high)
            else:
                val |= mask    # set bits   -> unprotected (active_low) or protected (active_high)
        backend.mem_write32(addr, val)
        addrs.append((addr, val))

    # Trigger via OPTSTRT in primary OPTCR; lock with OPTLOCK.
    # The primary OPTCR is profile.flash_regs.optcr -- find its modified value.
    optcr_val = next((v for a, v in addrs if a == optcr), backend.mem_read32(optcr))
    backend.mem_write32(optcr, optcr_val | OPTSTRT)
    _wait_bsy_sr(profile, backend)
    _check_sr_errors(profile, backend)
    backend.mem_write32(optcr, optcr_val | OPTLOCK)


# ---------------------------------------------------------------------------
# bit + ob_register_word (H7/L0) -- full WRP via WPSN register RMW
# ---------------------------------------------------------------------------
def _set_wrp_bit_ob_register(profile: ObProfile, backend: ObBackend, protect_all: bool = True) -> None:
    """Enable/disable full WRP on H7/L0 by modifying WRP bits in a FLASH register.

    H7 uses WPSN_PRG registers (Write Protection Sector Number) at FLASH
    addresses -- these are real registers that can be read-modified-written
    after unlock.  Trigger via OPTCR.OPTSTART; poll OPTSR_CUR.OPT_BUSY.

    ``protect_all=True``  -> clear/set bits to all-protect (per active_low).
    ``protect_all=False`` -> set/clear bits to no-protect (per active_low).

    L0 has fields at OB memory addresses -- this path may not work correctly
    for L0 (data may need fixing).  The code attempts a 32-bit RMW on the
    field address regardless.
    """
    wrp = profile.wrp

    _unlock_flash(profile, backend)
    _wait_bsy_sr(profile, backend)

    if profile.procedure.obl_trigger == "optcr_optstart":
        # H7: 32-bit RMW on WPSN register, trigger via OPTCR.OPTSTART
        optcr = profile.flash_regs.optcr
        if optcr is None:
            raise RuntimeError(f"{profile.device_id}: H7 WRP needs OPTCR (missing in profile)")
        for addr in dict.fromkeys(f.address for f in wrp.fields):
            val = backend.mem_read32(addr)
            for field in wrp.fields:
                if field.address != addr:
                    continue
                mask = ((1 << field.bit_width) - 1) << field.bit_offset
                protect = (field.active_low == protect_all)
                if protect:
                    val &= ~mask
                else:
                    val |= mask
            backend.mem_write32(addr, val)
        OPTSTART = _bit(profile.optcr_bits, "OPTSTART")
        OPTLOCK = _bit(profile.optcr_bits, "OPTLOCK")
        backend.mem_write32(optcr, OPTSTART)
        _wait_bsy_optsr(profile, backend)
        backend.mem_write32(optcr, OPTLOCK)
    else:
        # L4/G0-style: 32-bit RMW on the OB register, trigger via CR.OPTSTRT
        OPTSTRT = _bit(profile.cr_bits, "OPTSTRT")
        LOCK = _bit(profile.cr_bits, "LOCK")
        for addr in dict.fromkeys(f.address for f in wrp.fields):
            val = backend.mem_read32(addr)
            for field in wrp.fields:
                if field.address != addr:
                    continue
                mask = ((1 << field.bit_width) - 1) << field.bit_offset
                protect = (field.active_low == protect_all)
                if protect:
                    val &= ~mask
                else:
                    val |= mask
            backend.mem_write32(addr, val)
        backend.mem_write32(profile.flash_regs.cr, OPTSTRT)
        _wait_bsy_sr(profile, backend)
        _check_sr_errors(profile, backend)
        backend.mem_write32(profile.flash_regs.cr, LOCK)


# ---------------------------------------------------------------------------
# edge + ob_register_word (L4/G0/G4) -- full WRP via STRT/END
# ---------------------------------------------------------------------------
def _set_wrp_edge_ob_register(profile: ObProfile, backend: ObBackend, protect_all: bool = True) -> None:
    """Enable/disable full WRP on L4/G0/G4 by setting STRT/END page values.

    ``protect_all=True``  -> STRT=0 (lowest page),  END=max (highest page) -> full coverage.
    ``protect_all=False`` -> STRT=max (highest page), END=0 (lowest page) -> empty range (STRT>END).

    For each STRT/END pair found in ``wrp.fields`` (grouped by address): set
    STRT and END to their protect/no-protect values so the protected range
    covers the entire flash (or no flash when clearing).

    L4-style: write the WRP area register directly, trigger via CR.OPTSTRT.
    H7-style: write OPTSR_PRG, trigger via OPTCR.OPTSTART.
    """
    wrp = profile.wrp
    # Group STRT/END fields by their address (each WRP area register holds
    # both STRT and END bits for one area).
    addr_to_changes: dict[int, list[tuple[int, int]]] = {}
    for field in wrp.fields:
        name_up = field.name.upper()
        max_val = (1 << field.bit_width) - 1
        if "STRT" in name_up:
            val = 0 if protect_all else max_val   # protect: lowest page; clear: highest page
        elif "END" in name_up:
            val = max_val if protect_all else 0   # protect: highest page; clear: lowest page
        else:
            continue   # unknown field -- skip
        mask = ((1 << field.bit_width) - 1) << field.bit_offset
        shifted = (val << field.bit_offset) & mask
        addr_to_changes.setdefault(field.address, []).append((mask, shifted))

    if not addr_to_changes:
        raise RuntimeError(f"{profile.device_id}: no STRT/END fields in WRP profile (cannot apply edge-mode WRP)")

    _unlock_flash(profile, backend)
    _wait_bsy_sr(profile, backend)

    if profile.procedure.obl_trigger == "optcr_optstart":
        # H7: write OPTSR_PRG, trigger via OPTCR.OPTSTART, poll OPTSR_CUR.OPT_BUSY
        optcr = profile.flash_regs.optcr
        if optcr is None:
            raise RuntimeError(f"{profile.device_id}: H7 WRP needs OPTCR (missing in profile)")
        for addr, changes in addr_to_changes.items():
            cur = backend.mem_read32(addr)
            for mask, shifted in changes:
                cur = (cur & ~mask) | shifted
            backend.mem_write32(addr, cur)
        OPTSTART = _bit(profile.optcr_bits, "OPTSTART")
        OPTLOCK = _bit(profile.optcr_bits, "OPTLOCK")
        backend.mem_write32(optcr, OPTSTART)
        _wait_bsy_optsr(profile, backend)
        backend.mem_write32(optcr, OPTLOCK)
    else:
        # L4/G0-style: write the WRP area register(s), trigger via CR.OPTSTRT.
        OPTSTRT = _bit(profile.cr_bits, "OPTSTRT")
        LOCK = _bit(profile.cr_bits, "LOCK")
        for addr, changes in addr_to_changes.items():
            cur = backend.mem_read32(addr)
            for mask, shifted in changes:
                cur = (cur & ~mask) | shifted
            backend.mem_write32(addr, cur)
        backend.mem_write32(profile.flash_regs.cr, OPTSTRT)
        _wait_bsy_sr(profile, backend)
        _check_sr_errors(profile, backend)
        backend.mem_write32(profile.flash_regs.cr, LOCK)
