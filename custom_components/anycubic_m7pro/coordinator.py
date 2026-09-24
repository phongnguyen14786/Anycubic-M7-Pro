"""Polling coordinator for the Anycubic M7 Pro integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AnycubicAuthError, AnycubicCloud, AnycubicError, PrinterState
from .const import (
    CONF_PRINTER_ID,
    CONF_REGION,
    CONF_TOKEN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    REGION_INTERNATIONAL,
)

_LOGGER = logging.getLogger(__name__)


class AnycubicCoordinator(DataUpdateCoordinator[PrinterState]):
    """Fetches printer and job state on a fixed interval."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.printer_id: int = int(entry.data[CONF_PRINTER_ID])
        self.client = AnycubicCloud(
            session=async_get_clientsession(hass),
            token=entry.data[CONF_TOKEN],
            region=entry.data.get(CONF_REGION, REGION_INTERNATIONAL),
        )

    async def _async_update_data(self) -> PrinterState:
        try:
            return await self.client.async_poll(self.printer_id)
        except AnycubicAuthError as err:
            # Tokens expire (90 days for a web token), so this is expected
            # eventually. Raising ConfigEntryAuthFailed starts the reauth
            # flow instead of logging an error every minute forever.
            raise ConfigEntryAuthFailed(str(err)) from err
        except AnycubicError as err:
            raise UpdateFailed(str(err)) from err
