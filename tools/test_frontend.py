# /// script
# requires-python = ">=3.12"
# ///
"""Test the card registration logic with a stubbed Home Assistant.

This is the code path that failed in the wild -- the card was served but the
browser never loaded it -- so it is worth exercising rather than trusting.

    uv run tools/test_frontend.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "anycubic_m7pro"

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}{f'  -- {detail}' if detail else ''}")
    if not condition:
        FAILURES.append(label)


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------
class FakeStaticPathConfig:
    def __init__(self, url_path: str, path: str, cache_headers: bool = True) -> None:
        self.url_path = url_path
        self.path = path
        self.cache_headers = cache_headers


class FakeHttp:
    def __init__(self) -> None:
        self.registered: list[FakeStaticPathConfig] = []
        self.fail = False

    async def async_register_static_paths(self, configs: list[Any]) -> None:
        if self.fail:
            raise RuntimeError("already registered")
        self.registered.extend(configs)


class FakeResources:
    """Stands in for ResourceStorageCollection."""

    def __init__(self, items: list[dict] | None = None, store: Any = object()) -> None:
        self._items = items or []
        self.store = store
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self.info_calls = 0

    async def async_get_info(self) -> dict:
        self.info_calls += 1
        return {}

    def async_items(self) -> list[dict]:
        return self._items

    async def async_create_item(self, data: dict) -> dict:
        self.created.append(data)
        item = {"id": f"id{len(self._items)}", **data}
        self._items.append(item)
        return item

    async def async_update_item(self, item_id: str, data: dict) -> dict:
        self.updated.append((item_id, data))
        for item in self._items:
            if item["id"] == item_id:
                item.update(data)
        return {}


class FakeHass:
    def __init__(self, resources: Any = None) -> None:
        self.data: dict[str, Any] = {}
        self.http = FakeHttp()
        if resources is not None:
            self.data["lovelace"] = types.SimpleNamespace(resources=resources)


EXTRA_JS: list[str] = []


def _install_stubs() -> None:
    def mod(name: str) -> types.ModuleType:
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    ha = mod("homeassistant")
    ha.__path__ = []
    comps = mod("homeassistant.components")
    comps.__path__ = []

    http_mod = mod("homeassistant.components.http")
    http_mod.StaticPathConfig = FakeStaticPathConfig

    frontend_mod = mod("homeassistant.components.frontend")
    frontend_mod.add_extra_js_url = lambda hass, url, es5=False: EXTRA_JS.append(url)
    comps.frontend = frontend_mod

    lovelace_mod = mod("homeassistant.components.lovelace")
    lovelace_mod.DOMAIN = "lovelace"

    core = mod("homeassistant.core")
    core.HomeAssistant = object

    loader = mod("homeassistant.loader")
    loader.async_get_loaded_integration = lambda hass, domain: types.SimpleNamespace(
        version="1.2.1"
    )

    pkg = types.ModuleType("_anycubic_fe")
    pkg.__path__ = [str(COMPONENT)]
    sys.modules["_anycubic_fe"] = pkg


def _load(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_anycubic_fe.{name}", COMPONENT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_install_stubs()
_load("const")
fe = _load("frontend")


async def main() -> None:
    print("the card file exists where the code looks for it")
    card = COMPONENT / "www" / fe.CARD_FILENAME
    check("www/ card present", card.is_file(), str(card.relative_to(ROOT)))
    check("card is non-empty", card.is_file() and card.stat().st_size > 1000,
          f"{card.stat().st_size if card.is_file() else 0} bytes")
    check(
        "card is pure ASCII (no charset surprises)",
        card.is_file() and all(b < 128 for b in card.read_bytes()),
    )

    print("\nfresh registration, storage-mode dashboards")
    EXTRA_JS.clear()
    resources = FakeResources()
    hass = FakeHass(resources)
    await fe.async_register_card(hass)

    check("static path registered", len(hass.http.registered) == 1)
    if hass.http.registered:
        cfg = hass.http.registered[0]
        check("served at the expected url", cfg.url_path == fe.CARD_URL, cfg.url_path)
        check("points at the real file", Path(cfg.path).is_file())
        check("cache headers off", cfg.cache_headers is False)

    check("add_extra_js_url called once", len(EXTRA_JS) == 1, str(EXTRA_JS))
    check(
        "url carries a version for cache busting",
        EXTRA_JS and EXTRA_JS[0] == f"{fe.CARD_URL}?v=1.2.1",
        EXTRA_JS[0] if EXTRA_JS else "",
    )
    check("lovelace resource created", len(resources.created) == 1, str(resources.created))
    if resources.created:
        check("registered as a module", resources.created[0]["res_type"] == "module")
        check(
            "resource url matches",
            resources.created[0]["url"] == f"{fe.CARD_URL}?v=1.2.1",
            resources.created[0]["url"],
        )
    check("marked as registered", hass.data.get(fe._REGISTERED) is True)

    print("\nsecond call is a no-op (entry reload must not double-register)")
    before = len(hass.http.registered), len(EXTRA_JS), len(resources.created)
    await fe.async_register_card(hass)
    check(
        "nothing registered twice",
        (len(hass.http.registered), len(EXTRA_JS), len(resources.created)) == before,
    )

    print("\nupgrade re-points the existing resource instead of duplicating")
    EXTRA_JS.clear()
    resources = FakeResources(
        [{"id": "abc", "res_type": "module", "url": f"{fe.CARD_URL}?v=1.0.0"}]
    )
    hass = FakeHass(resources)
    await fe.async_register_card(hass)
    check("no duplicate created", len(resources.created) == 0, str(resources.created))
    check("existing item updated", len(resources.updated) == 1, str(resources.updated))
    if resources.updated:
        check(
            "updated to the new version",
            resources.updated[0][1]["url"] == f"{fe.CARD_URL}?v=1.2.1",
            resources.updated[0][1]["url"],
        )

    print("\nalready correct: left alone")
    resources = FakeResources(
        [{"id": "abc", "res_type": "module", "url": f"{fe.CARD_URL}?v=1.2.1"}]
    )
    hass = FakeHass(resources)
    await fe.async_register_card(hass)
    check("no create", not resources.created)
    check("no update", not resources.updated)

    print("\nunrelated resources are not touched")
    resources = FakeResources(
        [{"id": "x", "res_type": "module", "url": "/hacsfiles/other/other.js"}]
    )
    hass = FakeHass(resources)
    await fe.async_register_card(hass)
    check("other resource untouched", not resources.updated, str(resources.updated))
    check("ours added alongside", len(resources.created) == 1)

    print("\nYAML-mode dashboards are not written to")
    resources = FakeResources(store=None)
    hass = FakeHass(resources)
    await fe.async_register_card(hass)
    check("no resource written", not resources.created and not resources.updated)
    check("but still served", len(hass.http.registered) == 1)
    check("and still injected", EXTRA_JS[-1].startswith(fe.CARD_URL))

    print("\nno lovelace at all (should not explode)")
    hass = FakeHass(resources=None)
    await fe.async_register_card(hass)
    check("still served", len(hass.http.registered) == 1)

    print("\nserving fails: setup must survive")
    hass = FakeHass(FakeResources())
    hass.http.fail = True
    await fe.async_register_card(hass)
    check("no exception raised", True)
    check("not marked registered", hass.data.get(fe._REGISTERED) is not True)

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("All frontend checks passed.")


asyncio.run(main())
