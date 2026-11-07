"""Everything Sunhold learns, as pure functions.

Nothing here touches Home Assistant, so the tests drive it with plain numbers, and the
same code runs in the nightly refit and in the offline backtests.

Two separate things are learned. How the house answers the weather: a daily-rise model
per zone, plus how far the forecast's cloud cover can be trusted. And what the people in
it find comfortable: a per-zone offset on the configured targets, moved by feedback.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import datetime as dt
import math

from .const import DEFAULT_CUT_W, DEFAULT_RESUME_W, PUMP_RUNNING_W, Feedback, Season

# ------------------------------------------------------------------ house response

# rise = a * sun + b * (outdoor mean - indoor at 07:00) + d * heat + c, where rise is the
# climb from 07:00 to the afternoon peak and heat is what the heating put in that morning.
# Winter mornings almost always heat, so the model learns from heated days too and the
# decision asks it about heat = 0. The prior is a plausible floor-heated house; with few
# days of its own the fit leans on it, with many it forgets it.
PRIOR = (0.7, -0.05, 0.1, -0.6)
PRIOR_DAYS = 5.0
# Short enough to follow the season mid-winter, long enough to average out the weather.
HALF_LIFE_DAYS = 14.0
MIN_DAYS = 5
# The accuracy shown is measured on the most recent days only: older ones carry almost
# no weight in the fit anyway, and it keeps the nightly refit linear in the history.
ACCURACY_DAYS = 60


@dataclass(frozen=True)
class DayObservation:
    """One day of one zone."""

    day: dt.date
    sun: float
    delta_out: float
    heat: float
    rise: float

    def features(self) -> tuple[float, float, float, float]:
        """Return the model's inputs, in coefficient order."""
        return (self.sun, self.delta_out, self.heat, 1.0)


@dataclass(frozen=True)
class RiseModel:
    """A fitted daily-rise model."""

    a: float = PRIOR[0]
    b: float = PRIOR[1]
    d: float = PRIOR[2]
    c: float = PRIOR[3]
    days: int = 0
    mae: float | None = None

    def predict(self, sun: float, delta_out: float, heat: float = 0.0) -> float:
        """Return the predicted rise from 07:00 to the afternoon peak."""
        return self.a * sun + self.b * delta_out + self.d * heat + self.c

    def as_dict(self) -> dict:
        """Serialise for storage and the panel."""
        return {
            "a": self.a,
            "b": self.b,
            "d": self.d,
            "c": self.c,
            "days": self.days,
            "mae": self.mae,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> RiseModel:
        """Restore from storage, falling back to the prior."""
        if not data:
            return cls()
        return cls(data["a"], data["b"], data["d"], data["c"], data.get("days", 0), data.get("mae"))


def _solve(m: list[list[float]], v: list[float]) -> list[float]:
    """Solve a small linear system by Gaussian elimination with partial pivoting."""
    n = len(v)
    a = [[*row, v[i]] for i, row in enumerate(m)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            for k in range(col, n + 1):
                a[r][k] -= f * a[col][k]
    x = [0.0] * n
    for r in reversed(range(n)):
        x[r] = (a[r][n] - sum(a[r][k] * x[k] for k in range(r + 1, n))) / a[r][r]
    return x


def _weighted_fit(
    obs: Sequence[DayObservation], weights: Sequence[float], prior: Sequence[float]
) -> list[float]:
    # Ridge towards the prior: (X'WX + lI) b = X'Wy + l*b0. The prior weight is in
    # "days", so it means the same thing whatever the recency weights add up to.
    n = len(prior)
    lam = PRIOR_DAYS
    m = [[lam if i == j else 0.0 for j in range(n)] for i in range(n)]
    v = [lam * p for p in prior]
    for o, w in zip(obs, weights, strict=True):
        x = o.features()
        for i in range(n):
            v[i] += w * x[i] * o.rise
            for j in range(n):
                m[i][j] += w * x[i] * x[j]
    return _solve(m, v)


def fit_rise(
    obs: Iterable[DayObservation],
    today: dt.date,
    *,
    half_life: float = HALF_LIFE_DAYS,
    prior: Sequence[float] = PRIOR,
) -> RiseModel:
    """Fit the daily-rise model, recent days counting more.

    Recency weighting is what lets it follow the season: low winter sun reaches further
    into a house than September sun, so the same measured radiation is worth more rise
    in December, and a flat average over months would always lag behind. The error is
    leave-one-out, so it is what the panel can honestly show as accuracy.
    """
    obs = sorted(obs, key=lambda o: o.day)
    if not obs:
        return RiseModel(*prior)
    weights = [0.5 ** ((today - o.day).days / half_life) for o in obs]
    coef = _weighted_fit(obs, weights, prior)

    mae = None
    if len(obs) >= MIN_DAYS:
        errors = []
        for i in range(max(0, len(obs) - ACCURACY_DAYS), len(obs)):
            o = obs[i]
            ci = _weighted_fit(obs[:i] + obs[i + 1 :], weights[:i] + weights[i + 1 :], prior)
            errors.append(abs(sum(c * x for c, x in zip(ci, o.features(), strict=True)) - o.rise))
        mae = round(sum(errors) / len(errors), 3)
    return RiseModel(*coef, len(obs), mae)


# ------------------------------------------------------------------ the sun

# cloud cover N in [0, 1] keeps 1 - k * N**p of a clear day. Both are learned: the shape
# p shifts with the season, not just the depth k.
DEFAULT_CLOUD_K = 0.45
DEFAULT_CLOUD_P = 2.5
CLOUD_P_GRID = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)
# Before enough days are measured, only a clearly sunny forecast holds.
FALLBACK_MAX_CLOUD = 60.0
MIN_SUN_DAYS = 10


def solar_elevation(lat: float, lon: float, when: dt.datetime) -> float:
    """Return the sun's elevation in degrees (NOAA approximation, about 0.5 deg)."""
    utc = when.astimezone(dt.UTC)
    day = utc.timetuple().tm_yday
    hour = utc.hour + utc.minute / 60 + utc.second / 3600
    g = 2 * math.pi / 365 * (day - 1 + (hour - 12) / 24)
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    solar_minutes = hour * 60 + eqtime + 4 * lon
    ha = math.radians(solar_minutes / 4 - 180)
    phi = math.radians(lat)
    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, cos_zen))))


def clear_day(recent_sun: Sequence[float | None]) -> float | None:
    """Return what a clear day measures here now: the 90th percentile of recent days.

    Empirical rather than computed from the sun's position, because it has to carry the
    sensor's own scale - a station's reading drifts against modelled radiation with the
    sun's angle, and a PV array used as the sun sensor reads in kWh of its own.
    """
    days = sorted(s for s in recent_sun if s is not None and s >= 0)
    if len(days) < MIN_SUN_DAYS:
        return None
    return days[min(len(days) - 1, int(0.9 * len(days)))]


@dataclass(frozen=True)
class CloudMap:
    """How a forecast's cloud cover turns into measured sun."""

    k: float = DEFAULT_CLOUD_K
    p: float = DEFAULT_CLOUD_P
    days: int = 0
    mae: float | None = None

    def expected(self, clear: float, cloud_pct: float) -> float:
        """Return the sun the forecast implies, in the sun sensor's units."""
        n = max(0.0, min(1.0, cloud_pct / 100))
        return clear * max(0.0, 1 - self.k * n**self.p)

    def as_dict(self) -> dict:
        """Serialise for storage and the panel."""
        return {"k": self.k, "p": self.p, "days": self.days, "mae": self.mae}

    @classmethod
    def from_dict(cls, data: dict | None) -> CloudMap:
        """Restore from storage."""
        if not data:
            return cls()
        return cls(data["k"], data["p"], data.get("days", 0), data.get("mae"))


def fit_cloud_map(samples: Sequence[tuple[float, float, float]]) -> CloudMap:
    """Learn k and p from (clear day, forecast cloud %, measured sun) per archived day.

    For each p on a small grid, k has a closed-form least-squares answer; the pair with
    the smallest error wins.
    """
    usable = [(c, f, s) for c, f, s in samples if c > 0]
    if len(usable) < MIN_SUN_DAYS:
        return CloudMap()
    best: CloudMap | None = None
    for p in CLOUD_P_GRID:
        xs = [(max(0.0, min(1.0, f / 100)) ** p, 1 - s / c) for c, f, s in usable]
        den = sum(x * x for x, _ in xs)
        if den <= 0:
            continue
        k = max(0.0, min(1.0, sum(x * y for x, y in xs) / den))
        candidate = CloudMap(k, p, len(usable))
        mae = sum(abs(candidate.expected(c, f) - s) for c, f, s in usable) / len(usable)
        if best is None or mae < best.mae:
            best = CloudMap(round(k, 3), p, len(usable), round(mae, 3))
    return best or CloudMap()


# ------------------------------------------------------------------ people

COMFORT_LIMIT = 1.5
DIRECT_STEP = 0.25
INDIRECT_STEP = 0.1


@dataclass
class Comfort:
    """A zone's learned comfort offset, per season, applied on top of the set target."""

    winter: float = 0.0
    summer: float = 0.0
    history: list[dict] = field(default_factory=list)

    def offset(self, season: Season) -> float:
        """Return the offset for a season."""
        return self.summer if season is Season.SUMMER else self.winter

    def learn(self, season: Season, signal: Feedback, *, direct: bool, when: str) -> float:
        """Move the offset for one piece of feedback and return the new value.

        Direct feedback says how a day felt and moves twice as far as an override, which
        only says what one person wanted at one moment.
        """
        step = DIRECT_STEP if direct else INDIRECT_STEP
        delta = {Feedback.TOO_COLD: step, Feedback.TOO_WARM: -step}.get(signal, 0.0)
        value = max(-COMFORT_LIMIT, min(COMFORT_LIMIT, self.offset(season) + delta))
        value = round(value, 2)
        if season is Season.SUMMER:
            self.summer = value
        else:
            self.winter = value
        self.history.append(
            {
                "at": when,
                "season": season.value,
                "signal": signal.value,
                "direct": direct,
                "offset": value,
            }
        )
        del self.history[:-200]
        return value

    def as_dict(self) -> dict:
        """Serialise for storage."""
        return {"winter": self.winter, "summer": self.summer, "history": self.history}

    @classmethod
    def from_dict(cls, data: dict | None) -> Comfort:
        """Restore from storage."""
        if not data:
            return cls()
        return cls(data.get("winter", 0.0), data.get("summer", 0.0), data.get("history", []))


# ------------------------------------------------------------------ winter decision


@dataclass(frozen=True)
class WinterPlan:
    """The morning's decision for one zone."""

    hold: bool
    predicted_peak: float
    target: float
    sun: float
    delta_out: float

    def as_dict(self) -> dict:
        """Serialise for storage and the panel."""
        return {
            "hold": self.hold,
            "predicted_peak": round(self.predicted_peak, 2),
            "target": round(self.target, 2),
            "sun": round(self.sun, 3),
            "delta_out": round(self.delta_out, 2),
        }


def plan_winter(
    indoor_now: float,
    model: RiseModel,
    sun: float,
    outdoor_mean: float,
    target: float,
    margin: float,
) -> WinterPlan:
    """Decide whether the sun alone, with no heating, will bring a zone to its target."""
    delta_out = outdoor_mean - indoor_now
    peak = indoor_now + model.predict(sun, delta_out)
    return WinterPlan(peak >= target + margin, peak, target, sun, delta_out)


# ------------------------------------------------------------------ summer


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def cooling_thresholds(running_draws: Sequence[float]) -> tuple[float, float] | None:
    """Derive the solar cut and resume thresholds from the pump's own running draw.

    Cut once the sun clearly no longer covers a typical run, resume only once it would
    cover nearly any run. Rounded to 50 W.
    """
    if len(running_draws) < 30:
        return None
    cut = 0.85 * _percentile(running_draws, 0.5)
    resume = 1.1 * _percentile(running_draws, 0.9)
    return round(cut / 50) * 50, round(resume / 50) * 50


@dataclass
class SolarGate:
    """Pauses cooling while the panels cannot carry the heat pump.

    Aggressive on the cut, deliberately slow on the resume. Times are injected, so the
    tests and the offline replay drive it with recorded data.
    """

    cut_w: float = DEFAULT_CUT_W
    resume_w: float = DEFAULT_RESUME_W
    deficit_s: float = 600
    surplus_s: float = 1200
    min_pause_s: float = 3600
    min_run_s: float = 2700
    paused: bool = False
    paused_at: dt.datetime | None = None
    resumed_at: dt.datetime | None = None
    _deficit_since: dt.datetime | None = None
    _surplus_since: dt.datetime | None = None

    def update(
        self,
        now: dt.datetime,
        headroom: float | None,
        pump_w: float | None,
        sun_up: bool,
        too_warm: bool = False,
    ) -> bool:
        """Advance the gate and return whether cooling should be paused.

        `headroom` is PV minus the house's draw without the pump, so it does not move
        when the pump stops. None means the metering is gone, which must never read as
        "no sun" and hold cooling off. `too_warm` is the comfort guard: a zone past its
        summer limit ends any pause and blocks new ones.
        """
        if headroom is None or not sun_up or too_warm:
            self._deficit_since = self._surplus_since = None
            if self.paused:
                self._resume(now)
            return self.paused

        if headroom < self.cut_w:
            self._deficit_since = self._deficit_since or now
        else:
            self._deficit_since = None
        if headroom > self.resume_w:
            self._surplus_since = self._surplus_since or now
        else:
            self._surplus_since = None

        if self.paused:
            if (
                self._surplus_since is not None
                and (now - self._surplus_since).total_seconds() >= self.surplus_s
                and (now - self.paused_at).total_seconds() >= self.min_pause_s
            ):
                self._resume(now)
        elif (
            self._deficit_since is not None
            and (now - self._deficit_since).total_seconds() >= self.deficit_s
            and (pump_w or 0) > PUMP_RUNNING_W
            and (
                self.resumed_at is None or (now - self.resumed_at).total_seconds() >= self.min_run_s
            )
        ):
            self.paused = True
            self.paused_at = now
        return self.paused

    def _resume(self, now: dt.datetime) -> None:
        self.paused = False
        self.resumed_at = now


@dataclass
class SolarSurplus:
    """On while the panels have power to spare for the heat pump, sustained.

    In summer it shares the gate's thresholds the other way round; winter has its own,
    lower pair (the engine sets them). On after `on_w` for 20 minutes,
    off after under `off_w` for 10, so it never flickers between the two.
    """

    on_w: float = DEFAULT_RESUME_W
    off_w: float = DEFAULT_CUT_W
    on_s: float = 1200
    off_s: float = 600
    active: bool = False
    _above_since: dt.datetime | None = None
    _below_since: dt.datetime | None = None

    def update(self, now: dt.datetime, headroom: float | None, sun_up: bool) -> bool:
        """Advance and return whether there is a surplus to store."""
        if headroom is None or not sun_up:
            self.active = False
            self._above_since = self._below_since = None
            return False
        self._above_since = (self._above_since or now) if headroom > self.on_w else None
        self._below_since = (self._below_since or now) if headroom < self.off_w else None
        if not self.active and self._above_since is not None:
            self.active = (now - self._above_since).total_seconds() >= self.on_s
        elif self.active and self._below_since is not None:
            self.active = (now - self._below_since).total_seconds() < self.off_s
        return self.active
