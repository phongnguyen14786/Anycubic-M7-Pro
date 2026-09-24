# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp"]
# ///
"""Exercise the integration's own api.py against the live cloud.

Imports the real module from custom_components/, so this tests the shipped
code rather than a copy of it.

    uv run tools/test_api.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "anycubic_m7pro"


def _load_without_homeassistant(*names: str) -> dict[str, types.ModuleType]:
    """Load modules from the component under a synthetic package.

    The real package __init__ imports Home Assistant, which is not installed
    here. Registering an empty stand-in package lets api.py's relative import
    of .const resolve while nothing else is executed.
    """
    pkg_name = "_anycubic_under_test"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(COMPONENT)]
    sys.modules[pkg_name] = pkg

    loaded = {}
    for name in names:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{name}", COMPONENT / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded


_modules = _load_without_homeassistant("const", "api")
anycubic_api = _modules["api"]


async def main() -> None:
    token_file = ROOT / "secrets" / "token.txt"
    if not token_file.exists():
        sys.exit(f"No token at {token_file}")
    token = token_file.read_text(encoding="utf-8").strip()

    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        mark = "PASS" if condition else "FAIL"
        print(f"  [{mark}] {label}{f'  -- {detail}' if detail else ''}")
        if not condition:
            failures.append(label)

    async with aiohttp.ClientSession() as session:
        client = anycubic_api.AnycubicCloud(session, token)

        print("auth")
        user_id = await client.async_get_user_id()
        check("token accepted", bool(user_id), f"user_id={user_id}")

        print("\nprinter list")
        printers = await client.async_list_printers()
        check("at least one printer", len(printers) >= 1, f"{len(printers)} found")
        printer_id = int(printers[0]["id"])

        print("\nfull poll")
        state = await client.async_poll(printer_id)
        check("printer data", bool(state.printer))
        check("base block", bool(state.base), f"fw={state.base.get('firmware_version')}")
        check("machine_data", bool(state.machine_data))
        check(
            "device_message decoded",
            isinstance(state.message, dict),
            f"{len(state.message)} keys",
        )

        print("\nfields the sensors read")
        for label, value in [
            ("firmware_version", state.base.get("firmware_version")),
            ("print_count", state.base.get("print_count")),
            ("print_totaltime", state.base.get("print_totaltime")),
            ("material_used", state.base.get("material_used")),
            ("releaseFilm.layers", (state.printer.get("releaseFilm") or {}).get("layers")),
            ("device_status", state.printer.get("device_status")),
            ("need_update", state.printer.get("need_update")),
            ("job.print_status", state.job.get("print_status")),
            ("job.gcode_name", state.job.get("gcode_name")),
            ("job.remain_time", state.job.get("remain_time")),
            ("msg.curr_layer", state.message.get("curr_layer")),
            ("msg.total_layers", state.message.get("total_layers")),
            ("msg.progress", state.message.get("progress")),
            ("msg.state", state.message.get("state")),
            ("settings.on_time", state.settings.get("on_time")),
            ("settings.bottom_layers", state.settings.get("bottom_layers")),
        ]:
            present = value is not None
            print(f"  {'ok ' if present else '   '} {label:<24} {value}")

        print("\nerror handling")
        bad = anycubic_api.AnycubicCloud(session, "not-a-real-token")
        try:
            await bad.async_get_user_id()
            check("bad token rejected", False, "no exception raised")
        except anycubic_api.AnycubicAuthError as err:
            check("bad token rejected", True, f"{type(err).__name__}")
        except Exception as err:  # noqa: BLE001
            check(
                "bad token raises AnycubicAuthError",
                False,
                f"got {type(err).__name__}: {err}",
            )

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        sys.exit(1)
    print("All API checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
