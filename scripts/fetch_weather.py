#!/usr/bin/env python3
"""CLI: fetch weather forecast (rain + humidity). See services/weather/client.py."""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from services.irrigation import get_weather_decision_api
from services.weather import (
    forecast_to_dict,
    format_table,
    load_forecast,
    resolve_place_from_args,
)
from services.weather.client import _summarize_rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch weather forecast (rain + humidity)."
    )
    parser.add_argument("--city", help='City name, e.g. "San Jose, CA"')
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--auto-location", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--with-schedule",
        action="store_true",
        help="Include sprinkler ON/OFF and duration",
    )
    parser.add_argument("--base-minutes", type=float, default=20)
    parser.add_argument("--flow-gpm", type=float, default=8.0)
    args = parser.parse_args()

    try:
        place = resolve_place_from_args(
            city=args.city,
            lat=args.lat,
            lon=args.lon,
            auto_location=args.auto_location,
        )
        forecast = load_forecast(place, forecast_days=2, max_hours=max(48, args.hours))
        rows = forecast.hourly[: args.hours]
        summary = _summarize_rows(rows)

        schedule_block: dict | None = None
        if args.with_schedule:
            weather_payload = get_weather_decision_api(
                city=args.city,
                lat=args.lat,
                lon=args.lon,
                base_minutes=args.base_minutes,
                flow_gpm=args.flow_gpm,
            )
            schedule_block = {
                "sprinkler_on": weather_payload["sprinkler_on"],
                "duration_minutes": weather_payload["duration_minutes"],
                "duration_seconds": weather_payload["duration_seconds"],
                "duration": weather_payload["duration"],
                "skip_reason": weather_payload["weather"].get("skip_reason"),
                "humidity_band": weather_payload["weather"].get("humidity_band"),
            }

        if args.json:
            out = forecast_to_dict(forecast, hours=args.hours)
            if schedule_block:
                out["irrigation"] = schedule_block
                out["duration_minutes"] = schedule_block["duration_minutes"]
                out["duration"] = schedule_block["duration"]
            print(json.dumps(out, indent=2))
            return 0

        print(f"Location: {place.name}")
        print(f"Coords:   {place.latitude:.4f}, {place.longitude:.4f}")
        print(f"Timezone: {forecast.timezone}")
        print()
        print("Forecast — humidity & rain")
        print(format_table(rows))
        print()
        print("Summary")
        print(f"  Total precipitation: {summary['total_precip_mm']} mm")
        print(f"  Max rain probability: {summary['max_precip_probability_pct']}%")
        print(
            f"  Humidity avg/min/max: {summary['humidity_avg_pct']}% / "
            f"{summary['humidity_min_pct']}% / {summary['humidity_max_pct']}%"
        )
        if schedule_block:
            print()
            print("Irrigation schedule")
            print(f"  Sprinkler ON:        {schedule_block['sprinkler_on']}")
            print(f"  Duration:            {schedule_block['duration']}")
            print(f"  Duration (minutes):  {schedule_block['duration_minutes']}")
            if schedule_block.get("skip_reason"):
                print(f"  Skip reason:         {schedule_block['skip_reason']}")
        return 0

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
