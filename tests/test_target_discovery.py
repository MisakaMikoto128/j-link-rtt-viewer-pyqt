"""Tests for src/core/target_discovery.py.

These tests hit real pylink / pyOCD target enumerations (where installed) and
verify the filtering/caching/routing logic.
"""

from __future__ import annotations

import pytest

from core.probe.base import (
    BURNER_KIND_CMSIS_DAP,
    BURNER_KIND_JLINK,
    BURNER_KIND_STLINK,
)
from core.target_discovery import (
    get_pylink_target_names,
    get_pyocd_target_names,
    target_names_for_burner_kind,
)


@pytest.mark.parametrize("_first_call", [True])
def test_pylink_target_names_returns_sorted_uppercase(isolated_appdata, _first_call):
    """get_pylink_target_names returns an uppercase, sorted tuple with common MCUs."""
    names = get_pylink_target_names()

    assert isinstance(names, tuple)
    assert len(names) > 0
    assert all(name == name.upper() for name in names)
    assert list(names) == sorted(names)
    assert "STM32F030C8" in names


def test_pylink_target_names_filters_non_mcu_noise():
    """Non-MCU entries like ARM7/Cortex-A are filtered out; STM32 stays."""
    names = set(get_pylink_target_names())

    assert "ARM7" not in names
    assert "CORTEX-A5" not in names
    assert "CORTEX-A9" not in names
    assert any(name.startswith("STM32") for name in names)


def test_pyocd_target_names_returns_sorted_uppercase():
    """get_pyocd_target_names returns an uppercase, sorted tuple with known targets."""
    names = get_pyocd_target_names()

    assert isinstance(names, tuple)
    assert all(name == name.upper() for name in names)
    assert list(names) == sorted(names)
    assert "STM32F103RC" in names or "STM32F030C8" in names


@pytest.mark.parametrize("kind, expected", [
    (BURNER_KIND_JLINK, get_pylink_target_names),
    (BURNER_KIND_CMSIS_DAP, get_pyocd_target_names),
    (BURNER_KIND_STLINK, get_pyocd_target_names),
])
def test_target_names_for_burner_kind_routes_correctly(kind, expected):
    """target_names_for_burner_kind delegates to the correct source."""
    assert target_names_for_burner_kind(kind) is expected()


def test_get_pylink_target_names_cached():
    """The pylink result is cached: two calls return the same tuple object."""
    first = get_pylink_target_names()
    second = get_pylink_target_names()

    assert first is second


def test_target_discovery_no_config_chip_models_dependency(isolated_appdata):
    """Target discovery is independent of ConfigService chip_models."""
    names = get_pylink_target_names()

    assert isinstance(names, tuple)
    assert all(isinstance(name, str) for name in names)
