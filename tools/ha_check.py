# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp"]
# ///
"""Inspect the live Home Assistant instance: entities, resources, log.

Needs a long-lived access token in secrets/ha_token.txt (gitignored). The
token is never printed and never written to out/.

    uv run tools/ha_check.py
    uv run tools/ha_check.py --log        # just the anycubic log lines
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://homeassistant.local:8123"
DOMAIN_HINT = "m7_pro"


def load_token() -> str:
    path = ROOT / "secrets" / "ha_token.txt"
    if not path.exists():
        sys.exit(
            f"No token at {path}.\n"
            "Profile -> Security -> Long-lived access tokens -> Create token."
        )
    return path.read_text(encoding="utf-8").strip()


async def rest(session: aiohttp.ClientSession, token: str, path: str) -> Any:
    async with session.get(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        if resp.status != 200:
            return {"_error": resp.status, "_text": (await resp.text())[:200]}
        if "json" in (resp.headers.get("Content-Type") or ""):
            return await resp.json()
        return await resp.text()


async def ws_resources(token: str) -> Any:
    """Lovelace resources are only exposed over the websocket API."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{BASE}/api/websocket", timeout=30) as ws:
            await ws.receive_json()  # auth_required
            await ws.send_json({"type": "auth", "access_token": token})
            auth = await ws.receive_json()
            if auth.get("type") != "auth_ok":
                return {"_error": auth}
            await ws.send_json({"id": 1, "type": "lovelace/resources"})
            while True:
                msg = await ws.receive_json()
                if msg.get("id") == 1:
                    return msg.get("result")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true", help="only show log lines")
    args = ap.parse_args()

    token = load_token()

    async with aiohttp.ClientSession() as session:
        if not args.log:
            print("=== our entities ===")
            states = await rest(session, token, "/api/states")
            if isinstance(states, dict) and "_error" in states:
                sys.exit(f"Auth failed: {states}")

            ours = sorted(
                (s for s in states if DOMAIN_HINT in s["entity_id"]),
                key=lambda s: s["entity_id"],
            )
            if not ours:
                print("  none found")
            for s in ours:
                state = s["state"]
                flag = "   " if state not in ("unknown", "unavailable") else ">> "
                print(f"  {flag}{s['entity_id']:<58} {state}")

            print("\n=== lovelace resources ===")
            for r in await ws_resources(token) or []:
                print(f"  {r.get('type'):<8} {r.get('url')}")

        print("\n=== error log (anycubic lines) ===")
        log = await rest(session, token, "/api/error_log")
        if isinstance(log, str):
            hits = [
                line for line in log.splitlines()
                if "anycubic" in line.lower() or "m7pro" in line.lower()
            ]
            if hits:
                for line in hits[-40:]:
                    print(f"  {line}")
            else:
                print("  no anycubic lines in the log")

            print("\n=== recent WARNING/ERROR lines ===")
            bad = [
                line for line in log.splitlines()
                if " ERROR " in line or " WARNING " in line
            ]
            for line in bad[-15:]:
                print(f"  {line[:190]}")
        else:
            print(f"  could not read log: {log}")


if __name__ == "__main__":
    asyncio.run(main())
