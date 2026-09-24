# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp", "pycryptodome", "paho-mqtt>=2.0"]
# ///
"""Probe an Anycubic M7 Pro (or any LAN-enabled Anycubic printer) and dump
everything it tells us: open ports, /info, the /ctrl handshake, decrypted MQTT
credentials, and every MQTT message it publishes.

Usage:
    uv run tools/probe.py <printer-ip> [--seconds 120]
    uv run tools/probe.py --scan            # find printers on the LAN

Note: the printer accepts only ONE MQTT client. Close Photon Workshop / the
Anycubic app first, or this will kick it off (and it will kick us off).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import ipaddress
import json
import socket
import ssl
import time
import uuid
from datetime import datetime, timezone

import aiohttp
from Crypto.Cipher import AES

HTTP_PORT = 18910
MQTT_PORT = 8883
VIDEO_PORT = 18088
PORTS_OF_INTEREST = [80, 554, 3000, 6000, 8883, 11311, 18088, 18910]

# Topics are wildcarded hard so we see anything the M7 Pro emits, even subtopics
# the P1 integration never knew about.
SUB_TOPICS = [
    "anycubic/anycubicCloud/v1/printer/+/{model_id}/{device_id}/+/report",
    "anycubic/anycubicCloud/v1/#",
    "#",
]
PUB_TOPIC = "anycubic/anycubicCloud/v1/pc/printer/{model_id}/{device_id}/{subtopic}"

# (topic_suffix, json_type, action, data) - superset of the P1 startup queries.
QUERIES: list[tuple[str, str, str, object]] = [
    ("status", "status", "query", None),
    ("status", "lanInfo", "query", None),
    ("status", "autoOperation", "getStatus", None),
    ("print", "print", "query", None),
    ("light", "light", "query", None),
    ("peripherie", "peripherie", "query", None),
    ("releaseFilm", "releaseFilm", "get", None),
    ("properties", "properties", "read", ["connect"]),
    ("properties", "properties", "read", ["signal_strength"]),
    ("file", "file", "listLocal", None),
    ("file", "file", "listUdisk", None),
    ("info", "info", "query", None),
    ("system", "system", "query", None),
    ("multiColorBox", "multiColorBox", "query", None),
]


def log(tag: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
    print(f"[{ts}] {tag:<7} {msg}", flush=True)


def jdump(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


async def scan_lan() -> None:
    """Look for anything answering on the Anycubic HTTP port across the /24."""
    local = socket.gethostbyname(socket.gethostname())
    net = ipaddress.ip_network(f"{local}/24", strict=False)
    log("SCAN", f"local ip {local}, sweeping {net} for tcp/{HTTP_PORT}")

    loop = asyncio.get_running_loop()
    hosts = [str(h) for h in net.hosts()]
    results = await asyncio.gather(
        *(loop.run_in_executor(None, port_open, h, HTTP_PORT, 1.0) for h in hosts)
    )
    found = [h for h, ok in zip(hosts, results) if ok]
    if found:
        for h in found:
            log("SCAN", f"candidate: {h}")
    else:
        log("SCAN", f"nothing answered on tcp/{HTTP_PORT}")


def scan_ports(host: str) -> None:
    log("PORTS", f"probing {host}")
    for port in PORTS_OF_INTEREST:
        if port_open(host, port):
            log("PORTS", f"  {port:>5}/tcp  open")
        else:
            print(f"                   {port:>5}/tcp  closed", flush=True)


# --------------------------------------------------------------------------- #
# HTTP handshake
# --------------------------------------------------------------------------- #
def compute_sign(token: str, ts: str, nonce: str) -> str:
    """sign = md5(md5(token[:16]).hex() + ts + nonce).hex()"""
    inner = hashlib.md5(token[:16].encode()).hexdigest()
    return hashlib.md5((inner + ts + nonce).encode()).hexdigest()


def decrypt_mqtt_info(encoded: str, info_token: str, ctrl_token: str) -> dict:
    key = info_token[16:32].encode()
    iv = ctrl_token.encode()
    padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
    plaintext = AES.new(key, AES.MODE_CBC, iv).decrypt(base64.b64decode(padded))
    text = plaintext.rstrip(b"\x00").decode("utf-8", errors="ignore")
    depth = end = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return json.loads(text[:end])


async def handshake(host: str) -> tuple[dict, dict]:
    base = f"http://{host}:{HTTP_PORT}"
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15)
    ) as session:
        log("HTTP", f"GET {base}/info")
        async with session.get(f"{base}/info") as resp:
            info = await resp.json(content_type=None)
        log("HTTP", f"/info ->\n{jdump(info)}")

        token = info["token"]
        ts = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex[:15]
        sign = compute_sign(token, ts, nonce)
        url = f"{base}/ctrl?ts={ts}&nonce={nonce}&did=probe&sign={sign}"

        log("HTTP", f"POST /ctrl ts={ts} nonce={nonce} sign={sign}")
        async with session.post(url) as resp:
            ctrl = await resp.json(content_type=None)
        log("HTTP", f"/ctrl -> code={ctrl.get('code')} message={ctrl.get('message')}")

        if ctrl.get("code") not in (0, 200):
            raise SystemExit(f"/ctrl refused us:\n{jdump(ctrl)}")

        creds = decrypt_mqtt_info(ctrl["data"]["info"], token, ctrl["data"]["token"])
        redacted = dict(creds, password="***")
        log("HTTP", f"decrypted MQTT credentials ->\n{jdump(redacted)}")
        return info, creds


# --------------------------------------------------------------------------- #
# MQTT listen
# --------------------------------------------------------------------------- #
def listen_mqtt(host: str, info: dict, creds: dict, seconds: int) -> None:
    import paho.mqtt.client as mqtt

    model_id = str(info.get("modelId", ""))
    device_id = creds.get("deviceId") or info.get("cn", "")
    seen: set[str] = set()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=creds["clientId"],
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(creds["username"], creds["password"])
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    client.tls_set_context(ctx)

    def on_connect(c, _u, _f, reason, _p=None):
        log("MQTT", f"connected: {reason}")
        for tmpl in SUB_TOPICS:
            topic = tmpl.format(model_id=model_id, device_id=device_id)
            c.subscribe(topic, qos=0)
            log("MQTT", f"subscribed {topic}")
        for suffix, jtype, action, data in QUERIES:
            topic = PUB_TOPIC.format(
                model_id=model_id, device_id=device_id, subtopic=suffix
            )
            payload = {
                "type": jtype,
                "action": action,
                "timestamp": int(time.time() * 1000),
                "msgid": uuid.uuid4().hex,
                "data": data,
            }
            c.publish(topic, json.dumps(payload), qos=0)
            log("QUERY", f"{suffix:<14} type={jtype} action={action}")

    def on_message(_c, _u, msg):
        try:
            body = json.loads(msg.payload.decode("utf-8", errors="replace"))
            rendered = jdump(body)
        except Exception:
            rendered = repr(msg.payload[:400])
        first = "" if msg.topic in seen else "  <-- NEW TOPIC"
        seen.add(msg.topic)
        log("RECV", f"{msg.topic}{first}\n{rendered}")

    def on_disconnect(_c, _u, _f, reason, _p=None):
        log("MQTT", f"disconnected: {reason}")

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    broker = creds.get("broker") or "(none given)"
    log("MQTT", f"broker hint from printer: {broker}")
    client_id = creds["clientId"]
    log("MQTT", f"connecting to {host}:{MQTT_PORT} as clientId={client_id}")
    client.connect(host, MQTT_PORT, keepalive=60)

    client.loop_start()
    try:
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("MQTT", "interrupted")
    finally:
        client.loop_stop()
        client.disconnect()

    log("DONE", f"{len(seen)} distinct topics seen:")
    for topic in sorted(seen):
        print(f"                   {topic}", flush=True)


# --------------------------------------------------------------------------- #
async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("host", nargs="?", help="printer IP address")
    ap.add_argument("--scan", action="store_true", help="sweep the /24 for printers")
    ap.add_argument("--seconds", type=int, default=120, help="how long to listen")
    ap.add_argument("--no-mqtt", action="store_true", help="HTTP handshake only")
    args = ap.parse_args()

    if args.scan:
        await scan_lan()
        return
    if not args.host:
        ap.error("give a printer IP, or --scan")

    scan_ports(args.host)
    info, creds = await handshake(args.host)
    if args.no_mqtt:
        return
    listen_mqtt(args.host, info, creds, args.seconds)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
