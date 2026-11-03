// Small inline SVG charts, built as strings. Pure, so node can test the geometry.
import { esc, isNum, num, dateLabel } from "./logic.js";

const H = 168;
const PAD = { left: 34, right: 8, top: 14, bottom: 30 };
const FEEDBACK_COLOR = {
  too_cold: "var(--sh-cold)",
  ok: "var(--sh-ok)",
  too_warm: "var(--sh-warm)",
};

/** Integer-degree y range around the values, at least 3 °C tall. */
export function yRange(values) {
  const v = values.filter(isNum);
  if (!v.length) return [18, 22];
  let lo = Math.floor(Math.min(...v) - 0.3);
  let hi = Math.ceil(Math.max(...v) + 0.3);
  while (hi - lo < 3) {
    lo -= 1;
    if (hi - lo < 3) hi += 1;
  }
  return [lo, hi];
}

/** Grid steps so there are at most five lines. */
export function yTicks(lo, hi) {
  const step = Math.max(1, Math.ceil((hi - lo) / 4));
  const out = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) out.push(t);
  return out;
}

/**
 * Predicted vs actual afternoon peak per day, held days shaded, feedback dots on top,
 * overrides as ticks along the bottom. Every day is a tap target carrying data-date.
 */
export function historyChart(series, { selected = null, target = null, width = 360, label = "" } = {}) {
  const n = series.length;
  if (!n) return "";
  const W = Math.max(280, Math.round(width));
  const [lo, hi] = yRange([
    ...series.flatMap((r) => [r.actual, r.predicted]),
    ...(isNum(target) ? [target] : []),
  ]);
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const col = plotW / n;
  const x = (i) => PAD.left + col * (i + 0.5);
  const y = (v) => PAD.top + plotH * (1 - (v - lo) / (hi - lo));
  const parts = [];

  for (const t of yTicks(lo, hi)) {
    parts.push(
      `<line class="grid" x1="${PAD.left}" x2="${W - PAD.right}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}"/>`,
      `<text class="axis" x="${PAD.left - 4}" y="${(y(t) + 3).toFixed(1)}" text-anchor="end">${t}°</text>`,
    );
  }
  series.forEach((r, i) => {
    if (r.held || r.date === selected) {
      parts.push(
        `<rect class="${r.date === selected ? "sel" : "held"}${r.held && r.cancelled ? " off" : ""}" x="${(PAD.left + col * i).toFixed(1)}" y="${PAD.top}" width="${col.toFixed(1)}" height="${plotH}"/>`,
      );
    }
  });
  if (isNum(target)) {
    parts.push(
      `<line class="target" x1="${PAD.left}" x2="${W - PAD.right}" y1="${y(target).toFixed(1)}" y2="${y(target).toFixed(1)}"/>`,
    );
  }

  // Actual peaks as a line broken where a day is missing.
  let path = "";
  let pen = false;
  series.forEach((r, i) => {
    if (!isNum(r.actual)) {
      pen = false;
      return;
    }
    path += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(r.actual).toFixed(1)}`;
    pen = true;
  });
  if (path) parts.push(`<path class="actual" d="${path}"/>`);

  const dot = Math.max(1.6, Math.min(3, col * 0.35));
  series.forEach((r, i) => {
    if (isNum(r.actual)) parts.push(`<circle class="actual-dot" cx="${x(i).toFixed(1)}" cy="${y(r.actual).toFixed(1)}" r="${dot.toFixed(1)}"/>`);
    if (isNum(r.predicted)) parts.push(`<circle class="pred" cx="${x(i).toFixed(1)}" cy="${y(r.predicted).toFixed(1)}" r="${(dot + 0.4).toFixed(1)}"/>`);
    if (r.feedback) parts.push(`<circle cx="${x(i).toFixed(1)}" cy="${PAD.top - 7}" r="${dot.toFixed(1)}" fill="${FEEDBACK_COLOR[r.feedback] || "currentColor"}"/>`);
    if (r.overrides) {
      const by = H - PAD.bottom + 5;
      parts.push(`<path class="override" d="M${x(i).toFixed(1)} ${by}l${dot + 1} ${dot * 2 + 1}h${-(dot * 2 + 2)}z"/>`);
    }
  });

  const labelAt = n > 2 ? [0, Math.floor((n - 1) / 2), n - 1] : [...Array(n).keys()];
  for (const i of labelAt) {
    const anchor = i === 0 ? "start" : i === n - 1 ? "end" : "middle";
    const lx = i === 0 ? PAD.left : i === n - 1 ? W - PAD.right : x(i);
    parts.push(`<text class="axis" x="${lx.toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${esc(dateLabel(series[i].date, { short: true }))}</text>`);
  }
  series.forEach((r, i) => {
    parts.push(
      `<rect class="hit" data-date="${esc(r.date)}" x="${(PAD.left + col * i).toFixed(1)}" y="0" width="${col.toFixed(1)}" height="${H}"><title>${esc(dateLabel(r.date))}: peak ${num(r.actual)}${isNum(r.predicted) ? `, predicted ${num(r.predicted)}` : ""}</title></rect>`,
    );
  });

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label || "Forecast and actual afternoon peak per day")}">${parts.join("")}</svg>`;
}
