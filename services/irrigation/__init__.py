"""
Irrigation decision library: weather + soil + merged final output.

    from services.irrigation import get_final_decision_api, analyze_soil_api

HTTP: python3 scripts/api_server.py
"""

from __future__ import annotations

from services.irrigation.merge import get_final_decision, merge_decisions
from services.irrigation.soil import analyze_soil
from services.irrigation.types import (
    FinalIrrigationDecision,
    SoilDecision,
    SoilReading,
    WeatherDecision,
)
from services.irrigation.weather import decide_weather, get_weather_decision

__all__ = [
    "SoilReading",
    "SoilDecision",
    "WeatherDecision",
    "FinalIrrigationDecision",
    "analyze_soil",
    "decide_weather",
    "get_weather_decision",
    "merge_decisions",
    "get_final_decision",
    "get_weather_decision_api",
    "analyze_soil_api",
    "get_final_decision_api",
]


def get_weather_decision_api(
    *,
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    base_minutes: float = 20,
    flow_gpm: float = 8.0,
    efficiency: float = 0.8,
    zone_area_sqft: float = 1000,
) -> dict:
    from dataclasses import asdict

    forecast, decision = get_weather_decision(
        city=city,
        lat=lat,
        lon=lon,
        base_minutes=base_minutes,
        flow_gpm=flow_gpm,
        efficiency=efficiency,
        zone_area_sqft=zone_area_sqft,
    )
    return {
        "location": asdict(forecast.place),
        "timezone": forecast.timezone,
        "fetched_at": forecast.fetched_at,
        "sprinkler_on": decision.sprinkler_on,
        "duration_minutes": decision.duration_minutes,
        "duration_seconds": decision.duration_seconds,
        "duration": decision.duration,
        "weather": asdict(decision),
    }


def analyze_soil_api(
    sensor: SoilReading | dict | str,
    *,
    base_minutes: float = 20,
) -> dict:
    from dataclasses import asdict

    if isinstance(sensor, str):
        reading = SoilReading.from_csv_line(sensor)
    elif isinstance(sensor, dict):
        reading = SoilReading.from_dict(sensor)
    else:
        reading = sensor
    decision = analyze_soil(reading, base_minutes=base_minutes)
    return {
        "sprinkler_on": decision.sprinkler_on,
        "duration_minutes": decision.duration_minutes,
        "duration_seconds": decision.duration_seconds,
        "duration": decision.duration,
        "soil": asdict(decision),
    }


def get_final_decision_api(
    *,
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    sensor: SoilReading | dict | str,
    base_minutes: float = 20,
    flow_gpm: float = 8.0,
    efficiency: float = 0.8,
    zone_area_sqft: float = 1000,
) -> dict:
    from dataclasses import asdict

    forecast, weather, soil, final = get_final_decision(
        city=city,
        lat=lat,
        lon=lon,
        soil=sensor,
        base_minutes=base_minutes,
        flow_gpm=flow_gpm,
        efficiency=efficiency,
        zone_area_sqft=zone_area_sqft,
    )
    out = final.to_dict()
    out["location"] = asdict(forecast.place)
    out["timezone"] = forecast.timezone
    out["fetched_at"] = forecast.fetched_at
    return out
