# Sunhold

A self-learning heating and cooling assistant for Home Assistant. Sunhold decides, per
thermostat zone, when the schedule should give way, and learns from the house and the
people in it. It has two goals:

- **use your own solar power** for the heat pump, and
- **keep every zone comfortable.**

Sunhold never drives a thermostat. It publishes what each zone should do as entities, and
a small automation (or blueprint, or other integration) makes the device follow them. Any
thermostat works, and the logic stays independent of the hardware.

## What it recommends

| Recommendation | When | Set point |
|---|---|---|
| **Window pause** | A chosen door or window has been open for 5 minutes | Winter: a low limit. Summer: a high limit. |
| **Sun hold** (winter) | The morning forecast says the sun alone will bring the zone to its comfort target | A floor until midday. Heats only below it. |
| **Solar pause** (summer) | The panels can no longer carry the heat pump | A high limit. Cools only above it. |
| **Solar boost** | The panels have surplus to spare | The edge of the comfort band, to store the surplus in the house |
| **Schedule** | Otherwise | The thermostat's own schedule |

When several apply, the higher row wins. If a person overrides a recommendation, it is
dropped for the rest of that episode.

## What it learns

- **How the house answers the weather.** For each zone, a daily model predicts the
  climb from 07:00 to the afternoon peak from sun, outdoor temperature and heating.
  Recent days count more, so the model follows the seasons. The panel shows its
  measured accuracy.
- **How far the forecast can be trusted.** Each morning's forecast is archived and
  compared with the sun that was measured.
- **What feels comfortable.** Each zone has a comfort offset on its winter and summer
  targets, moved by:
  - direct feedback (*too cold / just right / too warm*: buttons, a service, or the
    daily notification), and
  - overrides (someone turning a held zone up says it was wanted warmer).
- **The heat pump's real draw**, which tunes the summer solar thresholds (this can be
  switched off in the settings).

On first start, Sunhold learns from the recorder's long-term statistics, so it does not
start blind.

## Setup

1. Install through HACS, then add the *Sunhold* integration.
2. **House:**
   - required: a forecast and a shaded outdoor temperature;
   - optional, each unlocks more: measured sun (irradiance, or solar power), solar
     power, whole-house power, heat pump power, a notify service.
   - *Solar thresholds* (collapsed): how much the panels must spare for the heat pump,
     after the house's own use. Winter boost starts above 1000 W and ends below 600 W;
     summer cooling pauses below 900 W and resumes above 1500 W. Change them under
     *Reconfigure* if your solar setup changes.
3. **Zones:** add one per thermostat with *Add zone*:
   - the indoor temperature sensor;
   - the thermostat (read only);
   - the doors and windows that pause it;
   - comfort targets for winter and summer, and the limits used while holding.

## Entities

Per zone:

| Entity | Content |
|---|---|
| `sensor.sunhold_<zone>_recommendation` | `schedule` / `sun_hold` / `window_pause` / `solar_pause` / `solar_boost`. Attributes: `setpoint`, `since`, `until`, `season`. |
| `sensor.sunhold_<zone>_set_point` | The temperature to apply, or unknown while the schedule rules |
| `binary_sensor.sunhold_<zone>_override` | On while the zone should leave its schedule |
| `sensor.sunhold_<zone>_comfort_target` | The target with what has been learned applied |
| `sensor.sunhold_<zone>_predicted_peak` | Today's predicted afternoon peak |
| Buttons | *Too cold*, *Just right*, *Too warm* |

House:

| Entity | Content |
|---|---|
| `binary_sensor.sunhold_solar_cooling_pause` | The summer solar pause |
| `sensor.sunhold_solar_headroom` | What the panels could give the heat pump, in W |
| Switches | Turn *Sun hold*, *Solar cooling*, *Solar boost* and *Window pause* on or off separately |

Service: `sunhold.feedback` with `signal` (`too_cold` / `ok` / `too_warm`), plus optional
`zone` and `date`.

## Driving a thermostat

The safest pattern uses a thermostat that treats a set point from Home Assistant as a
**temporary override**, with `auto` returning it to its own schedule. Renew the override
while the recommendation lasts. If Home Assistant stops, the device falls back to its
schedule by itself. An example for one zone:

```yaml
automation:
  - alias: Ground floor follows Sunhold
    mode: queued
    triggers:
      - trigger: state
        entity_id: sensor.sunhold_ground_floor_set_point
      - trigger: time_pattern
        minutes: "/30"
    actions:
      - variables:
          sp: "{{ states('sensor.sunhold_ground_floor_set_point') }}"
      - if: "{{ sp | is_number }}"
        then:
          - action: climate.set_temperature
            target: { entity_id: climate.ground_floor }
            data: { temperature: "{{ sp | float }}" }
        else:
          - action: climate.set_hvac_mode
            target: { entity_id: climate.ground_floor }
            data: { hvac_mode: auto }
```

## Panel

The *Sunhold* sidebar panel shows:

- **Today:** what each zone is doing and why, with the feedback buttons.
- **History:** predicted against actual peaks, holds, overrides and feedback.
- **Learning:** how accurate the models are, the comfort offsets, how far the forecast
  is trusted, and the two goals: heat pump energy taken from solar, and hours spent in
  the comfort band.

## Development

```
make setup   # once
make check   # lint, tests, panel checks
make preview # the panel with sample data, no Home Assistant needed
```
