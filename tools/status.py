# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp"]
# ///
"""Print what every Home Assistant entity would show, right now.

Runs the shipped api.py and the shipped entity value functions against the
live cloud, so this is the integration's own view rather than a second
opinion.

    uv run tools/status.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "anycubic_m7pro"


def _stub_homeassistant() -> None:
    def mod(name: str) -> types.ModuleType:
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    @dataclass(frozen=True, kw_only=True)
    class EntityDescription:
        key: str
        translation_key: str | None = None
        device_class: Any = None
        state_class: Any = None
        native_unit_of_measurement: Any = None
        entity_category: Any = None
        entity_registry_enabled_default: bool = True
        suggested_display_precision: int | None = None
        options: Any = None
        name: Any = None
        icon: str | None = None

    class _Names:
        def __getattr__(self, item: str) -> str:
            return item

    _T = TypeVar("_T")

    class _Generic(Generic[_T]):
        def __init__(self, *a: Any, **k: Any) -> None: ...

    ha = mod("homeassistant")
    ha.__path__ = []
    for name in ("components", "helpers", "util"):
        sub = mod(f"homeassistant.{name}")
        sub.__path__ = []

    core = mod("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda fn: fn

    const = mod("homeassistant.const")
    for attr in ("EntityCategory", "UnitOfLength", "UnitOfTime", "UnitOfVolume", "Platform"):
        setattr(const, attr, _Names())

    s = mod("homeassistant.components.sensor")
    s.SensorDeviceClass = _Names()
    s.SensorStateClass = _Names()
    s.SensorEntityDescription = EntityDescription
    s.SensorEntity = object

    b = mod("homeassistant.components.binary_sensor")
    b.BinarySensorDeviceClass = _Names()
    b.BinarySensorEntityDescription = EntityDescription
    b.BinarySensorEntity = object

    dt_mod = mod("homeassistant.util.dt")
    dt_mod.utcnow = lambda: datetime.now(timezone.utc)

    ep = mod("homeassistant.helpers.entity_platform")
    ep.AddEntitiesCallback = object

    upd = mod("homeassistant.helpers.update_coordinator")
    upd.CoordinatorEntity = _Generic
    upd.DataUpdateCoordinator = _Generic
    upd.UpdateFailed = type("UpdateFailed", (Exception,), {})

    dr = mod("homeassistant.helpers.device_registry")
    dr.DeviceInfo = dict

    ce = mod("homeassistant.config_entries")
    ce.ConfigEntry = object

    ex = mod("homeassistant.exceptions")
    ex.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})

    ac = mod("homeassistant.helpers.aiohttp_client")
    ac.async_get_clientsession = lambda hass: None

    pkg = types.ModuleType("_anycubic_status")
    pkg.__path__ = [str(COMPONENT)]
    pkg.AnycubicConfigEntry = object
    sys.modules["_anycubic_status"] = pkg


def _load(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_anycubic_status.{name}", COMPONENT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_stub_homeassistant()
_load("const")
api_mod = _load("api")
_load("entity")
sensor_mod = _load("sensor")
binary_mod = _load("binary_sensor")


def show(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        local = value.astimezone()
        delta = (datetime.now(timezone.utc) - value).total_seconds()
        when = local.strftime("%Y-%m-%d %H:%M:%S")
        if 0 <= delta < 86400:
            return f"{when}  ({int(delta // 60)} min ago)"
        if -86400 < delta < 0:
            return f"{when}  (in {int(-delta // 60)} min)"
        return when
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


async def main() -> None:
    token_file = ROOT / "secrets" / "token.txt"
    if not token_file.exists():
        sys.exit(f"No token at {token_file}")
    token = token_file.read_text(encoding="utf-8").strip()

    async with aiohttp.ClientSession() as session:
        client = api_mod.AnycubicCloud(session, token)
        printers = await client.async_list_printers()
        if not printers:
            sys.exit("No printers on the account.")
        state = await client.async_poll(int(printers[0]["id"]))

    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"Polled at {stamp}\n")

    units = {d.key: d.native_unit_of_measurement for d in sensor_mod.SENSORS}

    print("BINARY SENSORS")
    for d in binary_mod.BINARY_SENSORS:
        print(f"  {d.key:<28} {show(d.value_fn(state))}")

    active = sensor_mod._is_active(state)

    print("\nPRINTER")
    printer_keys = {
        "status", "printer_state", "firmware_version", "latest_firmware_version",
        "last_seen", "total_prints", "total_print_time", "total_resin_used",
        "release_film_layers",
    }
    for d in sensor_mod.SENSORS:
        if d.key in printer_keys:
            unit = f" {units[d.key]}" if units[d.key] else ""
            value = d.value_fn(state)
            print(f"  {d.key:<28} {show(value)}{unit if value is not None else ''}")

    print(f"\nCURRENT JOB   ({'running' if active else 'idle - these stay blank'})")
    for d in sensor_mod.SENSORS:
        if d.key not in printer_keys:
            unit = f" {units[d.key]}" if units[d.key] else ""
            value = d.value_fn(state)
            print(f"  {d.key:<28} {show(value)}{unit if value is not None else ''}")

    thumb = state.job.get("img") if active else None
    print(f"  {'job_thumbnail':<28} {thumb or '-'}")

    if not active:
        # The finished job is still the newest project, so it is worth
        # showing what the printer last did even though no entity reports it.
        last = state.job
        if last:
            print("\nLAST COMPLETED JOB (not exposed as entities)")
            print(f"  {'name':<28} {last.get('gcode_name')}")
            print(f"  {'finished':<28} {show(sensor_mod._epoch(last.get('end_time')))}")
            print(f"  {'took':<28} {last.get('total_time')}")
            print(f"  {'resin':<28} {last.get('material')} ml")
            print(f"  {'status code':<28} {last.get('print_status')}")


if __name__ == "__main__":
    asyncio.run(main())
