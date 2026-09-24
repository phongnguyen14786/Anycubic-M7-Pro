"""Config flow for the Anycubic M7 Pro integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AnycubicAuthError, AnycubicCloud, AnycubicError
from .const import (
    CONF_PRINTER_ID,
    CONF_REGION,
    CONF_TOKEN,
    DOMAIN,
    REGION_CHINA,
    REGION_INTERNATIONAL,
)

_LOGGER = logging.getLogger(__name__)

_TOKEN_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, multiline=True)
)
_REGION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=REGION_INTERNATIONAL, label="International"),
            SelectOptionDict(value=REGION_CHINA, label="China"),
        ],
        mode=SelectSelectorMode.DROPDOWN,
    )
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): _TOKEN_SELECTOR,
        vol.Required(CONF_REGION, default=REGION_INTERNATIONAL): _REGION_SELECTOR,
    }
)


class AnycubicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setting up an Anycubic printer."""

    VERSION = 1

    def __init__(self) -> None:
        self._token: str = ""
        self._region: str = REGION_INTERNATIONAL
        self._printers: list[dict[str, Any]] = []

    async def _async_validate(self, token: str, region: str) -> str | None:
        """Check the token and load the printer list.

        Returns an error key, or None when everything worked.
        """
        client = AnycubicCloud(
            session=async_get_clientsession(self.hass),
            token=token,
            region=region,
        )
        try:
            await client.async_get_user_id()
            self._printers = await client.async_list_printers()
        except AnycubicAuthError:
            return "invalid_auth"
        except AnycubicError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001 - surfaced to the user as "unknown"
            _LOGGER.exception("Unexpected error validating Anycubic token")
            return "unknown"

        if not self._printers:
            return "no_printers"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Pasting from a browser console tends to bring quotes and
            # newlines along with the token.
            self._token = user_input[CONF_TOKEN].strip().strip('"').strip("'")
            self._region = user_input[CONF_REGION]

            error = await self._async_validate(self._token, self._region)
            if error is None:
                return await self.async_step_printer()
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_printer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which printer to add, skipping the step when there is one."""
        if len(self._printers) == 1:
            return await self._async_create(self._printers[0])

        if user_input is not None:
            chosen = next(
                p
                for p in self._printers
                if str(p.get("id")) == user_input[CONF_PRINTER_ID]
            )
            return await self._async_create(chosen)

        schema = vol.Schema(
            {
                vol.Required(CONF_PRINTER_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=str(p.get("id")),
                                label=f"{p.get('name')} ({p.get('model')})",
                            )
                            for p in self._printers
                        ],
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="printer", data_schema=schema)

    async def _async_create(self, printer: dict[str, Any]) -> ConfigFlowResult:
        printer_id = str(printer.get("id"))
        await self.async_set_unique_id(printer_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=printer.get("name") or "Anycubic Photon Mono M7 Pro",
            data={
                CONF_TOKEN: self._token,
                CONF_REGION: self._region,
                CONF_PRINTER_ID: int(printer_id),
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Triggered when the token expires."""
        self._region = entry_data.get(CONF_REGION, REGION_INTERNATIONAL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a fresh token for an existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip().strip('"').strip("'")
            error = await self._async_validate(token, self._region)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _TOKEN_SELECTOR}),
            description_placeholders={"name": entry.title},
            errors=errors,
        )
