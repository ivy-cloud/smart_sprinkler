#!/usr/bin/env python3
"""
Decide sprinkler ON/OFF and run duration from weather (humidity + rain forecast).

Wraps fetch_weather.py and applies rules documented in:
  docs/irrigation_schedule_design.md

Examples:
  python3 scripts/sprinkler_schedule.py --city "San Jose"
  python3 scripts/sprinkler_schedule.py --lat 37.34 --lon -121.89 --json
  python3 scripts/sprinkler_schedule.py --city "San Jose" --base-minutes 20 --flow-gpm 8
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Allow running as script from repo root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_weather import (  # noqa: E402
    HourForecast,
    WeatherForecast,
    load_forecast,
    resolve_place_from_args,
)

# --- Defaults (override via CLI or docs/irrigation_config.example.json) ---

DEFAULT_BASE_MINUTES = 20
DEFAULT_FLOW_GPM = 8.0  # typical 3/4" residential zone @ ~75% design capacity
DEFAULT_EFFICIENCY = 0.80  # sprinkler delivery efficiency (FAO-style ~80%)
DEFAULT_ZONE_AREA_SQFT = 1000  # small lawn zone placeholder

# Rain skip thresholds (aligned with common smart controllers, e.g. Hydrawise-style)
RAIN_PROB_SKIP_PCT = 50
RAIN_PROB_REDUCE_PCT = 30
TOMORROW_PRECIP_SKIP_MM = 2.0
TONIGHT_WINDOW_START = 22  # 10 PM local
TONIGHT_WINDOW_END = 6  # 6 AM local

# Humidity → duration multiplier (air humidity proxy when soil sensor absent)
HUMIDITY_BANDS: list[tuple[float, float, float, str]] = [
    # (min_inclusive, max_exclusive, multiplier, label)
    (0, 35, 1.25, "very_dry"),
    (35, 50, 1.00, "dry"),
    (50, 65, 0.75, "moderate"),
    (65, 80, 0.50, "humid"),
    (80, 101, 0.25, "very_humid"),
]


@dataclass
class SprinklerDecision:
    sprinkler_on: bool
    duration_minutes: int
    duration_seconds: int
    duration: str
    duration_factor: float
    estimated_gallons: float
    estimated_depth_mm: float
    base_minutes: int
    flow_gpm: float
    efficiency: float
    humidity_avg_pct: float | None
    humidity_band: str
    skip_reason: str | None
    rain_checks: dict[str, Any]
    recommended_start: str
    notes: list[str]


def _parse_hour_time(iso_time: str) -> datetime:
    return datetime.fromisoformat(iso_time)


def _hour_in_night_window(dt: datetime) -> bool:
    h = dt.hour
    if TONIGHT_WINDOW_START <= TONIGHT_WINDOW_END:
        return TONIGHT_WINDOW_START <= h < TONIGHT_WINDOW_END
    return h >= TONIGHT_WINDOW_START or h < TONIGHT_WINDOW_END


def _rows_for_local_date(rows: list[HourForecast], day: date) -> list[HourForecast]:
    out = []
    for r in rows:
        dt = _parse_hour_time(r.time)
        if dt.date() == day:
            out.append(r)
    return out


def _rain_stats(rows: list[HourForecast]) -> dict[str, Any]:
    if not rows:
        return {
            "hours": 0,
            "total_precip_mm": 0.0,
            "max_precip_probability_pct": 0,
            "rain_likely_hours": 0,
        }
    precip = [r.precip_mm or 0.0 for r in rows]
    prob = [r.precip_probability_pct or 0 for r in rows]
    rain_hours = sum(1 for r in rows if r.rain_likely)
    return {
        "hours": len(rows),
        "total_precip_mm": round(sum(precip), 2),
        "max_precip_probability_pct": max(prob),
        "rain_likely_hours": rain_hours,
    }


def humidity_multiplier(humidity_avg: float | None) -> tuple[float, str]:
    if humidity_avg is None:
        return 1.0, "unknown"
    for lo, hi, mult, label in HUMIDITY_BANDS:
        if lo <= humidity_avg < hi:
            return mult, label
    return 0.25, "very_humid"


def gallons_per_minute_to_mm_per_min(gpm: float, area_sqft: float, efficiency: float) -> float:
    """Convert applied flow to equivalent depth (mm/min) over zone area."""
    # 1 US gallon = 231 in³; 1 ft² = 144 in²; 1 inch = 25.4 mm
    if area_sqft <= 0 or efficiency <= 0:
        return 0.0
    gal_per_min_effective = gpm * efficiency
    inches_per_min = (gal_per_min_effective * 231.0) / (area_sqft * 144.0)
    return inches_per_min * 25.4


def decide_sprinkler(
    forecast: WeatherForecast,
    *,
    base_minutes: float = DEFAULT_BASE_MINUTES,
    flow_gpm: float = DEFAULT_FLOW_GPM,
    efficiency: float = DEFAULT_EFFICIENCY,
    zone_area_sqft: float = DEFAULT_ZONE_AREA_SQFT,
    now: datetime | None = None,
) -> SprinklerDecision:
    now = now or datetime.now().astimezone()
    today = now.date()
    tomorrow = today.fromordinal(today.toordinal() + 1)

    tonight_rows = [
        r
        for r in forecast.hourly
        if _parse_hour_time(r.time).date() == today
        and _hour_in_night_window(_parse_hour_time(r.time))
    ]
    # Early tomorrow AM still inside tonight window (e.g. before 06:00)
    tomorrow_early = [
        r
        for r in forecast.hourly
        if _parse_hour_time(r.time).date() == tomorrow
        and _parse_hour_time(r.time).hour < TONIGHT_WINDOW_END
    ]
    tonight_window = tonight_rows + tomorrow_early
    tomorrow_day = _rows_for_local_date(forecast.hourly, tomorrow)

    tonight_stats = _rain_stats(tonight_window)
    tomorrow_stats = _rain_stats(tomorrow_day)
    next24 = forecast.hourly[:24]
    humid_vals = [r.humidity_pct for r in next24 if r.humidity_pct is not None]
    humidity_avg = round(sum(humid_vals) / len(humid_vals), 1) if humid_vals else None

    factor, band = humidity_multiplier(humidity_avg)
    notes: list[str] = []
    skip_reason: str | None = None
    rain_factor = 1.0

    rain_checks = {
        "tonight_window": tonight_stats,
        "tomorrow": tomorrow_stats,
        "humidity_avg_next_24h_pct": humidity_avg,
    }

    # Rule 1: rain during tonight's watering window → OFF
    if tonight_stats["max_precip_probability_pct"] >= RAIN_PROB_SKIP_PCT or tonight_stats["rain_likely_hours"] > 0:
        skip_reason = (
            "Rain likely during tonight's watering window "
            f"(max {tonight_stats['max_precip_probability_pct']}% probability)."
        )

    # Rule 2: substantial rain tomorrow → skip watering tonight (user requirement)
    elif (
        tomorrow_stats["total_precip_mm"] >= TOMORROW_PRECIP_SKIP_MM
        or tomorrow_stats["max_precip_probability_pct"] >= RAIN_PROB_SKIP_PCT
        or tomorrow_stats["rain_likely_hours"] >= 3
    ):
        skip_reason = (
            "Rain expected tomorrow; skip irrigation tonight to avoid over-watering "
            f"({tomorrow_stats['total_precip_mm']} mm forecast, "
            f"max {tomorrow_stats['max_precip_probability_pct']}% prob)."
        )

    # Rule 3: light rain chance → shorten run
    elif (
        tonight_stats["max_precip_probability_pct"] >= RAIN_PROB_REDUCE_PCT
        or tomorrow_stats["max_precip_probability_pct"] >= RAIN_PROB_REDUCE_PCT
    ):
        rain_factor = 0.5
        notes.append("Light rain possible; duration reduced 50%.")

    # Rule 4: very humid air → minimal or no supplemental water from humidity alone
    if skip_reason is None and humidity_avg is not None and humidity_avg >= 85:
        skip_reason = f"Very high humidity ({humidity_avg}% avg); soil evaporation likely low."
    elif skip_reason is None and humidity_avg is not None and humidity_avg >= 80:
        factor = min(factor, 0.25)
        notes.append("High humidity; using minimum duration band.")

    duration_factor = round(factor * rain_factor, 2)
    raw_minutes = base_minutes * duration_factor
    duration_minutes = int(round(raw_minutes))
    duration_minutes = max(0, min(duration_minutes, 120))

    sprinkler_on = skip_reason is None and duration_minutes > 0

    if skip_reason:
        duration_minutes = 0

    estimated_gallons = round(flow_gpm * duration_minutes, 1) if sprinkler_on else 0.0
    depth_per_min = gallons_per_minute_to_mm_per_min(flow_gpm, zone_area_sqft, efficiency)
    estimated_depth_mm = round(depth_per_min * duration_minutes, 2) if sprinkler_on else 0.0

    recommended_start = f"{TONIGHT_WINDOW_START:02d}:00 local (configurable)"

    duration_seconds = duration_minutes * 60
    if sprinkler_on:
        duration_label = f"{duration_minutes} min ({duration_seconds} sec)"
    else:
        duration_label = "0 min (off)"

    return SprinklerDecision(
        sprinkler_on=sprinkler_on,
        duration_minutes=duration_minutes,
        duration_seconds=duration_seconds,
        duration=duration_label,
        duration_factor=duration_factor,
        estimated_gallons=estimated_gallons,
        estimated_depth_mm=estimated_depth_mm,
        base_minutes=int(base_minutes),
        flow_gpm=flow_gpm,
        efficiency=efficiency,
        humidity_avg_pct=humidity_avg,
        humidity_band=band,
        skip_reason=skip_reason,
        rain_checks=rain_checks,
        recommended_start=recommended_start,
        notes=notes,
    )


def format_decision_summary(decision: SprinklerDecision) -> str:
    state = "ON" if decision.sprinkler_on else "OFF"
    return (
        f"Sprinkler: {state} | Duration: {decision.duration} | "
        f"Start: {decision.recommended_start}"
    )


def format_decision_table(decision: SprinklerDecision) -> str:
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
    parser.add_argument("--base-minutes", type=float, default=DEFAULT_BASE_MINUTES)
    parser.add_argument("--flow-gpm", type=float, default=DEFAULT_FLOW_GPM)
    parser.add_argument("--efficiency", type=float, default=DEFAULT_EFFICIENCY)
    parser.add_argument("--zone-area-sqft", type=float, default=DEFAULT_ZONE_AREA_SQFT)
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
        decision = decide_sprinkler(
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

    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
