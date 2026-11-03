// Sunhold sidebar panel: Today (one card per zone), History, Learning.
// Plain custom element, no build step. Renders on data, tab or tap changes only - never on
// the frequent `hass` updates - so it costs nothing while it sits open.
import {
  SIGNALS,
  esc,
  isNum,
  num,
  temp,
  pct,
  signalLabel,
  setLocale,
  time,
  dayLabel,
  dateLabel,
  daysAgo,
  ago,
  dayOf,
  zoneDay,
  comfort,
  nudge,
  bandSentence,
  canRate,
  headline,
  houseLine,
  zoneSeries,
  dayTag,
  dayDetails,
  kpis,
  kpiNote,
  modelVerdict,
  modelEffects,
  cloudLines,
  gateSentence,
  comfortLast,
  stepsSentence,
} from "./logic.js";
import { historyChart } from "./charts.js";

const REFRESH_MS = 60000;
const FLASH_MS = 4000;
const DAYS = 60;
const RECENT_DAYS = 14;
// "Rate" nudges only on the last week; older days can still be rated from their details.
const NUDGE_DAYS = 7;
const TABS = [
  ["today", "Today"],
  ["history", "History"],
  ["learning", "Learning"],
];
const SETTINGS_PATH = "/config/integrations/integration/sunhold";

const STYLE = /* css */ `
:host {
  display: block;
  min-height: 100%;
  background: var(--primary-background-color);
  color: var(--primary-text-color);
  font-family: var(--ha-font-family-body, Roboto, sans-serif);
  --sh-cold: var(--blue-color, #2196f3);
  --sh-ok: var(--green-color, #4caf50);
  --sh-warm: var(--deep-orange-color, #ff5722);
  --sh-sun: var(--amber-color, #ffc107);
  --sh-pause: var(--purple-color, #926bc7);
  --sh-solar: var(--light-green-color, #8bc34a);
  --sh-line: var(--divider-color, rgba(0, 0, 0, 0.12));
  --sh-radius: var(--ha-card-border-radius, 12px);
}
header {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--app-header-background-color, var(--primary-color));
  color: var(--app-header-text-color, var(--text-primary-color, #fff));
  border-bottom: var(--app-header-border-bottom, none);
}
.bar {
  display: flex;
  align-items: center;
  height: var(--header-height, 56px);
  padding: 0 12px;
  gap: 4px;
}
.bar h1 { margin: 0 0 0 8px; font-size: 20px; font-weight: 400; }
[role="tablist"] { display: flex; padding: 0 8px; }
[role="tab"] {
  flex: 1 1 0;
  max-width: 160px;
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
  color: inherit;
  opacity: 0.85;
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  padding: 10px 8px 12px;
  cursor: pointer;
}
[role="tab"][aria-selected="true"] { opacity: 1; border-bottom-color: currentColor; }
[role="tab"]:focus-visible { outline: 2px solid currentColor; outline-offset: -4px; }
main {
  max-width: 760px;
  margin: 0 auto;
  padding: 16px 16px 48px;
  display: grid;
  gap: 16px;
}
main:focus { outline: none; }
.card {
  background: var(--card-background-color, #fff);
  border-radius: var(--sh-radius);
  border: 1px solid var(--sh-line);
  box-shadow: var(--ha-card-box-shadow, none);
  padding: 16px;
}
.card.tone-sun { border-left: 4px solid var(--sh-sun); }
.card.tone-pause { border-left: 4px solid var(--sh-pause); }
.card.tone-solar { border-left: 4px solid var(--sh-solar); }
.banner {
  border-radius: var(--sh-radius);
  padding: 8px 8px 8px 16px;
  background: color-mix(in srgb, var(--error-color, #db4437) 14%, var(--card-background-color, #fff));
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 14px;
}
h2 {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--secondary-text-color);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
h3 { margin: 18px 0 4px; font-size: 15px; font-weight: 500; }
.headline { margin: 0; font-size: 22px; line-height: 1.3; font-weight: 400; }
p { margin: 4px 0; line-height: 1.45; }
.muted, .detail, small { color: var(--secondary-text-color); }
small { font-size: 12px; }
.detail { margin-top: 6px; }
.note { margin: 0; color: var(--secondary-text-color); font-size: 14px; padding: 0 4px; }
.fine { font-size: 12px; margin-top: 8px; }
.stats { display: flex; flex-wrap: wrap; gap: 8px 28px; margin: 16px 0; }
.stat { display: grid; gap: 2px; }
.stat .label { font-size: 12px; color: var(--secondary-text-color); }
.stat .value { font-size: 20px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 12px; }
.kpi { display: grid; gap: 2px; align-content: start; }
.kpi b { font-size: 28px; font-weight: 400; line-height: 1.1; }
.kpi .label { font-size: 13px; }
.kpi small { line-height: 1.3; }
.kpi .wait { font-size: 14px; color: var(--secondary-text-color); line-height: 1.3; padding-top: 4px; }
.ask { margin: 0 0 8px; font-size: 14px; color: var(--secondary-text-color); }
.seg { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.pill, .seg button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font: inherit;
  font-size: 14px;
  min-height: 44px;
  padding: 0 14px;
  border-radius: 22px;
  border: 1px solid var(--sh-line);
  background: transparent;
  color: var(--primary-text-color);
  text-decoration: none;
  cursor: pointer;
}
.seg button { padding: 0 6px; }
.seg button[aria-pressed="true"] { font-weight: 500; border-width: 2px; }
.seg button[data-signal="too_cold"][aria-pressed="true"] {
  border-color: var(--sh-cold);
  background: color-mix(in srgb, var(--sh-cold) 18%, transparent);
}
.seg button[data-signal="ok"][aria-pressed="true"] {
  border-color: var(--sh-ok);
  background: color-mix(in srgb, var(--sh-ok) 18%, transparent);
}
.seg button[data-signal="too_warm"][aria-pressed="true"] {
  border-color: var(--sh-warm);
  background: color-mix(in srgb, var(--sh-warm) 18%, transparent);
}
.seg button:disabled { opacity: 0.6; cursor: progress; }
.flash { margin: 8px 0 0; font-size: 13px; color: var(--secondary-text-color); }
.flash:empty { display: none; }
.zones { display: flex; gap: 8px; overflow-x: auto; }
.zones button { white-space: nowrap; }
.zones button[aria-pressed="true"] {
  background: var(--primary-color);
  color: var(--text-primary-color, #fff);
  border-color: transparent;
}
.chart { width: 100%; height: auto; display: block; margin: 8px 0 4px; }
.chart .grid { stroke: var(--sh-line); stroke-width: 1; }
.chart .axis { font-size: 12px; fill: var(--secondary-text-color); }
.chart .held { fill: var(--sh-sun); opacity: 0.16; }
.chart .held.off { opacity: 0.08; }
.chart .sel { fill: var(--primary-color); opacity: 0.18; }
.chart .target { stroke: var(--secondary-text-color); stroke-dasharray: 4 3; stroke-width: 1; }
.chart .actual { fill: none; stroke: var(--primary-color); stroke-width: 1.6; }
.chart .actual-dot { fill: var(--primary-color); }
.chart .pred { fill: var(--card-background-color, #fff); stroke: var(--primary-text-color); stroke-width: 1.2; }
.chart .override { fill: var(--sh-pause); }
.chart .hit { fill: transparent; cursor: pointer; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); }
.legend i { display: inline-block; width: 10px; height: 10px; margin-right: 5px; vertical-align: -1px; border-radius: 50%; }
.legend .l-actual { background: var(--primary-color); }
.legend .l-pred { border: 1.5px solid var(--primary-text-color); box-sizing: border-box; }
.legend .l-held { background: var(--sh-sun); opacity: 0.5; border-radius: 2px; }
.legend .l-target { border-top: 1.5px dashed var(--secondary-text-color); border-radius: 0; height: 0; width: 14px; vertical-align: 3px; }
.legend .l-over { width: 0; height: 0; border-radius: 0; border: 5px solid transparent; border-bottom: 9px solid var(--sh-pause); border-top: 0; }
.legend .l-fb { background: linear-gradient(90deg, var(--sh-cold) 33%, var(--sh-ok) 33% 66%, var(--sh-warm) 66%); }
table.k { width: 100%; border-collapse: collapse; font-size: 14px; }
table.k th { font-weight: 400; color: var(--secondary-text-color); font-size: 12px; text-align: right; padding: 0 0 6px 8px; }
table.k td { padding: 8px 0 8px 8px; text-align: right; border-top: 1px solid var(--sh-line); }
table.k td:first-child, table.k th:first-child { text-align: left; padding-left: 0; }
table.k td.big { font-size: 18px; white-space: nowrap; }
ul.days { list-style: none; margin: 0 -16px -16px; padding: 0; }
ul.days li { border-top: 1px solid var(--sh-line); }
ul.days:not(:last-child) { margin-bottom: 0; }
.row {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
  min-height: 52px;
  padding: 8px 16px;
  font: inherit;
  font-size: 14px;
  color: inherit;
  background: none;
  border: 0;
  text-align: left;
  cursor: pointer;
}
.row .date { flex: 0 0 84px; }
.row .main { flex: 1 1 auto; display: grid; }
.row[aria-expanded="true"] { background: rgba(127, 127, 127, 0.08); }
.tag, .chip {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
  background: rgba(127, 127, 127, 0.15);
}
.tag.sun { background: color-mix(in srgb, var(--sh-sun) 30%, transparent); }
.tag.pause { background: color-mix(in srgb, var(--sh-pause) 25%, transparent); }
.tag.solar { background: color-mix(in srgb, var(--sh-solar) 30%, transparent); }
.chip.rate { border: 1px solid var(--primary-color); background: transparent; color: var(--primary-color); }
.chip.good { background: color-mix(in srgb, var(--sh-ok) 25%, transparent); }
.chip.fair { background: color-mix(in srgb, var(--sh-sun) 30%, transparent); }
.chip.poor { background: color-mix(in srgb, var(--sh-warm) 25%, transparent); }
.mark { font-size: 12px; white-space: nowrap; }
.mark .ovr { color: var(--sh-pause); }
.fbdot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; vertical-align: -1px; }
.fbdot.too_cold { background: var(--sh-cold); }
.fbdot.ok { background: var(--sh-ok); }
.fbdot.too_warm { background: var(--sh-warm); }
.more { padding: 4px 16px 16px; font-size: 14px; }
.more p { margin: 2px 0 6px; }
.older { margin: 0 -16px -16px; border-top: 1px solid var(--sh-line); }
.older button { width: 100%; min-height: 48px; background: none; border: 0; font: inherit; font-size: 14px; color: var(--primary-color); cursor: pointer; }
.verdict { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 18px; margin: 0; }
details { margin-top: 8px; font-size: 14px; }
summary { cursor: pointer; color: var(--primary-color); min-height: 32px; display: flex; align-items: center; }
details p { color: var(--secondary-text-color); }
.empty { text-align: center; padding: 32px 16px; }
.empty .headline { font-size: 18px; margin-bottom: 8px; }
.empty .pill { margin-top: 12px; }
@media (max-width: 450px) {
  main { padding: 12px 12px 40px; gap: 12px; }
  .headline { font-size: 20px; }
}
`;

class SunholdPanel extends HTMLElement {
  constructor() {
    super();
    this._tab = "today";
    this._zone = null;
    this._selected = null;
    this._showAll = false;
    this._busy = new Set();
    this._flash = new Map();
    this._data = null;
    this._error = null;
    this._updated = null;
    this._onVisible = () => {
      if (!document.hidden) this._load();
    };
    // The chart is drawn for the width it has, so redraw after a resize where it shows.
    this._onResize = () => {
      clearTimeout(this._resizeTimer);
      this._resizeTimer = setTimeout(() => {
        if (this._tab === "learning") this._render();
      }, 250);
    };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (hass?.locale !== this._locale) {
      this._locale = hass?.locale;
      setLocale(hass?.locale);
      if (!first) this._render();
    }
    if (this._menu) this._menu.hass = hass;
    if (first && this.isConnected) this._load();
  }

  get hass() {
    return this._hass;
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  set panel(panel) {
    this._panel = panel;
  }

  connectedCallback() {
    if (!this.shadowRoot) this._shell();
    this._timer = setInterval(() => {
      if (!document.hidden) this._load();
    }, REFRESH_MS);
    document.addEventListener("visibilitychange", this._onVisible);
    window.addEventListener("resize", this._onResize);
    if (this._hass) this._load();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._onVisible);
    window.removeEventListener("resize", this._onResize);
  }

  _shell() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${STYLE}</style>
      <header>
        <div class="bar"><span class="menu"></span><h1>Sunhold</h1></div>
        <div role="tablist" aria-label="Sunhold">${TABS.map(
          ([id, label]) => `<button role="tab" id="tab-${id}" data-tab="${id}" aria-controls="view">${label}</button>`,
        ).join("")}</div>
      </header>
      <main id="view" role="tabpanel" tabindex="-1"></main>`;
    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    root.querySelector(".menu").append(this._menu);
    this._main = root.querySelector("main");
    root.addEventListener("click", (ev) => this._click(ev));
    root.querySelector('[role="tablist"]').addEventListener("keydown", (ev) => this._tabKey(ev));
    this._renderTabs();
    this._render();
  }

  get _tz() {
    return this._hass?.config?.time_zone;
  }

  get _admin() {
    return this._hass?.user?.is_admin !== false;
  }

  async _load() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    try {
      this._data = await this._hass.callWS({ type: "sunhold/overview", days: DAYS });
      this._error = null;
      this._updated = new Date();
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
    }
    this._render();
  }

  // ---------------------------------------------------------------- events

  _setTab(tab, focus = false) {
    this._tab = tab;
    this._renderTabs();
    this._render();
    if (focus) this.shadowRoot.getElementById(`tab-${tab}`)?.focus();
  }

  _tabKey(ev) {
    const ids = TABS.map(([id]) => id);
    const at = ids.indexOf(this._tab);
    const next = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: ids.length - 1 }[ev.key];
    if (next === undefined) return;
    ev.preventDefault();
    this._setTab(ids[(next + ids.length) % ids.length], true);
  }

  _click(ev) {
    const target = ev.target.closest(
      "[data-tab],[data-signal],[data-zone-pick],[data-date],[data-retry],[data-older],[data-nav]",
    );
    if (!target) return;
    const d = target.dataset;
    if (d.tab) {
      this._setTab(d.tab);
    } else if (d.signal) {
      this._feedback(d.zone, d.signal, d.day);
    } else if (d.zonePick) {
      this._zone = d.zonePick;
      this._selected = null;
      this._render();
    } else if (d.retry !== undefined) {
      this._load();
    } else if (d.older !== undefined) {
      this._showAll = true;
      this._render();
    } else if (d.nav) {
      ev.preventDefault();
      history.pushState(null, "", d.nav);
      window.dispatchEvent(new CustomEvent("location-changed"));
    } else if (d.date) {
      const fromChart = !target.classList.contains("row");
      this._selected = this._selected === d.date && !fromChart ? null : d.date;
      if (!fromChart) {
        this._render();
        return;
      }
      // A day tapped on the accuracy chart opens in History, where it can be read and rated.
      if (d.zone) this._zone = d.zone;
      if (daysAgo(d.date, this._data.today) >= RECENT_DAYS) this._showAll = true;
      this._setTab("history");
      this.shadowRoot
        .querySelector(`.row[data-date="${CSS.escape(d.date)}"]`)
        ?.scrollIntoView({ block: "center" });
    }
  }

  _say(key, text) {
    const old = this._flash.get(key);
    if (old) clearTimeout(old.timer);
    const find = () => this.shadowRoot?.querySelector(`.flash[data-key="${CSS.escape(key)}"]`);
    const timer = setTimeout(() => {
      this._flash.delete(key);
      const el = find();
      if (el) el.textContent = "";
    }, FLASH_MS);
    this._flash.set(key, { text, timer });
    const el = find();
    if (el) el.textContent = text;
  }

  async _feedback(zoneId, signal, date) {
    const data = this._data;
    if (!data) return;
    const day = date || data.today;
    const key = `${zoneId}|${day}`;
    if (this._busy.has(key)) return;
    const current = zoneDay(data, day, zoneId)?.feedback;
    if (current === signal) {
      this._say(key, `You said “${signalLabel(signal)}” — tap another to change it.`);
      return;
    }
    this._busy.add(key);
    this._setFeedback(day, zoneId, signal);
    this._render();
    let text;
    try {
      const msg = { type: "sunhold/feedback", zone_id: zoneId, signal };
      if (day !== data.today) msg.date = day;
      await this._hass.callWS(msg);
      text = current ? "Changed — Sunhold will learn from this." : "Saved — Sunhold will learn from this.";
    } catch (err) {
      text = `Couldn't save: ${err?.message || err}`;
    } finally {
      this._busy.delete(key);
    }
    await this._load(); // also puts back the old choice if saving failed
    this._say(key, text);
  }

  _setFeedback(date, zoneId, signal) {
    let day = dayOf(this._data, date);
    if (!day) {
      day = { date, house: {}, zones: {} };
      this._data.days = [...(this._data.days || []), day];
    }
    day.zones = day.zones || {};
    day.zones[zoneId] = { ...(day.zones[zoneId] || {}), feedback: signal };
  }

  // ---------------------------------------------------------------- render

  _renderTabs() {
    for (const btn of this.shadowRoot.querySelectorAll('[role="tab"]')) {
      const on = btn.dataset.tab === this._tab;
      btn.setAttribute("aria-selected", String(on));
      btn.tabIndex = on ? 0 : -1;
    }
    this._main?.setAttribute("aria-labelledby", `tab-${this._tab}`);
  }

  _render() {
    if (!this._main) return;
    let html;
    if (this._error && !this._data) {
      html = this._empty("Couldn't load Sunhold", esc(this._error), `<button class="pill" data-retry>Try again</button>`);
    } else if (!this._data) {
      html = `<p class="note">Loading…</p>`;
    } else if (!this._data.zones?.length) {
      html = this._empty(
        "No zones yet",
        "Add a zone for each thermostat, then come back here.",
        `<a class="pill" href="${SETTINGS_PATH}" data-nav="${SETTINGS_PATH}">Open Sunhold settings</a>`,
      );
    } else {
      const views = { today: () => this._today(), history: () => this._history(), learning: () => this._learning() };
      html = this._banner() + views[this._tab]();
    }
    this._main.innerHTML = html;
  }

  _banner() {
    if (!this._error) return "";
    const when = this._updated ? ` Showing what it knew at ${time(this._updated.toISOString(), this._tz)}.` : "";
    return `<div class="banner" role="alert"><span>Couldn't refresh: ${esc(this._error)}.${when}</span><button class="pill" data-retry>Retry</button></div>`;
  }

  _empty(title, text, extra = "") {
    return `<section class="card empty"><p class="headline">${title}</p><p class="muted">${text}</p>${extra}</section>`;
  }

  _feedbackBlock(zone, date) {
    const data = this._data;
    if (zone.season === "off" || !canRate(date, data.today)) return "";
    if (!this._admin) return `<p class="muted fine">Only an administrator can give feedback.</p>`;
    const current = zoneDay(data, date, zone.id)?.feedback;
    const key = `${zone.id}|${date}`;
    const busy = this._busy.has(key);
    const isToday = date === data.today;
    const id = `ask-${esc(zone.id)}-${esc(date)}`;
    const buttons = SIGNALS.map(
      ([signal, label]) =>
        `<button data-signal="${signal}" data-zone="${esc(zone.id)}"${isToday ? "" : ` data-day="${esc(date)}"`} aria-pressed="${current === signal}"${busy ? " disabled" : ""}>${current === signal ? "✓ " : ""}${label}</button>`,
    ).join("");
    return `<p class="ask" id="${id}">${isToday ? "How does it feel today?" : `How did ${esc(dateLabel(date))} feel?`}</p>
      <div class="seg" role="group" aria-labelledby="${id}">${buttons}</div>
      <p class="flash" role="status" data-key="${esc(key)}">${esc(this._flash.get(key)?.text || "")}</p>`;
  }

  _updatedLine() {
    return this._updated ? `Updated ${time(this._updated.toISOString(), this._tz)}` : "";
  }

  // Today: what each zone is doing, and the one thing to do here - say how it feels.
  _today() {
    const data = this._data;
    const fresh = !(data.days || []).some((d) => d.date !== data.today);
    const line = [houseLine(data), this._updatedLine()].filter(Boolean).join(" · ");
    const cards = data.zones.map((zone) => {
      const h = headline(zone, data, this._tz);
      const c = comfort(zone);
      const stats = [this._stat("Indoor now", temp(zone.indoor))];
      const nudged = c ? nudge(c.offset) : "";
      if (c && (nudged || h.tone !== "idle")) stats.push(this._stat("Aim", temp(c.target), nudged));
      return `<section class="card tone-${h.tone}">
        <h2>${esc(zone.title)}</h2>
        <p class="headline">${esc(h.text)}</p>
        ${h.detail ? `<p class="detail">${esc(h.detail)}</p>` : ""}
        <div class="stats">${stats.join("")}</div>
        ${this._feedbackBlock(zone, data.today)}
      </section>`;
    });
    return [
      fresh
        ? `<section class="card"><h2>Welcome</h2><p>Sunhold is new here. It starts from sensible defaults and learns this house day by day — telling it how each day feels is the fastest way to teach it.</p></section>`
        : "",
      this._kpiCard(),
      line ? `<p class="note">${esc(line)}</p>` : "",
      ...cards,
    ].join("");
  }

  _stat(label, value, sub = "") {
    return `<div class="stat"><span class="label">${label}</span><span class="value">${value}</span>${sub ? `<small>${esc(sub)}</small>` : ""}</div>`;
  }

  // The two goals - run the heat pump on sun, keep people comfortable - as big numbers.
  _kpiCard() {
    const k = kpis(this._data, 7);
    const band = bandSentence(this._data);
    const tile = (label, x, describe) => {
      const body = x.ready
        ? `<b>${pct(x.value)}</b><span class="label">${esc(label)}</span><small>${esc(kpiNote(x))}</small>`
        : `<span class="label">${esc(label)}</span><span class="wait">${esc(kpiNote(x))}</span>`;
      return `<div class="kpi" title="${esc(describe)}" aria-description="${esc(describe)}">${body}</div>`;
    };
    const tiles = [tile("Heat pump on solar", k.solar, "Share of the heat pump's energy that came from the solar panels.")];
    for (const z of k.zones) tiles.push(tile(`${z.title} comfortable`, z.comfort, band));
    return `<section class="card"><h2>Last 7 days</h2><div class="kpis">${tiles.join("")}</div>
      <p class="muted fine">${esc(band)} Full days; today is not counted.</p></section>`;
  }

  _kpiTable() {
    const data = this._data;
    const spans = [kpis(data, 7), kpis(data, 30)];
    const cell = (x) =>
      x.ready
        ? `<td class="big">${pct(x.value)}</td>`
        : `<td><small>collecting, day ${Math.min(x.days + 1, x.span)} of ${x.span}</small></td>`;
    const rows = [`<tr><td>Heat pump on solar</td>${spans.map((k) => cell(k.solar)).join("")}</tr>`];
    rows.push(
      `<tr><td>Heat pump energy used</td>${spans.map((k) => `<td>${k.pumpKwh === null ? "–" : `${num(k.pumpKwh, 0)} kWh`}</td>`).join("")}</tr>`,
    );
    data.zones.forEach((z, i) => {
      rows.push(`<tr><td>${esc(z.title)} comfortable</td>${spans.map((k) => cell(k.zones[i].comfort)).join("")}</tr>`);
    });
    return `<section class="card">
      <h2>Solar and comfort</h2>
      <table class="k"><tr><th></th><th>Last 7 days</th><th>Last 30 days</th></tr>${rows.join("")}</table>
      <p class="muted fine">${esc(bandSentence(data))} Full days; today is not counted.</p>
    </section>`;
  }

  _zonePicker(zones, zone) {
    if (zones.length < 2) return "";
    const buttons = zones
      .map((z) => `<button class="pill" data-zone-pick="${esc(z.id)}" aria-pressed="${z.id === zone.id}">${esc(z.title)}</button>`)
      .join("");
    return `<div class="zones" role="group" aria-label="Zone">${buttons}</div>`;
  }

  _pickZone() {
    const zones = this._data.zones;
    if (!zones.some((z) => z.id === this._zone)) this._zone = zones[0].id;
    return zones.find((z) => z.id === this._zone);
  }

  // History: the goals over 7 and 30 days, then each day in words - tap to read or rate it.
  _history() {
    const data = this._data;
    const zone = this._pickZone();
    const rows = zoneSeries(data, zone.id)
      .filter((r) => !r.empty || (r.date === data.today && zone.season !== "off"))
      .reverse();
    const head = this._kpiTable() + this._zonePicker(data.zones, zone);
    if (!rows.length) {
      return head + this._empty(
        "Nothing recorded yet",
        "Each day shows up here once Sunhold has seen it: the morning plan, the afternoon peak and anything that paused the heating.",
      );
    }
    const recent = rows.filter((r) => daysAgo(r.date, data.today) < RECENT_DAYS);
    const shown = this._showAll ? rows : recent;
    const rateable = this._admin && zone.season !== "off";
    const list = shown.map((r) => {
      const open = r.date === this._selected;
      const tag = dayTag(r);
      const peak = isNum(r.actual) ? `Peak ${temp(r.actual)}` : r.date === data.today ? "So far today" : "";
      const forecast = isNum(r.predicted) ? `<small>forecast ${temp(r.predicted)}</small>` : "";
      const marks = [];
      if (r.overrides) marks.push(`<span class="ovr" role="img" aria-label="Thermostat changed by hand" title="Changed by hand">▲</span>`);
      if (r.feedback) {
        const said = signalLabel(r.feedback);
        marks.push(`<span class="fbdot ${esc(r.feedback)}" role="img" aria-label="Rated ${esc(said.toLowerCase())}" title="${esc(said)}"></span>`);
      }
      const rate = rateable && !r.feedback && daysAgo(r.date, data.today) < NUDGE_DAYS ? `<span class="chip rate">Rate</span>` : "";
      const more = open
        ? `<div class="more">${dayDetails(dayOf(data, r.date), zone.id, data, this._tz).map((l) => `<p>${esc(l)}</p>`).join("") || `<p class="muted">Nothing else recorded.</p>`}
            ${this._feedbackBlock(zone, r.date)}</div>`
        : "";
      return `<li><button class="row" data-date="${esc(r.date)}" aria-expanded="${open}">
          <span class="date">${esc(dayLabel(r.date, data.today))}</span>
          <span class="main"><span>${peak}</span>${forecast}</span>
          ${tag ? `<span class="tag ${tag.kind}">${tag.label}</span>` : ""}
          ${marks.length ? `<span class="mark">${marks.join(" ")}</span>` : ""}
          ${rate}
        </button>${more}</li>`;
    });
    const older =
      !this._showAll && rows.length > recent.length
        ? `<div class="older"><button data-older>Show older days (${rows.length - recent.length})</button></div>`
        : "";
    return `${head}
      <section class="card">
        <h2>${this._showAll ? `Last ${DAYS} days` : `Last ${RECENT_DAYS} days`}</h2>
        <p class="muted fine" style="margin:0 0 8px">Tap a day to see what happened${rateable ? " and say how it felt" : ""}.</p>
        <ul class="days">${list.join("")}</ul>
        ${older}
      </section>`;
  }

  _accuracyChart(zone, width) {
    const series = zoneSeries(this._data, zone.id);
    if (!series.some((r) => isNum(r.predicted) && isNum(r.actual))) return "";
    const c = comfort(zone);
    const label = `${zone.title}: forecast and actual afternoon peak per day. The History tab lists the same days in words.`;
    const svg = historyChart(series, { target: c?.target, width, label }).replace(
      /data-date=/g,
      `data-zone="${esc(zone.id)}" data-date=`,
    );
    return `${svg}
      <div class="legend">
        <span><i class="l-actual"></i>Actual peak</span>
        <span><i class="l-pred"></i>Forecast</span>
        <span><i class="l-held"></i>Sun hold</span>
        ${c ? `<span><i class="l-target"></i>Aim now</span>` : ""}
        <span><i class="l-fb"></i>Rated</span>
        <span><i class="l-over"></i>Changed by hand</span>
      </div>`;
  }

  // Learning: what Sunhold believes about the house and the people, in words.
  _learning() {
    const data = this._data;
    const width = Math.min(720, (this._main.clientWidth || 390) - 64);
    const zoneCards = data.zones.map((zone) => {
      const v = modelVerdict(zone.model, data);
      const effects = modelEffects(zone.model, data);
      const order = zone.season === "summer" ? ["summer", "winter"] : ["winter", "summer"];
      const seasons = order.map((season, i) => {
        const s = comfort(zone, season);
        if (!s) return "";
        const last = comfortLast(zone, season);
        const how = nudge(s.offset);
        // The other season only once something was learned for it.
        if (i > 0 && !last && !how) return "";
        const name = season === "winter" ? "Winter" : "Summer";
        return `<p>${name} aim <b>${temp(s.target)}</b>${how ? ` — ${esc(how)}` : ""}</p>
          <p class="muted">${last ? `Last: ${esc(last.text)}, ${esc(ago(last.at))}.` : "No feedback yet."}</p>`;
      });
      const details = effects.length
        ? `<details><summary>How it works it out</summary>${effects.map((e) => `<p>${esc(e[0].toUpperCase() + e.slice(1))}.</p>`).join("")}</details>`
        : "";
      return `<section class="card">
        <h2>${esc(zone.title)}</h2>
        <h3>Afternoon peak forecast</h3>
        <p class="verdict">${esc(v.text)}${v.quality ? ` <span class="chip ${v.quality}">${v.quality}</span>` : ""}</p>
        <p class="muted">${esc(v.sub)}</p>
        ${this._accuracyChart(zone, width)}
        ${details}
        <h3>Comfort</h3>
        ${seasons.join("")}
      </section>`;
    });
    const cmap = data.cloud_map && Object.keys(data.cloud_map).length ? data.cloud_map : null;
    const cloud = cloudLines(cmap, data.clear_day, data);
    return `${zoneCards.join("")}
      <section class="card">
        <h2>House</h2>
        <h3>Forecast trust</h3>
        <p>${esc(cloud.head)}</p>
        <p class="muted">${esc(cloud.sub)}</p>
        <h3>Solar pause</h3>
        <p>${esc(gateSentence(data.gate, data.features))}</p>
      </section>
      <p class="note">${esc(stepsSentence(data))}</p>`;
  }
}

if (!customElements.get("sunhold-panel")) customElements.define("sunhold-panel", SunholdPanel);
