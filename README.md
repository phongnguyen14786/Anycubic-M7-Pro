# Anycubic M7 Pro for Home Assistant

A small, self-contained Home Assistant integration for the **Anycubic Photon Mono M7 Pro**,
talking to Anycubic's cloud with an account token.

**No third-party dependencies.** `manifest.json` declares `"requirements": []`, so nothing is
installed from PyPI when the integration loads. The entire cloud client is
[`api.py`](custom_components/anycubic_m7pro/api.py) — about 200 lines of `aiohttp`, which Home
Assistant already ships.

**Read-only.** There is no code path here that can start, pause, stop or alter a print. The
integration issues three HTTP GETs and nothing else.

---

## Why cloud and not LAN

The M7 Pro has no LAN mode. A full TCP port scan of the printer found only busybox inetd
leftovers (echo, daytime, telnet, time), LLMNR, and one silent port on 11311 — no HTTP API, no
MQTT broker, no mDNS, no UDP discovery. It makes outbound connections to Anycubic's cloud and
serves nothing locally.

The LAN-mode integrations written for the Photon P1 and the Kobra series do not apply. Cloud is
the only route, so cloud is what this does.

---

## What you get

Updates every 60 seconds.

### Always available

| Entity | Example |
|---|---|
| Status | `idle` / `printing` / `complete` / `cancelled` / `preheating` / … |
| Online | connectivity |
| Problem | set when the running job reports an error |
| Firmware version | `4.0.8.6` |
| Firmware update available | update |
| Total prints | `5` |
| Total print time | `217 min` |
| Total resin used | `205.19 ml` |
| Release film layers | `2811` — film wear counter |
| Wi-Fi signal | disabled by default |

### While a print is running

| Entity | Notes |
|---|---|
| Printing | running |
| Job name | `01_Right Tire` |
| Progress | % |
| Current layer / Total layers | `700` / `1109` |
| Time elapsed / Time remaining | minutes |
| Estimated finish | timestamp, for use in automations |
| Job resin used | ml |
| Model height | mm |
| Layer height | mm |
| Exposure time | s |
| Light off time | s, disabled by default |
| Bottom exposure time | s |
| Bottom layers | |
| Anti-aliasing | disabled by default |

Job entities report **nothing while the printer is idle**, rather than leaving the last finished
print's numbers on screen. Anycubic keeps the completed job as the newest project indefinitely,
so showing it unconditionally would make an idle printer look busy forever.

### Not available

No camera and no light control. The printer reports `LCD_PEER_VIDEO: false` and
`has_controllable_light: false`, so there is nothing to expose.

---

## Install

### Via HACS (recommended)

1. **HACS** → **⋮** (top right) → **Custom repositories**
2. Add `https://github.com/phongnguyen14786/Anycubic-M7-Pro` with type **Integration**
3. Find **Anycubic M7 Pro** in the HACS list and **Download**
4. Restart Home Assistant
5. **Settings → Devices & services → + Add integration → Anycubic M7 Pro**
6. Paste your token

### Manually

1. Copy `custom_components/anycubic_m7pro/` into your Home Assistant `config/custom_components/`
   directory.
2. Restart Home Assistant.
3. **Settings → Devices & services → + Add integration → Anycubic M7 Pro**.
4. Paste your token.

### Getting a token

1. Sign in at <https://cloud-universe.anycubic.com/file>
2. Open developer tools (**F12**) → **Console**
3. Run:
   ```js
   window.localStorage["XX-Token"]
   ```
4. Copy the value **without the surrounding quotes** (~238 characters)

If it returns `undefined`, you are on a tab that just redirected through OAuth. Read it from the
tab you were already signed in on.

The token is an account credential — treat it like a password. It is stored in Home Assistant's
config entry, the same place every other integration keeps its secrets.

Web tokens last about **90 days**. When one expires the integration raises a reauth prompt rather
than failing silently; paste a fresh token and it carries on.

---

## Testing

Three scripts under [`tools/`](tools/), all runnable with `uv run` — no venv needed. They read
the token from `secrets/token.txt` (gitignored).

```bash
uv run tools/verify_minimal.py    # the cloud client works, standalone
uv run tools/test_api.py          # the shipped api.py, against the live cloud
uv run tools/test_entities.py     # sensor logic, against real captured payloads
```

`test_entities.py` stubs the small part of Home Assistant the component imports, because Home
Assistant will not pip-install on Windows without a C toolchain. It exercises the real value
functions with real payloads — the parsers and the idle-gating, which is where the logic is.

### Current state

All three pass. The entity suite covers 45 checks including both the idle case and a simulated
running print.

One caveat, stated plainly: **the running-print path has been tested against a reconstructed
payload, not a live print.** The field names and values come from a real completed job pulled
off the cloud, and the print-status code is set to `Printing` to exercise the gating. It should
be right, but until a print actually runs it is inference. Start a print and check.

---

## Layout

```
custom_components/anycubic_m7pro/
  api.py            cloud client — aiohttp only, read-only
  coordinator.py    60-second polling, reauth on expiry
  entity.py         shared device info
  sensor.py         22 sensors
  binary_sensor.py  4 binary sensors
  config_flow.py    setup + reauth
  const.py          status codes
tools/              test and exploration scripts
```

### Endpoints used

All under `https://cloud-universe.anycubic.com/p/p/workbench/api`:

| Endpoint | Purpose |
|---|---|
| `GET /user/profile/userInfo` | validate token, get account id |
| `GET /work/printer/getPrinters` | list printers |
| `GET /v2/printer/info?id=` | printer state, firmware, lifetime stats |
| `GET /work/project/getProjects` | newest job, including live layer and progress |

Every request carries a signed header set: an MD5 over a fixed app id, millisecond timestamp,
app version, app secret and a UUID nonce, with the app id repeated at both ends of the input.
That repetition is what the server recomputes and checks — it is not a mistake.

---

## Credits

The protocol groundwork came from two existing projects, both MIT licensed:

- [modrzew/hass-anycubic-photon-p1](https://github.com/modrzew/hass-anycubic-photon-p1) —
  reverse-engineered the Photon P1 LAN protocol, which mirrors the cloud one.
- [WaresWichall/hass-anycubic_cloud](https://github.com/WaresWichall/hass-anycubic_cloud) and
  its maintained fork [Nino6689/hass-anycubic](https://github.com/Nino6689/hass-anycubic) —
  worked out the cloud endpoints and request signing.

This integration shares no code with either. It is a much smaller, read-only, dependency-free
implementation aimed at one printer.

## License

MIT — see [LICENSE](LICENSE).
