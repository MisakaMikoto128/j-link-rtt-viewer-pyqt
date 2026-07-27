"""ObBackend adapters -- wrap a connected ProbeBackend into the ObBackend protocol.

The OB executor (:mod:`core.option_bytes.ops`) needs mem_read32 / mem_write32 /
mem_write16 / reset.  PylinkBackend and PyOCDBackend already hold a connected
pylink / pyOCD session; these adapters expose the narrow interface the OB
executor uses, without modifying the backend classes.

Also provides :func:`read_device_id` which reads DBGMCU_IDCODE from the chip and
validates it against the ST XML database (data-driven: only returns a device_id
whose ``STM32_Prog_DB_{id}.xml`` exists).
"""
from __future__ import annotations

from core.option_bytes import ob_profile
from core.probe.jlink_backend import PylinkBackend
from core.probe.pyocd_backend import PyOCDBackend

# DBGMCU_IDCODE addresses across STM32 families (RM-dependent; not in SVD
# uniformly -- DBGMCU peripheral name/offset varies).  Try each, validate
# against the XML database.
_DBGMCU_IDCODE_ADDRS = (
    0xE0042000,   # F1/F2/F3/F4/F7/L1/L4/G0/G4/C0/WB/WL
    0x40015800,   # F0/L0/U0
    0x5C001000,   # H7
    0xE0044000,   # L5/U5/WBA (TrustZone-capable, separate DBGMCU block)
)


class PylinkObAdapter:
    """ObBackend over a connected :class:`PylinkBackend`."""

    def __init__(self, backend: PylinkBackend) -> None:
        self._b = backend

    @property
    def _j(self):
        j = self._b._jlink
        if j is None or not j.opened():
            raise RuntimeError("J-Link not connected")
        return j

    def mem_read32(self, addr: int) -> int:
        return int(self._j.memory_read32(addr, 1)[0])

    def mem_write32(self, addr: int, value: int) -> None:
        self._j.memory_write32(addr, [value])

    def mem_write16(self, addr: int, value: int) -> None:
        # MUST use memory_write16 -- memory_write(addr, [v], nbits=16) does not
        # send a 16-bit write (verified on F030, see CLAUDE.md).
        self._j.memory_write16(addr, [value])

    def reset(self) -> None:
        self._b.reset(halt=False, run=True)


class PyocdObAdapter:
    """ObBackend over a connected :class:`PyOCDBackend`."""

    def __init__(self, backend: PyOCDBackend) -> None:
        self._b = backend

    @property
    def _t(self):
        t = self._b._target
        if t is None:
            raise RuntimeError("pyOCD target not connected")
        return t

    def mem_read32(self, addr: int) -> int:
        return int(self._t.read32(addr))

    def mem_write32(self, addr: int, value: int) -> None:
        self._t.write32(addr, value)

    def mem_write16(self, addr: int, value: int) -> None:
        self._t.write16(addr, value)

    def reset(self) -> None:
        self._b.reset(halt=False, run=True)


def make_ob_adapter(backend) -> PylinkObAdapter | PyocdObAdapter:
    """Wrap a connected ProbeBackend into the ObBackend protocol."""
    if isinstance(backend, PylinkBackend):
        return PylinkObAdapter(backend)
    if isinstance(backend, PyOCDBackend):
        return PyocdObAdapter(backend)
    raise TypeError(f"unsupported backend type: {type(backend).__name__}")


def read_device_id(adapter) -> str:
    """Read DBGMCU_IDCODE from the chip and return ``"0xXXX"``.

    Tries the known IDCODE addresses (vary by family), validates each against
    the ST XML database -- only returns a device_id whose XML exists.  Raises
    ``RuntimeError`` if no valid IDCODE could be read.
    """
    profiles_dir = ob_profile.find_profiles_dir()
    tried: list[str] = []
    for addr in _DBGMCU_IDCODE_ADDRS:
        tried.append(f"0x{addr:08X}")
        try:
            val = adapter.mem_read32(addr)
        except Exception:
            continue
        if not val:
            continue
        dev_id = val & 0xFFF   # lower 12 bits = DEV_ID
        if not dev_id:
            continue
        candidate = f"0x{dev_id:03X}"
        if (profiles_dir / f"{candidate}.json").exists():
            return candidate
    raise RuntimeError(
        f"cannot read a valid DBGMCU_IDCODE (tried {', '.join(tried)}; "
        f"none matched a built OB profile)"
    )
