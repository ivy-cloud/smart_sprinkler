"""
Irrigation decision library: weather + soil + merged final output (+ optional ML).

    from services.irrigation import get_final_decision_api, analyze_soil_api

HTTP: python3 scripts/api_server.py
"""

from __future__ import annotations

from services.irrigation.merge import get_final_decision, merge_decisions
from services.irrigation.ml_inference import ml_insights_to_dict
from services.irrigation.soil import analyze_soil
from services.irrigation.types import (
    FinalIrrigationDecision,
    SoilDecision,
    SoilReading,
    WeatherDecision,
)
from services.irrigation.weather import decide_weather, get_weather_decision
from services.weather.client import load_forecast, resolve_place_from_args

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


def _agro_summary_dict(forecast) -> dict:
    agro = forecast.summary_24h()
    return {
        "et0_avg_mm": agro.get("et0_avg_mm"),
        "vpd_avg_kpa": agro.get("vpd_avg_kpa"),
        "rain_recent": agro.get("rain_recent"),
        "humidity_avg_pct": agro.get("humidity_avg_pct"),
    }


def _load_forecast_optional(
    *,
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
):
    if city is None and lat is None and lon is None:
        return None
    place = resolve_place_from_args(city=city, lat=lat, lon=lon, auto_location=False)
    return load_forecast(place, forecast_days=2)


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
        "agro_summary": _agro_summary_dict(forecast),
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
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    use_ml: bool = True,
) -> dict:
    from dataclasses import asdict

    if isinstance(sensor, str):
        reading = SoilReading.from_csv_line(sensor)
    elif isinstance(sensor, dict):
        reading = SoilReading.from_dict(sensor)
    else:
        reading = sensor

    forecast = _load_forecast_optional(city=city, lat=lat, lon=lon) if use_ml else None
    decision, ml_insights = analyze_soil(
        reading,
        base_minutes=base_minutes,
        forecast=forecast,
        use_ml=use_ml,
    )
    out = {
        "sprinkler_on": decision.sprinkler_on,
        "duration_minutes": decision.duration_minutes,
        "duration_seconds": decision.duration_seconds,
        "duration": decision.duration,
        "soil": asdict(decision),
        "ml": ml_insights_to_dict(ml_insights),
    }
    if forecast is not None:
        out["agro_summary"] = _agro_summary_dict(forecast)
    return out


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
    use_ml: bool = True,
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
        use_ml=use_ml,
    )
    out = final.to_dict()
    out["location"] = asdict(forecast.place)
    out["timezone"] = forecast.timezone
    out["fetched_at"] = forecast.fetched_at
    out["agro_summary"] = _agro_summary_dict(forecast)
    return out
