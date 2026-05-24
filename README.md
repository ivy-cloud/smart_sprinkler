# Smart Sprinkler System (Waterlytics)

An intelligent irrigation system that uses visual analysis to detect where water is needed and irrigates with precision—reducing waste while keeping landscapes healthy.

**Proposal reference:** [Water Analytics / Smart Sprinkler](https://collegeappivy.wixsite.com/wateranalytics)

## Vision

Most irrigation systems treat landscapes as uniform surfaces, watering entire zones equally regardless of actual conditions. This project explores responsive irrigation: **see the environment before acting**, using visual intelligence and automation for more sustainable water use.

## Weather & irrigation schedule

Forecast (Open-Meteo, no API key):

```bash
python3 scripts/fetch_weather.py --city "San Jose"
python3 scripts/fetch_weather.py --city "San Jose" --with-schedule
```

Sprinkler ON/OFF + duration from humidity & rain (see `docs/irrigation_schedule_design.md`):

```bash
python3 scripts/sprinkler_schedule.py --city "San Jose"
python3 scripts/sprinkler_schedule.py --city "San Jose" --json
```

JSON includes top-level `duration`, `duration_minutes`, and `duration_seconds`.

Copy `scripts/irrigation_config.example.json` and tune flow rate after a bucket test.

## Repository status

Application code, firmware, and scripts are added as development progresses.

## License

TBD.
