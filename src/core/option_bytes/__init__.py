"""STM32 option bytes -- JSON-driven read/write.

Build-time (``tools/build_ob_database.py``) reads ST XML + SVD + RM constants
and writes a unified per-chip JSON database to ``data/ob_profiles/``.  Runtime
loads only JSON -- one format, one parser, no CubeProgrammer/SVD dependency.

Each ``data/ob_profiles/{device_id}.json`` is self-contained: RDP read/write
views, FLASH register addresses, bit offsets, and the procedure (programming
model + OPTKEY + OBL trigger + behavioral flags).  :mod:`ops` consumes
:class:`ObProfile` directly.

Hardware-verified: F030 + J-Link (halfword), F103 + DAPLink/pyOCD (halfword +
RAMCode for L1->L0).  F4/L4/H7 translated from RM, pending
hardware verification.

Usage:
    from core.option_bytes import read_rdp_level, set_rdp_level, RdpLevel

    level = read_rdp_level("0x410", backend)            # F103
    result = set_rdp_level("0x410", RdpLevel.L0, backend)
    if result.obl_status == "needs_power_cycle":
        ...  # prompt user to power-cycle (F0)
"""
from .ob_profile import (
    FlashRegAddrs,
    ObProcedure,
    ObProfile,
    RdpView,
    WrpField,
    WrpInfo,
    available_device_ids,
    find_profiles_dir,
    load_profile,
)
from .ops import (
    WRP_STATUS_ALL,
    WRP_STATUS_NONE,
    WRP_STATUS_PARTIAL,
    ObBackend,
    RdpLevel,
    SetRdpResult,
    read_rdp_level,
    read_wrp_status,
    set_rdp_level,
    set_wrp,
)

__all__ = [  # noqa: RUF022  # 刻意按 profile / ops 分组排序，非字母序
    # profile (JSON-loaded)
    "ObProfile",
    "RdpView",
    "FlashRegAddrs",
    "ObProcedure",
    "WrpField",
    "WrpInfo",
    "load_profile",
    "available_device_ids",
    "find_profiles_dir",
    # ops
    "ObBackend",
    "RdpLevel",
    "SetRdpResult",
    "read_rdp_level",
    "read_wrp_status",
    "set_rdp_level",
    "set_wrp",
    "WRP_STATUS_ALL",
    "WRP_STATUS_NONE",
    "WRP_STATUS_PARTIAL",
]
