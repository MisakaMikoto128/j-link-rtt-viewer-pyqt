"""Runtime: load unified option-byte profiles from JSON.

The JSON files in ``data/ob_profiles/{device_id}.json`` are built by
``tools/build_ob_database.py`` from ST XML + SVD + RM constants.  At runtime we
parse ONE format (JSON) -- no CubeProgrammer, SVD, or pyOCD SVD dependency.

Each profile is self-contained: RDP read/write views, FLASH register addresses,
bit offsets, and the procedure (programming model + OPTKEY + OBL trigger +
behavioral flags).  ``ops.py`` consumes :class:`ObProfile` directly.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Profile directory resolution
# ---------------------------------------------------------------------------
# Default: <project_root>/data/ob_profiles/  (this file is src/core/option_bytes/)
_DEFAULT_PROFILES_DIR = Path(__file__).resolve().parents[3] / "data" / "ob_profiles"


def find_profiles_dir() -> Path:
    """Locate the OB profiles directory (JSON database)."""
    env = os.environ.get("OB_PROFILES_DIR")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    if _DEFAULT_PROFILES_DIR.is_dir():
        return _DEFAULT_PROFILES_DIR
    raise FileNotFoundError(
        f"OB profiles directory not found at {_DEFAULT_PROFILES_DIR}; "
        f"set OB_PROFILES_DIR or run tools/build_ob_database.py"
    )


# ---------------------------------------------------------------------------
# Data model (mirrors the JSON schema)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RdpView:
    address: int
    bit_offset: int
    bit_width: int
    access: str               # "R" | "W" | "RW"
    values: dict[int, str]    # value -> label (e.g. {0xA5: "Level 0"})


@dataclass(frozen=True)
class FlashRegAddrs:
    keyr: int
    optkeyr: int
    cr: int
    sr: int
    obr: int | None = None
    optcr: int | None = None
    optr: int | None = None
    optsr_cur: int | None = None
    optsr_prg: int | None = None
    optccr: int | None = None


@dataclass(frozen=True)
class ObProcedure:
    ob_programming: str               # "halfword" | "optcr32" | "ob_register_word"
    flash_key_seq: tuple[int, int]
    optkey_seq: tuple[int, int]
    obl_trigger: str                  # "reset"|"power_cycle"|"cr_obl_launch"|"opt_start"|"optcr_optstart"
    obl_requires_power_cycle: bool


@dataclass(frozen=True)
class WrpField:
    """A single WRP field descriptor (bit-mode: WRP0/WRP1/...; edge-mode: WRP1A_STRT/END, ...)."""
    name: str
    address: int
    bit_offset: int
    bit_width: int
    access: str                        # "W" | "RW" | "R"
    active_low: bool                   # True: 0 = protected (F0/F1/F4 nWRP convention)
    values: dict[int, str] = field(default_factory=dict)   # value -> label (seldom populated for WRP)


@dataclass(frozen=True)
class WrpInfo:
    """Per-chip WRP scheme descriptor."""
    present: bool
    model: str                          # "bit" | "edge" | "none"
    fields: tuple[WrpField, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ObProfile:
    """Self-contained OB profile for one chip -- everything ops.py needs."""
    device_id: str
    device_name: str
    family: str
    verified: bool
    rdp_read_view: RdpView | None
    rdp_write_view: RdpView
    flash_regs: FlashRegAddrs
    cr_bits: dict[str, int]
    sr_bits: dict[str, int]
    optcr_bits: dict[str, int]
    optsr_cur_bits: dict[str, int]
    procedure: ObProcedure
    wrp: WrpInfo = field(default_factory=lambda: WrpInfo(present=False, model="none"))


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------
def _parse_hex(s: str | None) -> int | None:
    if s is None:
        return None
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def _parse_view(d: dict | None) -> RdpView | None:
    if d is None:
        return None
    values = {}
    for k, v in d.get("values", {}).items():
        try:
            values[int(k, 16) if k.lower().startswith("0x") else int(k)] = v
        except ValueError:
            continue
    return RdpView(
        address=_parse_hex(d["address"]) or 0,
        bit_offset=int(d["bit_offset"]),
        bit_width=int(d["bit_width"]),
        access=d.get("access", ""),
        values=values,
    )


def _parse_wrp_field(d: dict) -> WrpField:
    values: dict[int, str] = {}
    for k, v in d.get("values", {}).items():
        try:
            values[int(k, 16) if k.lower().startswith("0x") else int(k)] = v
        except ValueError:
            continue
    return WrpField(
        name=d["name"],
        address=_parse_hex(d["address"]) or 0,
        bit_offset=int(d.get("bit_offset", 0)),
        bit_width=int(d.get("bit_width", 0)),
        access=d.get("access", "W"),
        active_low=bool(d.get("active_low", True)),
        values=values,
    )


def _parse_wrp(d: dict | None) -> WrpInfo:
    """Parse the optional `wrp` section.

    Backward compat: missing or `{"present": false}` -> ``WrpInfo(present=False,
    model="none")``.  When `present` is True, requires `model` and `fields`.
    """
    if d is None:
        return WrpInfo(present=False, model="none")
    if not bool(d.get("present", False)):
        return WrpInfo(present=False, model="none")
    model = d.get("model", "none")
    fields = tuple(_parse_wrp_field(f) for f in d.get("fields", []))
    return WrpInfo(present=True, model=model, fields=fields)


def _parse_profile(d: dict) -> ObProfile:
    fr = d["flash_regs"]
    proc = d["procedure"]
    return ObProfile(
        device_id=d["device_id"],
        device_name=d["device_name"],
        family=d["family"],
        verified=bool(d["verified"]),
        rdp_read_view=_parse_view(d.get("rdp_read_view")),
        rdp_write_view=_parse_view(d["rdp_write_view"]),  # type: ignore[arg-type]
        flash_regs=FlashRegAddrs(
            keyr=_parse_hex(fr["keyr"]),
            optkeyr=_parse_hex(fr["optkeyr"]),
            cr=_parse_hex(fr["cr"]),
            sr=_parse_hex(fr["sr"]),
            obr=_parse_hex(fr.get("obr")),
            optcr=_parse_hex(fr.get("optcr")),
            optr=_parse_hex(fr.get("optr")),
            optsr_cur=_parse_hex(fr.get("optsr_cur")),
            optsr_prg=_parse_hex(fr.get("optsr_prg")),
            optccr=_parse_hex(fr.get("optccr")),
        ),
        cr_bits=dict(d["bits"]["cr"]),
        sr_bits=dict(d["bits"]["sr"]),
        optcr_bits=dict(d["bits"].get("optcr", {})),
        optsr_cur_bits=dict(d["bits"].get("optsr_cur", {})),
        procedure=ObProcedure(
            ob_programming=proc["ob_programming"],
            flash_key_seq=tuple(_parse_hex(x) for x in proc["flash_key_seq"]),  # type: ignore[arg-type]
            optkey_seq=tuple(_parse_hex(x) for x in proc["optkey_seq"]),  # type: ignore[arg-type]
            obl_trigger=proc["obl_trigger"],
            obl_requires_power_cycle=bool(proc["obl_requires_power_cycle"]),
        ),
        wrp=_parse_wrp(d.get("wrp")),
    )


@lru_cache(maxsize=64)
def load_profile(device_id: str) -> ObProfile:
    """Load the unified OB profile for a chip (cached by device_id).

    ``device_id`` is the DBGMCU_IDCODE in hex form, e.g. ``"0x410"``.
    """
    path = find_profiles_dir() / f"{device_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no OB profile for DeviceID {device_id}: {path}")
    return _parse_profile(json.loads(path.read_text(encoding="utf-8")))


def available_device_ids() -> list[str]:
    """Return all device_ids with a built profile (for UI/diagnostics)."""
    try:
        d = find_profiles_dir()
    except FileNotFoundError:
        return []
    return sorted(p.stem for p in d.glob("*.json"))
