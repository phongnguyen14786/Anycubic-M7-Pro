# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp"]
# ///
"""Test the integration's sensor logic against real payloads.

Home Assistant itself will not pip-install on Windows without a C toolchain,
so this stubs the handful of Home Assistant names sensor.py and
binary_sensor.py import, then exercises the real value functions -- the
parsers and the idle-gating, which is where the logic actually lives.

    uv run tools/test_entities.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "anycubic_m7pro"

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}{f'  -- {detail}' if detail else ''}")
    if not condition:
        FAILURES.append(label)


# --------------------------------------------------------------------------
# Minimal Home Assistant stand-ins
# --------------------------------------------------------------------------
def _install_ha_stubs() -> None:
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
        """Any attribute access returns its own name, like an enum would."""

        def __getattr__(self, item: str) -> str:
            return item

    ha = mod("homeassistant")
    ha.__path__ = []

    core = mod("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda fn: fn

    config_entries = mod("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    config_entries.ConfigFlow = object
    config_entries.ConfigFlowResult = dict

    exceptions = mod("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type(
        "ConfigEntryAuthFailed", (Exception,), {}
    )

    aiohttp_client = mod("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: None

    const = mod("homeassistant.const")
    const.EntityCategory = _Names()
    const.UnitOfLength = _Names()
    const.UnitOfTime = _Names()
    const.UnitOfVolume = _Names()
    const.Platform = _Names()

    components = mod("homeassistant.components")
    components.__path__ = []

    sensor_mod = mod("homeassistant.components.sensor")
    sensor_mod.SensorDeviceClass = _Names()
    sensor_mod.SensorStateClass = _Names()
    sensor_mod.SensorEntityDescription = EntityDescription
    sensor_mod.SensorEntity = object

    bs_mod = mod("homeassistant.components.binary_sensor")
    bs_mod.BinarySensorDeviceClass = _Names()
    bs_mod.BinarySensorEntityDescription = EntityDescription
    bs_mod.BinarySensorEntity = object

    helpers = mod("homeassistant.helpers")
    helpers.__path__ = []

    ep = mod("homeassistant.helpers.entity_platform")
    ep.AddEntitiesCallback = object

    # Both are generic in the real Home Assistant, and the component
    # subscripts them, so plain `object` will not do.
    _T = TypeVar("_T")

    class _Generic(Generic[_T]):
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    upd = mod("homeassistant.helpers.update_coordinator")
    upd.CoordinatorEntity = _Generic
    upd.DataUpdateCoordinator = _Generic
    upd.UpdateFailed = type("UpdateFailed", (Exception,), {})

    dr = mod("homeassistant.helpers.device_registry")
    dr.DeviceInfo = dict

    util = mod("homeassistant.util")
    util.__path__ = []
    dt_mod = mod("homeassistant.util.dt")
    dt_mod.utcnow = lambda: datetime.now(timezone.utc)
    util.dt = dt_mod

    # sensor.py does `from . import AnycubicConfigEntry`; the real package
    # __init__ imports Home Assistant config entries, so stand in for it.
    pkg = types.ModuleType("_anycubic_under_test")
    pkg.__path__ = [str(COMPONENT)]
    pkg.AnycubicConfigEntry = object
    sys.modules["_anycubic_under_test"] = pkg


def _load(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_anycubic_under_test.{name}", COMPONENT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_install_ha_stubs()
const_mod = _load("const")
api_mod = _load("api")
# entity.py is imported by sensor.py; load it first so the stubbed
# CoordinatorEntity base is in place.
_load("entity")
sensor_mod = _load("sensor")
binary_mod = _load("binary_sensor")

PrinterState = api_mod.PrinterState


def state_from_files() -> PrinterState:
    """Rebuild a real poll from the JSON captured earlier."""
    printer = json.loads(
        (ROOT / "out" / "02-printer-info.json").read_text(encoding="utf-8")
    )["data"]
    projects = json.loads(
        (ROOT / "out" / "05-projects.json").read_text(encoding="utf-8")
    )["data"]
    job = projects[0]
    return PrinterState(
        printer=printer,
        job=job,
        message=api_mod._maybe_json(job.get("device_message")),
    )


def values(state: PrinterState) -> dict[str, Any]:
    return {d.key: d.value_fn(state) for d in sensor_mod.SENSORS}


def binary_values(state: PrinterState) -> dict[str, Any]:
    return {d.key: d.value_fn(state) for d in binary_mod.BINARY_SENSORS}


def main() -> None:
    print("parsers")
    pdm = sensor_mod._parse_duration_minutes
    check("'3hour37min' -> 217", pdm("3hour37min") == 217, str(pdm("3hour37min")))
    check("'1hour11min' -> 71", pdm("1hour11min") == 71, str(pdm("1hour11min")))
    check("'45min' -> 45", pdm("45min") == 45, str(pdm("45min")))
    check("'2day3hour' -> 3060", pdm("2day3hour") == 3060, str(pdm("2day3hour")))
    check("None -> None", pdm(None) is None)
    check("garbage -> None", pdm("banana") is None, str(pdm("banana")))

    pn = sensor_mod._parse_number
    check("'205.19ml' -> 205.19", pn("205.19ml") == 205.19, str(pn("205.19ml")))
    check("plain int passes", pn(5) == 5.0)
    check("None -> None", pn(None) is None)

    print("\nidle printer (real captured data)")
    idle = state_from_files()
    v = values(idle)
    b = binary_values(idle)

    check("status is idle", v["status"] == "idle", v["status"])
    check("printing is False", b["printing"] is False)
    check("online is True", b["online"] is True)
    check("no problem", b["problem"] is False)
    check("firmware present", v["firmware_version"] == "4.0.8.6", v["firmware_version"])
    check("total prints", v["total_prints"] == 5, str(v["total_prints"]))
    check("total print time", v["total_print_time"] == 217, str(v["total_print_time"]))
    check("total resin", v["total_resin_used"] == 205.19, str(v["total_resin_used"]))
    check("release film", v["release_film_layers"] == 2811, str(v["release_film_layers"]))

    job_keys = [
        "job_name", "progress", "current_layer", "total_layers",
        "time_elapsed", "time_remaining", "estimated_finish",
        "job_resin_used", "model_height", "layer_height",
        "exposure_time", "bottom_exposure_time", "bottom_layers",
    ]
    blank = [k for k in job_keys if v[k] is None]
    check(
        "all job sensors blank while idle",
        len(blank) == len(job_keys),
        f"{len(blank)}/{len(job_keys)} blank",
    )

    print("\nprinting (same job, status forced to Printing)")
    live_job = dict(idle.job)
    live_job["print_status"] = 1
    live_job["remain_time"] = 42
    message = dict(idle.message)
    message["progress"] = 63
    message["curr_layer"] = 700
    live = PrinterState(printer=idle.printer, job=live_job, message=message)
    v2 = values(live)
    b2 = binary_values(live)

    check("status is printing", v2["status"] == "printing", v2["status"])
    check("printing is True", b2["printing"] is True)
    check("job name", v2["job_name"] == "01_Right Tire", str(v2["job_name"]))
    check("progress", v2["progress"] == 63, str(v2["progress"]))
    check("current layer", v2["current_layer"] == 700, str(v2["current_layer"]))
    check("total layers", v2["total_layers"] == 1109, str(v2["total_layers"]))
    check("time remaining", v2["time_remaining"] == 42, str(v2["time_remaining"]))
    check("exposure time", v2["exposure_time"] == 1.5, str(v2["exposure_time"]))
    check("bottom exposure", v2["bottom_exposure_time"] == 28, str(v2["bottom_exposure_time"]))
    check("bottom layers", v2["bottom_layers"] == 5, str(v2["bottom_layers"]))
    check("layer height", v2["layer_height"] == 0.03, str(v2["layer_height"]))
    check("model height", v2["model_height"] == 33.27, str(v2["model_height"]))
    check("resin used", v2["job_resin_used"] is not None, str(v2["job_resin_used"]))

    finish = v2["estimated_finish"]
    check("estimated finish is a datetime", isinstance(finish, datetime), str(finish))
    if isinstance(finish, datetime):
        delta = (finish - datetime.now(timezone.utc)).total_seconds() / 60
        check("finish ~42 min out", 41 <= delta <= 43, f"{delta:.1f} min")

    print("\nnever-printed printer (no job at all)")
    empty = PrinterState(printer=idle.printer, job={}, message={})
    v3 = values(empty)
    b3 = binary_values(empty)
    check("status idle", v3["status"] == "idle", v3["status"])
    check("not printing", b3["printing"] is False)
    check("printer sensors still work", v3["total_prints"] == 5)
    check("job sensors blank", all(v3[k] is None for k in job_keys))

    print("\nerror surfacing")
    errored = PrinterState(
        printer=idle.printer,
        job={"print_status": 1},
        message={"err_message": "Resin low"},
    )
    check("problem raised", binary_values(errored)["problem"] is True)

    print("\nunique ids")
    keys = [d.key for d in sensor_mod.SENSORS] + [
        d.key for d in binary_mod.BINARY_SENSORS
    ]
    check("no duplicate keys", len(keys) == len(set(keys)), f"{len(keys)} entities")

    print("\ntranslations cover every entity")
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    s_named = set(strings["entity"]["sensor"])
    b_named = set(strings["entity"]["binary_sensor"])
    missing_s = {d.translation_key for d in sensor_mod.SENSORS} - s_named
    missing_b = {d.translation_key for d in binary_mod.BINARY_SENSORS} - b_named
    check("all sensors named", not missing_s, str(missing_s or "none missing"))
    check("all binary sensors named", not missing_b, str(missing_b or "none missing"))

    status_opts = next(d for d in sensor_mod.SENSORS if d.key == "status").options
    declared = set(strings["entity"]["sensor"]["status"]["state"])
    check(
        "status options all translated",
        set(status_opts) <= declared,
        str(set(status_opts) - declared or "all covered"),
    )

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("All entity checks passed.")


if __name__ == "__main__":
    main()
