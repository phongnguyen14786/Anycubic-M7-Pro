"""Binary sensors for the Anycubic M7 Pro integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AnycubicConfigEntry
from .api import PrinterState
from .const import ACTIVE_PRINT_STATUSES
from .entity import AnycubicEntity


def _is_printing(state: PrinterState) -> bool:
    status = state.job.get("print_status")
    return status is not None and int(status) in ACTIVE_PRINT_STATUSES


def _has_error(state: PrinterState) -> bool:
    """An error message on the running job.

    `reason` is 200 on a healthy job, so it is not a usable error flag on its
    own -- only a non-empty message is.
    """
    return bool(str(state.message.get("err_message") or "").strip())


@dataclass(frozen=True, kw_only=True)
class AnycubicBinarySensorDescription(BinarySensorEntityDescription):
    """Describes an Anycubic binary sensor."""

    value_fn: Callable[[PrinterState], bool]


BINARY_SENSORS: tuple[AnycubicBinarySensorDescription, ...] = (
    AnycubicBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        # device_status is 1 when the cloud has a live connection to the
        # printer. It is the only online signal a polled web token gets.
        value_fn=lambda s: int(s.printer.get("device_status") or 0) == 1,
    ),
    AnycubicBinarySensorDescription(
        key="printing",
        translation_key="printing",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_is_printing,
    ),
    AnycubicBinarySensorDescription(
        key="problem",
        translation_key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_has_error,
    ),
    AnycubicBinarySensorDescription(
        key="firmware_update_available",
        translation_key="firmware_update_available",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: bool(s.printer.get("need_update")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnycubicConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        AnycubicBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class AnycubicBinarySensor(AnycubicEntity, BinarySensorEntity):
    """A single on/off state read from the Anycubic cloud."""

    entity_description: AnycubicBinarySensorDescription

    def __init__(
        self, coordinator, description: AnycubicBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data)
