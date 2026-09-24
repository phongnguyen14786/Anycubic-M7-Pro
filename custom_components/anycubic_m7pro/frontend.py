"""Serve and register the Anycubic M7 Pro dashboard card.

Registration happens twice over, on purpose:

* `add_extra_js_url` injects the module into the frontend bootstrap. Cheap
  and works in both storage and YAML dashboard modes.
* A Lovelace **resource** entry is what HACS-installed cards use, and is what
  the card picker reliably picks up. Storage mode only -- a YAML dashboard
  declares its own resources and must not be written to.

Serving the file was never the problem in practice; getting the browser to
fetch it is, so belt and braces.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILENAME = "anycubic-m7pro-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"

_REGISTERED = f"{DOMAIN}_card_registered"


def _versioned_url(hass: HomeAssistant) -> str:
    """Append the integration version, so an update busts the browser cache.

    Without this a browser holds the previous card indefinitely: the URL is
    unchanged, and it was served with a 200 rather than a revalidation.
    """
    try:
        from homeassistant.loader import async_get_loaded_integration  # noqa: PLC0415

        version = async_get_loaded_integration(hass, DOMAIN).version
    except Exception:  # noqa: BLE001 - version is a nicety, never fatal
        version = None
    return f"{CARD_URL}?v={version}" if version else CARD_URL


async def _async_add_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Add (or re-point) the Lovelace resource entry. True if it is in place."""
    try:
        from homeassistant.components.lovelace import DOMAIN as LOVELACE_DOMAIN  # noqa: PLC0415
    except ImportError:
        return False

    data = hass.data.get(LOVELACE_DOMAIN)
    if data is None:
        return False

    # Older Home Assistant kept a plain dict here; newer uses a LovelaceData
    # dataclass. Both expose the same collection under the same name.
    resources = (
        data.get("resources") if isinstance(data, dict)
        else getattr(data, "resources", None)
    )
    if resources is None:
        return False

    # In YAML dashboard mode there is no store and resources come from
    # configuration.yaml. Writing there is not ours to do.
    if getattr(resources, "store", None) is None:
        _LOGGER.debug(
            "Dashboards are in YAML mode; add %s to your lovelace resources "
            "manually if the card does not appear",
            url,
        )
        return False

    try:
        if hasattr(resources, "async_get_info"):
            await resources.async_get_info()

        existing: list[dict[str, Any]] = list(resources.async_items() or [])
        ours = [
            item
            for item in existing
            if str(item.get("url") or "").split("?")[0] == CARD_URL
        ]

        # More than one resource for this same file -- usually one added by
        # hand alongside the one registered here. The browser treats each
        # query string as a separate module, so the file loads twice and the
        # card appears twice in the picker. Keep one.
        for extra in ours[1:]:
            try:
                await resources.async_delete_item(extra["id"])
                _LOGGER.info(
                    "Removed a duplicate Lovelace resource for %s (%s)",
                    CARD_URL,
                    extra.get("url"),
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not remove a duplicate resource: %s", err)

        if ours:
            current = str(ours[0].get("url") or "")
            if current == url:
                return True
            # Same card, stale version query: re-point it rather than
            # accumulating one dead resource per release.
            await resources.async_update_item(ours[0]["id"], {"url": url})
            _LOGGER.debug("Updated Lovelace resource to %s", url)
            return True

        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.info("Added Lovelace resource %s", url)
        return True
    except Exception as err:  # noqa: BLE001 - never fail setup over a card
        _LOGGER.warning("Could not add the Lovelace resource: %s", err)
        return False


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and make the frontend load it, once per run.

    Both registrations raise if repeated and every config entry setup calls
    this, so the guard matters even with a single printer -- reloading the
    entry would otherwise fail on the second pass.
    """
    if hass.data.get(_REGISTERED):
        return

    from homeassistant.components import frontend  # noqa: PLC0415

    path = Path(__file__).parent / "www" / CARD_FILENAME
    if not path.is_file():
        _LOGGER.warning("Dashboard card missing at %s; skipping", path)
        return

    url = _versioned_url(hass)

    try:
        # Cached deliberately. Without it the browser refetches the card on
        # every page load, and on a refresh -- when everything else comes
        # straight from cache -- the dashboard renders before the element is
        # defined and the card shows a configuration error. Opening a fresh
        # tab was slow enough to win the race, which is why it only failed on
        # refresh. The version query above is what busts the cache, the same
        # way HACS uses ?hacstag=.
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(path), cache_headers=True)]
        )
    except (RuntimeError, ValueError) as err:
        # A missing card costs the dashboard, not the integration, so the
        # entities stay up rather than setup failing outright.
        _LOGGER.warning("Could not serve the dashboard card: %s", err)
        return

    try:
        frontend.add_extra_js_url(hass, url)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("add_extra_js_url declined %s: %s", url, err)

    await _async_add_lovelace_resource(hass, url)

    hass.data[_REGISTERED] = True
    _LOGGER.info("Anycubic M7 Pro card registered at %s", url)
