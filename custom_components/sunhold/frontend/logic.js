// Pure helpers for the Sunhold panel: formatting and the plain-language sentences.
// No DOM here, so node runs them in tests/frontend/test_logic.mjs.

export const SIGNALS = [
  ["too_cold", "Too cold"],
  ["ok", "Just right"],
  ["too_warm", "Too warm"],
];

const SIGNAL_LABEL = Object.fromEntries(SIGNALS);

export const INTENT_LABEL = {
  schedule: "Schedule",
  sun_hold: "Sun hold",
  window_pause: "Window pause",
  solar_pause: "Solar pause",
  solar_boost: "Solar boost",
};

/** Days back that feedback is still accepted for (the backend refuses older ones). */
export const FEEDBACK_DAYS = 30;

// Fallbacks for an older backend that does not send these yet.
const DEFAULTS = {
  comfort_band: { low: 0.5, high: 1.0 },
  min_days: { model: 5, sun: 10 },
  learning_steps: { feedback: 0.25, override: 0.1 },
  window_delays: { open: 300, closed: 120 },
};

export const setting = (overview, key) => ({ ...DEFAULTS[key], ...(overview?.[key] || {}) });

export function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
  );
}

export const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** One decimal, a real minus sign, "–" when there is nothing to show. */
export function num(value, digits = 1) {
  if (!isNum(value)) return "–";
  const text = (Math.abs(value) < 0.5 * 10 ** -digits ? 0 : value).toFixed(digits);
  return text.replace("-", "−");
}

export const temp = (value) => (isNum(value) ? `${num(value)} °C` : "–");
export const pct = (v) => (isNum(v) ? `${Math.round(v)} %` : "–");

/** "+0.3", "−0.3", "0.0". */
export function signed(value, digits = 1) {
  if (!isNum(value)) return "–";
  const text = num(value, digits);
  return text === (0).toFixed(digits) || text.startsWith("−") ? text : `+${text}`;
}

export const signalLabel = (signal) => SIGNAL_LABEL[signal] || "";

/** "5 minutes", "1 minute", "30 seconds". */
export function duration(seconds) {
  if (!isNum(seconds)) return "";
  if (seconds < 60) return `${Math.round(seconds)} seconds`;
  const m = Math.round(seconds / 60);
  return `${m} ${m === 1 ? "minute" : "minutes"}`;
}

// ------------------------------------------------------------------ locale

// Panel text is English; dates and times follow the user's Home Assistant profile.
let LOCALE = { language: "en-GB", hour12: false };

/** Take the language and 12/24 h choice from `hass.locale`. Unknown → en-GB, 24 h. */
export function setLocale(locale) {
  let language = locale?.language || "en-GB";
  try {
    new Intl.DateTimeFormat(language);
  } catch {
    language = "en-GB";
  }
  const tf = locale?.time_format;
  let hour12 = false;
  if (tf === "12") hour12 = true;
  else if (tf === "language" || tf === "system") {
    hour12 = new Intl.DateTimeFormat(tf === "system" ? undefined : language, { hour: "numeric" })
      .resolvedOptions().hour12 === true;
  }
  LOCALE = { language, hour12 };
}

function clock(date, timeZone) {
  const opts = { hour: "2-digit", minute: "2-digit", timeZone: timeZone || undefined };
  if (LOCALE.hour12) opts.hour12 = true;
  else opts.hourCycle = "h23";
  try {
    return new Intl.DateTimeFormat(LOCALE.language, opts).format(date);
  } catch {
    return new Intl.DateTimeFormat(LOCALE.language, { ...opts, timeZone: undefined }).format(date);
  }
}

/** A time of day in the house's zone. Accepts ISO strings and "HH:MM[:SS]". */
export function time(value, timeZone) {
  if (!value) return "";
  const hm = /^(\d{1,2}):(\d{2})(:\d{2})?$/.exec(value);
  if (hm) return clock(new Date(Date.UTC(2000, 0, 1, Number(hm[1]), Number(hm[2]))), "UTC");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return clock(date, timeZone);
}

const dayMs = 86400000;
const dayNumber = (iso) => Date.parse(`${iso}T00:00:00Z`) / dayMs;

/** "Tue 3 Nov" for a calendar date (YYYY-MM-DD), whatever the browser's zone. */
export function dateLabel(iso, { short = false } = {}) {
  const date = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return iso || "";
  return new Intl.DateTimeFormat(LOCALE.language, {
    timeZone: "UTC",
    weekday: short ? undefined : "short",
    day: "numeric",
    month: "short",
  }).format(date);
}

/** "Today", "Yesterday", or the date. */
export function dayLabel(iso, today) {
  const diff = dayNumber(today) - dayNumber(iso);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return dateLabel(iso);
}

export const daysAgo = (iso, today) => dayNumber(today) - dayNumber(iso);

/** "2 days ago" style, for when something was last learned. */
export function ago(iso, nowMs = Date.now()) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const minutes = Math.round((nowMs - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

// ------------------------------------------------------------------ data access

export function dayOf(overview, iso) {
  return (overview?.days || []).find((d) => d.date === iso) || null;
}

export function zoneDay(overview, iso, zoneId) {
  return dayOf(overview, iso)?.zones?.[zoneId] || null;
}

/** The aim a zone works to: the set target with what was learned on top. */
export function comfort(zone, season = zone?.season) {
  if (!zone || (season !== "winter" && season !== "summer")) return null;
  const set = zone.targets?.[season];
  if (!isNum(set)) return null;
  const offset = isNum(zone.comfort?.[season]) ? zone.comfort[season] : 0;
  return { set, offset, target: set + offset };
}

/** "nudged 0.3 °C warmer by your feedback" - the one phrasing for a learned offset. */
export function nudge(offset) {
  if (!isNum(offset) || Math.abs(offset) < 0.05) return "";
  return `nudged ${num(Math.abs(offset))} °C ${offset > 0 ? "warmer" : "cooler"} by your feedback`;
}

/** Plain words for the comfort band around the aim. */
export function bandSentence(overview) {
  const band = setting(overview, "comfort_band");
  return `Comfortable = from ${num(band.low)} °C below to ${num(band.high)} °C above the aim, 07–23.`;
}

/** Whether a day can still be rated: today or up to FEEDBACK_DAYS back. */
export function canRate(iso, today) {
  const back = daysAgo(iso, today);
  return back >= 0 && back <= FEEDBACK_DAYS;
}

// ------------------------------------------------------------------ today

function limitPhrase(zone, kind) {
  const l = zone.limits || {};
  const winter = zone.season === "winter";
  if (kind === "sun") return isNum(l.sun_floor) ? `heats only below ${temp(l.sun_floor)}` : "";
  if (kind === "window") {
    if (winter) return isNum(l.window_pause_heat) ? `heats only below ${temp(l.window_pause_heat)}` : "";
    return isNum(l.summer_pause) ? `cools only above ${temp(l.summer_pause)}` : "";
  }
  if (kind === "solar") return isNum(l.summer_pause) ? `cools only above ${temp(l.summer_pause)}` : "";
  return "";
}

/**
 * What a zone is doing right now, as one sentence, plus the reason as a second line.
 * tone: "sun" | "pause" | "solar" | "idle" - drives the card's accent only.
 */
export function headline(zone, overview, timeZone) {
  const today = zoneDay(overview, overview?.today, zone.id) || {};
  const plan = today.plan;
  const winter = zone.season === "winter";
  const delays = setting(overview, "window_delays");

  switch (zone.intent) {
    case "sun_hold": {
      const until = time(zone.until, timeZone) || time(overview?.hold_until) || "";
      const limit = limitPhrase(zone, "sun");
      return {
        tone: "sun",
        text: `Holding for the sun${until ? ` until ${until}` : ""}${limit ? ` — ${limit}` : ""}`,
        detail: plan && isNum(plan.predicted_peak)
          ? `The sun should bring it to ${temp(plan.predicted_peak)} by the afternoon.`
          : plan?.fallback
            ? `Clear forecast (${num(plan.cloud, 0)} % cloud). Still learning this house, so it goes on the forecast alone.`
            : "",
      };
    }
    case "window_pause": {
      const limit = limitPhrase(zone, "window");
      return {
        tone: "pause",
        text: `Window open — ${winter ? "heating" : "cooling"} paused${limit ? ` (${limit})` : ""}`,
        detail: `Back on schedule ${duration(delays.closed)} after it closes.`,
      };
    }
    case "solar_pause": {
      const limit = limitPhrase(zone, "solar");
      return {
        tone: "pause",
        text: `Cooling paused — the panels can't cover it${limit ? ` (${limit})` : ""}`,
        detail: "Resumes when there is enough solar surplus.",
      };
    }
    case "solar_boost": {
      const sp = isNum(zone.setpoint) ? temp(zone.setpoint) : "";
      return {
        tone: "solar",
        text: winter
          ? `Storing solar heat${sp ? ` — up to ${sp}` : ""}`
          : `Storing solar cool${sp ? ` — down to ${sp}` : ""}`,
        detail: "There is spare solar power, so the floor stores it for later.",
      };
    }
    default:
      break;
  }
  if (zone.season === "off") {
    return { tone: "idle", text: "Heating and cooling are off", detail: "Nothing to do until the thermostat is switched on." };
  }
  return { tone: "idle", text: "On its schedule", detail: scheduleReason(zone, overview, today, timeZone) };
}

function scheduleReason(zone, overview, today, timeZone) {
  const features = overview?.features || {};
  const delays = setting(overview, "window_delays");
  if (zone.window_open && features.window_pause) {
    const limit = limitPhrase(zone, "window");
    return `A window is open — ${zone.season === "winter" ? "heating" : "cooling"} pauses after ${duration(delays.open)}${limit ? ` (${limit})` : ""}.`;
  }
  if (zone.season === "summer") {
    if (!features.solar_cooling) return "";
    const limit = limitPhrase(zone, "solar");
    return `If the solar panels can't cover the heat pump, cooling pauses${limit ? ` (${limit})` : ""}.`;
  }
  if (!features.sun_hold) return "Sun hold is switched off.";
  const plan = today.plan;
  if (today.cancelled === "no_sun") return "The sun hold was called off: the forecast sun didn't come.";
  if (!plan) return "No plan yet today — it is made each morning from the forecast.";
  if (plan.hold) return `The sun hold ended at ${time(overview?.hold_until) || time(zone.until, timeZone)}.`;
  if (plan.fallback || plan.cloud_vetoed) return `No sun hold today: too cloudy (${num(plan.cloud, 0)} % cloud).`;
  if (isNum(plan.predicted_peak) && isNum(plan.target)) {
    const limit = limitPhrase(zone, "sun");
    return `No sun hold today: the sun would bring it to ${temp(plan.predicted_peak)}, not safely above ${temp(plan.target)}${limit ? `. On a sun hold it ${limit}` : ""}.`;
  }
  return "No sun hold today.";
}

/** One line about the house today: the forecast, a solar surplus, summer headroom. */
export function houseLine(overview) {
  const parts = [];
  const forecast = dayOf(overview, overview?.today)?.house?.forecast;
  if (forecast && isNum(forecast.cloud)) {
    parts.push(`Forecast ${num(forecast.cloud, 0)} % cloud${isNum(forecast.outdoor) ? `, ${temp(forecast.outdoor)} outside` : ""}`);
  }
  if (overview?.surplus && overview?.features?.solar_boost) parts.push("Solar surplus now");
  const summer = (overview?.zones || []).some((z) => z.season === "summer");
  const gate = overview?.gate;
  if (summer && overview?.features?.solar_cooling && gate && isNum(gate.headroom)) {
    parts.push(`Solar headroom ${Math.round(gate.headroom)} W`);
  }
  return parts.join(" · ");
}

// ------------------------------------------------------------------ history

/** Per day, oldest first, what the chart and the list need for one zone. */
export function zoneSeries(overview, zoneId) {
  return (overview?.days || [])
    .map((day) => {
      const z = day.zones?.[zoneId] || {};
      const plan = z.plan || null;
      const eps = z.episodes || [];
      return {
        date: day.date,
        actual: isNum(z.peak) ? z.peak : null,
        predicted: plan && isNum(plan.predicted_peak) ? plan.predicted_peak : null,
        held: !!plan?.hold,
        cancelled: z.cancelled === "no_sun",
        overrides: z.overrides?.length || 0,
        feedback: z.feedback || null,
        windows: eps.filter((e) => e.intent === "window_pause").length,
        solar: eps.filter((e) => e.intent === "solar_pause").length,
        boost: eps.filter((e) => e.intent === "solar_boost").length,
        comfortPct: isNum(z.comfort_pct) ? z.comfort_pct : null,
        empty: !z.plan && !isNum(z.peak) && !eps.length && !z.overrides?.length && !z.feedback && !isNum(z.comfort_pct),
      };
    })
    .sort((a, b) => (a.date < b.date ? -1 : 1));
}

/** A short tag for a day in the list. */
export function dayTag(row) {
  if (row.cancelled) return { label: "Called off", kind: "warn" };
  if (row.held) return { label: "Sun hold", kind: "sun" };
  if (row.solar) return { label: "Solar pause", kind: "pause" };
  if (row.windows) return { label: "Window", kind: "pause" };
  if (row.boost) return { label: "Solar boost", kind: "solar" };
  return null;
}

/** The lines of a day's detail, in plain words. */
export function dayDetails(day, zoneId, overview, timeZone) {
  const z = day?.zones?.[zoneId] || {};
  const house = day?.house || {};
  const unit = overview?.sun_unit || null;
  const lines = [];
  const plan = z.plan;
  if (plan) {
    const cloud = isNum(plan.cloud) ? `${num(plan.cloud, 0)} % cloud forecast` : "";
    if (plan.fallback || plan.cloud_vetoed) {
      lines.push(`${plan.hold ? "Held for the sun" : "No sun hold"}: ${cloud || "forecast only"} (still learning).`);
    } else {
      const why = isNum(plan.predicted_peak) && isNum(plan.target)
        ? `forecast peak ${temp(plan.predicted_peak)} vs aim ${temp(plan.target)}`
        : "";
      lines.push(`${plan.hold ? "Held for the sun" : "No sun hold"}${why ? `: ${why}` : ""}${cloud ? ` (${cloud})` : ""}.`);
    }
  }
  if (z.cancelled === "no_sun") lines.push("Called off mid-morning: the sun didn't come.");
  if (isNum(z.comfort_pct)) lines.push(`Comfortable ${pct(z.comfort_pct)} of 07–23.`);
  if (isNum(z.t07) || isNum(z.peak)) {
    lines.push(`Indoor ${temp(z.t07)} at 07:00, afternoon peak ${temp(z.peak)}.`);
  }
  const weather = [];
  if (unit && isNum(house.sun)) weather.push(`sun ${num(house.sun)} ${unit}`);
  if (isNum(house.outdoor_mean)) weather.push(`outdoor ${temp(house.outdoor_mean)}`);
  if (weather.length) lines.push(`Measured: ${weather.join(", ")}.`);
  const pump = pumpLine(house);
  if (pump) lines.push(pump);
  for (const ep of z.episodes || []) {
    const span = `${time(ep.start, timeZone)}${ep.end ? `–${time(ep.end, timeZone)}` : " onwards"}`;
    lines.push(`${INTENT_LABEL[ep.intent] || ep.intent} ${span}.`);
  }
  for (const ov of z.overrides || []) {
    const during = ov.intent && ov.intent !== "schedule" ? ` during ${INTENT_LABEL[ov.intent]?.toLowerCase() || ov.intent}` : "";
    const wanted = isNum(ov.setpoint) ? ` (Sunhold had ${temp(ov.setpoint)})` : "";
    const counted = ov.signal ? `; counted as “${signalLabel(ov.signal).toLowerCase()}”` : "";
    lines.push(`${time(ov.at, timeZone)} someone set ${temp(ov.to)}${during}${wanted}${counted}.`);
  }
  return lines;
}

// ------------------------------------------------------------------ heat pump and comfort KPIs

/** "Heat pump 12.4 kWh, 63 % on solar." for one day, or "" without data. */
export function pumpLine(house) {
  if (!isNum(house?.pump_kwh)) return "";
  const share = solarShare(house.pump_kwh, house.pump_solar_kwh);
  return `Heat pump ${num(house.pump_kwh)} kWh${share === null ? "" : `, ${share} % on solar`}.`;
}

function solarShare(total, solar) {
  if (!isNum(total) || !isNum(solar) || total <= 0) return null;
  return Math.round(Math.max(0, Math.min(1, solar / total)) * 100);
}

// Both KPIs use the same window: the `span` full days before today, `skip` windows back.
function inWindow(iso, today, span, skip) {
  const back = daysAgo(iso, today);
  return back >= 1 + skip * span && back <= span + skip * span;
}

/** Heat pump energy over a window of full days. */
export function pumpTotals(overview, span, skip = 0) {
  const today = overview?.today;
  if (!today) return null;
  let total = 0;
  let solar = 0;
  let days = 0;
  let solarKnown = true;
  for (const day of overview.days || []) {
    if (!inWindow(day.date, today, span, skip) || !isNum(day.house?.pump_kwh)) continue;
    days += 1;
    total += day.house.pump_kwh;
    if (isNum(day.house.pump_solar_kwh)) solar += day.house.pump_solar_kwh;
    else solarKnown = false;
  }
  if (!days) return null;
  return { total, solar, days, share: solarKnown ? solarShare(total, solar) : null };
}

/** Mean share of waking hours a zone spent comfortable over a window of full days. */
export function comfortMean(overview, zoneId, span, skip = 0) {
  const today = overview?.today;
  if (!today) return null;
  const values = [];
  for (const day of overview.days || []) {
    const v = day.zones?.[zoneId]?.comfort_pct;
    if (inWindow(day.date, today, span, skip) && isNum(v)) values.push(v);
  }
  if (!values.length) return null;
  return { pct: Math.round(values.reduce((a, b) => a + b, 0) / values.length), days: values.length };
}

/** Fewer full days than this and a percentage would mislead. */
export const KPI_MIN_DAYS = 3;

/**
 * One headline number: value for the window, change against the window before, and
 * whether there is enough data yet. `get(skip)` returns {pct|share, days} or null.
 */
function kpi(span, get, key) {
  const now = get(0);
  const before = get(1);
  const value = now ? now[key] : null;
  const days = now?.days || 0;
  if (days < KPI_MIN_DAYS || !isNum(value)) {
    return { ready: false, days, span, value: null, delta: null };
  }
  const prev = before && before.days >= KPI_MIN_DAYS && isNum(before[key]) ? before[key] : null;
  return { ready: true, days, span, value, delta: prev === null ? null : value - prev };
}

/** The two goals for one window: heat pump on solar, and comfortable hours per zone. */
export function kpis(overview, span) {
  const pump = pumpTotals(overview, span);
  return {
    span,
    solar: kpi(span, (skip) => pumpTotals(overview, span, skip), "share"),
    pumpKwh: pump ? pump.total : null,
    zones: (overview?.zones || []).map((z) => ({
      id: z.id,
      title: z.title,
      comfort: kpi(span, (skip) => comfortMean(overview, z.id, span, skip), "pct"),
    })),
  };
}

/** "Collecting data — day 2 of 7", "▲ 5 points vs previous 7 days", "Same as previous 7 days". */
export function kpiNote(k) {
  if (!k.ready) return `Collecting data — day ${Math.min(k.days + 1, k.span)} of ${k.span}`;
  if (k.delta === null) return `No previous ${k.span} days to compare`;
  const d = Math.round(k.delta);
  if (d === 0) return `Same as previous ${k.span} days`;
  return `${d > 0 ? "▲" : "▼"} ${Math.abs(d)} ${Math.abs(d) === 1 ? "point" : "points"} vs previous ${k.span} days`;
}

// ------------------------------------------------------------------ learning

/** Verdict on the house model: text, quality chip, and learning progress. */
export function modelVerdict(model, overview) {
  const need = setting(overview, "min_days").model;
  const days = model?.days || 0;
  if (days < need || !isNum(model?.mae)) {
    return {
      learning: true,
      text: `Learning: day ${days} of ${need}`,
      sub: "Until then it uses a starting guess for a typical house.",
    };
  }
  const quality = model.mae < 0.5 ? "good" : model.mae < 1 ? "fair" : "poor";
  return {
    learning: false,
    quality,
    text: `Accurate to about ±${num(model.mae)} °C over ${days} days`,
    sub: "How close its afternoon-peak forecast came, on days it had not seen.",
  };
}

/** What each input is worth, per unit, as short phrases. */
export function modelEffects(model, overview) {
  if (!model) return [];
  const unit = overview?.sun_unit;
  const out = [];
  if (unit && isNum(model.a)) out.push(`1 ${unit} of sun adds ${signed(model.a, 2)} °C to the afternoon peak`);
  if (isNum(model.b)) out.push(`each °C it is colder outside than inside changes it by ${signed(-model.b, 2)} °C`);
  if (isNum(model.d)) out.push(`1 kWh of morning heating adds ${signed(model.d, 2)} °C`);
  return out;
}

/** Fraction of a clear day's sun a forecast cloud cover (0-100) is worth. */
export function sunFraction(cloudMap, cloudPct) {
  const k = isNum(cloudMap?.k) ? cloudMap.k : 0.45;
  const p = isNum(cloudMap?.p) ? cloudMap.p : 2.5;
  const n = Math.max(0, Math.min(1, cloudPct / 100));
  return Math.max(0, 1 - k * n ** p);
}

/** Forecast trust: the headline mapping, then where it came from. */
export function cloudLines(cloudMap, clearDay, overview) {
  const need = setting(overview, "min_days").sun;
  const unit = overview?.sun_unit;
  const days = cloudMap?.days || 0;
  const head = `50 % cloud ≈ ${Math.round(sunFraction(cloudMap, 50) * 100)} % of a clear day's sun · overcast ≈ ${Math.round(sunFraction(cloudMap, 100) * 100)} %`;
  const subs = [];
  if (days < need) subs.push(`Learning: day ${days} of ${need} — a starting guess until then.`);
  else subs.push(`Learned from ${days} days${unit && isNum(cloudMap?.mae) ? `, typically off by ${num(cloudMap.mae)} ${unit}` : ""}.`);
  if (unit && isNum(clearDay)) subs.push(`A clear day here brings ${num(clearDay)} ${unit}.`);
  return { head, sub: subs.join(" ") };
}

export function gateSentence(gate, features) {
  if (!features?.solar_cooling) return "Solar pause is switched off.";
  if (!gate) return "";
  const now = isNum(gate.headroom) ? ` Right now: ${Math.round(gate.headroom)} W spare.` : "";
  let samples;
  if (gate.learning === false) samples = " Set in the integration's settings.";
  else if (gate.learned) samples = " Tuned from the heat pump's measured draw.";
  else if (gate.draw_samples)
    samples = ` From the settings until the heat pump has been measured (${gate.draw_samples} ${gate.draw_samples === 1 ? "reading" : "readings"} so far).`;
  else samples = " From the settings until the heat pump has been measured.";
  return `Cooling pauses when the solar surplus stays below ${Math.round(gate.cut)} W and resumes above ${Math.round(gate.resume)} W.${now}${samples}`;
}

/** The latest comfort step for one season, in words. */
export function comfortLast(zone, season) {
  const history = (zone?.comfort?.history || []).filter((h) => h.season === season);
  const last = history[history.length - 1];
  if (!last) return null;
  return {
    text: `${signalLabel(last.signal) || last.signal}${last.direct ? "" : " (from an override)"}`,
    at: last.at,
    count: history.length,
  };
}

/** "Each “too cold” or “too warm” moves the aim 0.25 °C; a thermostat change 0.1 °C." */
export function stepsSentence(overview) {
  const s = setting(overview, "learning_steps");
  return `Each “too cold” or “too warm” moves the aim ${num(s.feedback, 2)} °C; a thermostat change that disagrees with Sunhold moves it ${num(s.override, 2)} °C.`;
}
