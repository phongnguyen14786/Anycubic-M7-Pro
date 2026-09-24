"""Sensors for the Anycubic M7 Pro integration."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AnycubicConfigEntry
from .api import PrinterState
from .const import ACTIVE_PRINT_STATUSES, PRINT_STATUS
from .entity import AnycubicEntity

_DURATION_RE = re.compile(
    r"(?:(?P<day>\d+)day)?(?:(?P<hour>\d+)hour)?(?:(?P<min>\d+)min)?"
    r"(?:(?P<sec>\d+)s(?:ec)?)?",
    re.IGNORECASE,
)


def _parse_duration_minutes(value: Any) -> float | None:
    """Turn Anycubic's "3hour37min" into minutes.

    The cloud composes this string from whichever units are non-zero, so
    "45min", "2day3hour" and "1hour11min" are all valid shapes.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION_RE.fullmatch(str(value).strip().replace(" ", ""))
    if not match or not any(match.groupdict().values()):
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return (
        parts["day"] * 1440
        + parts["hour"] * 60
        + parts["min"]
        + parts["sec"] / 60
    )


def _parse_number(value: Any) -> float | None:
    """Pull a number out of values like "205.19ml" or a plain float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def _is_active(state: PrinterState) -> bool:
    """Whether the newest job is actually on the machine right now."""
    status = state.job.get("print_status")
    return status is not None and int(status) in ACTIVE_PRINT_STATUSES


def _job_only(fn: Callable[[PrinterState], Any]) -> Callable[[PrinterState], Any]:
    """Report a job value only while a job is running.

    Without this the last finished print's numbers would sit in the UI
    indefinitely, which reads as though the printer were still working.
    """

    def _wrapped(state: PrinterState) -> Any:
        return fn(state) if _is_active(state) else None

    return _wrapped


def _finish_time(state: PrinterState) -> datetime | None:
    remaining = _parse_number(state.job.get("remain_time"))
    if not _is_active(state) or remaining is None:
        return None
    return dt_util.utcnow() + timedelta(minutes=remaining)


def _epoch(value: Any, *, milliseconds: bool = False) -> datetime | None:
    """Turn an Anycubic epoch into an aware datetime.

    The cloud mixes units: `last_update_time` is milliseconds while
    `start_time` is seconds, so the caller says which.
    """
    number = _parse_number(value)
    if not number:  # 0 means "never", not 1970
        return None
    if milliseconds:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _resin_profile(state: PrinterState) -> str | None:
    """Name of the resin profile the job was sliced with.

    `material_name` is localised by the slicer, so it can come back in
    Chinese. `active_resins` holds the untranslated profile string and is a
    better answer when it is present.
    """
    resins = state.slice_param.get("active_resins")
    if isinstance(resins, list) and resins:
        # Shaped "Vendor@Profile@Machine@Quality"; the profile reads best.
        parts = str(resins[0]).split("@")
        if len(parts) >= 2 and parts[1].strip():
            return parts[1].strip()
    name = state.slice_param.get("material_name")
    return str(name) if name else None


def _status(state: PrinterState) -> str:
    if not _is_active(state):
        return "idle"
    status = state.job.get("print_status")
    return PRINT_STATUS.get(int(status), "unknown") if status is not None else "unknown"


@dataclass(frozen=True, kw_only=True)
class AnycubicSensorDescription(SensorEntityDescription):
    """Describes an Anycubic sensor."""

    value_fn: Callable[[PrinterState], Any]


SENSORS: tuple[AnycubicSensorDescription, ...] = (
    # --- printer ---------------------------------------------------------
    AnycubicSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", *PRINT_STATUS.values(), "unknown"],
        value_fn=_status,
    ),
    AnycubicSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.base.get("firmware_version"),
    ),
    AnycubicSensorDescription(
        key="total_prints",
        translation_key="total_prints",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda s: s.base.get("print_count"),
    ),
    AnycubicSensorDescription(
        key="total_print_time",
        translation_key="total_print_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda s: _parse_duration_minutes(s.base.get("print_totaltime")),
    ),
    AnycubicSensorDescription(
        key="total_resin_used",
        translation_key="total_resin_used",
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda s: _parse_number(s.base.get("material_used")),
    ),
    AnycubicSensorDescription(
        key="release_film_layers",
        translation_key="release_film_layers",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: (s.printer.get("releaseFilm") or {}).get("layers"),
    ),
    AnycubicSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        native_unit_of_measurement="%",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.job.get("signal_strength"),
    ),
    AnycubicSensorDescription(
        key="printer_state",
        translation_key="printer_state",
        # Free text from the cloud ("free" when idle). Not an ENUM device
        # class, because the full set of words it can return is unknown and
        # an unlisted option would log an error on every poll.
        value_fn=lambda s: s.status.get("reason") or None,
    ),
    AnycubicSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: _epoch(
            s.status.get("last_update_time"), milliseconds=True
        ),
    ),
    AnycubicSensorDescription(
        key="latest_firmware_version",
        translation_key="latest_firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.version.get("target_version"),
    ),
    # --- current job -----------------------------------------------------
    AnycubicSensorDescription(
        key="job_name",
        translation_key="job_name",
        value_fn=_job_only(
            lambda s: s.job.get("gcode_name") or s.message.get("filename")
        ),
    ),
    AnycubicSensorDescription(
        key="progress",
        translation_key="progress",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_job_only(
            lambda s: s.message.get("progress", s.job.get("progress"))
        ),
    ),
    AnycubicSensorDescription(
        key="current_layer",
        translation_key="current_layer",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_job_only(lambda s: s.message.get("curr_layer")),
    ),
    AnycubicSensorDescription(
        key="total_layers",
        translation_key="total_layers",
        value_fn=_job_only(lambda s: s.message.get("total_layers")),
    ),
    AnycubicSensorDescription(
        key="time_elapsed",
        translation_key="time_elapsed",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        suggested_display_precision=0,
        value_fn=_job_only(
            lambda s: _parse_number(
                s.message.get("print_time", s.job.get("print_time"))
            )
        ),
    ),
    AnycubicSensorDescription(
        key="time_remaining",
        translation_key="time_remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        suggested_display_precision=0,
        value_fn=_job_only(lambda s: _parse_number(s.job.get("remain_time"))),
    ),
    AnycubicSensorDescription(
        key="estimated_finish",
        translation_key="estimated_finish",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_finish_time,
    ),
    AnycubicSensorDescription(
        key="job_started",
        translation_key="job_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_job_only(lambda s: _epoch(s.job.get("start_time"))),
    ),
    AnycubicSensorDescription(
        key="estimated_duration",
        translation_key="estimated_duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        suggested_display_precision=0,
        # The slicer's original estimate, in seconds, for comparison against
        # how long the job actually takes.
        value_fn=_job_only(
            lambda s: (
                None
                if (est := _parse_number(s.job.get("estimate"))) is None
                else est / 60
            )
        ),
    ),
    AnycubicSensorDescription(
        key="resin_profile",
        translation_key="resin_profile",
        value_fn=_job_only(_resin_profile),
    ),
    AnycubicSensorDescription(
        key="job_resin_used",
        translation_key="job_resin_used",
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        suggested_display_precision=1,
        value_fn=_job_only(
            lambda s: _parse_number(
                s.message.get("supplies_usage", s.job.get("material"))
            )
        ),
    ),
    AnycubicSensorDescription(
        key="model_height",
        translation_key="model_height",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        suggested_display_precision=2,
        value_fn=_job_only(lambda s: _parse_number(s.message.get("model_hight"))),
    ),
    # --- resin settings for the running job ------------------------------
    AnycubicSensorDescription(
        key="layer_height",
        translation_key="layer_height",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=3,
        value_fn=_job_only(lambda s: _parse_number(s.message.get("z_thick"))),
    ),
    AnycubicSensorDescription(
        key="exposure_time",
        translation_key="exposure_time",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=2,
        value_fn=_job_only(lambda s: _parse_number(s.settings.get("on_time"))),
    ),
    AnycubicSensorDescription(
        key="off_time",
        translation_key="off_time",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=_job_only(lambda s: _parse_number(s.settings.get("off_time"))),
    ),
    AnycubicSensorDescription(
        key="bottom_exposure_time",
        translation_key="bottom_exposure_time",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=2,
        value_fn=_job_only(lambda s: _parse_number(s.settings.get("bottom_time"))),
    ),
    AnycubicSensorDescription(
        key="bottom_layers",
        translation_key="bottom_layers",
        value_fn=_job_only(lambda s: s.settings.get("bottom_layers")),
    ),
    AnycubicSensorDescription(
        key="anti_aliasing",
        translation_key="anti_aliasing",
        entity_registry_enabled_default=False,
        value_fn=_job_only(lambda s: s.message.get("anti_count")),
    ),
    AnycubicSensorDescription(
        key="lift_height",
        translation_key="lift_height",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_job_only(lambda s: _parse_number(s.settings.get("z_up_height"))),
    ),
    AnycubicSensorDescription(
        key="lift_speed",
        translation_key="lift_speed",
        native_unit_of_measurement="mm/s",
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_job_only(lambda s: _parse_number(s.settings.get("z_up_speed"))),
    ),
    AnycubicSensorDescription(
        key="retract_speed",
        translation_key="retract_speed",
        native_unit_of_measurement="mm/s",
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_job_only(lambda s: _parse_number(s.settings.get("z_down_speed"))),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnycubicConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        AnycubicSensor(coordinator, description) for description in SENSORS
    )


class AnycubicSensor(AnycubicEntity, SensorEntity):
    """A single value read from the Anycubic cloud."""

    entity_description: AnycubicSensorDescription

    def __init__(self, coordinator, description: AnycubicSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)
