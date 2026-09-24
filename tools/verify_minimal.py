# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp"]
# ///
"""Prove the from-scratch Anycubic cloud client works, using nothing but
aiohttp (which Home Assistant already ships).

No anycubic-cloud-api, no third-party package. If this prints your printer's
status, the integration built on the same code will too.

    uv run tools/verify_minimal.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent

# Observed from Anycubic's own web client. These identify the "app" making the
# request; they are not secret and not account-specific.
APP_ID = "f9b3528877c94d5c9c5af32245db46ef"
APP_SECRET = "0cf75926606049a3937f56b0373b99fb"
APP_VERSION = "1.0.0"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

BASE = "https://cloud-universe.anycubic.com/p/p/workbench/api"
ORIGIN = "https://uc.makeronline.com"


def auth_headers(token: str) -> dict[str, str]:
    """Every request carries a signed, timestamped header set.

    The signature is an md5 over app id, timestamp, version, secret and nonce,
    with the app id repeated at the end -- that repetition is not a mistake,
    it is what the server checks against.
    """
    nonce = str(uuid.uuid1())
    timestamp = int(time.time() * 1e3)
    sig_input = f"{APP_ID}{timestamp}{APP_VERSION}{APP_SECRET}{nonce}{APP_ID}"
    return {
        "Xx-Device-Type": "web",
        "Xx-Is-Cn": "1",
        "Xx-Nonce": nonce,
        "Xx-Signature": hashlib.md5(sig_input.encode()).hexdigest(),
        "Xx-Timestamp": str(timestamp),
        "Xx-Version": APP_VERSION,
        "Content-Type": "application/json",
        "XX-Token": token,
        "XX-LANGUAGE": "US",
        "User-Agent": USER_AGENT,
        "Origin": ORIGIN,
    }


async def get(session: aiohttp.ClientSession, token: str, path: str, **params):
    async with session.get(
        f"{BASE}{path}", params=params or None, headers=auth_headers(token)
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def main() -> None:
    token_file = ROOT / "secrets" / "token.txt"
    if not token_file.exists():
        sys.exit(f"No token at {token_file}")
    token = token_file.read_text(encoding="utf-8").strip()

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        user = await get(session, token, "/user/profile/userInfo")
        udata = user.get("data") or {}
        if not udata.get("id"):
            sys.exit(f"Token rejected. Server said: {json.dumps(user)[:300]}")
        print(f"account   id={udata['id']}")

        listing = await get(session, token, "/work/printer/getPrinters")
        printers = listing.get("data") or []
        print(f"printers  {len(printers)} on the account")
        if not printers:
            sys.exit("No printers returned.")

        for p in printers:
            print(f"          id={p.get('id')}  {p.get('name')}")

        pid = printers[0]["id"]
        info = await get(session, token, "/v2/printer/info", id=str(pid))
        d = info.get("data") or {}
        base = d.get("base") or {}
        md = d.get("machine_data") or {}
        film = d.get("releaseFilm") or {}

        print()
        print("--- what the integration will show ---")
        for label, value in [
            ("name", d.get("name")),
            ("model", d.get("model")),
            ("firmware", base.get("firmware_version")),
            ("update available", "yes" if d.get("need_update") else "no"),
            ("material", base.get("material_type")),
            ("device_status", d.get("device_status")),
            ("is_printing code", d.get("is_printing")),
            ("total prints", base.get("print_count")),
            ("total print time", base.get("print_totaltime")),
            ("material used", base.get("material_used")),
            ("release film layers", film.get("layers")),
            ("mac", base.get("machine_mac")),
            ("serial", base.get("description")),
            ("build volume", f"{md.get('size_x')} x {md.get('size_y')} x {md.get('size_z')} mm"),
            ("resolution", f"{md.get('res_x')} x {md.get('res_y')}"),
        ]:
            print(f"  {label:<22} {value}")

        print()
        print("OK -- zero-dependency client works.")


if __name__ == "__main__":
    asyncio.run(main())
