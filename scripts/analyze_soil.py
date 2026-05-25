#!/usr/bin/env python3
"""
Analyze soil sensor data and optionally merge with weather forecast.

Examples:
  python3 scripts/analyze_soil.py --csv "12.1,0.4,0.0,28,22.5,41"
  python3 scripts/analyze_soil.py --csv "12.1,0.4,0.0,28,22.5,41" --city "San Jose"
  python3 scripts/analyze_soil.py --csv "12.1,0.4,0.0,28,22.5,41" --city "San Jose" --json
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from services.irrigation import analyze_soil_api, get_final_decision_api


def main() -> int:
    parser = argparse.ArgumentParser(description="Soil sensor irrigation analysis")
    parser.add_argument(
        "--csv",
        required=True,
        help="Sensor line: voltage,current,flow,waterLevel,soilTemp,humidity",
    )
    parser.add_argument("--city")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--base-minutes", type=float, default=20)
    parser.add_argument("--flow-gpm", type=float, default=8.0)
    parser.add_argument("--soil-only", action="store_true", help="Soil analysis only (no weather)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        if args.soil_only:
            payload = analyze_soil_api(args.csv, base_minutes=args.base_minutes)
        else:
            payload = get_final_decision_api(
                city=args.city,
                lat=args.lat,
                lon=args.lon,
                sensor=args.csv,
                base_minutes=args.base_minutes,
                flow_gpm=args.flow_gpm,
            )

        if args.json:
            print(json.dumps(payload, indent=2))
            return 0

        print("=== Soil + weather irrigation decision ===")
        print(f"Sprinkler ON:  {payload.get('sprinkler_on')}")
        print(f"Duration:      {payload.get('duration')}")
        print(f"Duration (min): {payload.get('duration_minutes')}")
        if payload.get("decision_source"):
            print(f"Source:        {payload.get('decision_source')}")
        if payload.get("skip_reason"):
            print(f"Skip reason:   {payload.get('skip_reason')}")
        return 0

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
