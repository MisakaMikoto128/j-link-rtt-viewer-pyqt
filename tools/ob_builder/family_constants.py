"""Layer 3: STM32 family procedure table -- ONLY irreducible RM knowledge.

Everything that can be derived from data IS derived elsewhere:
  - OB field layout + enum values  <- ST XML (:mod:`st_xml_db`)
  - FLASH register addresses + bits <- SVD (:mod:`svd_db`)
  - programming model (halfword/optcr32/ob_register_word) <- SVD structure
    (:func:`svd_db.derive_programming_model`)
  - OBL trigger when an OBL bit exists <- SVD bits
    (:func:`svd_db.derive_obl_trigger`)
  - SVD file selection <- family key -> ``STM32{family}*.svd`` glob
    (:func:`svd_db.find_svd_for_family`)

What stays here is what NO ST data file contains (confirmed by pack audit,
``scratch/agent4_pack_sequences_findings.md``):
  - OPTKEY magic values -- not in XML, not in SVD, not in .pdsc debug sequences,
    .FLM binary extraction is incomplete/unstable.  RM-only knowledge.
  - F0 <=64KB has no OBL_LAUNCH bit and reset does NOT reload OBL -- only
    physical POR does (silicon behavior; F1/L0/L1 without OBL bits DO reload on
    reset).

So the table is 4 fields per family: optkey pattern (1 bit: legacy/modern),
no-OBL-bit behavior (reset/power_cycle), verified flag.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Family name normalization (data-driven: derived from ST XML <Series>)
# ---------------------------------------------------------------------------
_SERIES_TO_FAMILY = {
    "STM32F0": "F0", "STM32F1": "F1", "STM32F2": "F2", "STM32F3": "F3",
    "STM32F4": "F4", "STM32F7": "F7",
    "STM32G0": "G0", "STM32G4": "G4",
    "STM32H5": "H5", "STM32H7": "H7",
    "STM32L0": "L0", "STM32L1": "L1", "STM32L4": "L4", "STM32L5": "L5",
    "STM32U0": "U0", "STM32U3": "U3", "STM32U5": "U5",
    # WBA must come before WB: "STM32WBA" startswith "STM32WB" would match WB.
    "STM32WBA": "WBA", "STM32WB": "WB", "STM32WL": "WL",
    "STM32C0": "C0",
}


def family_from_series(series: str) -> str:
    """Map an ST XML <Series> value (e.g. ``"STM32F1"``) to a family key (``"F1"``)."""
    if not series:
        raise ValueError("empty series")
    key = series.strip().upper()
    for prefix, fam in _SERIES_TO_FAMILY.items():
        if key.startswith(prefix.upper()):
            return fam
    raise ValueError(f"unknown STM32 series: {series!r}")


# ---------------------------------------------------------------------------
# Procedure dataclass -- only irreducible fields
# ---------------------------------------------------------------------------
# OPTKEY magic values (RM constants; NOT in any ST data file)
_FLASH_KEY = (0x45670123, 0xCDEF89AB)           # same for ALL STM32 families
_LEGACY_OPTKEY = (0x45670123, 0xCDEF89AB)        # F0/F1/F3/L0/L1: OPTKEYR reuses FLASH key
_MODERN_OPTKEY = (0x08192A3B, 0x4C5D6E7F)        # F2/F4/F7/L4/L5/G0/G4/H7/WB/WL/C0/U5: distinct


@dataclass(frozen=True)
class FamilyProcedure:
    """Per-family IRREDUCIBLE procedure knowledge (everything else is SVD/XML-derived)."""

    family: str

    # OPTKEY pattern -- which magic pair OPTKEYR accepts.  Not in any ST data
    # file; confirmed by pack audit (agent4).  "legacy" == FLASH key reuse.
    optkey_pattern: str   # "legacy" | "modern"

    # What OBL trigger to use when SVD shows NO OBL bit (F0/F1/L0/L1).
    # F0 <=64KB = "power_cycle" (no OBL_LAUNCH, reset doesn't reload); others = "reset".
    no_obl_bit_trigger: str = "reset"

    verified: bool = False   # hardware-verified on real silicon?

    @property
    def flash_key_seq(self) -> tuple[int, int]:
        return _FLASH_KEY   # identical for all STM32 families

    @property
    def optkey_seq(self) -> tuple[int, int]:
        if self.optkey_pattern == "legacy":
            return _LEGACY_OPTKEY
        if self.optkey_pattern == "modern":
            return _MODERN_OPTKEY
        raise ValueError(f"unknown optkey_pattern {self.optkey_pattern!r}")


# ---------------------------------------------------------------------------
# Family table -- only irreducible fields (4 per entry)
# ---------------------------------------------------------------------------
# Verified (5): F0, F1, F4, L4, H7 -- three programming models HW-confirmed.
#   see scratch/agent1_f030_findings.md, agent2_f103_findings.md, agent3_findings.md
# Unverified (12): RM-translated using closest verified family's OPTKEY pattern.
#   Programming model + OBL trigger + SVD file all auto-derived at runtime.
FAMILY_PROCEDURES: dict[str, FamilyProcedure] = {
    # --- HARDWARE-VERIFIED -------------------------------------------------
    "F0": FamilyProcedure("F0", "legacy", no_obl_bit_trigger="power_cycle", verified=True),
    "F1": FamilyProcedure("F1", "legacy", no_obl_bit_trigger="reset", verified=True),
    "F4": FamilyProcedure("F4", "modern", verified=True),
    "L4": FamilyProcedure("L4", "modern", verified=True),
    "H7": FamilyProcedure("H7", "modern", verified=True),

    # --- UNVERIFIED -- RM OPTKEY pattern only; rest derived from SVD --------
    "F2": FamilyProcedure("F2", "modern"),
    "F3": FamilyProcedure("F3", "legacy", no_obl_bit_trigger="reset"),
    "F7": FamilyProcedure("F7", "modern"),
    "L0": FamilyProcedure("L0", "legacy", no_obl_bit_trigger="reset"),
    "L1": FamilyProcedure("L1", "legacy", no_obl_bit_trigger="reset"),
    "L5": FamilyProcedure("L5", "modern"),
    "G0": FamilyProcedure("G0", "modern"),
    "G4": FamilyProcedure("G4", "modern"),
    "WB": FamilyProcedure("WB", "modern"),
    "WL": FamilyProcedure("WL", "modern"),
    "C0": FamilyProcedure("C0", "modern"),
    "U5": FamilyProcedure("U5", "modern"),
    # New families (added 2026-07): modern OPTKEY, OBL via reset.
    "H5": FamilyProcedure("H5", "modern"),
    "U0": FamilyProcedure("U0", "modern"),
    "U3": FamilyProcedure("U3", "modern"),
    "WBA": FamilyProcedure("WBA", "modern"),
}


def get_family_procedure(family: str) -> FamilyProcedure:
    """Look up a family procedure.  Raises ``KeyError`` for unknown families."""
    try:
        return FAMILY_PROCEDURES[family]
    except KeyError:
        raise KeyError(
            f"family {family!r} has no procedure table entry; "
            f"known: {sorted(FAMILY_PROCEDURES)}"
        ) from None
