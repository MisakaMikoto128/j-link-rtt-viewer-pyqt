"""Layer 1: ST CubeProgrammer option-bytes XML database.

Parses ST's authoritative per-chip OB database
(``STM32_Prog_DB_{DeviceID}.xml``, keyed by DBGMCU_IDCODE) to obtain OB field
layout, addresses, bit offsets, and enumerated values -- with zero chip-specific
hardcoding.

Schema (cross-family consistent, verified on 91 chips / 86 with OB):
    <Peripheral><Name>Option Bytes</Name>
      <Bank interface="JTAG_SWD|Bootloader">
        <Parameters address=.. size=../>
        <Category><Name>Read Out Protection</Name>
          <Field><Parameters address=.. name=.. size=../>
            <AssignedBits><Bit>
              <Name>RDP</Name><BitOffset>..</BitOffset><BitWidth>..</BitWidth>
              <Access>R|W|RW</Access>
              <Values><Val value="0xA5">Level 0</Val>...</Values>

ST models read-view (Access=R, live register like F1 OBR / H7 OPTSR_CUR) and
write-view (Access=W, physical OB byte or PRG register) as separate fields.
Both are captured here.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# CubeProgrammer install detection
# ---------------------------------------------------------------------------
_DEFAULT_DATA_BASE = Path(
    r"C:/DevTools/STMicroelectronics/STM32Cube/STM32CubeProgrammer/api/Data_Base"
)


def find_data_base_dir() -> Path:
    """Locate the CubeProgrammer ``Data_Base`` directory.

    Resolution order: ``$STM32CUBEPROG_DATA_BASE`` env var, then the default
    install path.  Raises ``FileNotFoundError`` if missing -- the OB feature
    requires CubeProgrammer's XML database on disk.
    """
    env = os.environ.get("STM32CUBEPROG_DATA_BASE")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    if _DEFAULT_DATA_BASE.is_dir():
        return _DEFAULT_DATA_BASE
    raise FileNotFoundError(
        f"STM32CubeProgrammer Data_Base not found at {_DEFAULT_DATA_BASE}; "
        f"set STM32CUBEPROG_DATA_BASE to point at it"
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class EnumVal:
    value_raw: str   # attribute string e.g. "0xA5"
    label: str

    @property
    def value(self) -> int:
        return int(self.value_raw, 16) if self.value_raw.lower().startswith("0x") else int(self.value_raw)


@dataclass
class Bit:
    name: str
    offset: int          # BitOffset (hex)
    width: int           # BitWidth
    access: str          # R / W / RW
    values: list[EnumVal] = field(default_factory=list)
    by_bit: bool = False


@dataclass
class Field:
    address: int | None
    name: str
    size: int | None
    bits: list[Bit] = field(default_factory=list)


@dataclass
class Category:
    name: str
    fields: list[Field] = field(default_factory=list)


@dataclass
class Bank:
    interface: str
    address: int | None
    size: int | None
    categories: list[Category] = field(default_factory=list)


@dataclass
class ObLayout:
    device_id: str
    device_name: str
    series: str
    cpu: str
    banks: list[Bank] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def _int(s: str | None) -> int | None:
    if s is None:
        return None
    return int(s, 16) if s.lower().startswith("0x") else int(s)


@lru_cache(maxsize=64)
def parse_ob_layout(device_id: str) -> ObLayout:
    """Parse ``STM32_Prog_DB_{device_id}.xml`` and return the OB layout.

    Cached by device_id.  ``device_id`` is the DBGMCU_IDCODE in hex form,
    e.g. ``"0x410"`` for STM32F103, ``"0x440"`` for STM32F030.
    """
    path = find_data_base_dir() / f"STM32_Prog_DB_{device_id}.xml"
    if not path.exists():
        raise FileNotFoundError(f"OB database not found for DeviceID {device_id}: {path}")
    root = ET.parse(path).getroot()
    dev = root.find("Device")
    if dev is None:
        raise ValueError(f"{device_id}: no <Device> in {path}")

    layout = ObLayout(
        device_id=device_id,
        device_name=(dev.findtext("Name") or "").strip(),
        series=(dev.findtext("Series") or "").strip(),
        cpu=(dev.findtext("CPU") or "").strip(),
    )

    for periph in dev.iter("Peripheral"):
        if (periph.findtext("Name") or "").strip() != "Option Bytes":
            continue
        # Some newer chips (U5/L5/WBA/V8) wrap <Bank> elements inside
        # <Configuration config="..."> elements (one per chip variant / TZEN
        # state).  Use iter("Bank") to find banks at any depth, covering both
        # the legacy layout (direct <Bank> children) and the new layout
        # (banks nested inside <Configuration>).  The dedup logic in
        # find_rdp_views / find_wrp_fields handles duplicate banks across
        # configurations (same bit name+offset -> one entry).
        for bank_el in periph.iter("Bank"):
            params = bank_el.find("Parameters")
            bank = Bank(
                interface=bank_el.get("interface", ""),
                address=_int(params.get("address")) if params is not None else None,
                size=_int(params.get("size")) if params is not None else None,
            )
            for cat_el in bank_el.findall("Category"):
                cat = Category(name=(cat_el.findtext("Name") or "").strip())
                for fld_el in cat_el.findall("Field"):
                    fp = fld_el.find("Parameters")
                    fld = Field(
                        address=_int(fp.get("address")) if fp is not None else None,
                        name=(fp.get("name") or "").strip() if fp is not None else "",
                        size=_int(fp.get("size")) if fp is not None else None,
                    )
                    for bit_el in fld_el.iter("Bit"):
                        vals_el = bit_el.find("Values")
                        by_bit = vals_el is not None and vals_el.get("ByBit", "").lower() == "true"
                        vals = [
                            EnumVal(v.get("value", ""), (v.text or "").strip())
                            for v in (vals_el.findall("Val") if vals_el is not None else [])
                        ]
                        fld.bits.append(Bit(
                            name=(bit_el.findtext("Name") or "").strip(),
                            offset=_int(bit_el.findtext("BitOffset")) or 0,
                            width=_int(bit_el.findtext("BitWidth")) or 0,
                            access=(bit_el.findtext("Access") or "").strip(),
                            values=vals,
                            by_bit=by_bit,
                        ))
                    cat.fields.append(fld)
                bank.categories.append(cat)
            layout.banks.append(bank)
    return layout


# ---------------------------------------------------------------------------
# RDP view lookup
# ---------------------------------------------------------------------------
def _iter_rdp_bits(layout: ObLayout):
    """Yield (bank, category, field, bit) for every RDP bit, skipping Bootloader bank."""
    for bank in layout.banks:
        if bank.interface == "Bootloader":
            continue
        for cat in bank.categories:
            if "Protection" not in cat.name and "RDP" not in cat.name:
                continue
            for fld in cat.fields:
                for bit in fld.bits:
                    if "RDP" in bit.name.upper():
                        yield bank, cat, fld, bit


def find_rdp_views(layout: ObLayout) -> tuple[Field | None, Bit | None, Field | None, Bit | None]:
    """Return (read_field, read_bit, write_field, write_bit).

    Read-view: Access contains ``R`` but not ``W`` (live status register like
    F1 OBR / H7 OPTSR_CUR) -- readable even under RDP Level 1.

    Write-view: Access contains ``W`` (physical OB byte or PRG register).

    For chips with a single RW field (F0/F3/L4/G0/...), it is returned as the
    write-view; read falls back to the same field.
    """
    read_f, read_b = None, None
    write_f, write_b = None, None
    rw_fallback_f, rw_fallback_b = None, None
    for _bank, _cat, fld, bit in _iter_rdp_bits(layout):
        a = bit.access.upper()
        if "R" in a and "W" not in a and read_b is None:
            read_f, read_b = fld, bit
        elif "W" in a and "R" not in a and write_b is None:
            write_f, write_b = fld, bit
        elif "R" in a and "W" in a and rw_fallback_b is None:
            rw_fallback_f, rw_fallback_b = fld, bit
    if read_b is None:
        read_f, read_b = rw_fallback_f, rw_fallback_b
    if write_b is None:
        write_f, write_b = rw_fallback_f, rw_fallback_b
    return read_f, read_b, write_f, write_b


def rdp_enum(bit: Bit | None) -> dict[int, str]:
    """Return ``{value: label}`` for an RDP bit's enum values."""
    if bit is None:
        return {}
    out: dict[int, str] = {}
    for v in bit.values:
        try:
            out[v.value] = v.label
        except ValueError:
            continue
    return out


def extract_rdp_field(raw_register_value: int, bit: Bit) -> int:
    """Extract the RDP field value from a raw register read per XML offset/width."""
    mask = (1 << bit.width) - 1
    return (raw_register_value >> bit.offset) & mask


# ---------------------------------------------------------------------------
# WRP (Write Protection) field extraction
# ---------------------------------------------------------------------------
# Access preference rank for deduping R/W views of the same logical WRP bit.
# W(0) > RW(1) > R(2): the write-view (physical OB byte / PRG register) is the
# one the runtime writes, mirroring find_rdp_views semantics. Encoded in
# _wrp_access_rank below.


def _wrp_access_rank(access: str) -> int | None:
    a = access.upper()
    has_r = "R" in a
    has_w = "W" in a
    if has_w and not has_r:
        return 0
    if has_r and has_w:
        return 1
    if has_r:
        return 2
    return None


def _is_wrp_category(name: str) -> bool:
    """True for ST XML "Write Protection" category and its bank-qualified variants.

    Accepts (verified across all 91 ST XMLs in CubeProgrammer Data_Base):
      - "Write Protection"                         (F0/F1/F4/H7 single category)
      - "Write Protection WRP0"                    (F04x/F070x6 suffix variant)
      - "Write Protection 1" / "Write Protection 2"(U3/U5/L5 per-bank split)
      - "Write Protection (FLASH_WRP1AR)"          (L4R dual-bank per-register)
      - "Write Protection (Bank 1)" / "Bank 2"     (L496 dual-bank)
      - "Write sector group protection 1/2"        (H5 sector-group WRP variant)

    Rejects:
      - "Read/Write Protection"  (F7 PCROP -- starts with "Read", different
        mechanism; PCROP is modeled separately and not part of WRP JSON).
      - "PCROP Protection" / "Secure Protection"  (separate categories).
    """
    n = name.strip()
    if n.startswith("Write Protection"):
        return True
    if n.startswith("Write sector group protection"):
        return True
    return False


def _infer_active_low(values: list[EnumVal]) -> bool | None:
    """Infer active_low ONLY from XML <Values>: 0x0 label says
    "protection active" -> true; "not active" -> false.

    Returns None when undeterminable (no values or 0x0 not present) -- this is
    the correct answer for edge-style STRT/END fields, where the bit is an
    address-range endpoint, not a protection flag. Do NOT guess from name.
    """
    for v in values:
        try:
            if v.value != 0:
                continue
        except ValueError:
            continue
        label = v.label.lower()
        if "not active" in label:
            return False
        if "active" in label:
            return True
    return None


def find_wrp_fields(layout: ObLayout) -> list[dict]:
    """Extract WRP fields from "Write Protection" categories, skipping the
    Bootloader bank.

    Returns a list of dicts matching the agreed wrp.fields JSON schema::

        {
          "name": "WRP0",
          "address": "0x1FFFF808",
          "bit_offset": 0,
          "bit_width": 8,
          "access": "W",
          "active_low": true,
          "values": {"0x0": "Write protection active ...", ...}
        }

    Dedup / view-selection rules (see scratch/wrp_xml_audit.md for the per-
    family rationale):

      1. Key by (bit_name, bit_offset) so two views of the same logical bit
         (R-view at live register + W-view at OB / PRG) collapse to one entry.
      2. Within a key, pick by access rank W > RW > R, then by largest
         bit_width (handles H743's duplicate nWRP0 width=8/1 quirk), then by
         largest offset.
      3. ST XML on L4R lists the same <Bit> multiple times in one field --
         identical (name, offset, width, access) tuples collapse naturally.

    The output is sorted by name for deterministic JSON.
    """
    # candidates[(name, offset)] = list of tuples
    # (bit_offset, bit_width, rank, width_tiebreak, values, access_str, address)
    candidates: dict[tuple[str, int], list[tuple[int, int, int, int, list[EnumVal], str, int]]] = {}

    for bank in layout.banks:
        if bank.interface == "Bootloader":
            continue
        for cat in bank.categories:
            if not _is_wrp_category(cat.name):
                continue
            for fld in cat.fields:
                if fld.address is None:
                    continue
                for bit in fld.bits:
                    if not bit.name:
                        continue
                    rank = _wrp_access_rank(bit.access)
                    if rank is None:
                        continue
                    key = (bit.name, bit.offset)
                    candidates.setdefault(key, []).append(
                        (bit.offset, bit.width, rank, bit.width, bit.values, bit.access.strip(), fld.address)
                    )

    out: list[dict] = []
    for (name, _offset_key), opts in candidates.items():
        # Lowest rank first (W preferred), then largest width, then largest offset.
        opts.sort(key=lambda t: (t[2], -t[3], -t[0]))
        bit_offset, bit_width, _rank, _w, values, access_str, address = opts[0]
        out.append({
            "name": name,
            "address": f"0x{address:X}",
            "bit_offset": bit_offset,
            "bit_width": bit_width,
            "access": access_str,
            "active_low": _infer_active_low(values),
            "values": {f"0x{v.value:X}": v.label for v in values} if values else {},
        })
    out.sort(key=lambda d: d["name"])
    return out
