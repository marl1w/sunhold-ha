"""Turning hourly statistics into days."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from custom_components.sunhold import history

TZ = ZoneInfo("Europe/Rome")
DAY = dt.date(2026, 11, 3)
SRC = history.Sources(zone_temp="t", outdoor="o", sun="s", pump="p", pv="pv", house="h")


def _series(value) -> dict:
    return {
        dt.datetime(2026, 11, 3, h, tzinfo=TZ): {"mean": value(h), "max": value(h) + 0.1}
        for h in range(24)
    }


def _data() -> history.Hourly:
    return {
        "t": _series(lambda h: 19.0 + max(0, h - 9) * 0.2),
        "o": _series(lambda h: 8.0),
        "s": _series(lambda h: 200.0 if 8 <= h < 16 else 0.0),
        "p": _series(lambda h: 2000.0 if 6 <= h < 9 else 10.0),
        "pv": _series(lambda h: 1500.0 if 8 <= h < 16 else 0.0),
        "h": _series(lambda h: 2400.0 if 6 <= h < 9 else 300.0),
    }


def test_house_day() -> None:
    house = history.house_day(_data(), SRC, DAY, TZ)
    assert house["outdoor_mean"] == 8.0
    assert house["sun"] == pytest.approx(1.6)  # 8 hours at 200 W/m2
    assert house["heat"] == pytest.approx(6.04)  # 05-12: three hours at 2 kW
    # 08:00 pumps with sun: 1500 - (2400 - 2000) = 1100 W covered; then 7 sunny hours
    # cover the 10 W standby.
    assert house["pump_solar_kwh"] == pytest.approx(1.17)


def test_zone_day() -> None:
    zone = history.zone_day(_data(), SRC, DAY, TZ)
    assert zone["t07"] == 19.0
    assert zone["peak"] == pytest.approx(20.7)  # 17:00 mean 20.6, max + 0.1
    assert len(zone["hours"]) == 16


def test_incomplete_days_are_skipped() -> None:
    data = _data()
    del data["o"][dt.datetime(2026, 11, 3, 9, tzinfo=TZ)]
    assert history.house_day(data, SRC, DAY, TZ) is None


def test_sun_so_far_needs_most_of_the_morning() -> None:
    since = dt.datetime(2026, 11, 3, 8, tzinfo=TZ)
    rows = {since + dt.timedelta(minutes=5 * i): {"mean": 120.0} for i in range(30)}
    now = since + dt.timedelta(minutes=150)
    assert history.sun_so_far({"s": rows}, "s", since, now) == pytest.approx(0.3)
    sparse = dict(list(rows.items())[:10])
    assert history.sun_so_far({"s": sparse}, "s", since, now) is None
