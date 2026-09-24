"""Minimal Anycubic cloud client.

Deliberately dependency-free beyond aiohttp, which Home Assistant already
ships. Everything here is plain HTTP against Anycubic's public cloud using the
account token the user supplies; nothing is installed from PyPI at setup time.

Only read endpoints are implemented. This integration does not send orders to
the printer, so there is no code path here that can start, stop or alter a
print.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import REGION_CHINA

_LOGGER = logging.getLogger(__name__)

# Identifies the client making the request. Taken from Anycubic's own web
# app -- these are not secrets and not tied to any account; the server checks
# that the signature below was built with them.
APP_ID = "f9b3528877c94d5c9c5af32245db46ef"
APP_SECRET = "0cf75926606049a3937f56b0373b99fb"
APP_VERSION = "1.0.0"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_ENDPOINTS = {
    "international": (
        "https://cloud-universe.anycubic.com/p/p/workbench/api",
        "https://uc.makeronline.com",
    ),
    # Reported by the community, not verified here -- nobody testing this has
    # a China account. If it is wrong the symptom is an empty printer list,
    # which the config flow reports as such rather than failing obscurely.
    REGION_CHINA: (
        "https://cloud-platform.anycubicloud.com/p/p/workbench/api",
        "https://uc.makeronline.cn",
    ),
}

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class AnycubicError(Exception):
    """Any failure talking to the Anycubic cloud."""


class AnycubicAuthError(AnycubicError):
    """The token was rejected. It is invalid, or it has expired."""


@dataclass(slots=True)
class PrinterState:
    """Everything one poll produced, already flattened.

    Every field defaults to an empty dict rather than None, so consumers can
    index freely without guarding for a printer that has never printed.
    """

    printer: dict[str, Any]
    status: dict[str, Any] = field(default_factory=dict)
    job: dict[str, Any] = field(default_factory=dict)
    message: dict[str, Any] = field(default_factory=dict)

    @property
    def base(self) -> dict[str, Any]:
        return self.printer.get("base") or {}

    @property
    def machine_data(self) -> dict[str, Any]:
        return self.printer.get("machine_data") or {}

    @property
    def version(self) -> dict[str, Any]:
        return self.printer.get("version") or {}

    @property
    def settings(self) -> dict[str, Any]:
        """Resin exposure/lift settings for the current job."""
        return self.message.get("settings") or {}

    @property
    def slice_param(self) -> dict[str, Any]:
        """Slicer settings for the current job, including the resin profile."""
        return _maybe_json(self.job.get("slice_param"))


def _maybe_json(value: Any) -> dict[str, Any]:
    """Some fields arrive as a JSON string, others already decoded."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except ValueError:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


class AnycubicCloud:
    """Read-only client for one Anycubic account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        region: str = "international",
    ) -> None:
        self._session = session
        self._token = token
        self._base, self._origin = _ENDPOINTS.get(
            region, _ENDPOINTS["international"]
        )

    def _headers(self) -> dict[str, str]:
        """Build the signed header set every request needs.

        The signature repeats the app id at both ends of the input. That is
        not a typo -- it is what the server recomputes and compares against.
        """
        nonce = str(uuid.uuid1())
        timestamp = int(time.time() * 1e3)
        raw = f"{APP_ID}{timestamp}{APP_VERSION}{APP_SECRET}{nonce}{APP_ID}"
        return {
            "Xx-Device-Type": "web",
            "Xx-Is-Cn": "1",
            "Xx-Nonce": nonce,
            "Xx-Signature": hashlib.md5(raw.encode()).hexdigest(),
            "Xx-Timestamp": str(timestamp),
            "Xx-Version": APP_VERSION,
            "Content-Type": "application/json",
            "XX-Token": self._token,
            "XX-LANGUAGE": "US",
            "User-Agent": USER_AGENT,
            "Origin": self._origin,
        }

    async def _get(self, path: str, **params: str) -> dict[str, Any]:
        url = f"{self._base}{path}"
        try:
            async with self._session.get(
                url,
                params=params or None,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise AnycubicAuthError(f"Token rejected ({resp.status})")
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except AnycubicAuthError:
            raise
        except aiohttp.ClientError as err:
            raise AnycubicError(f"Request to {path} failed: {err}") from err

        if not isinstance(payload, dict):
            raise AnycubicError(f"Unexpected response from {path}")

        # The cloud reports its own errors in the body with HTTP 200. An
        # expired token comes back this way, so it has to be caught here
        # rather than from the status code.
        message = str(payload.get("msg") or "")
        if payload.get("data") is None and message:
            if "token" in message.lower() or "login" in message.lower():
                raise AnycubicAuthError(message)

        return payload

    async def async_get_user_id(self) -> int:
        """Validate the token and return the account id."""
        payload = await self._get("/user/profile/userInfo")
        data = payload.get("data") or {}
        user_id = data.get("id")
        if not user_id:
            # A rejected token still returns a data object on this endpoint,
            # just without an id, so an absent id is the auth signal.
            raise AnycubicAuthError(
                payload.get("msg") or "Token rejected by Anycubic"
            )
        return int(user_id)

    async def async_list_printers(self) -> list[dict[str, Any]]:
        payload = await self._get("/work/printer/getPrinters")
        return list(payload.get("data") or [])

    async def async_get_printer(self, printer_id: int) -> dict[str, Any]:
        payload = await self._get("/v2/printer/info", id=str(printer_id))
        data = payload.get("data")
        if not data:
            raise AnycubicError(f"No printer with id {printer_id}")
        return dict(data)

    async def async_get_status(self, printer_id: int) -> dict[str, Any]:
        """Live status list, which carries fields /v2/printer/info omits.

        Notably `last_update_time` (when the printer last reached the cloud)
        and `reason` (a human-readable state word).
        """
        payload = await self._get("/work/printer/printersStatus")
        for entry in payload.get("data") or []:
            if int(entry.get("id") or 0) == printer_id:
                return dict(entry)
        return {}

    async def async_get_latest_job(self, printer_id: int) -> dict[str, Any]:
        """Newest project for this printer, or {} if it has never printed.

        Projects come back newest first, so the first match wins. The list is
        account-wide, hence the printer_id filter.
        """
        payload = await self._get(
            "/work/project/getProjects", page="1", limit="20"
        )
        for project in payload.get("data") or []:
            if int(project.get("printer_id") or 0) == printer_id:
                return dict(project)
        return {}

    async def async_poll(self, printer_id: int) -> PrinterState:
        """One full refresh: printer, live status, and most recent job."""
        printer = await self.async_get_printer(printer_id)

        # The printer's own record is the only required call. The other two
        # add detail, so a failure there degrades the update rather than
        # failing it -- a printer reporting its state is worth showing even
        # when the project list is briefly unavailable.
        try:
            status = await self.async_get_status(printer_id)
            if not status:
                # The call succeeded but this printer was not in the list.
                # Silence here would leave "last seen" and "printer state"
                # blank with nothing anywhere to explain why.
                _LOGGER.warning(
                    "Printer %s was not in the printersStatus response; "
                    "'last seen' and 'printer state' will be blank",
                    printer_id,
                )
        except AnycubicError as err:
            _LOGGER.warning(
                "Could not fetch printer status (%s); 'last seen' and "
                "'printer state' will be blank this poll",
                err,
            )
            status = {}

        try:
            job = await self.async_get_latest_job(printer_id)
        except AnycubicError as err:
            _LOGGER.warning(
                "Could not fetch the latest job (%s); job fields will be "
                "blank this poll",
                err,
            )
            job = {}

        return PrinterState(
            printer=printer,
            status=status,
            job=job,
            message=_maybe_json(job.get("device_message")),
        )
