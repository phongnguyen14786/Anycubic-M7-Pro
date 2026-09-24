# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp"]
# ///
"""Sweep every read-only Anycubic cloud endpoint and report every field.

Read-only by construction: the mutating endpoints (sendOrder, firmware
update, file delete, upload, storage lock) are deliberately absent from the
table below, so there is no code path here that can change anything on the
printer or the account.

Output:
  * out/probe/<name>.json   raw response per endpoint
  * a flattened field report, marking which fields the integration already
    exposes and which are going unused

    uv run tools/probe_cloud.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "anycubic_m7pro"
OUT = ROOT / "out" / "probe"


def _load_api() -> types.ModuleType:
    pkg_name = "_anycubic_probe"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(COMPONENT)]
    sys.modules[pkg_name] = pkg
    for name in ("const", "api"):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{name}", COMPONENT / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{pkg_name}.api"]


anycubic_api = _load_api()

# (label, method, path, needs) -- needs is "printer", "project" or None.
ENDPOINTS: list[tuple[str, str, str, str | None]] = [
    ("user-info", "GET", "/user/profile/userInfo", None),
    ("user-store", "POST", "/work/index/getUserStore", None),
    ("printers", "GET", "/work/printer/getPrinters", None),
    ("printers-status", "GET", "/work/printer/printersStatus", None),
    ("printer-all", "GET", "/v2/printer/all", None),
    ("printer-info", "GET", "/v2/printer/info", "printer"),
    ("printer-status", "GET", "/v2/Printer/status", "printer"),
    ("printer-tool", "GET", "/v2/printer/tool", "printer"),
    ("printer-functions", "GET", "/v2/printer/functions", "printer"),
    ("projects", "GET", "/work/project/getProjects", None),
    ("print-history", "GET", "/v2/project/printHistory", None),
    ("project-info", "GET", "/v2/project/info", "project"),
    ("project-monitor", "GET", "/v2/project/monitor", "project"),
    ("cloud-files", "POST", "/work/index/files", None),
]

# Every field the integration currently reads, as dotted paths into the
# printer-info and projects payloads.
IN_USE = {
    "printer-info:name",
    "printer-info:model",
    "printer-info:device_status",
    "printer-info:need_update",
    "printer-info:base.firmware_version",
    "printer-info:base.description",
    "printer-info:base.print_count",
    "printer-info:base.print_totaltime",
    "printer-info:base.material_used",
    "printer-info:releaseFilm.layers",
    "projects:print_status",
    "projects:gcode_name",
    "projects:remain_time",
    "projects:print_time",
    "projects:progress",
    "projects:material",
    "projects:signal_strength",
    "projects:device_message.progress",
    "projects:device_message.curr_layer",
    "projects:device_message.total_layers",
    "projects:device_message.print_time",
    "projects:device_message.filename",
    "projects:device_message.supplies_usage",
    "projects:device_message.model_hight",
    "projects:device_message.z_thick",
    "projects:device_message.anti_count",
    "projects:device_message.err_message",
    "projects:device_message.settings.on_time",
    "projects:device_message.settings.off_time",
    "projects:device_message.settings.bottom_time",
    "projects:device_message.settings.bottom_layers",
}


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten to dotted paths, decoding JSON held in strings.

    Anycubic nests real structure inside string fields (device_message,
    slice_param), so a naive flatten would stop at the string and hide the
    most useful values in the whole payload.
    """
    out: dict[str, Any] = {}

    if isinstance(obj, str) and obj.strip()[:1] in "{[":
        try:
            return flatten(json.loads(obj), prefix)
        except ValueError:
            pass

    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(obj, list):
        if not obj:
            out[prefix] = "[]"
        else:
            # One representative element is enough to learn the shape.
            out.update(flatten(obj[0], f"{prefix}[]"))
            if len(obj) > 1:
                out[f"{prefix}[count]"] = len(obj)
    else:
        out[prefix] = obj
    return out


# Account fields are personal data, and some are credentials in their own
# right (casdoor_user_token is a second JWT). None of it is printer status,
# so it is masked rather than printed or reported.
_SECRET_HINTS = (
    "token", "email", "phone", "mobile", "avatar", "password", "secret",
    "birthday", "address", "id_card", "social",
    # Only person-shaped name fields. A bare "name" is usually a device or
    # a hardware component, which is exactly what this probe is looking for.
    "user_name", "username", "display_name", "first_name", "last_name",
    "nick_name", "nickname", "real_name",
)


def _is_sensitive(path: str) -> bool:
    lowered = path.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def render(value: Any, width: int = 58, path: str = "") -> str:
    if path and _is_sensitive(path):
        return "<masked>"
    text = "null" if value is None else str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 3] + "..."


async def main() -> None:
    token_file = ROOT / "secrets" / "token.txt"
    if not token_file.exists():
        sys.exit(f"No token at {token_file}")
    token = token_file.read_text(encoding="utf-8").strip()

    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}

    async with aiohttp.ClientSession() as session:
        client = anycubic_api.AnycubicCloud(session, token)

        printers = await client.async_list_printers()
        if not printers:
            sys.exit("No printers on the account.")
        printer_id = str(printers[0]["id"])
        # The tool endpoint keys off the model, not the printer instance.
        model_id = str(printers[0].get("machine_type") or "")

        job = await client.async_get_latest_job(int(printer_id))
        project_id = str(job.get("id") or "")

        print(f"printer id {printer_id}, newest project {project_id or '(none)'}\n")

        for label, method, path, needs in ENDPOINTS:
            params: dict[str, str] = {}
            if needs == "printer":
                params["id"] = printer_id
                if label == "printer-tool":
                    # This one keys off the model, not the printer.
                    params = {"model_id": model_id}
            elif needs == "project":
                if not project_id:
                    print(f"  {label:<18} skipped (no project)")
                    continue
                params["id"] = project_id
            if label in ("projects", "print-history", "cloud-files"):
                params.update(page="1", limit="5")

            try:
                if method == "GET":
                    payload = await client._get(path, **params)
                else:
                    async with session.post(
                        f"{client._base}{path}",
                        params=params or None,
                        headers=client._headers(),
                        timeout=anycubic_api.REQUEST_TIMEOUT,
                    ) as resp:
                        payload = await resp.json(content_type=None)
            except Exception as err:  # noqa: BLE001 - probing, report and move on
                print(f"  {label:<18} ERROR {type(err).__name__}: {err}")
                continue

            results[label] = payload
            # The account endpoints carry an email and a second JWT. Saving
            # those to disk is not worth it for a probe, so only a key list
            # is kept -- enough to show the endpoint answered.
            if label in ("user-info", "user-store"):
                to_save: Any = {
                    "note": "contents withheld: account data, not printer status",
                    "msg": payload.get("msg"),
                    "keys": sorted((payload.get("data") or {}).keys())
                    if isinstance(payload.get("data"), dict)
                    else None,
                }
            else:
                to_save = payload
            (OUT / f"{label}.json").write_text(
                json.dumps(to_save, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            data = payload.get("data")
            shape = (
                f"{len(data)} items" if isinstance(data, list)
                else f"{len(data)} keys" if isinstance(data, dict)
                else "empty" if data is None else type(data).__name__
            )
            print(f"  {label:<18} ok   msg={payload.get('msg')!r:<26} {shape}")

    # ---- flattened report -------------------------------------------------
    print("\n" + "=" * 92)
    print("EVERY FIELD, BY ENDPOINT   [*] = already exposed as an entity")
    print("=" * 92)

    unused_interesting: list[tuple[str, Any]] = []

    for label, payload in results.items():
        # The account endpoints carry personal data and a second credential,
        # and no printer status at all, so they are fetched (to prove they
        # work) but never enumerated.
        if label in ("user-info", "user-store"):
            print(f"\n--- {label} --- (reachable; contents withheld, account data)")
            continue

        data = payload.get("data")
        if data is None:
            continue
        sample = data[0] if isinstance(data, list) and data else data
        if not isinstance(sample, (dict, list)):
            continue

        fields = flatten(sample)
        if not fields:
            continue

        print(f"\n--- {label}  ({len(fields)} fields) ---")
        for key in sorted(fields):
            marker = "[*]" if f"{label}:{key}" in IN_USE else "   "
            print(f"  {marker} {key:<48} {render(fields[key], path=key)}")
            if marker == "   " and label in ("printer-info", "projects"):
                value = fields[key]
                if value not in (None, "", "[]", 0) and "url" not in key.lower():
                    unused_interesting.append((f"{label}:{key}", value))

    print("\n" + "=" * 92)
    print(f"UNUSED FIELDS WITH DATA  ({len(unused_interesting)})")
    print("=" * 92)
    for key, value in unused_interesting:
        print(f"  {key:<56} {render(value, 30, path=key)}")

    print(f"\nRaw responses saved to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    asyncio.run(main())

