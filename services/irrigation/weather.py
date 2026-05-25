"""Weather-based irrigation decision (forecast humidity + rain)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from services.weather.client import HourForecast, Place, WeatherForecast, load_forecast

from .config import (
    DEFAULT_BASE_MINUTES,
    DEFAULT_EFFICIENCY,
    DEFAULT_FLOW_GPM,
    DEFAULT_ZONE_AREA_SQFT,
    HUMIDITY_BANDS,
    RAIN_PROB_REDUCE_PCT,
    RAIN_PROB_SKIP_PCT,
    TOMORROW_PRECIP_SKIP_MM,
    TONIGHT_WINDOW_END,
    TONIGHT_WINDOW_START,
)
from .types import WeatherDecision, duration_label


def _parse_hour_time(iso_time: str) -> datetime:
    return datetime.fromisoformat(iso_time)


def _hour_in_night_window(dt: datetime) -> bool:
    h = dt.hour
    if TONIGHT_WINDOW_START <= TONIGHT_WINDOW_END:
        return TONIGHT_WINDOW_START <= h < TONIGHT_WINDOW_END
    return h >= TONIGHT_WINDOW_START or h < TONIGHT_WINDOW_END


def _rows_for_local_date(rows: list[HourForecast], day: date) -> list[HourForecast]:
    return [r for r in rows if _parse_hour_time(r.time).date() == day]


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
    return {
        "hours": len(rows),
        "total_precip_mm": round(sum(precip), 2),
        "max_precip_probability_pct": max(prob),
        "rain_likely_hours": sum(1 for r in rows if r.rain_likely),
    }


def humidity_multiplier(humidity_avg: float | None) -> tuple[float, str]:
    if humidity_avg is None:
        return 1.0, "unknown"
    for lo, hi, mult, label in HUMIDITY_BANDS:
        if lo <= humidity_avg < hi:
            return mult, label
    return 0.25, "very_humid"


def gallons_per_minute_to_mm_per_min(gpm: float, area_sqft: float, efficiency: float) -> float:
    if area_sqft <= 0 or efficiency <= 0:
        return 0.0
    gal_per_min_effective = gpm * efficiency
    inches_per_min = (gal_per_min_effective * 231.0) / (area_sqft * 144.0)
    return inches_per_min * 25.4


def decide_weather(
    forecast: WeatherForecast,
    *,
    base_minutes: float = DEFAULT_BASE_MINUTES,
    flow_gpm: float = DEFAULT_FLOW_GPM,
    efficiency: float = DEFAULT_EFFICIENCY,
    zone_area_sqft: float = DEFAULT_ZONE_AREA_SQFT,
    now: datetime | None = None,
) -> WeatherDecision:
    now = now or datetime.now().astimezone()
    today = now.date()
    tomorrow = today.fromordinal(today.toordinal() + 1)

    tonight_rows = [
        r
        for r in forecast.hourly
        if _parse_hour_time(r.time).date() == today
        and _hour_in_night_window(_parse_hour_time(r.time))
    ]
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
    rain_skip_hard = False

    rain_checks = {
        "tonight_window": tonight_stats,
        "tomorrow": tomorrow_stats,
        "humidity_avg_next_24h_pct": humidity_avg,
    }

    if (
        tonight_stats["max_precip_probability_pct"] >= RAIN_PROB_SKIP_PCT
        or tonight_stats["rain_likely_hours"] > 0
    ):
        rain_skip_hard = True
        skip_reason = (
            "Rain likely during tonight's watering window "
            f"(max {tonight_stats['max_precip_probability_pct']}% probability)."
        )
    elif (
        tomorrow_stats["total_precip_mm"] >= TOMORROW_PRECIP_SKIP_MM
        or tomorrow_stats["max_precip_probability_pct"] >= RAIN_PROB_SKIP_PCT
        or tomorrow_stats["rain_likely_hours"] >= 3
    ):
        rain_skip_hard = True
        skip_reason = (
            "Rain expected tomorrow; skip irrigation tonight "
            f"({tomorrow_stats['total_precip_mm']} mm, "
            f"max {tomorrow_stats['max_precip_probability_pct']}% prob)."
        )
    elif (
        tonight_stats["max_precip_probability_pct"] >= RAIN_PROB_REDUCE_PCT
        or tomorrow_stats["max_precip_probability_pct"] >= RAIN_PROB_REDUCE_PCT
    ):
        rain_factor = 0.5
        notes.append("Light rain possible; duration reduced 50%.")

    if skip_reason is None and humidity_avg is not None and humidity_avg >= 85:
        skip_reason = f"Very high humidity ({humidity_avg}% avg); soil evaporation likely low."
    elif skip_reason is None and humidity_avg is not None and humidity_avg >= 80:
        factor = min(factor, 0.25)
        notes.append("High humidity; using minimum duration band.")

    duration_factor = round(factor * rain_factor, 2)
    duration_minutes = int(round(max(0, min(base_minutes * duration_factor, 120))))

    sprinkler_on = skip_reason is None and duration_minutes > 0
    if skip_reason:
        duration_minutes = 0

    estimated_gallons = round(flow_gpm * duration_minutes, 1) if sprinkler_on else 0.0
    depth_per_min = gallons_per_minute_to_mm_per_min(flow_gpm, zone_area_sqft, efficiency)
    estimated_depth_mm = round(depth_per_min * duration_minutes, 2) if sprinkler_on else 0.0

    mins, secs, label = duration_label(duration_minutes)

    return WeatherDecision(
        sprinkler_on=sprinkler_on,
        duration_minutes=mins,
        duration_seconds=secs,
        duration=label,
        duration_factor=duration_factor,
        estimated_gallons=estimated_gallons,
        estimated_depth_mm=estimated_depth_mm,
        base_minutes=int(base_minutes),
        flow_gpm=flow_gpm,
        efficiency=efficiency,
        humidity_avg_pct=humidity_avg,
        humidity_band=band,
        skip_reason=skip_reason,
        rain_checks={**rain_checks, "rain_skip_hard": rain_skip_hard},
        recommended_start=f"{TONIGHT_WINDOW_START:02d}:00 local",
        notes=notes,
    )


def get_weather_decision(
    *,
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    place: Place | None = None,
    base_minutes: float = DEFAULT_BASE_MINUTES,
    flow_gpm: float = DEFAULT_FLOW_GPM,
    efficiency: float = DEFAULT_EFFICIENCY,
    zone_area_sqft: float = DEFAULT_ZONE_AREA_SQFT,
) -> tuple[WeatherForecast, WeatherDecision]:
    if place is None:
        from services.weather.client import resolve_place_from_args

        place = resolve_place_from_args(city=city, lat=lat, lon=lon, auto_location=False)
    forecast = load_forecast(place, forecast_days=2)
    decision = decide_weather(
        forecast,
        base_minutes=base_minutes,
        flow_gpm=flow_gpm,
        efficiency=efficiency,
        zone_area_sqft=zone_area_sqft,
    )
    return forecast, decision
