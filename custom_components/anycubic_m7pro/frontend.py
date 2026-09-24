"""Serve and register the Anycubic M7 Pro dashboard card.

Registering the card here means there is nothing for the user to install
separately and no Lovelace resource to add by hand. `add_extra_js_url` loads
it on every dashboard, which is how a storage-mode dashboard picks up a card
without the user editing resources.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILENAME = "anycubic-m7pro-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"

_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and add it to the frontend, once per Home Assistant run.

    Both steps raise if repeated, and every config entry setup calls this, so
    the guard matters even with a single printer -- reloading the entry would
    otherwise fail on the second registration.
    """
    if hass.data.get(_REGISTERED):
        return

    # Imported lazily: it pulls in the frontend component, which need not be
    # loaded before this point.
    from homeassistant.components import frontend  # noqa: PLC0415

    path = Path(__file__).parent / "www" / CARD_FILENAME
    if not path.is_file():
        _LOGGER.warning("Dashboard card missing at %s; skipping", path)
        return

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(path), cache_headers=False)]
        )
        frontend.add_extra_js_url(hass, CARD_URL)
    except (RuntimeError, ValueError) as err:
        # A missing card costs the dashboard, not the integration, so the
        # entities stay up rather than the setup failing outright.
        _LOGGER.warning("Could not register the dashboard card: %s", err)
        return

    hass.data[_REGISTERED] = True
    _LOGGER.debug("Registered %s at %s", CARD_FILENAME, CARD_URL)
