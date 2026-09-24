/*
 * Test the dashboard card's logic without a browser or Home Assistant.
 *
 * Stubs just enough DOM for the card to define and render, then feeds it
 * hass states matching what the integration actually produces, and asserts
 * on the rendered text.
 *
 *   node tools/test_card.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CARD = join(
  ROOT,
  "custom_components",
  "anycubic_m7pro",
  "www",
  "anycubic-m7pro-card.js"
);

const failures = [];
function check(label, condition, detail = "") {
  const mark = condition ? "PASS" : "FAIL";
  console.log(`  [${mark}] ${label}${detail ? `  -- ${detail}` : ""}`);
  if (!condition) failures.push(label);
}

// ---------------------------------------------------------------------------
// Minimal DOM
// ---------------------------------------------------------------------------
class El {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = {};
    this._text = "";
    this._html = "";
    this.style = {};
    this.classList = {
      _set: new Set(),
      add: (c) => this.classList._set.add(c),
      remove: (c) => this.classList._set.delete(c),
      toggle: (c, on) =>
        on ? this.classList._set.add(c) : this.classList._set.delete(c),
      contains: (c) => this.classList._set.has(c),
    };
  }
  set textContent(v) {
    this._text = String(v);
  }
  get textContent() {
    return this._text;
  }
  set innerHTML(v) {
    this._html = String(v);
    // Index anything with an id so querySelector can find it.
    this._ids = {};
    for (const m of this._html.matchAll(/id="([^"]+)"/g)) {
      this._ids[m[1]] = new El();
    }
  }
  get innerHTML() {
    return this._html;
  }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
  }
  getAttribute(k) {
    return this.attributes[k] ?? null;
  }
  querySelector(sel) {
    if (sel.startsWith("#") && this._ids) return this._ids[sel.slice(1)];
    return undefined;
  }
}

globalThis.HTMLElement = El;
const registry = new Map();
globalThis.customElements = {
  define: (n, c) => registry.set(n, c),
  get: (n) => registry.get(n),
};
globalThis.window = { customCards: [] };
globalThis.console.info = () => {};

// ---------------------------------------------------------------------------
const source = readFileSync(CARD, "utf8");
new Function(source)();

const Card = registry.get("anycubic-m7pro-card");
if (!Card) {
  console.error("card did not register");
  process.exit(1);
}

const P = "anycubic_photon_mono_m7_pro";
const s = (state, attributes = {}) => ({ state: String(state), attributes });

function idleStates() {
  return {
    [`sensor.${P}_release_film_layers`]: s(2811),
    [`sensor.${P}_status`]: s("idle", {
      friendly_name: "Anycubic Photon Mono M7 Pro Status",
    }),
    [`sensor.${P}_printer_state`]: s("free"),
    [`binary_sensor.${P}_online`]: s("on"),
    [`binary_sensor.${P}_printing`]: s("off"),
    [`binary_sensor.${P}_problem`]: s("off"),
    [`sensor.${P}_progress`]: s("unknown"),
    [`sensor.${P}_job_name`]: s("unknown"),
    [`sensor.${P}_total_prints`]: s(5),
    [`sensor.${P}_total_resin_used`]: s(205.19),
    [`sensor.${P}_last_seen`]: s("2026-09-24T11:34:47+00:00"),
  };
}

function printingStates() {
  return {
    ...idleStates(),
    [`sensor.${P}_status`]: s("printing", {
      friendly_name: "Anycubic Photon Mono M7 Pro Status",
    }),
    [`binary_sensor.${P}_printing`]: s("on"),
    [`sensor.${P}_progress`]: s(63),
    [`sensor.${P}_job_name`]: s("01_Right Tire"),
    [`sensor.${P}_current_layer`]: s(700),
    [`sensor.${P}_total_layers`]: s(1109),
    [`sensor.${P}_time_elapsed`]: s(45),
    [`sensor.${P}_time_remaining`]: s(139),
    [`sensor.${P}_estimated_finish`]: s("2026-09-24T14:52:00+00:00"),
    [`sensor.${P}_job_resin_used`]: s(56.949574),
    [`image.${P}_job_thumbnail`]: s("2026-09-24T12:00:00+00:00", {
      entity_picture: "/api/image_proxy/image.x?token=abc",
    }),
  };
}

function render(states, config = {}) {
  const card = new Card();
  card.setConfig(config);
  card.hass = { states, locale: { language: "en" } };
  return card;
}

const rowsOf = (card) => card._el.rows.innerHTML;

console.log("registration");
check("custom element defined", typeof Card === "function");
check(
  "listed in customCards",
  globalThis.window.customCards.some((c) => c.type === "anycubic-m7pro-card")
);
check("getStubConfig works", typeof Card.getStubConfig() === "object");

console.log("\nidle printer");
{
  const card = render(idleStates());
  check("title derived from entity", card._el.title.textContent === "Anycubic Photon Mono M7 Pro",
        card._el.title.textContent);
  check("online dot lit", card._el.dot.classList.contains("on"));
  check("no job name shown", card._el.job.textContent === "");
  check("shows Idle not a percentage", card._el.pct.textContent === "Idle",
        card._el.pct.textContent);
  check("progress bar empty", card._el.fill.style.width === "0%",
        card._el.fill.style.width);
  check("thumbnail hidden", card._el.thumb.classList.contains("hidden"));
  const rows = rowsOf(card);
  check("shows lifetime totals when idle", rows.includes("Total prints") && rows.includes("5"));
  check("shows film layers", rows.includes("Film layers") && rows.includes("2811"));
  check("no ETA row when idle", !rows.includes("ETA"));
  // Regression: these rendered as a dash in the wild while the entity had a
  // perfectly good value, because the payload behind them was empty.
  check(
    "last seen renders a time, not a dash",
    rows.includes("Last seen") && !/Last seen<\/span><span class="v">—/.test(rows),
    rows.match(/Last seen<\/span><span class="v">([^<]*)/)?.[1] ?? "missing"
  );
}

console.log("\nidle but printersStatus came back empty");
{
  // Exactly the failure seen in the wild: printer/info is fine, so most
  // rows fill, but the two fields fed by printersStatus have nothing.
  const states = idleStates();
  delete states[`sensor.${P}_last_seen`];
  delete states[`sensor.${P}_printer_state`];
  const card = render(states);
  const rows = rowsOf(card);
  check("does not throw", typeof card._el.pct.textContent === "string");
  check("last seen falls back to a dash", rows.includes("Last seen"));
  check("other rows still populated", rows.includes("2811"));
  check("still reports Idle while online", card._el.pct.textContent === "Idle",
        card._el.pct.textContent);
}

console.log("\nprinting");
{
  const card = render(printingStates());
  check("job name shown", card._el.job.textContent === "01_Right Tire",
        card._el.job.textContent);
  check("percentage shown", card._el.pct.textContent === "63%", card._el.pct.textContent);
  check("progress bar filled", card._el.fill.style.width === "63%",
        card._el.fill.style.width);
  const rows = rowsOf(card);
  check("layer row", rows.includes("700 / 1109"), rows.includes("700 / 1109") ? "" : rows);
  check("elapsed formatted", rows.includes("45m"));
  check("remaining formatted as h+m", rows.includes("2h 19m"));
  check("resin rounded", rows.includes("56.9 mL"));
  check("ETA present", rows.includes("ETA"));
  check("thumbnail shown", !card._el.thumb.classList.contains("hidden"));
  check(
    "thumbnail src set",
    card._el.thumbimg.getAttribute("src") === "/api/image_proxy/image.x?token=abc"
  );
}

console.log("\noffline printer");
{
  const states = idleStates();
  states[`binary_sensor.${P}_online`] = s("off");
  states[`sensor.${P}_printer_state`] = s("unavailable reason:printer offline");
  const card = render(states);
  check("dot not lit", !card._el.dot.classList.contains("on"));
  check(
    "reason prefix stripped",
    card._el.pct.textContent === "printer offline",
    card._el.pct.textContent
  );
}

console.log("\noffline with no printer_state entity");
{
  // Distinguishes the two ways the card can say the printer is away: the
  // cloud's own wording, versus the generic fallback. Seeing the generic
  // one in the wild is the signal that printersStatus returned nothing.
  const states = idleStates();
  states[`binary_sensor.${P}_online`] = s("off");
  delete states[`sensor.${P}_printer_state`];
  const card = render(states);
  check(
    "falls back to the generic word",
    card._el.pct.textContent === "Offline",
    card._el.pct.textContent
  );
}

console.log("\nproblem reported");
{
  const states = printingStates();
  states[`binary_sensor.${P}_problem`] = s("on");
  const card = render(states);
  check("banner shown", !card._el.problem.classList.contains("hidden"));
}

console.log("\nlifecycle: order must not matter");
{
  // The card picker's preview does not guarantee setConfig runs before the
  // hass setter. Getting this wrong threw, and the picker rendered a
  // spinner that never resolved.
  const card = new Card();
  let threw = null;
  try {
    card.hass = { states: printingStates(), locale: { language: "en" } };
  } catch (err) {
    threw = err;
  }
  check("hass before setConfig does not throw", threw === null,
        threw ? threw.message : "");
  check("still rendered", card._el && card._el.pct.textContent === "63%",
        card._el?.pct.textContent);

  card.setConfig({});
  check("setConfig afterwards still fine", card._el.pct.textContent === "63%");
}

{
  // And the reverse: config with no hass yet must still draw something, or
  // the preview has zero height and spins.
  const card = new Card();
  let threw = null;
  try {
    card.setConfig({});
  } catch (err) {
    threw = err;
  }
  check("setConfig with no hass does not throw", threw === null,
        threw ? threw.message : "");
  check("shell drawn before hass arrives", Boolean(card._el), "");
  check(
    "placeholder shown rather than nothing",
    card._el && card._el.pct.textContent.length > 0,
    card._el?.pct.textContent
  );
  check(
    "art drawn so the card has height",
    card._el && card._el.art.innerHTML.includes("<svg")
  );
  card.hass = { states: printingStates(), locale: { language: "en" } };
  check("fills in once hass arrives", card._el.pct.textContent === "63%");
}

{
  // connectedCallback is how the picker attaches it.
  const card = new Card();
  let threw = null;
  try {
    card.connectedCallback();
  } catch (err) {
    threw = err;
  }
  check("connectedCallback before anything does not throw", threw === null,
        threw ? threw.message : "");
}

{
  // A render that throws must not leave a blank element behind.
  const card = new Card();
  card.setConfig({});
  let threw = null;
  try {
    card.hass = { get states() { throw new Error("boom"); } };
  } catch (err) {
    threw = err;
  }
  check("render errors are contained", threw === null, threw ? threw.message : "");
  check(
    "error surfaced on the card",
    card._el.job.textContent.startsWith("Card error:"),
    card._el.job.textContent
  );
}

console.log("\nno printer present");
{
  const card = render({ "sensor.something_else": s(1) });
  check(
    "explains itself rather than throwing",
    card._el.job.textContent.includes("No Anycubic M7 Pro found"),
    card._el.job.textContent
  );
}

console.log("\nexplicit prefix overrides detection");
{
  const card = render(printingStates(), { prefix: P });
  check("renders with configured prefix", card._el.pct.textContent === "63%");
}

console.log("\nmissing entities do not throw");
{
  const card = render({ [`sensor.${P}_release_film_layers`]: s(10) });
  check("renders with only the anchor entity", typeof card._el.pct.textContent === "string",
        card._el.pct.textContent);
}

console.log();
if (failures.length) {
  console.log(`FAILED: ${failures.length} -> ${failures.join(", ")}`);
  process.exit(1);
}
console.log("All card checks passed.");
