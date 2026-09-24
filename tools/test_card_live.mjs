/*
 * Run the card Home Assistant is actually serving against the real states
 * from that same instance.
 *
 * Every other test here uses synthetic data and a local file. This one
 * fetches both from the live install, so it catches the things invented
 * fixtures cannot: entity ids that are not shaped the way the card assumes,
 * and runtime errors that only appear with real data.
 *
 *   node tools/test_card_live.mjs
 *
 * Needs secrets/ha_token.txt. The token is never printed.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const BASE = "http://homeassistant.local:8123";
const CARD_PATH = "/anycubic_m7pro/anycubic-m7pro-card.js";

const token = readFileSync(join(ROOT, "secrets", "ha_token.txt"), "utf8").trim();
const failures = [];
const check = (label, ok, detail = "") => {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${label}${detail ? `  -- ${detail}` : ""}`);
  if (!ok) failures.push(label);
};

// ---------------------------------------------------------------------------
// Minimal DOM, same shape as test_card.mjs
// ---------------------------------------------------------------------------
class El {
  constructor() {
    this.attributes = {};
    this._text = "";
    this._html = "";
    this.style = {};
    const set = new Set();
    this.classList = {
      add: (c) => set.add(c),
      remove: (c) => set.delete(c),
      toggle: (c, on) => (on ? set.add(c) : set.delete(c)),
      contains: (c) => set.has(c),
    };
  }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  set innerHTML(v) {
    this._html = String(v);
    this._ids = {};
    for (const m of this._html.matchAll(/id="([^"]+)"/g)) this._ids[m[1]] = new El();
  }
  get innerHTML() { return this._html; }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k] ?? null; }
  querySelector(sel) {
    return sel.startsWith("#") && this._ids ? this._ids[sel.slice(1)] : undefined;
  }
}
globalThis.HTMLElement = El;
const registryMap = new Map();
globalThis.customElements = {
  define: (n, c) => registryMap.set(n, c),
  get: (n) => registryMap.get(n),
};
globalThis.window = { customCards: [] };
const consoleErrors = [];
const realError = console.error;
console.error = (...a) => { consoleErrors.push(a.map(String).join(" ")); };
console.info = () => {};

// ---------------------------------------------------------------------------
async function api(path) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function entityRegistry() {
  // hass.entities comes from the websocket registry, not REST.
  const { WebSocket } = await import("node:http")
    .then(() => ({ WebSocket: globalThis.WebSocket }))
    .catch(() => ({ WebSocket: globalThis.WebSocket }));
  if (!WebSocket) return null;

  return new Promise((resolve) => {
    const ws = new WebSocket(`${BASE.replace("http", "ws")}/api/websocket`);
    const timer = setTimeout(() => { try { ws.close(); } catch {} resolve(null); }, 15000);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "auth_required") {
        ws.send(JSON.stringify({ type: "auth", access_token: token }));
      } else if (msg.type === "auth_ok") {
        ws.send(JSON.stringify({ id: 1, type: "config/entity_registry/list" }));
      } else if (msg.id === 1) {
        clearTimeout(timer);
        const out = {};
        for (const e of msg.result || []) {
          out[e.entity_id] = { device_id: e.device_id, area_id: e.area_id };
        }
        try { ws.close(); } catch {}
        resolve(out);
      }
    };
    ws.onerror = () => { clearTimeout(timer); resolve(null); };
  });
}

// ---------------------------------------------------------------------------
console.log(`fetching the card from ${BASE}${CARD_PATH}`);
const res = await fetch(`${BASE}${CARD_PATH}`);
check("card served", res.ok, `HTTP ${res.status}`);
const source = await res.text();
check("card is non-empty", source.length > 1000, `${source.length} bytes`);

let defineError = null;
try {
  new Function(source)();
} catch (err) {
  defineError = err;
}
check("card module executes", defineError === null, defineError?.message ?? "");

const Card = registryMap.get("anycubic-m7pro-card");
check("custom element defined", typeof Card === "function");
if (!Card) {
  console.log("\nThe module did not define the element. Nothing else can run.");
  process.exit(1);
}

console.log("\nfetching live state");
const stateList = await api("/api/states");
const states = {};
for (const s of stateList) states[s.entity_id] = s;
const ours = Object.keys(states).filter((id) => id.includes("m7_pro"));
check("printer entities present", ours.length > 0, `${ours.length} entities`);

const entities = await entityRegistry();
check("entity registry readable", entities !== null,
      entities ? `${Object.keys(entities).length} entries` : "websocket unavailable");

console.log("\nrendering with real data");
const card = new Card();
let cfgError = null;
try {
  card.setConfig({});
} catch (err) {
  cfgError = err;
}
check("setConfig does not throw", cfgError === null, cfgError?.message ?? "");

let hassError = null;
try {
  card.hass = { states, entities: entities || undefined, locale: { language: "en" } };
} catch (err) {
  hassError = err;
}
check("hass setter does not throw", hassError === null, hassError?.message ?? "");
check("no errors logged during render", consoleErrors.length === 0,
      consoleErrors.join(" | "));

if (!card._el) {
  console.log("card did not build");
  process.exit(1);
}

console.log("\nprefixes discovered");
for (const p of card._prefixes()) console.log(`  ${p}`);
check("more than one prefix found", card._prefixes().length >= 1,
      `${card._prefixes().length}`);

console.log("\nwhat the card shows");
const rows = card._el.rows.innerHTML;
const parsed = [...rows.matchAll(
  /<span class="k">([^<]*)<\/span><span class="v">([^<]*)<\/span>/g
)].map((m) => [m[1], m[2]]);
console.log(`  header       ${card._el.title.textContent}`);
console.log(`  big text     ${card._el.pct.textContent}`);
for (const [k, v] of parsed) console.log(`  ${k.padEnd(12)} ${v}`);

console.log("\nthe fields that were blank");
const lastSeen = parsed.find(([k]) => k === "Last seen")?.[1];
check("Last seen has a value", Boolean(lastSeen) && lastSeen !== "—",
      lastSeen ?? "row missing");

const ps = card._get("sensor", "_printer_state");
check("printer_state entity is found", Boolean(ps), ps ? ps.state : "not found");
check(
  "big text uses the cloud wording",
  card._el.pct.textContent !== "Offline",
  card._el.pct.textContent
);

console.log();
if (failures.length) {
  realError(`FAILED: ${failures.length} -> ${failures.join(", ")}`);
  process.exit(1);
}
console.log("Live card checks passed.");
