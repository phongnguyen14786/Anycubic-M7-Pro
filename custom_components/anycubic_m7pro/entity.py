"""Shared entity base for the Anycubic M7 Pro integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AnycubicCoordinator


class AnycubicEntity(CoordinatorEntity[AnycubicCoordinator]):
    """Base entity tying everything to the one printer device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AnycubicCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.printer_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        state = self.coordinator.data
        base = state.base
        return DeviceInfo(
            identifiers={(DOMAIN, str(self.coordinator.printer_id))},
            name=state.printer.get("name") or "Anycubic Photon Mono M7 Pro",
            manufacturer="Anycubic",
            model=state.printer.get("model") or "Photon Mono M7 Pro",
            sw_version=base.get("firmware_version"),
            serial_number=base.get("description"),
            configuration_url="https://cloud-universe.anycubic.com/file",
        )
