# /// script
# requires-python = ">=3.12"
# dependencies = ["anycubic-cloud-api==0.4.29", "aiohttp"]
# ///
"""Test whether the Anycubic cloud path works for an M7 Pro, without needing
Home Assistant. Uses the exact library the HA integration depends on.

It does three things:
  1. lists the printers on your account (raw cloud JSON)
  2. pulls full info for the M7 Pro (raw cloud JSON)
  3. reports which of the library's ~183 printer properties actually have
     values -- i.e. which HA entities would populate and which would be blank
  4. optionally connects MQTT and captures live messages

Token goes in a file, never on the command line (shell history).

    echo "<your token>" > secrets/token.txt
    uv run tools/cloud_test.py --mode slicer
    uv run tools/cloud_test.py --mode slicer --mqtt 120

Output is written to out/ as JSON for later diffing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from anycubic_cloud_api import AnycubicAuthMode, AnycubicMQTTAPI

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
MODES = {
    "web": AnycubicAuthMode.WEB,
    "slicer": AnycubicAuthMode.SLICER,
    "android": AnycubicAuthMode.ANDROID,
}

# Properties grouped so the report reads as "what would work", not 183 lines of
# noise. Anything not listed here lands in "other".
GROUPS: dict[str, tuple[str, ...]] = {
    "identity": (
        "id", "name", "model", "machine_type", "machine_name", "printer_type",
        "fw_version", "local_firmware_version", "machine_mac", "material_type",
    ),
    "state": (
        "device_status", "printer_online", "is_printing", "is_available",
        "is_busy", "ready_status", "current_status", "status", "reason",
        "latest_error_code", "latest_error_message",
    ),
    "job": (
        "latest_project_name", "latest_project_progress_percentage",
        "latest_project_print_status", "latest_project_print_in_progress",
        "latest_project_print_current_layer", "latest_project_print_total_layers",
        "latest_project_print_time_elapsed_minutes",
        "latest_project_print_time_remaining_minutes",
        "latest_project_print_approximate_completion_time",
        "latest_project_image_url",
    ),
    "resin-specific": (
        "latest_project_print_on_time", "latest_project_print_off_time",
        "latest_project_print_bottom_time", "latest_project_print_bottom_layers",
        "latest_project_print_anti_alias_count",
        "latest_project_print_model_height",
        "latest_project_print_z_up_height", "latest_project_print_z_up_speed",
        "latest_project_print_z_down_speed", "latest_project_z_thick",
        "supports_function_exposure_test", "supports_function_release_film",
        "supports_function_m7pro_automatic_operation",
        "supports_function_automatic_operation",
        "supports_function_residue_clean",
        "supports_function_lcd_peer_video",
        "supports_function_lcd_intelligent_materials_box",
    ),
    "light / camera": (
        "light_type", "has_controllable_light", "light_is_on",
        "light_brightness_pct", "has_peripheral_camera", "camera_stream_url",
        "video_taskid",
    ),
    "fdm-only (expect blank)": (
        "curr_nozzle_temp", "curr_hotbed_temp", "chamber_temperature",
        "target_chamber_temperature", "fan_speed_pct", "aux_fan_speed_pct",
        "has_peripheral_multi_color_box", "multi_color_box",
        "connected_ace_units", "primary_multi_color_box",
        "latest_project_target_nozzle_temp", "latest_project_target_hotbed_temp",
        "supports_function_multi_color_box", "supports_function_auto_leveler",
    ),
    "stats": (
        "total_print_time_hrs", "print_count", "material_used_kg",
        "create_time", "last_update_time",
    ),
}

def ts() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


def log(tag: str, msg: str) -> None:
    print(f"[{ts()}] {tag:<7} {msg}", flush=True)


def load_token(path: Path) -> str:
    if env := os.environ.get("ANYCUBIC_TOKEN"):
        log("AUTH", "using token from $ANYCUBIC_TOKEN")
        return env.strip()
    if not path.exists():
        sys.exit(
            f"No token found.\n"
            f"  Put it in {path}\n"
            f"  or set $ANYCUBIC_TOKEN.\n"
            f"See README section '2. Get an auth token'."
        )
    token = path.read_text(encoding="utf-8").strip().strip('"').strip("'")
    if not token:
        sys.exit(f"{path} is empty.")
    log("AUTH", f"using token from {path} ({len(token)} chars)")
    return token


def redact(obj: Any, token: str) -> Any:
    """Strip the token (and anything token-shaped) out of anything we save."""
    text = json.dumps(obj, default=str)
    if token:
        text = text.replace(token, "<REDACTED-TOKEN>")
    return json.loads(text)


def save(name: str, data: Any, token: str) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / name
    path.write_text(
        json.dumps(redact(data, token), indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    log("SAVE", f"{path.relative_to(ROOT)}")
    return path


def describe(value: Any) -> tuple[bool, str]:
    """Return (has_value, short rendering)."""
    if value is None:
        return False, "None"
    if isinstance(value, (list, dict, tuple, set)) and len(value) == 0:
        return False, f"empty {type(value).__name__}"
    if isinstance(value, str) and not value.strip():
        return False, "empty str"
    text = str(value)
    if len(text) > 70:
        text = text[:67] + "..."
    return True, text


def report_properties(printer: Any) -> dict[str, Any]:
    """Print a grouped live/blank report and return it as data."""
    grouped: dict[str, Any] = {}
    seen: set[str] = set()

    all_props = sorted(
        name
        for name in dir(type(printer))
        if isinstance(getattr(type(printer), name, None), property)
    )

    def read(name: str) -> tuple[bool, str]:
        try:
            return describe(getattr(printer, name))
        except Exception as exc:  # properties can raise when data is absent
            return False, f"<raises {type(exc).__name__}: {exc}>"

    for group, names in GROUPS.items():
        rows = []
        for name in names:
            if name not in all_props:
                rows.append((name, False, "<no such property>"))
                seen.add(name)
                continue
            ok, text = read(name)
            rows.append((name, ok, text))
            seen.add(name)
        grouped[group] = rows

    other = [n for n in all_props if n not in seen]
    grouped["other"] = [(n, *read(n)) for n in other]

    for group, rows in grouped.items():
        live = sum(1 for _, ok, _ in rows if ok)
        print(f"\n--- {group}  ({live}/{len(rows)} populated) ---", flush=True)
        for name, ok, text in rows:
            mark = "OK " if ok else "   "
            print(f"  {mark} {name:<52} {text}", flush=True)

    total = sum(len(r) for r in grouped.values())
    live = sum(1 for rows in grouped.values() for _, ok, _ in rows if ok)
    print(f"\n==> {live}/{total} properties populated", flush=True)

    return {
        group: {name: {"populated": ok, "value": text} for name, ok, text in rows}
        for group, rows in grouped.items()
    }


async def run(args: argparse.Namespace) -> None:
    token = load_token(ROOT / args.token_file)
    mode = MODES[args.mode]

    jar = aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(cookie_jar=jar) as session:
        api = AnycubicMQTTAPI(
            session=session,
            cookie_jar=jar,
            auth_token=token,
            auth_mode=mode,
            device_id=args.device_id,
            region=args.region,
            debug_logger=_DebugLogger() if args.verbose else None,
        )

        log("API", f"region={args.region} mode={args.mode}")

        # Populates user id / email / mobile on the auth object. MQTT derives
        # its client id from the email, so this has to happen before connecting
        # or connect_mqtt raises before it opens a socket.
        user = await api.get_user_info()
        ident = user.get("user_email") or user.get("mobile") or ""
        masked = f"{ident[:2]}***{ident[-8:]}" if ident else "<none>"
        log("API", f"account id={user.get('id')} contact={masked}")

        log("API", "listing printers on the account...")
        raw_list = await api.list_my_printers(raw_data=True)
        save("01-printer-list.json", raw_list, token)

        printers = raw_list.get("data") or []
        if not printers:
            sys.exit(
                "The account lists zero printers.\n"
                "  - Is the printer paired in the Anycubic app?\n"
                "  - Is the token from the same account?\n"
                "  - Wrong region? Try --region china."
            )

        print(flush=True)
        log("API", f"{len(printers)} printer(s) on the account:")
        for p in printers:
            print(
                f"         id={p.get('id')}  {p.get('name')!r}  "
                f"model={p.get('model')!r}  online={p.get('printer_online')}",
                flush=True,
            )

        target = _pick(printers, args.printer_id)
        printer_id = int(target["id"])
        print(flush=True)
        log("API", f"fetching full info for id={printer_id} ({target.get('name')!r})")

        raw_info = await api.printer_info_for_id(printer_id, raw_data=True)
        save("02-printer-info.json", raw_info, token)

        printer = await api.printer_info_for_id(printer_id, ignore_init_errors=True)
        if printer is None:
            sys.exit("The library could not parse this printer. See 02-printer-info.json.")
        if getattr(printer, "initialisation_error", None):
            log("WARN", f"initialisation_error: {printer.initialisation_error}")

        log("API", "supported function strings reported by the printer:")
        try:
            for fn in sorted(printer.supported_function_strings or []):
                print(f"         {fn}", flush=True)
        except Exception as exc:
            log("WARN", f"could not read supported_function_strings: {exc}")

        report = report_properties(printer)
        save("03-property-report.json", report, token)

        if args.mqtt:
            await _capture_mqtt(api, printer, args.mqtt, token)


def _pick(printers: list[dict], wanted: int | None) -> dict:
    if wanted is not None:
        for p in printers:
            if int(p.get("id", -1)) == wanted:
                return p
        sys.exit(f"No printer with id={wanted} on this account.")
    for p in printers:
        blob = f"{p.get('model', '')} {p.get('name', '')} {p.get('machine_name', '')}".lower()
        if "m7" in blob or "photon" in blob or "mono" in blob:
            return p
    log("WARN", "no obvious resin printer matched; using the first one")
    return printers[0]


async def _capture_mqtt(api: Any, printer: Any, seconds: int, token: str) -> None:
    print(flush=True)
    log("MQTT", f"connecting and capturing for {seconds}s...")
    log("MQTT", "(web-auth tokens are blocked from MQTT by Anycubic -- expect")
    log("MQTT", " this to fail unless you used a slicer token)")

    captured: list[dict[str, Any]] = []
    api.set_mqtt_log_all_messages(True)
    api.mqtt_add_subscribed_printer(printer)
    api.connect_mqtt()

    ok = await api.mqtt_wait_for_connect()
    if not ok:
        log("MQTT", "FAILED to connect")
        log("MQTT", "if you used a web token this is expected, not a bug")
        return

    log("MQTT", "connected -- watching")
    before = _snapshot(printer)
    await asyncio.sleep(seconds)

    after = _snapshot(printer)
    changed = {k: (before.get(k), v) for k, v in after.items() if before.get(k) != v}

    log("MQTT", f"{len(changed)} properties changed during capture")
    for name, (old, new) in sorted(changed.items()):
        print(f"         {name}: {old!r} -> {new!r}", flush=True)

    captured.append({"changed": {k: [str(a), str(b)] for k, (a, b) in changed.items()}})
    save("04-mqtt-changes.json", captured, token)

    api.disconnect_mqtt()
    await api.mqtt_wait_for_disconnect()
    log("MQTT", "disconnected")


def _snapshot(printer: Any) -> dict[str, str]:
    out = {}
    for name in dir(type(printer)):
        if not isinstance(getattr(type(printer), name, None), property):
            continue
        try:
            out[name] = str(getattr(printer, name))
        except Exception:
            pass
    return out


class _DebugLogger:
    def debug(self, msg: Any, *a: Any) -> None:
        print(f"         [debug] {msg}", flush=True)

    info = warning = error = debug


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--mode", choices=sorted(MODES), default="slicer",
        help="which kind of token you have (default: slicer)",
    )
    ap.add_argument(
        "--token-file", default="secrets/token.txt",
        help="path to the token file, relative to the project root",
    )
    ap.add_argument("--device-id", default=None, help="required for android tokens")
    ap.add_argument(
        "--region", default="international", choices=["international", "china"],
    )
    ap.add_argument("--printer-id", type=int, default=None, help="pick a printer by id")
    ap.add_argument(
        "--mqtt", type=int, metavar="SECONDS", default=0,
        help="after the report, connect MQTT and watch for this many seconds",
    )
    ap.add_argument("--verbose", action="store_true", help="log every API call")
    args = ap.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
