// node tests/frontend/test_logic.mjs
import assert from "node:assert/strict";
import * as L from "../../custom_components/sunhold/frontend/logic.js";
import { yRange, yTicks, historyChart } from "../../custom_components/sunhold/frontend/charts.js";

let passed = 0;
const test = (name, fn) => {
  try {
    L.setLocale(null);
    fn();
    passed += 1;
  } catch (err) {
    console.error(`FAIL ${name}`);
    throw err;
  }
};

const zone = (over = {}) => ({
  id: "ground",
  title: "Ground floor",
  season: "winter",
  intent: "schedule",
  setpoint: null,
  until: null,
  indoor: 19.6,
  window_open: false,
  targets: { winter: 20.5, summer: 25.5 },
  comfort: { winter: 0.25, summer: 0, history: [] },
  model: { a: 0.8, b: -0.05, d: 0.1, c: -0.6, days: 34, mae: 0.271 },
  limits: { sun_floor: 19, window_pause_heat: 16, summer_pause: 30 },
  ...over,
});
const overview = (over = {}) => ({
  today: "2026-11-03",
  features: { sun_hold: true, solar_cooling: true, window_pause: true, solar_boost: true },
  hold_until: "13:00",
  comfort_band: { low: 0.5, high: 1 },
  min_days: { model: 5, sun: 10 },
  learning_steps: { feedback: 0.25, override: 0.1 },
  window_delays: { open: 300, closed: 120 },
  sun_unit: "kWh/m²",
  gate: { paused: false, cut: 820, resume: 1380, headroom: null, draw_samples: 12 },
  zones: [zone()],
  days: [],
  ...over,
});
const iso = (back) => new Date(Date.UTC(2026, 10, 3) - back * 86400000).toISOString().slice(0, 10);

test("numbers", () => {
  assert.equal(L.num(19.649), "19.6");
  assert.equal(L.num(-0.04), "0.0");
  assert.equal(L.num(-1.25), "−1.3");
  assert.equal(L.num(null), "–");
  assert.equal(L.temp(20), "20.0 °C");
  assert.equal(L.signed(0.25), "+0.3");
  assert.equal(L.signed(-0.25), "−0.3");
  assert.equal(L.signed(0), "0.0");
  assert.equal(L.pct(55.4), "55 %");
  assert.equal(L.pct(null), "–");
  assert.equal(L.duration(300), "5 minutes");
  assert.equal(L.duration(60), "1 minute");
  assert.equal(L.duration(45), "45 seconds");
});

test("escaping", () => {
  assert.equal(L.esc(`<b a="1">&'`), "&lt;b a=&quot;1&quot;&gt;&amp;&#39;");
  assert.equal(L.esc(null), "");
});

test("times follow the house zone and the user's 12/24 h choice", () => {
  assert.equal(L.time("2026-11-03T12:00:00+00:00", "Europe/Rome"), "13:00");
  assert.equal(L.time("13:00"), "13:00");
  assert.equal(L.time("7:30:00"), "07:30");
  assert.equal(L.time("nonsense"), "");
  assert.equal(L.time("2026-11-03T12:00:00+00:00", "Not/AZone"), L.time("2026-11-03T12:00:00+00:00"));
  L.setLocale({ language: "en-US", time_format: "12" });
  assert.match(L.time("13:00"), /^01:00\s?PM$/i);
  L.setLocale({ language: "en-US", time_format: "24" });
  assert.equal(L.time("13:00"), "13:00");
  L.setLocale({ language: "xx-INVALID-@@", time_format: "24" });
  assert.equal(L.dateLabel("2026-11-03"), "Tue 3 Nov");
});

test("dates", () => {
  assert.equal(L.dateLabel("2026-11-03"), "Tue 3 Nov");
  assert.equal(L.dateLabel("2026-11-03", { short: true }), "3 Nov");
  assert.equal(L.dayLabel("2026-11-03", "2026-11-03"), "Today");
  assert.equal(L.dayLabel("2026-03-29", "2026-03-30"), "Yesterday"); // across DST
  assert.equal(L.dayLabel("2026-10-30", "2026-11-03"), "Fri 30 Oct");
  const now = Date.parse("2026-11-03T12:00:00Z");
  assert.equal(L.ago("2026-11-03T11:30:00Z", now), "30 min ago");
  assert.equal(L.ago("2026-11-02T12:00:00Z", now), "yesterday");
  assert.equal(L.ago("2026-10-30T12:00:00Z", now), "4 days ago");
  assert.equal(L.canRate("2026-11-03", "2026-11-03"), true);
  assert.equal(L.canRate(iso(30), "2026-11-03"), true);
  assert.equal(L.canRate(iso(31), "2026-11-03"), false);
  assert.equal(L.canRate("2026-11-04", "2026-11-03"), false);
});

test("aim, nudge and band come from the data", () => {
  assert.deepEqual(L.comfort(zone()), { set: 20.5, offset: 0.25, target: 20.75 });
  assert.equal(L.comfort(zone({ season: "off" })), null);
  assert.equal(L.nudge(0.25), "nudged 0.3 °C warmer by your feedback");
  assert.equal(L.nudge(-0.5), "nudged 0.5 °C cooler by your feedback");
  assert.equal(L.nudge(0.01), "");
  assert.equal(
    L.bandSentence(overview({ comfort_band: { low: 1, high: 2 } })),
    "Comfortable = from 1.0 °C below to 2.0 °C above the aim, 07–23.",
  );
  assert.match(L.bandSentence({}), /0\.5 °C below to 1\.0 °C above/); // older backend
  assert.match(L.stepsSentence(overview({ learning_steps: { feedback: 0.3, override: 0.05 } })), /0\.30 °C.*0\.05 °C/);
});

test("headline: sun hold quotes the configured floor, not the live setpoint", () => {
  const z = zone({ intent: "sun_hold", setpoint: 18.5, until: "2026-11-03T12:00:00+00:00" });
  const ov = overview({
    days: [{ date: "2026-11-03", house: {}, zones: { ground: { plan: { hold: true, predicted_peak: 22.14, target: 20.75 } } } }],
  });
  const h = L.headline(z, ov, "Europe/Rome");
  assert.equal(h.text, "Holding for the sun until 13:00 — heats only below 19.0 °C");
  assert.equal(h.tone, "sun");
  assert.match(h.detail, /22\.1 °C/);
  assert.match(L.headline({ ...z, until: null }, ov).text, /until 13:00/);
  assert.equal(L.headline({ ...z, until: null, limits: {} }, ov).text, "Holding for the sun until 13:00");
});

test("headline: pauses use limits and window delays", () => {
  const w = L.headline(zone({ intent: "window_pause", window_open: true }), overview());
  assert.equal(w.text, "Window open — heating paused (heats only below 16.0 °C)");
  assert.equal(w.detail, "Back on schedule 2 minutes after it closes.");
  const ws = L.headline(zone({ intent: "window_pause", season: "summer" }), overview({ window_delays: { open: 300, closed: 60 } }));
  assert.equal(ws.text, "Window open — cooling paused (cools only above 30.0 °C)");
  assert.equal(ws.detail, "Back on schedule 1 minute after it closes.");
  const p = L.headline(zone({ intent: "solar_pause", season: "summer" }), overview());
  assert.equal(p.text, "Cooling paused — the panels can't cover it (cools only above 30.0 °C)");
  const counting = L.headline(zone({ window_open: true }), overview());
  assert.equal(counting.detail, "A window is open — heating pauses after 5 minutes (heats only below 16.0 °C).");
});

test("headline: solar boost and unknown intents", () => {
  const w = L.headline(zone({ intent: "solar_boost", setpoint: 21 }), overview());
  assert.equal(w.text, "Storing solar heat — up to 21.0 °C");
  assert.equal(w.tone, "solar");
  const s = L.headline(zone({ intent: "solar_boost", season: "summer", setpoint: 25 }), overview());
  assert.equal(s.text, "Storing solar cool — down to 25.0 °C");
  assert.equal(L.headline(zone({ intent: "something_new" }), overview()).text, "On its schedule");
  assert.match(L.houseLine(overview({ surplus: true })), /Solar surplus now/);
  assert.equal(L.houseLine(overview({ surplus: true, features: { solar_boost: false } })), "");
});

test("headline: schedule explains why", () => {
  const plan = (p, extra = {}) => overview({ days: [{ date: "2026-11-03", house: {}, zones: { ground: { plan: p, ...extra } } }] });
  assert.match(L.headline(zone(), overview()).detail, /No plan yet/);
  assert.match(
    L.headline(zone(), plan({ hold: false, predicted_peak: 20.9, target: 20.75 })).detail,
    /20\.9 °C, not safely above 20\.8 °C\. On a sun hold it heats only below 19\.0 °C\./,
  );
  assert.match(L.headline(zone(), plan({ hold: false, fallback: true, cloud: 80 })).detail, /too cloudy \(80 % cloud\)/);
  // The model alone would have held; the forecast's cloud vetoed it.
  assert.match(
    L.headline(zone(), plan({ hold: false, cloud_vetoed: true, predicted_peak: 21.2, target: 20.25, cloud: 100 })).detail,
    /too cloudy \(100 % cloud\)/,
  );
  assert.match(L.headline(zone(), plan({ hold: true }, { cancelled: "no_sun" })).detail, /called off/);
  assert.match(L.headline(zone(), plan({ hold: true })).detail, /ended at 13:00/);
  assert.equal(L.headline(zone(), overview({ features: { sun_hold: false } })).detail, "Sun hold is switched off.");
  const off = L.headline(zone({ season: "off" }), overview());
  assert.equal(off.text, "Heating and cooling are off");
  assert.ok(off.detail);
  assert.match(L.headline(zone({ season: "summer" }), overview()).detail, /cools only above 30\.0 °C/);
});

test("history series and tags", () => {
  const ov = overview({
    days: [
      { date: "2026-11-02", house: {}, zones: { ground: { peak: 21.4, plan: { hold: true, predicted_peak: 21.2 }, overrides: [{}], feedback: "too_cold" } } },
      { date: "2026-11-01", house: {}, zones: { ground: { comfort_pct: 80 } } },
      { date: "2026-10-31", house: {}, zones: {} },
    ],
  });
  const s = L.zoneSeries(ov, "ground");
  assert.deepEqual(s.map((r) => r.date), ["2026-10-31", "2026-11-01", "2026-11-02"]);
  assert.equal(s[0].empty, true);
  assert.equal(s[1].empty, false);
  assert.equal(s[2].held, true);
  assert.deepEqual(L.dayTag(s[2]), { label: "Sun hold", kind: "sun" });
  assert.equal(L.dayTag(s[1]), null);
  assert.deepEqual(L.dayTag({ ...s[2], cancelled: true }), { label: "Called off", kind: "warn" });
  assert.deepEqual(L.dayTag({ ...s[1], boost: 1 }), { label: "Solar boost", kind: "solar" });
});

test("day details read as sentences", () => {
  const day = {
    date: "2026-11-02",
    house: { sun: 3.12, outdoor_mean: 7.44, heat: 0, pump_kwh: 12.44, pump_solar_kwh: 7.8 },
    zones: {
      ground: {
        t07: 19.2,
        peak: 21.46,
        comfort_pct: 85,
        plan: { hold: true, predicted_peak: 21.3, target: 20.75, cloud: 15 },
        episodes: [{ intent: "sun_hold", start: "2026-11-02T06:00:00+00:00", end: "2026-11-02T12:00:00+00:00" }],
        overrides: [{ at: "2026-11-02T09:15:00+00:00", intent: "sun_hold", setpoint: 19, to: 21, signal: "too_cold" }],
      },
    },
  };
  assert.deepEqual(L.dayDetails(day, "ground", overview(), "Europe/Rome"), [
    "Held for the sun: forecast peak 21.3 °C vs aim 20.8 °C (15 % cloud forecast).",
    "Comfortable 85 % of 07–23.",
    "Indoor 19.2 °C at 07:00, afternoon peak 21.5 °C.",
    "Measured: sun 3.1 kWh/m², outdoor 7.4 °C.",
    "Heat pump 12.4 kWh, 63 % on solar.",
    "Sun hold 07:00–13:00.",
    "10:15 someone set 21.0 °C during sun hold (Sunhold had 19.0 °C); counted as “too cold”.",
  ]);
  // No sun sensor unit: no sun figure at all.
  assert.ok(L.dayDetails(day, "ground", overview({ sun_unit: null }), "UTC").includes("Measured: outdoor 7.4 °C."));
  assert.deepEqual(L.dayDetails({ zones: {} }, "ground", overview()), []);
  const vetoed = { zones: { ground: { plan: { hold: false, cloud_vetoed: true, predicted_peak: 21.2, cloud: 100 } } } };
  assert.equal(L.dayDetails(vetoed, "ground", overview())[0], "No sun hold: 100 % cloud forecast (still learning).");
});

test("KPIs: full days only, a baseline, and no percentage on too little data", () => {
  const days = [];
  for (let back = 0; back < 15; back += 1) {
    days.push({
      date: iso(back),
      house: { pump_kwh: 10, pump_solar_kwh: back === 0 ? 0 : back <= 7 ? 6 : 4 },
      zones: { ground: { comfort_pct: back === 0 ? 0 : back <= 7 ? 90 : 80 } },
    });
  }
  const ov = overview({ days });
  assert.deepEqual(L.pumpTotals(ov, 7), { total: 70, solar: 42, days: 7, share: 60 }); // today left out
  assert.deepEqual(L.pumpTotals(ov, 7, 1), { total: 70, solar: 28, days: 7, share: 40 });
  assert.deepEqual(L.comfortMean(ov, "ground", 7), { pct: 90, days: 7 });
  const k = L.kpis(ov, 7);
  assert.deepEqual(k.solar, { ready: true, days: 7, span: 7, value: 60, delta: 20 });
  assert.equal(L.kpiNote(k.solar), "▲ 20 points vs previous 7 days");
  assert.equal(L.kpiNote(k.zones[0].comfort), "▲ 10 points vs previous 7 days");
  assert.equal(k.pumpKwh, 70);
  assert.equal(L.kpiNote({ ...k.solar, delta: -1 }), "▼ 1 point vs previous 7 days");
  assert.equal(L.kpiNote({ ...k.solar, delta: 0.2 }), "Same as previous 7 days");
  assert.equal(L.kpiNote({ ...k.solar, delta: null }), "No previous 7 days to compare");
  // Two full days: collecting, not a misleading percentage.
  const young = L.kpis(overview({ days: days.slice(0, 3) }), 7);
  assert.equal(young.solar.ready, false);
  assert.equal(L.kpiNote(young.solar), "Collecting data — day 3 of 7");
  assert.equal(L.kpiNote(L.kpis(overview(), 7).solar), "Collecting data — day 1 of 7");
  assert.equal(L.pumpLine({ pump_kwh: 5 }), "Heat pump 5.0 kWh.");
  assert.equal(L.pumpLine({}), "");
});

test("learning: verdict, progress, effects, forecast trust", () => {
  assert.deepEqual(L.modelVerdict(zone().model, overview()), {
    learning: false,
    quality: "good",
    text: "Accurate to about ±0.3 °C over 34 days",
    sub: "How close its afternoon-peak forecast came, on days it had not seen.",
  });
  assert.equal(L.modelVerdict({ days: 20, mae: 0.7 }, overview()).quality, "fair");
  assert.equal(L.modelVerdict({ days: 20, mae: 1.2 }, overview()).quality, "poor");
  assert.equal(L.modelVerdict({ days: 3, mae: null }, overview()).text, "Learning: day 3 of 5");
  assert.equal(L.modelVerdict({ days: 6, mae: 0.3 }, overview({ min_days: { model: 7, sun: 10 } })).text, "Learning: day 6 of 7");
  assert.equal(L.modelVerdict(null, overview()).text, "Learning: day 0 of 5");
  assert.deepEqual(L.modelEffects({ a: 0.8, b: -0.05, d: 0.1 }, overview()), [
    "1 kWh/m² of sun adds +0.80 °C to the afternoon peak",
    "each °C it is colder outside than inside changes it by +0.05 °C",
    "1 kWh of morning heating adds +0.10 °C",
  ]);
  assert.equal(L.modelEffects({ a: 0.8 }, overview({ sun_unit: null })).length, 0);
  assert.equal(L.sunFraction({ k: 0.5, p: 1 }, 100), 0.5);
  const learned = L.cloudLines({ k: 0.5, p: 2, days: 38, mae: 0.41 }, 3.4, overview());
  assert.equal(learned.head, "50 % cloud ≈ 88 % of a clear day's sun · overcast ≈ 50 %");
  assert.equal(learned.sub, "Learned from 38 days, typically off by 0.4 kWh/m². A clear day here brings 3.4 kWh/m².");
  assert.equal(
    L.cloudLines({ k: 0.5, p: 2, days: 4 }, null, overview({ sun_unit: null })).sub,
    "Learning: day 4 of 10 — a starting guess until then.",
  );
  assert.match(
    L.gateSentence({ cut: 820, resume: 1380, headroom: 640.4, draw_samples: 212 }, { solar_cooling: true }),
    /below 820 W and resumes above 1380 W\. Right now: 640 W spare\. From the settings until .*\(212 readings so far\)/,
  );
  assert.match(L.gateSentence({ cut: 820, resume: 1380, learned: true }, { solar_cooling: true }), /Tuned from the heat pump's measured draw\./);
  assert.match(L.gateSentence({ cut: 820, resume: 1380, learning: false }, { solar_cooling: true }), /Set in the integration's settings\./);
  assert.equal(L.gateSentence({}, { solar_cooling: false }), "Solar pause is switched off.");
});

test("comfort: last step per season", () => {
  const z = zone({
    comfort: {
      winter: 0.35,
      summer: 0,
      history: [
        { at: "2026-10-01T20:00:00Z", season: "winter", signal: "too_cold", direct: true, offset: 0.25 },
        { at: "2026-07-01T20:00:00Z", season: "summer", signal: "too_warm", direct: true, offset: -0.25 },
        { at: "2026-10-20T20:00:00Z", season: "winter", signal: "too_cold", direct: false, offset: 0.35 },
      ],
    },
  });
  assert.deepEqual(L.comfortLast(z, "winter"), { text: "Too cold (from an override)", at: "2026-10-20T20:00:00Z", count: 2 });
  assert.equal(L.comfortLast(zone(), "summer"), null);
});

test("chart geometry", () => {
  assert.deepEqual(yRange([]), [18, 22]);
  const [lo, hi] = yRange([20.1, 20.2]);
  assert.ok(hi - lo >= 3 && lo <= 20.1 && hi >= 20.2);
  assert.deepEqual(yTicks(18, 22), [18, 19, 20, 21, 22]);
  assert.ok(yTicks(10, 30).length <= 5);
  const series = [
    { date: "2026-11-01", actual: 21, predicted: 20.8, held: true, overrides: 1, feedback: "ok" },
    { date: "2026-11-02", actual: null, predicted: null, held: false, overrides: 0, feedback: null },
    { date: "2026-11-03", actual: 20.4, predicted: null, held: false, overrides: 0, feedback: null },
  ];
  const svg = historyChart(series, { selected: "2026-11-03", target: 20.75, width: 600, label: "A & B" });
  assert.match(svg, /viewBox="0 0 600 168"/);
  assert.match(svg, /aria-label="A &amp; B"/);
  assert.match(historyChart(series, { width: 100 }), /viewBox="0 0 280 168"/);
  assert.equal((svg.match(/class="hit"/g) || []).length, 3);
  assert.equal((svg.match(/<path class="actual" d="M[^"]*"/)[0].match(/M/g) || []).length, 2); // gap splits the line
  assert.match(svg, /class="sel"/);
  assert.match(svg, /class="held"/);
  assert.match(svg, /class="target"/);
  assert.equal(historyChart([]), "");
});

console.log(`ok ${passed} tests`);
