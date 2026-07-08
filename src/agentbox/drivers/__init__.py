from __future__ import annotations

from .base import CommonDriverSettings, Diagnostic, HarnessDriver, MountSpec
from .codex import CodexDriver, CodexSettings
from .kilo import KiloDriver, KiloSettings, merged_kilo_config_content


DRIVERS: tuple[HarnessDriver, ...] = (CodexDriver(), KiloDriver())
_BY_ID = {driver.id: driver for driver in DRIVERS}
_ALIASES = {alias: driver.id for driver in DRIVERS for alias in driver.aliases}


def get_driver(driver_id: str) -> HarnessDriver:
    canonical = canonical_driver_id(driver_id)
    try:
        return _BY_ID[canonical]
    except KeyError as exc:
        raise RuntimeError(f"unknown driver: {driver_id}") from exc


def canonical_driver_id(driver_id: str) -> str:
    return _ALIASES.get(driver_id, driver_id)


def all_drivers() -> tuple[HarnessDriver, ...]:
    return DRIVERS


__all__ = [
    "CodexDriver",
    "CodexSettings",
    "CommonDriverSettings",
    "Diagnostic",
    "HarnessDriver",
    "KiloDriver",
    "KiloSettings",
    "MountSpec",
    "all_drivers",
    "canonical_driver_id",
    "get_driver",
    "merged_kilo_config_content",
]
