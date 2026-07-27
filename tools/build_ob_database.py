"""Build-time script: convert ST CubeProgrammer XML + SVD + RM constants into a
unified per-chip JSON database for option bytes.

This is the ONLY place that parses multiple ST formats (XML + SVD).  The runtime
in ``src/core/option_bytes/`` reads only the generated JSON -- one format, one
parser, no CubeProgrammer / SVD dependency at runtime.

Data sources (all read here, nowhere else at runtime):
  - ST XML  ``STM32_Prog_DB_{DeviceID}.xml``  -> OB field layout + enum values
  - ST SVD  ``STM32{family}*.svd``            -> FLASH register addresses + bit offsets
  - RM constants (family table below)         -> OPTKEY magic + OBL behavior

Output: ``data/ob_profiles/{device_id}.json`` per chip (self-contained: everything
``ops.py`` needs to read/write RDP for that chip).

Usage:
    venv\\Scripts\\python.exe tools\\build_ob_database.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Build-time parsers (moved out of runtime src/ -- runtime only reads JSON)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ob_builder import family_constants as family_procedures  # noqa: E402
from ob_builder import xml_parser as st_xml_db  # noqa: E402
from ob_builder import svd_parser as svd_db  # noqa: E402


# ---------------------------------------------------------------------------
# Family RM constants (the IRREDUCIBLE knowledge -- not in XML/SVD/pack).
# Encoded ONCE here; baked into every chip's JSON at build time.
# ---------------------------------------------------------------------------
# Same 5 fields as the old runtime family_procedures.py, but build-time only.
_RM_FAMILY = {
    "F0": dict(optkey_pattern="legacy", no_obl_bit_trigger="power_cycle", verified=True),
    "F1": dict(optkey_pattern="legacy", no_obl_bit_trigger="reset", verified=True),
    "F4": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=True),
    "L4": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=True),
    "H7": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=True),
    "F2": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "F3": dict(optkey_pattern="legacy", no_obl_bit_trigger="reset", verified=False),
    "F7": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "L0": dict(optkey_pattern="legacy", no_obl_bit_trigger="reset", verified=False),
    "L1": dict(optkey_pattern="legacy", no_obl_bit_trigger="reset", verified=False),
    "L5": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "G0": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "G4": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "WB": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "WL": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "C0": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "U5": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "H5": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "U0": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "U3": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
    "WBA": dict(optkey_pattern="modern", no_obl_bit_trigger="reset", verified=False),
}

_FLASH_KEY = (0x45670123, 0xCDEF89AB)
_LEGACY_OPTKEY = (0x45670123, 0xCDEF89AB)
_MODERN_OPTKEY = (0x08192A3B, 0x4C5D6E7F)


def _optkey_seq(pattern: str) -> tuple[int, int]:
    return _LEGACY_OPTKEY if pattern == "legacy" else _MODERN_OPTKEY


def _hex(v: int | None) -> str | None:
    return f"0x{v:X}" if v is not None else None


def _detect_wrp_model(wrp_fields: list[dict]) -> str:
    """Classify WRP model from extracted fields.

    - "edge": any field name contains STRT or END (L4/H7 dual-bank style with
      start/end address-range endpoints).
    - "bit":  bit-style bitmap (F0/F1/F4/H7 WPSN -- each bit/halfword covers a
      sector group, typically active_low).
    - "none": no WRP fields extracted (no "Write Protection" category in XML,
      or category exists but no usable bits).
    """
    if not wrp_fields:
        return "none"
    for f in wrp_fields:
        n = f["name"].upper()
        if "STRT" in n or "END" in n:
            return "edge"
    return "bit"


def _build_one(device_id: str) -> tuple[dict | None, str]:
    """Build a unified profile dict for one chip.  Returns (profile, reason).

    profile is None when the chip can't be built (non-chip XML, no OB, unknown
    family, no SVD, etc.) -- reason explains why.
    """
    try:
        layout = st_xml_db.parse_ob_layout(device_id)
    except FileNotFoundError:
        return None, "XML not found"

    try:
        family = family_procedures.family_from_series(layout.series)
    except Exception as e:
        return None, f"family resolution failed: {e}"
    rm = _RM_FAMILY.get(family)
    if rm is None:
        return None, f"family {family} not in RM table"

    read_f, read_b, write_f, write_b = st_xml_db.find_rdp_views(layout)
    if write_b is None:
        return None, "no RDP write-view"

    # Reject chips whose RDP write-view address is a placeholder sentinel
    # (0xF9F9F9F9 etc.) indicating a non-standard flash architecture that
    # doesn't fit the register-based OB model.  WB0x (WB05/WB06/WB07/WB09) and
    # WL33 use a command-based FLASH_CTRL with a completely different RDP
    # mechanism (32-bit values 0xFF/0xAA/0xABACABAD, not 0xA5/0xBB/0xCC).
    # Valid RDP addresses are either OB memory (0x1FFFF000-0x1FFFFF00) or
    # peripheral registers (0x40000000-0x5FFFFFFF).
    rdp_addr = write_f.address if write_f and write_f.address else 0
    if rdp_addr > 0xF0000000:
        return None, f"RDP address 0x{rdp_addr:X} is placeholder (non-standard flash architecture)"

    # WRP (Write Protection) fields extracted from "Write Protection" category.
    wrp_fields = st_xml_db.find_wrp_fields(layout)
    wrp_model = _detect_wrp_model(wrp_fields)
    wrp_section = {
        "present": bool(wrp_fields),
        "model": wrp_model,
        "fields": wrp_fields,
    }

    # SVD: FLASH registers + bits
    try:
        svd_filename = svd_db.find_svd_for_family(family)
        regs = svd_db.parse_flash_peripheral(svd_filename)
        ob_programming = svd_db.derive_programming_model(regs)
        obl_trigger = svd_db.derive_obl_trigger(regs, rm["no_obl_bit_trigger"])
    except Exception as e:
        return None, f"SVD/derive failed: {e}"

    def _view(fld, bit):
        if fld is None or bit is None:
            return None
        return {
            "address": _hex(fld.address),
            "bit_offset": bit.offset,
            "bit_width": bit.width,
            "access": bit.access,
            "values": {f"0x{v.value:X}": v.label for v in bit.values} if bit.values else {},
        }

    return {
        "device_id": device_id,
        "device_name": layout.device_name,
        "family": family,
        "verified": rm["verified"],
        "rdp_read_view": _view(read_f, read_b),
        "rdp_write_view": _view(write_f, write_b),
        "wrp": wrp_section,
        "flash_regs": {
            "keyr": _hex(regs.keyr_addr),
            "optkeyr": _hex(regs.optkeyr_addr),
            "cr": _hex(regs.cr_addr),
            "sr": _hex(regs.sr_addr),
            "obr": _hex(regs.obr_addr),
            "optcr": _hex(regs.optcr_addr),
            "optr": _hex(regs.optr_addr),
            "optsr_cur": _hex(regs.optsr_cur_addr),
            "optsr_prg": _hex(regs.optsr_prg_addr),
            "optccr": _hex(regs.optccr_addr),
        },
        "bits": {
            "cr": dict(regs.cr_bits),
            "sr": dict(regs.sr_bits),
            "optcr": dict(regs.optcr_bits),
            "optsr_cur": dict(regs.optsr_cur_bits),
        },
        "procedure": {
            "ob_programming": ob_programming,
            "flash_key_seq": [f"0x{v:X}" for v in _FLASH_KEY],
            "optkey_seq": [f"0x{v:X}" for v in _optkey_seq(rm["optkey_pattern"])],
            "obl_trigger": obl_trigger,
            "obl_requires_power_cycle": rm["no_obl_bit_trigger"] == "power_cycle",
        },
    }, None


def main() -> int:
    db_dir = st_xml_db.find_data_base_dir()
    out_dir = Path(__file__).resolve().parents[1] / "data" / "ob_profiles"
    out_dir.mkdir(parents=True, exist_ok=True)

    xml_files = sorted(db_dir.glob("STM32_Prog_DB_*.xml"))
    print(f"found {len(xml_files)} ST XML files in {db_dir}")

    built = 0
    skipped: list[tuple[str, str]] = []
    for xf in xml_files:
        device_id = xf.stem.replace("STM32_Prog_DB_", "")  # e.g. "0x410"
        profile, reason = _build_one(device_id)
        if profile is None:
            skipped.append((device_id, reason))
            continue
        (out_dir / f"{device_id}.json").write_text(
            json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        built += 1

    print(f"built {built} JSON profiles -> {out_dir}")
    if skipped:
        print(f"skipped {len(skipped)}:")
        for did, why in skipped[:10]:
            print(f"  {did}: {why}")
        if len(skipped) > 10:
            print(f"  ... and {len(skipped) - 10} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
