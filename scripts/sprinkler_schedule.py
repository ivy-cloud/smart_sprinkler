#!/usr/bin/env python3
"""
CLI for weather-based sprinkler schedule.

Library/API: services.irrigation or scripts/api_server.py
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

import _bootstrap  # noqa: F401

from services.irrigation.config import HUMIDITY_BANDS
from services.irrigation.weather import decide_weather
from services.weather import load_forecast, resolve_place_from_args


def format_decision_summary(decision) -> str:
    state = "ON" if decision.sprinkler_on else "OFF"
    return (
        f"Sprinkler: {state} | Duration: {decision.duration} | "
        f"Start: {decision.recommended_start}"
    )


def format_decision_table(decision) -> str:
    lines = [
        "+---------------------------+------------------------------------------+",
        "| Field                     | Value                                    |",
        "+---------------------------+------------------------------------------+",
        f"| Sprinkler ON              | {'YES' if decision.sprinkler_on else 'NO':<40} |",
        f"| Duration                  | {decision.duration:<40} |",
        f"| Duration (minutes)        | {decision.duration_minutes:<40} |",
        f"| Duration (seconds)        | {decision.duration_seconds:<40} |",
        f"| Base × factor             | {decision.base_minutes} × {decision.duration_factor:<31} |",
        f"| Humidity (24h avg)        | {str(decision.humidity_avg_pct) + '%':<40} |",
        f"| Humidity band             | {decision.humidity_band:<40} |",
        f"| Flow rate (GPM)           | {decision.flow_gpm:<40} |",
        f"| Est. water (gallons)      | {decision.estimated_gallons:<40} |",
        f"| Est. depth (mm on zone)   | {decision.estimated_depth_mm:<40} |",
        f"| Recommended start         | {decision.recommended_start:<40} |",
        "+---------------------------+------------------------------------------+",
    ]
    if decision.skip_reason:
        lines.append(f"Skip reason: {decision.skip_reason}")
    for note in decision.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprinkler schedule from weather forecast.")
    parser.add_argument("--city")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--auto-location", action="store_true")
    parser.add_argument("--base-minutes", type=float, default=20)
    parser.add_argument("--flow-gpm", type=float, default=8.0)
    parser.add_argument("--efficiency", type=float, default=0.8)
    parser.add_argument("--zone-area-sqft", type=float, default=1000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        place = resolve_place_from_args(
            city=args.city,
            lat=args.lat,
            lon=args.lon,
            auto_location=args.auto_location,
        )
        forecast = load_forecast(place, forecast_days=2)
        decision = decide_weather(
            forecast,
            base_minutes=args.base_minutes,
            flow_gpm=args.flow_gpm,
            efficiency=args.efficiency,
            zone_area_sqft=args.zone_area_sqft,
        )

        payload = {
            "location": asdict(forecast.place),
            "timezone": forecast.timezone,
            "fetched_at": forecast.fetched_at,
            "sprinkler_on": decision.sprinkler_on,
            "duration_minutes": decision.duration_minutes,
            "duration_seconds": decision.duration_seconds,
            "duration": decision.duration,
            "decision": asdict(decision),
            "humidity_duration_table": [
                {"humidity_pct_range": f"[{lo}, {hi})", "multiplier": m, "label": label}
                for lo, hi, m, label in HUMIDITY_BANDS
            ],
        }

        if args.json:
            print(json.dumps(payload, indent=2))
            return 0

        print(f"Location: {place.name}")
        print(f"Timezone: {forecast.timezone}")
        print()
        print(format_decision_summary(decision))
        print()
        print(format_decision_table(decision))
        print()
        print("Rain checks")
        print(json.dumps(decision.rain_checks, indent=2))
        return 0

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
