#!/usr/bin/env python3
"""Preview the Sunhold panel in a browser, without Home Assistant.

    python3 scripts/preview.py --port 8124      # then open http://localhost:8124/

Serves custom_components/sunhold/frontend/ plus an index.html that mounts <sunhold-panel>
with a fake `hass` whose callWS answers from made-up sample data (two generic zones, about
45 days). Feedback sent from the panel is kept in memory, so the refresh shows it.

Other scenes: /?scene=fresh (a fresh install, nothing learned yet), /?scene=summer,
/?scene=nozones, /?dark=1 for the dark theme; /?tab=history&day=YYYY-MM-DD opens a
tab, /?w=390 lays out at phone width, /?admin=0 as a non-admin, /?fail=1 fails refreshes,
/?lang=it&tf=12 tries another locale.
Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import random
from urllib.parse import parse_qs, urlparse

FRONTEND = Path(__file__).resolve().parent.parent / "custom_components" / "sunhold" / "frontend"
TZ = dt.UTC

INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sunhold preview</title>
<style>
  html, body { margin: 0; height: 100%; }
  body { font-family: Roboto, system-ui, sans-serif; }
  :root {
    --primary-color: #03a9f4; --primary-text-color: #212121; --secondary-text-color: #727272;
    --primary-background-color: #fafafa; --card-background-color: #fff;
    --divider-color: rgba(0,0,0,.12); --text-primary-color: #fff;
    --app-header-background-color: #03a9f4; --app-header-text-color: #fff;
  }
  :root.dark {
    --primary-text-color: #e1e1e1; --secondary-text-color: #9b9b9b;
    --primary-background-color: #111; --card-background-color: #1c1c1c;
    --divider-color: rgba(225,225,225,.12);
    --app-header-background-color: #101e24; --app-header-text-color: #e1e1e1;
  }
</style>
</head>
<body>
<script type="module">
  const params = new URLSearchParams(location.search);
  if (params.get("dark")) document.documentElement.classList.add("dark");
  // Headless browsers keep a minimum window width; ?w=390 lays the page out at phone width.
  if (params.get("w")) document.body.style.width = `${Number(params.get("w"))}px`;
  const scene = params.get("scene") || "";

  // Stand-in for Home Assistant's sidebar toggle.
  customElements.define("ha-menu-button", class extends HTMLElement {
    set narrow(v) { this.hidden = !v; }
    connectedCallback() {
      this.innerHTML = '<button aria-label="Menu" style="background:none;border:0;color:inherit;'
        + 'font-size:22px;width:40px;height:40px;cursor:pointer">&#9776;</button>';
    }
  });

  // ?admin=0 shows what a non-admin sees; ?fail=1 fails every refresh after the first.
  let calls = 0;
  const hass = {
    config: { time_zone: "UTC" },
    user: { is_admin: params.get("admin") !== "0" },
    locale: { language: params.get("lang") || "en-GB", time_format: params.get("tf") || "24" },
    async callWS(msg) {
      calls += 1;
      if (params.get("fail") && calls > 1) throw new Error("Connection lost");
      const res = await fetch(`/api?scene=${encodeURIComponent(scene)}`, {
        method: "POST", body: JSON.stringify(msg),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.message);
      // Fail straight after the first load, so the stale-data banner shows at once.
      if (params.get("fail") && calls === 1) setTimeout(() => panel._load(), 0);
      return body;
    },
  };

  await import("/sunhold-panel.js");
  const panel = document.createElement("sunhold-panel");
  const narrow = () => innerWidth < 870;
  panel.hass = hass;
  panel.narrow = narrow();
  panel.panel = { config: {} };
  // Screenshot helpers: open on a tab (?tab=history) or with a day expanded (?day=YYYY-MM-DD).
  if (params.get("tab")) panel._tab = params.get("tab");
  if (params.get("day")) panel._selected = params.get("day");
  addEventListener("resize", () => { panel.narrow = narrow(); });
  document.body.append(panel);
</script>
</body>
</html>
"""


def _iso(day: dt.date, hour: int, minute: int = 0) -> str:
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ).isoformat()


def sample(scene: str, feedback: dict) -> dict:
    """Build an overview like the backend's, deterministic for a given day."""
    rnd = random.Random(42)
    today = dt.datetime.now(TZ).date()
    summer = scene == "summer"
    fresh = scene == "fresh"
    season = "summer" if summer else "winter"
    zones_cfg = [
        ("ground", "Ground floor", 20.5, 25.5, (0.82, -0.06, 0.12, -1.9), 0.27, 0.25),
        ("first", "First floor", 20.0, 26.0, (1.05, -0.04, 0.08, -2.5), 0.34, 0.35),
    ]
    clear = 3.4
    k, p = 0.52, 2.0
    days = []
    span = 0 if fresh else 45
    overrides_on = {today - dt.timedelta(days=6): "ground", today - dt.timedelta(days=19): "first"}
    feedback_on = {
        today - dt.timedelta(days=3): ("ground", "too_cold"),
        today - dt.timedelta(days=11): ("first", "too_warm"),
        today - dt.timedelta(days=24): ("ground", "ok"),
    }
    for back in range(span, -1, -1):
        day = today - dt.timedelta(days=back)
        cloud = rnd.choice([5, 10, 20, 35, 50, 70, 85, 95, 100])
        if back == 0:
            cloud = 10
        outdoor = round(8 + rnd.uniform(-4, 4) if not summer else 27 + rnd.uniform(-3, 3), 1)
        sun = round(max(0.2, clear * (1 - k * (cloud / 100) ** p) + rnd.uniform(-0.4, 0.4)), 2)
        heat = round(rnd.uniform(0, 3.5), 1) if not summer else None
        pump = round(rnd.uniform(8, 22), 1)
        house = {
            "forecast": {"cloud": cloud, "outdoor": outdoor},
            "pump_kwh": pump,
            "pump_solar_kwh": round(pump * min(0.9, sun / clear * 0.8), 1),
        }
        if back > 0:
            house.update(sun=sun, outdoor_mean=outdoor, heat=heat)
        zrecs = {}
        for zid, _title, winter_t, _summer_t, (a, b, d, c), _mae, off in zones_cfg:
            t07 = round(19.3 + rnd.uniform(-0.5, 0.6), 1)
            if back == 0:
                t07 = 19.8
            rec: dict = {"t07": t07, "season": season}
            if not summer:
                exp_sun = clear * (1 - k * (cloud / 100) ** p)
                pred = t07 + a * exp_sun + b * (outdoor - t07) + c
                target = winter_t + off
                hold = pred >= target + 0.4
                rec["plan"] = {
                    "hold": hold,
                    "predicted_peak": round(pred, 2),
                    "target": round(target, 2),
                    "sun": round(exp_sun, 3),
                    "delta_out": round(outdoor - t07, 2),
                    "indoor": t07,
                    "cloud": cloud,
                }
                if hold:
                    end = None if back == 0 else _iso(day, 13)
                    rec["episodes"] = [{"intent": "sun_hold", "start": _iso(day, 7), "end": end}]
                if back > 0:
                    rec["peak"] = round(
                        t07
                        + a * sun
                        + b * (outdoor - t07)
                        + d * (0 if hold else heat)
                        + c
                        + rnd.uniform(-0.3, 0.3),
                        1,
                    )
                if hold and back == 9:
                    rec["cancelled"] = "no_sun"
                    rec["episodes"][0]["end"] = _iso(day, 10, 30)
            elif back > 0:
                rec["peak"] = round(25 + rnd.uniform(-0.8, 1.2), 1)
                if rnd.random() < 0.3:
                    rec["episodes"] = [
                        {
                            "intent": "solar_pause",
                            "start": _iso(day, 17, 10),
                            "end": _iso(day, 18, 40),
                        }
                    ]
            if overrides_on.get(day) == zid:
                rec["overrides"] = [
                    {
                        "at": _iso(day, 10, 15),
                        "intent": "sun_hold",
                        "setpoint": 19.0,
                        "to": 21.0,
                        "signal": "too_cold",
                    }
                ]
            if day in feedback_on and feedback_on[day][0] == zid:
                rec["feedback"] = feedback_on[day][1]
            if back == 0 and zid == "first":
                rec.setdefault("episodes", []).append(
                    {"intent": "window_pause", "start": _iso(day, 9, 40), "end": None}
                )
            if back > 0 and not summer and zid == "ground" and sun > 3.2:
                rec.setdefault("episodes", []).append(
                    {"intent": "solar_boost", "start": _iso(day, 12), "end": _iso(day, 15, 20)}
                )
            if back > 0:
                miss = 12 if rec.get("feedback") == "too_cold" or rec.get("overrides") else 0
                rec["comfort_pct"] = round(min(100, max(55, 93 - miss + rnd.uniform(-8, 6))))
            zrecs[zid] = rec
        days.append({"date": day.isoformat(), "house": house, "zones": zrecs})

    for (zid, date), signal in feedback.items():
        rec = next((x for x in days if x["date"] == date), None)
        if rec is None:
            rec = {"date": date, "house": {}, "zones": {}}
            days.append(rec)
        rec["zones"].setdefault(zid, {})["feedback"] = signal

    days.sort(key=lambda x: x["date"])
    today_rec = next((x["zones"] for x in days if x["date"] == today.isoformat()), {})
    zones = []
    for zid, title, winter_t, summer_t, (a, b, d, c), mae, off in zones_cfg:
        hold = bool(today_rec.get(zid, {}).get("plan", {}).get("hold"))
        if zid == "first":
            intent, setpoint, until = "window_pause", (16.0 if not summer else 30.0), None
        elif summer:
            intent, setpoint, until = "solar_boost", summer_t - 0.5, None
        elif hold:
            intent, setpoint, until = "sun_hold", 19.0, _iso(today, 13)
        else:
            intent, setpoint, until = "schedule", None, None
        history = []
        if not fresh:
            value = 0.0
            steps = {
                "ground": [
                    ("too_cold", True),
                    ("too_cold", True),
                    ("ok", True),
                    ("too_warm", True),
                ],
                "first": [("too_cold", True), ("ok", True), ("too_cold", False)],
            }[zid]
            for i, (sig, direct) in enumerate(steps):
                step = 0.25 if direct else 0.1
                value += {"too_cold": step, "too_warm": -step}.get(sig, 0)
                when = dt.datetime.combine(today - dt.timedelta(days=30 - 8 * i), dt.time(20), TZ)
                history.append(
                    {
                        "at": when.isoformat(),
                        "season": "winter",
                        "signal": sig,
                        "direct": direct,
                        "offset": round(value, 2),
                    }
                )
        zones.append(
            {
                "id": zid,
                "title": title,
                "season": season,
                "intent": intent,
                "setpoint": setpoint,
                "since": _iso(today, 7) if intent != "schedule" else None,
                "until": until,
                "indoor": round(19.4 + (0.6 if zid == "ground" else 0.1), 1),
                "window_open": zid == "first",
                "targets": {"winter": winter_t, "summer": summer_t},
                "comfort": {"winter": 0.0 if fresh else off, "summer": 0.0, "history": history},
                "model": (
                    {"a": 0.7, "b": -0.05, "d": 0.1, "c": -0.6, "days": 0, "mae": None}
                    if fresh
                    else {"a": a, "b": b, "d": d, "c": c, "days": 34, "mae": mae}
                ),
                "limits": {"sun_floor": 19.0, "window_pause_heat": 16.0, "summer_pause": 30.0},
            }
        )
    return {
        "today": today.isoformat(),
        "comfort_band": {"low": 0.5, "high": 1.0},
        "min_days": {"model": 5, "sun": 10},
        "learning_steps": {"feedback": 0.25, "override": 0.1},
        "window_delays": {"open": 300, "closed": 120},
        "sun_unit": "kWh/m²",
        "features": {
            "sun_hold": True,
            "solar_cooling": True,
            "window_pause": True,
            "solar_boost": True,
        },
        "surplus": summer,
        "hold_until": "13:00",
        "gate": {
            "paused": summer,
            "cut": 900 if fresh else 820,
            "resume": 1500 if fresh else 1380,
            "headroom": 640.0 if summer else None,
            "draw_samples": 0 if fresh else 212,
        },
        "cloud_map": {} if fresh else {"k": k, "p": p, "days": 38, "mae": 0.41},
        "clear_day": None if fresh else clear,
        "zones": [] if scene == "nozones" else zones,
        "days": days,
        "events": [],
    }


FEEDBACK_DAYS = 30
FEEDBACK: dict[tuple[str, str], str] = {}


class Handler(SimpleHTTPRequestHandler):
    """Static files from the frontend directory, the index page and a fake websocket API."""

    def do_GET(self) -> None:
        """Serve the index page, or a file from the frontend directory."""
        if urlparse(self.path).path in ("/", "/index.html"):
            self._send(200, INDEX.encode(), "text/html; charset=utf-8")
            return
        super().do_GET()

    def do_POST(self) -> None:
        """Answer the panel's websocket commands from the sample data."""
        url = urlparse(self.path)
        if url.path != "/api":
            self._send(404, b"{}", "application/json")
            return
        scene = parse_qs(url.query).get("scene", [""])[0]
        msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if msg.get("type") == "sunhold/overview":
            body = sample(scene, FEEDBACK)
        elif msg.get("type") == "sunhold/feedback":
            today = dt.datetime.now(TZ).date()
            date = msg.get("date") or today.isoformat()
            if not 0 <= (today - dt.date.fromisoformat(date)).days <= FEEDBACK_DAYS:
                error = json.dumps({"message": "Unknown zone, or a day outside the last 30"})
                self._send(400, error.encode(), "application/json")
                return
            FEEDBACK[(msg["zone_id"], date)] = msg["signal"]
            body = {"ok": True}
        else:
            error = json.dumps({"message": "unknown command"}).encode()
            self._send(400, error, "application/json")
            return
        self._send(200, json.dumps(body).encode(), "application/json")

    def end_headers(self) -> None:
        """Never cache, so an edited module shows on reload."""
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def guess_type(self, path: str) -> str:
        """Serve modules as JavaScript whatever the platform's MIME table says."""
        return "text/javascript" if str(path).endswith(".js") else super().guess_type(path)


def main() -> None:
    """Run the preview server until Ctrl+C."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8124)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    handler = partial(Handler, directory=str(FRONTEND))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Sunhold preview on http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
