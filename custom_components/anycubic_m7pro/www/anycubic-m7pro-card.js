/*
 * Anycubic M7 Pro card.
 *
 * A plain custom element: no Lit, no build step, no dependencies. The
 * integration serves this file and registers it, so there is nothing to
 * install separately.
 */

const STATUS_LABEL = {
  idle: "Idle",
  printing: "Printing",
  complete: "Complete",
  cancelled: "Cancelled",
  downloading: "Downloading",
  checking: "Checking",
  preheating: "Preheating",
  slicing: "Slicing",
  levelling: "Levelling",
  unknown: "Unknown",
};

// Anchor used to find the printer automatically. This suffix is unique to
// the integration, so if it is present the rest of the entity ids follow.
const ANCHOR = "_release_film_layers";

const isBlank = (state) =>
  !state || state.state === "unknown" || state.state === "unavailable";

const num = (state) => {
  if (isBlank(state)) return null;
  const parsed = Number(state.state);
  return Number.isFinite(parsed) ? parsed : null;
};

/** Minutes as "2h 19m", or "48m" under an hour. */
function duration(minutes) {
  if (minutes === null) return "\u2014";
  const total = Math.max(0, Math.round(minutes));
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h ? `${h}h ${m}m` : `${m}m`;
}

function clockTime(state, hass) {
  if (isBlank(state)) return "\u2014";
  const when = new Date(state.state);
  if (Number.isNaN(when.getTime())) return "\u2014";
  return when.toLocaleTimeString(hass.locale?.language || undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

class AnycubicM7ProCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = config || {};
    this._prefix = null;
    this._built = false;
  }

  getCardSize() {
    return 8;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) {
      this._build();
      this._built = true;
    }
    this._render();
  }

  /**
   * Work out the entity prefix.
   *
   * Explicit config wins. Otherwise look for the anchor entity, so the card
   * works when simply dropped on a dashboard with no configuration.
   */
  _resolvePrefix() {
    if (this._config.prefix) return this._config.prefix;
    if (this._prefix) return this._prefix;
    const found = Object.keys(this._hass.states).find(
      (id) => id.startsWith("sensor.") && id.endsWith(ANCHOR)
    );
    if (found) {
      this._prefix = found.slice("sensor.".length, -ANCHOR.length);
    }
    return this._prefix;
  }

  _get(domain, suffix) {
    const prefix = this._resolvePrefix();
    if (!prefix) return undefined;
    return this._hass.states[`${domain}.${prefix}${suffix}`];
  }

  _build() {
    this.innerHTML = `
      <ha-card>
        <style>
          .wrap { padding: 16px; }
          .head {
            display: flex; align-items: center; justify-content: center;
            gap: 8px; margin-bottom: 4px;
          }
          .dot {
            width: 10px; height: 10px; border-radius: 50%;
            background: var(--error-color, #e5484d); flex: none;
          }
          .dot.on { background: var(--success-color, #3dab5c); }
          .title {
            font-size: 1.3rem; font-weight: 600;
            color: var(--primary-text-color); text-align: center;
          }
          .job {
            text-align: center; color: var(--secondary-text-color);
            font-size: .85rem; margin-bottom: 12px; min-height: 1.1em;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
          }
          .main { display: flex; align-items: center; gap: 16px; }
          .art { flex: 0 0 132px; }
          .art svg { width: 100%; height: auto; display: block; }
          .readout { flex: 1 1 auto; min-width: 0; }
          .pct {
            font-size: 2.6rem; font-weight: 700; line-height: 1;
            text-align: right; color: var(--primary-text-color);
            margin-bottom: 10px;
          }
          .pct.idle { color: var(--secondary-text-color); font-size: 1.6rem; }
          .rows { display: grid; gap: 3px; }
          .row {
            display: flex; justify-content: space-between; gap: 10px;
            font-size: .92rem;
          }
          .row .k { color: var(--secondary-text-color); font-weight: 500; }
          .row .v {
            color: var(--primary-text-color); font-weight: 600;
            text-align: right; overflow: hidden; text-overflow: ellipsis;
            white-space: nowrap;
          }
          .bar {
            margin-top: 14px; height: 6px; border-radius: 3px;
            background: var(--divider-color, #3a3a3a); overflow: hidden;
          }
          .bar > i {
            display: block; height: 100%; width: 0%;
            background: var(--primary-color); border-radius: 3px;
            transition: width .6s ease;
          }
          .thumb { margin-top: 14px; border-radius: 10px; overflow: hidden; }
          .thumb img { width: 100%; display: block; background: #000; }
          .problem {
            margin-top: 12px; padding: 8px 10px; border-radius: 8px;
            background: var(--error-color, #e5484d); color: #fff;
            font-size: .85rem; font-weight: 600; text-align: center;
          }
          .hidden { display: none; }
        </style>
        <div class="wrap">
          <div class="head">
            <span class="dot" id="dot"></span>
            <span class="title" id="title">Anycubic M7 Pro</span>
          </div>
          <div class="job" id="job"></div>
          <div class="main">
            <div class="art" id="art"></div>
            <div class="readout">
              <div class="pct" id="pct">\u2014</div>
              <div class="rows" id="rows"></div>
            </div>
          </div>
          <div class="bar"><i id="fill"></i></div>
          <div class="thumb hidden" id="thumb"><img id="thumbimg" alt=""/></div>
          <div class="problem hidden" id="problem"></div>
        </div>
      </ha-card>
    `;
    this._el = {
      dot: this.querySelector("#dot"),
      title: this.querySelector("#title"),
      job: this.querySelector("#job"),
      art: this.querySelector("#art"),
      pct: this.querySelector("#pct"),
      rows: this.querySelector("#rows"),
      fill: this.querySelector("#fill"),
      thumb: this.querySelector("#thumb"),
      thumbimg: this.querySelector("#thumbimg"),
      problem: this.querySelector("#problem"),
    };
  }

  /**
   * A resin printer, drawn to progress.
   *
   * On an LCD machine the model is pulled upward out of the vat, so the
   * platform starts submerged and rises as the print advances -- the
   * opposite of the FDM cards, where the bed drops away.
   */
  _art(progress, printing) {
    const travel = 52;
    const y = 96 - (travel * (progress ?? 0)) / 100;
    const accent = printing
      ? "var(--primary-color, #03a9f4)"
      : "var(--disabled-text-color, #6f6f6f)";
    return `
      <svg viewBox="0 0 120 150" role="img" aria-label="Printer">
        <!-- tower and base -->
        <rect x="14" y="8" width="16" height="118" rx="3"
              fill="none" stroke="var(--secondary-text-color)"
              stroke-width="3" opacity=".55"/>
        <rect x="8" y="126" width="104" height="14" rx="4"
              fill="var(--secondary-text-color)" opacity=".35"/>
        <!-- vat -->
        <rect x="34" y="96" width="72" height="30" rx="3"
              fill="none" stroke="var(--secondary-text-color)"
              stroke-width="3" opacity=".55"/>
        <rect x="37" y="104" width="66" height="19" rx="2"
              fill="${accent}" opacity=".18"/>
        <!-- arm -->
        <rect x="22" y="${y - 3}" width="26" height="6" rx="2"
              fill="${accent}" opacity=".85"/>
        <!-- build platform, with the part hanging beneath it -->
        <rect x="46" y="${y}" width="52" height="5" rx="2"
              fill="${accent}"/>
        <rect x="58" y="${y + 5}" width="28"
              height="${Math.max(0, (travel * (progress ?? 0)) / 100)}"
              rx="2" fill="${accent}" opacity=".5"/>
      </svg>
    `;
  }

  _render() {
    const hass = this._hass;
    if (!hass || !this._el) return;

    const prefix = this._resolvePrefix();
    if (!prefix) {
      this._el.job.textContent =
        "No Anycubic M7 Pro found. Add the integration, or set 'prefix:' in the card config.";
      return;
    }

    const status = this._get("sensor", "_status");
    const online = this._get("binary_sensor", "_online");
    const printing = this._get("binary_sensor", "_printing");
    const problem = this._get("binary_sensor", "_problem");
    const progress = num(this._get("sensor", "_progress"));
    const isPrinting = printing?.state === "on";

    // Title from the device name the entity already carries.
    const friendly = status?.attributes?.friendly_name;
    if (friendly) {
      this._el.title.textContent = friendly.replace(/\s+Status$/i, "");
    }

    this._el.dot.classList.toggle("on", online?.state === "on");

    const jobName = this._get("sensor", "_job_name");
    this._el.job.textContent = isBlank(jobName) ? "" : jobName.state;

    // Percentage, or the reason there isn't one.
    if (isPrinting && progress !== null) {
      this._el.pct.textContent = `${Math.round(progress)}%`;
      this._el.pct.classList.remove("idle");
    } else {
      const state = this._get("sensor", "_printer_state");
      this._el.pct.classList.add("idle");
      this._el.pct.textContent =
        online?.state === "on"
          ? STATUS_LABEL[status?.state] || "Idle"
          : isBlank(state)
            ? "Offline"
            : state.state.replace(/^unavailable reason:\s*/i, "");
    }

    this._el.art.innerHTML = this._art(isPrinting ? progress : 0, isPrinting);
    this._el.fill.style.width = `${isPrinting ? (progress ?? 0) : 0}%`;

    const layer = num(this._get("sensor", "_current_layer"));
    const layers = num(this._get("sensor", "_total_layers"));
    const rows = [];

    rows.push(["Status", STATUS_LABEL[status?.state] || "\u2014"]);

    if (isPrinting) {
      rows.push([
        "Layer",
        layer !== null && layers !== null ? `${layer} / ${layers}` : "\u2014",
      ]);
      rows.push(["ETA", clockTime(this._get("sensor", "_estimated_finish"), hass)]);
      rows.push(["Elapsed", duration(num(this._get("sensor", "_time_elapsed")))]);
      rows.push(["Remaining", duration(num(this._get("sensor", "_time_remaining")))]);
      const resin = num(this._get("sensor", "_job_resin_used"));
      rows.push(["Resin", resin === null ? "\u2014" : `${resin.toFixed(1)} mL`]);
    } else {
      // Idle: lifetime figures are more use than a column of dashes.
      const seen = this._get("sensor", "_last_seen");
      rows.push(["Last seen", isBlank(seen) ? "\u2014" : clockTime(seen, hass)]);
      rows.push(["Total prints", this._get("sensor", "_total_prints")?.state ?? "\u2014"]);
      const total = num(this._get("sensor", "_total_resin_used"));
      rows.push(["Resin used", total === null ? "\u2014" : `${total.toFixed(0)} mL`]);
      const film = this._get("sensor", "_release_film_layers");
      rows.push(["Film layers", isBlank(film) ? "\u2014" : film.state]);
    }

    this._el.rows.innerHTML = rows
      .map(
        ([k, v]) =>
          `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`
      )
      .join("");

    // Thumbnail. The image entity carries a cache-busting token in its
    // entity_picture, so assigning it directly is enough.
    const thumb = this._get("image", "_job_thumbnail");
    const picture = thumb?.attributes?.entity_picture;
    if (isPrinting && picture) {
      if (this._el.thumbimg.getAttribute("src") !== picture) {
        this._el.thumbimg.setAttribute("src", picture);
      }
      this._el.thumb.classList.remove("hidden");
    } else {
      this._el.thumb.classList.add("hidden");
    }

    if (problem?.state === "on") {
      this._el.problem.textContent = "Printer reported a problem";
      this._el.problem.classList.remove("hidden");
    } else {
      this._el.problem.classList.add("hidden");
    }
  }
}

if (!customElements.get("anycubic-m7pro-card")) {
  customElements.define("anycubic-m7pro-card", AnycubicM7ProCard);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "anycubic-m7pro-card",
    name: "Anycubic M7 Pro Card",
    description: "Status, progress and job preview for an Anycubic M7 Pro.",
    preview: true,
  });

  // eslint-disable-next-line no-console
  console.info("%c ANYCUBIC-M7PRO-CARD ", "color:#03a9f4;font-weight:700");
}
