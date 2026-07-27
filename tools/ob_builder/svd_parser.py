"""Layer 2: STM32 SVD FLASH register extraction.

Parses ST's SVD files (shipped with CubeProgrammer) to obtain FLASH peripheral
register addresses and bit offsets -- the register-level addressing that the
option-byte procedure needs.  Zero hardcoded addresses; everything comes from
SVD.

Cross-family bit differences verified by ``scratch/agent3_findings.md``:
  - LOCK: F0/F1=7, F4/L4=31, H7=0
  - BSY:  F0/F1=0, F4/L4=16, H7=0
  - OPTER/OPTPG/OPTWRE: only F0/F1 (halfword model)
  - OBL_LAUNCH: only L4 (CR bit 27)
  - OPTSTRT (F4) vs OPTSTART (H7): both bit 1 of OPTCR
  - L4 register offsets differ from F0/F1 despite same base 0x40022000
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# pyOCD bundles an SVD parser; reuse it rather than rolling our own.
from pyocd.debug.svd.parser import SVDParser

_DEFAULT_SVD_DIR = Path(
    r"C:/DevTools/STMicroelectronics/STM32Cube/STM32CubeProgrammer/SVD"
)

# Register name aliases: canonical name -> acceptable SVD names (first match wins).
# Handles H7 bank1 suffixes, F0/F1 legacy names, and new-family NS/SEC + FLASH_ prefix variants.
#
# New families (C0/U0/U3/U5/WBA/H5) prefix all FLASH register names with "FLASH_"
# (e.g. "FLASH_KEYR", "FLASH_CR").  The prefix is stripped by _normalize_reg_name
# before alias lookup, so aliases use the bare name.
#
# TrustZone families (U5/WBA/H5) split registers into NS (non-secure) and SEC
# variants: NSKEYR/NSSR/NSCR replace KEYR/SR/CR.  We map the NS variant to the
# canonical name (the runtime always operates in non-secure mode via SWD).
#
# L1 uses a different flash controller: PECR replaces CR, PEKEYR replaces KEYR.
# L1 profiles still can't be built (PROG/ERASE bits don't map to OPTPG/OPTER),
# but including these aliases makes the SVD parse populate cr_bits for diagnostics.
_REG_ALIASES: dict[str, tuple[str, ...]] = {
    "KEYR":       ("KEYR", "KEYR1", "NSKEYR", "PEKEYR"),
    "OPTKEYR":    ("OPTKEYR",),
    "CR":         ("CR", "CR1", "NSCR", "NSCR1", "PECR"),
    "SR":         ("SR", "SR1", "NSSR"),
    "OBR":        ("OBR",),
    "OPTCR":      ("OPTCR",),
    "OPTR":       ("OPTR",),
    "OPTSR_CUR":  ("OPTSR_CUR",),
    "OPTSR_PRG":  ("OPTSR_PRG",),
    "OPTCCR":     ("OPTCCR", "OPTCCR1"),
}

# Bit name aliases: canonical -> acceptable SVD names.
_BIT_ALIASES: dict[str, tuple[str, ...]] = {
    "PGSERR":       ("PGSERR", "PGERR"),
    "WRPERR":       ("WRPERR", "WRPRTERR", "WRPRT"),
    "OPT_BUSY":     ("OPT_BUSY",),
    "OPTCHANGEERR": ("OPTCHANGEERR",),
    # C0 uses BSY1 instead of BSY (same bit, different name across families)
    "BSY":          ("BSY", "BSY1"),
}


def find_svd_dir() -> Path:
    """Locate the CubeProgrammer ``SVD`` directory."""
    env = os.environ.get("STM32CUBEPROG_SVD_DIR")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    if _DEFAULT_SVD_DIR.is_dir():
        return _DEFAULT_SVD_DIR
    raise FileNotFoundError(
        f"STM32CubeProgrammer SVD directory not found at {_DEFAULT_SVD_DIR}; "
        f"set STM32CUBEPROG_SVD_DIR to point at it"
    )


@dataclass
class RegInfo:
    address: int
    bits: dict[str, tuple[int, int]] = field(default_factory=dict)  # name -> (offset, width)


@dataclass
class FlashPeripheral:
    base_addr: int
    registers: dict[str, RegInfo] = field(default_factory=dict)  # canonical name -> RegInfo


@dataclass
class FlashRegs:
    """FLASH peripheral registers + bit offsets needed by the OB procedure."""

    base_addr: int
    keyr_addr: int
    optkeyr_addr: int
    cr_addr: int
    sr_addr: int
    # optional per-family registers
    obr_addr: int | None = None           # F0, F1
    optcr_addr: int | None = None         # F4, H7
    optr_addr: int | None = None          # L4, G0, G4, WB, WL
    optsr_cur_addr: int | None = None     # H7
    optsr_prg_addr: int | None = None     # H7
    optccr_addr: int | None = None        # H7
    # bit offset dicts (canonical bit name -> offset; all 1-bit unless noted)
    cr_bits: dict[str, int] = field(default_factory=dict)
    optcr_bits: dict[str, int] = field(default_factory=dict)
    sr_bits: dict[str, int] = field(default_factory=dict)
    optsr_cur_bits: dict[str, int] = field(default_factory=dict)


def _canonical_bit(name: str, aliases: dict[str, tuple[str, ...]]) -> str | None:
    """Return canonical name for an SVD bit name, or None if not relevant."""
    upper = name.upper()
    for canon, names in aliases.items():
        if upper in names:
            return canon
    return None


def _normalize_reg_name(name: str) -> str:
    """Strip family-specific prefix from a register name for alias matching.

    New families (C0/U0/U3/U5/WBA/H5/N6) prefix all FLASH register names with
    "FLASH_" (e.g. "FLASH_KEYR", "FLASH_CR", "FLASH_OPTR").  Legacy families
    (F0/F1/F4/L4/H7/WB55/WL5x) use bare names.  Stripping the prefix unifies
    both schemas before _REG_ALIASES lookup.
    """
    upper = name.upper()
    if upper.startswith("FLASH_"):
        return upper[6:]  # len("FLASH_") == 6
    return upper


# Registers whose bits are consumed by the runtime (ops.py).  For these, we
# strip the "NS" prefix from bit names so that TrustZone families (L5/U5/WBA/H5)
# with NSCR/NSSR registers produce the same bit names as legacy families.
# Without this, L5 would have "NSLOCK" where the runtime expects "LOCK", etc.
_NS_STRIP_REGISTERS = frozenset({"CR", "SR", "OPTCR", "OPTSR_CUR", "OPTSR_PRG"})


def _normalize_bit_name(name: str, canonical_reg: str) -> str:
    """Normalize a bit field name for the given canonical register.

    For TrustZone families (L5/U5/WBA/H5), the NS-variant registers (NSCR, NSSR)
    prefix all bit names with "NS" (e.g. NSPG, NSLOCK, NSBSY, NSPGSERR).  The
    runtime (ops.py) expects bare names (PG, LOCK, BSY, PGSERR).  Strip the "NS"
    prefix for registers in _NS_STRIP_REGISTERS so the bit names match.

    For other registers (OPTR, OBR, KEYR, etc.) bit names are kept as-is -- the
    runtime doesn't use bits from those registers, so normalization is unnecessary
    and could mangle names like "nSWBOOT0".
    """
    upper = name.upper()
    if canonical_reg in _NS_STRIP_REGISTERS and upper.startswith("NS"):
        return upper[2:]  # len("NS") == 2
    return upper


def _parse_svd_raw(svd_path: Path) -> FlashPeripheral:
    """Parse an SVD file and extract the FLASH peripheral (first match).

    Matches any peripheral whose name contains "FLASH" (case-insensitive).  This
    covers "Flash" (L1/WB55), "FLASH" (F0/F1/F4/G0/G4/H7), and "FLASH_CTRL"
    (WB0x/WL33 new architecture).  The caller (_parse_svd_raw) is used both for
    profiling (find_svd_for_family) and for the real parse; FLASH_CTRL peripherals
    won't have KEYR/CR so they naturally fail to produce a usable FlashRegs.
    """
    svd = SVDParser.for_xml_file(str(svd_path)).get_device()
    for periph in svd.peripherals:
        pname = (periph.name or "").upper()
        if "FLASH" not in pname:
            continue
        base = periph.base_address or 0
        out = FlashPeripheral(base_addr=base)
        # build name -> register map (normalized: strip FLASH_ prefix for lookup)
        reg_by_name: dict[str, any] = {}
        for reg in periph.registers or []:
            if reg.name:
                reg_by_name[_normalize_reg_name(reg.name)] = reg
        for canon, aliases in _REG_ALIASES.items():
            reg = next((reg_by_name[a] for a in aliases if a in reg_by_name), None)
            if reg is None:
                continue
            addr = base + (reg.address_offset or 0)
            ri = RegInfo(address=addr)
            for field in reg.fields or []:
                fname = _normalize_bit_name(field.name or "", canon)
                canon_bit = _canonical_bit(fname, _BIT_ALIASES)
                if canon_bit:
                    ri.bits[canon_bit] = (field.bit_offset or 0, field.bit_width or 1)
                else:
                    # keep raw name for bits we care about (LOCK, STRT, etc.)
                    ri.bits[fname] = (field.bit_offset or 0, field.bit_width or 1)
            out.registers[canon] = ri
        return out
    raise ValueError(f"no FLASH peripheral in {svd_path}")


def _reg_addr(fp: FlashPeripheral, canon: str) -> int | None:
    ri = fp.registers.get(canon)
    return ri.address if ri else None


def _bit_offsets(fp: FlashPeripheral, canon_reg: str) -> dict[str, int]:
    ri = fp.registers.get(canon_reg)
    if ri is None:
        return {}
    return {name: off for name, (off, _w) in ri.bits.items()}


@lru_cache(maxsize=16)
def parse_flash_peripheral(svd_filename: str) -> FlashRegs:
    """Parse a family's SVD file and return the FLASH registers needed for OB.

    Cached by filename.  ``svd_filename`` is relative to the CubeProgrammer SVD
    directory, e.g. ``"STM32F103.svd"``.
    """
    path = find_svd_dir() / svd_filename
    if not path.exists():
        raise FileNotFoundError(f"SVD file not found: {path}")
    fp = _parse_svd_raw(path)
    return FlashRegs(
        base_addr=fp.base_addr,
        keyr_addr=_reg_addr(fp, "KEYR"),
        optkeyr_addr=_reg_addr(fp, "OPTKEYR"),
        cr_addr=_reg_addr(fp, "CR"),
        sr_addr=_reg_addr(fp, "SR"),
        obr_addr=_reg_addr(fp, "OBR"),
        optcr_addr=_reg_addr(fp, "OPTCR"),
        optr_addr=_reg_addr(fp, "OPTR"),
        optsr_cur_addr=_reg_addr(fp, "OPTSR_CUR"),
        optsr_prg_addr=_reg_addr(fp, "OPTSR_PRG"),
        optccr_addr=_reg_addr(fp, "OPTCCR"),
        cr_bits=_bit_offsets(fp, "CR"),
        optcr_bits=_bit_offsets(fp, "OPTCR"),
        sr_bits=_bit_offsets(fp, "SR"),
        optsr_cur_bits=_bit_offsets(fp, "OPTSR_CUR"),
    )

# ---------------------------------------------------------------------------
# Derivation: programming model + OBL trigger from SVD structure
# ---------------------------------------------------------------------------
def derive_programming_model(regs: FlashRegs) -> str:
    """Derive the OB programming model from SVD register/bit structure.

    - "halfword":         CR has OPTER/OPTPG/OPTWRE bits (F0/F1/F3/L0/L1)
    - "optcr32":          OPTCR register with inline OB bits, no separate OPTR/OPTSR_PRG (F2/F4/F7)
    - "ob_register_word": separate OPTR or OPTSR_PRG register (L4/L5/G0/G4/H7/WB/WL/C0/U5)
    """
    if "OPTER" in regs.cr_bits and "OPTPG" in regs.cr_bits:
        return "halfword"
    if regs.optsr_prg_addr is not None or regs.optr_addr is not None:
        return "ob_register_word"
    if regs.optcr_addr is not None:
        return "optcr32"
    raise ValueError(
        f"cannot derive programming model from SVD "
        f"(cr_bits={sorted(regs.cr_bits)}, optcr={regs.optcr_addr}, optr={regs.optr_addr})"
    )


def derive_obl_trigger(regs: FlashRegs, no_bit_behavior: str) -> str:
    """Derive the OBL reload trigger from SVD bits; fall back to family behavior.

    - "cr_obl_launch":  CR has OBL_LAUNCH bit (F3/L4/L5/G0/G4/WB/WL/C0/U5)
    - "opt_start":      OPTCR has OPTSTRT bit (F2/F4/F7)
    - "optcr_optstart": OPTCR has OPTSTART bit (H7)
    - "reset" / "power_cycle": no OBL bit anywhere -> family silicon behavior
      (F0 <=64KB = power_cycle; F1/L0/L1 = reset)
    """
    if "OBL_LAUNCH" in regs.cr_bits:
        return "cr_obl_launch"
    if "OPTSTRT" in regs.optcr_bits:
        return "opt_start"
    if "OPTSTART" in regs.optcr_bits:
        return "optcr_optstart"
    if no_bit_behavior not in ("reset", "power_cycle"):
        raise ValueError(f"no_bit_behavior must be 'reset' or 'power_cycle', got {no_bit_behavior!r}")
    return no_bit_behavior


def find_svd_for_family(family: str) -> str:
    """Find a representative SVD file for a family by searching the SVD directory.

    The family key (e.g. ``"F1"``) maps directly to the ``STM32{family}`` prefix
    (``"STM32F1"``).  FLASH register layout is family-level stable for the
    standard flash controller, so any family member's SVD with a standard FLASH
    peripheral (KEYR + CR + SR) works for OB purposes.

    Some families have two flash architectures:
      - WB: standard FLASH (WB55/WB35) + new FLASH_CTRL (WB05/WB06/WB07/WB09)
      - WL: standard FLASH (WL5x/WLE5) + new FLASH_CTRL (WL33)
    The FLASH_CTRL architecture uses a command-based interface (COMMAND/CONFIG/
    PAGEPROT) that doesn't fit any of the 3 OB programming models.  We prefer
    SVD files with the standard FLASH peripheral by checking for KEYR+CR.

    Returns the first matching filename with a standard FLASH peripheral, or
    falls back to the first candidate if none qualify.
    """
    svd_dir = find_svd_dir()
    prefix = f"STM32{family}"
    candidates = sorted(svd_dir.glob(f"{prefix}*.svd"))
    if not candidates:
        raise FileNotFoundError(f"no SVD file matching {prefix}*.svd in {svd_dir}")
    # Prefer SVD files with standard FLASH peripheral (KEYR + CR after normalization)
    for c in candidates:
        try:
            fp = _parse_svd_raw(c)
            if "KEYR" in fp.registers and "CR" in fp.registers:
                return c.name
        except Exception:
            continue
    # Fallback: first candidate (may be FLASH_CTRL-only for new-arch families)
    return candidates[0].name

