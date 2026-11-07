"""Constants for Sunhold."""

from __future__ import annotations

from enum import StrEnum

DOMAIN = "sunhold"
PANEL_URL_PATH = "sunhold"
STATIC_URL = "/sunhold_static"
STORAGE_KEY = "sunhold.journal"
STORAGE_VERSION = 1

SUBENTRY_ZONE = "zone"

# House-wide inputs, on the main entry.
CONF_WEATHER = "weather"
CONF_OUTDOOR_TEMP = "outdoor_temperature"
CONF_SUN = "sun_sensor"
CONF_PV_POWER = "pv_power"
CONF_HOUSE_POWER = "house_power"
CONF_PUMP_POWER = "heat_pump_power"
CONF_NOTIFY = "notify_service"
CONF_HOLD_UNTIL = "hold_until"
# The solar thresholds, in their own collapsed section of the house form.
CONF_THRESHOLDS = "solar_thresholds"
CONF_SUMMER_CUT_W = "summer_cut_w"
CONF_SUMMER_RESUME_W = "summer_resume_w"
CONF_LEARN_SUMMER = "learn_summer_thresholds"
CONF_WINTER_ON_W = "winter_surplus_on_w"
CONF_WINTER_OFF_W = "winter_surplus_off_w"

# Per zone, on each zone subentry.
CONF_CLIMATE = "climate"
CONF_ZONE_TEMP = "temperature_sensor"
CONF_OPENINGS = "openings"
CONF_WINTER_TARGET = "winter_target"
CONF_SUMMER_TARGET = "summer_target"
CONF_SUN_FLOOR = "sun_floor"
CONF_WINDOW_HEAT = "window_pause_heat"
CONF_SUMMER_PAUSE = "summer_pause"

DEFAULT_HOLD_UNTIL = "13:00:00"
DEFAULT_WINTER_TARGET = 20.5
DEFAULT_SUMMER_TARGET = 25.5
DEFAULT_SUN_FLOOR = 19.0
DEFAULT_WINDOW_HEAT = 16.0
DEFAULT_SUMMER_PAUSE = 30.0

# An opening counts once it has stayed open this long, so walking through a door does
# not pause anything, and is over once it has stayed shut this long.
WINDOW_OPEN_SECONDS = 5 * 60
WINDOW_CLOSED_SECONDS = 2 * 60

# How long a thermostat that was following a recommendation may disagree with it before
# that is taken as a person's choice. Rides out a device briefly showing a stale value.
MANUAL_GRACE_SECONDS = 3 * 60

# Draw above which the heat pump counts as running: well above any standby draw, well
# below any compressor run.
PUMP_RUNNING_W = 200
# Default solar thresholds; each is a setting. Summer, until the pump's own draw has been
# learned (or for good, with learning off): cut cooling once the panels can spare less
# than this, call it a surplus once they can spare more than that.
DEFAULT_CUT_W = 900
DEFAULT_RESUME_W = 1500
# A winter surplus needs less: the panels peak lower, and a boost only starts a run the
# schedule would have made later on grid power anyway. Above 1000 W the sun still covers
# most of a typical run (Villa 1 heats at 1.3-1.9 kW); under 600 W it covers too little.
# The summer pair stays, because a pause there must not chatter around the pump's draw.
DEFAULT_WINTER_ON_W = 1000
DEFAULT_WINTER_OFF_W = 600

# Winter: the sun hold is taken when the predicted free-running peak clears the comfort
# target by this much - about the model's 90th-percentile error in practice, so a
# wrong hold is a rare and small miss.
HOLD_MARGIN = 0.4
# The comfort band around a zone's (learned) target: what counts as comfortable in the
# daily comfort score, and how far a solar boost may push a zone to store surplus.
COMFORT_BAND_LOW = 0.5
COMFORT_BAND_HIGH = 1.0
BOOST_DELTA = 0.5
# The hours the comfort score covers: when people are up and about.
COMFORT_HOURS = range(7, 23)

# At 10:30 the sun measured since 08:00 is compared with what the forecast promised, and
# a hold whose sun has not turned up by half is called off.
SUN_CHECK_MIN_RATIO = 0.5


class Intent(StrEnum):
    """What Sunhold wants a zone to be doing right now."""

    SCHEDULE = "schedule"
    SUN_HOLD = "sun_hold"
    WINDOW_PAUSE = "window_pause"
    SOLAR_PAUSE = "solar_pause"
    SOLAR_BOOST = "solar_boost"


class Season(StrEnum):
    """Which way a zone is working, read from its own HVAC mode."""

    WINTER = "winter"
    SUMMER = "summer"
    OFF = "off"


class Feedback(StrEnum):
    """How a day felt, from a person."""

    TOO_COLD = "too_cold"
    OK = "ok"
    TOO_WARM = "too_warm"


# Notification buttons only; the entities and service use the translations.
FEEDBACK_LABELS = {
    Feedback.TOO_COLD: "Too cold",
    Feedback.OK: "Just right",
    Feedback.TOO_WARM: "Too warm",
}
