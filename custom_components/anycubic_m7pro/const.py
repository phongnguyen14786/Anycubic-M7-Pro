"""Constants for the Anycubic M7 Pro integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "anycubic_m7pro"

CONF_TOKEN: Final = "token"
CONF_PRINTER_ID: Final = "printer_id"
CONF_REGION: Final = "region"

REGION_INTERNATIONAL: Final = "international"
REGION_CHINA: Final = "china"
REGIONS: Final = [REGION_INTERNATIONAL, REGION_CHINA]

# Anycubic's cloud polls comfortably at a minute; the web token this
# integration uses is rate limited well above that and the printer's own state
# does not change faster than the cloud is told about it.
DEFAULT_SCAN_INTERVAL: Final = 60

# Print status codes, as the cloud reports them on a project.
PRINT_STATUS: Final[dict[int, str]] = {
    1: "printing",
    2: "complete",
    3: "cancelled",
    4: "downloading",
    5: "checking",
    6: "preheating",
    7: "slicing",
    9: "levelling",
}

# Statuses that mean a job is actively on the machine. "complete" and
# "cancelled" are terminal: the project stays the newest one long after it
# finished, so treating its mere presence as "printing" would leave the
# printer looking busy forever.
ACTIVE_PRINT_STATUSES: Final = frozenset({1, 4, 5, 6, 9})
