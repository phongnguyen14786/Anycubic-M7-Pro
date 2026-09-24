"""Job thumbnail for the Anycubic M7 Pro integration."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AnycubicConfigEntry
from .const import ACTIVE_PRINT_STATUSES
from .coordinator import AnycubicCoordinator
from .entity import AnycubicEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnycubicConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the job thumbnail."""
    async_add_entities([AnycubicJobThumbnail(entry.runtime_data)])


class AnycubicJobThumbnail(AnycubicEntity, ImageEntity):
    """The sliced model preview Anycubic renders for the running job."""

    _attr_translation_key = "job_thumbnail"

    def __init__(self, coordinator: AnycubicCoordinator) -> None:
        AnycubicEntity.__init__(self, coordinator, "job_thumbnail")
        ImageEntity.__init__(self, coordinator.hass)
        self._attr_image_url = self._current_url()
        self._attr_image_last_updated = (
            dt_util.utcnow() if self._attr_image_url else None
        )

    def _current_url(self) -> str | None:
        """Thumbnail for the running job, or None when idle.

        Gated the same way the job sensors are: the finished job stays the
        newest project indefinitely, so an ungated thumbnail would leave the
        last print on the dashboard forever.
        """
        state = self.coordinator.data
        status = state.job.get("print_status")
        if status is None or int(status) not in ACTIVE_PRINT_STATUSES:
            return None
        url = state.job.get("img") or state.job.get("image_id")
        return str(url) if url else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the cached image only when the URL actually changes.

        ImageEntity caches against `image_last_updated`; bumping it on every
        poll would re-download the same picture once a minute.
        """
        url = self._current_url()
        if url != self._attr_image_url:
            self._attr_image_url = url
            self._cached_image = None
            self._attr_image_last_updated = dt_util.utcnow() if url else None
        super()._handle_coordinator_update()
