"""Merge weather forecast decision with live soil sensor analysis."""

from __future__ import annotations

from .config import SOIL_CRITICAL_WATER_LEVEL_PCT, SOIL_MIN_RUN_MINUTES
from .soil import analyze_soil
from .types import FinalIrrigationDecision, SoilDecision, SoilReading, WeatherDecision, duration_label
from .weather import get_weather_decision

from services.weather.client import Place, WeatherForecast


def merge_decisions(
    weather: WeatherDecision,
    soil: SoilDecision,
    *,
    flow_gpm: float | None = None,
) -> FinalIrrigationDecision:
    """Combine weather cap/skip rules with soil need."""
    flow = flow_gpm if flow_gpm is not None else weather.flow_gpm
    notes: list[str] = list(weather.notes) + list(soil.notes)
    skip_reason: str | None = None
    decision_source = "merged"

    rain_hard = bool(weather.rain_checks.get("rain_skip_hard"))
    soil_critical = (
        soil.reading is not None
        and soil.reading.water_level_pct is not None
        and soil.reading.water_level_pct <= SOIL_CRITICAL_WATER_LEVEL_PCT
    )

    # Hard OFF: rain forecast (soil cannot override)
    if rain_hard:
        skip_reason = weather.skip_reason
        decision_source = "weather_rain_skip"
        mins = 0
    # Hard OFF: soil already wet
    elif soil.skip_reason:
        skip_reason = soil.skip_reason
        decision_source = "soil_skip"
        mins = 0
    # Hard OFF: weather soft skip and soil not critical
    elif weather.skip_reason and not soil_critical:
        skip_reason = weather.skip_reason
        decision_source = "weather_skip"
        mins = 0
    else:
        if weather.skip_reason and soil_critical:
            notes.append(
                "Weather suggested skip, but soil is critically dry; limited watering allowed."
            )
            decision_source = "soil_critical_override"

        if not soil.needs_water and not soil_critical:
            skip_reason = "Soil moisture adequate; no irrigation needed."
            decision_source = "soil_no_need"
            mins = 0
        else:
            if weather.sprinkler_on and soil.sprinkler_on:
                mins = min(weather.duration_minutes, soil.duration_minutes)
                decision_source = "merged_min"
                notes.append(
                    f"Duration = min(weather {weather.duration_minutes}, "
                    f"soil {soil.duration_minutes}) minutes."
                )
            elif soil.sprinkler_on:
                mins = soil.duration_minutes
                decision_source = "soil_primary"
            elif weather.sprinkler_on:
                mins = weather.duration_minutes
                decision_source = "weather_primary"
            else:
                mins = 0
                skip_reason = weather.skip_reason or soil.skip_reason or "No irrigation needed."
                decision_source = "both_off"

            if soil_critical and mins > 0:
                mins = max(mins, SOIL_MIN_RUN_MINUTES)

    mins = max(0, int(mins))
    sprinkler_on = mins > 0 and skip_reason is None
    mins_s, secs, label = duration_label(mins)
    gallons = round(flow * mins_s, 1) if sprinkler_on else 0.0
    depth = weather.estimated_depth_mm
    if sprinkler_on and weather.duration_minutes > 0:
        depth = round(weather.estimated_depth_mm * (mins_s / weather.duration_minutes), 2)
    elif not sprinkler_on:
        depth = 0.0

    return FinalIrrigationDecision(
        sprinkler_on=sprinkler_on,
        duration_minutes=mins_s,
        duration_seconds=secs,
        duration=label,
        estimated_gallons=gallons,
        estimated_depth_mm=depth,
        recommended_start=weather.recommended_start,
        decision_source=decision_source,
        skip_reason=skip_reason,
        notes=notes,
        weather=weather,
        soil=soil,
    )


def get_final_decision(
    *,
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    place: Place | None = None,
    soil: SoilReading | dict | str,
    base_minutes: float = 20,
    flow_gpm: float = 8.0,
    efficiency: float = 0.8,
    zone_area_sqft: float = 1000,
) -> tuple[WeatherForecast, WeatherDecision, SoilDecision, FinalIrrigationDecision]:
    if isinstance(soil, str):
        soil_reading = SoilReading.from_csv_line(soil)
    elif isinstance(soil, dict):
        soil_reading = SoilReading.from_dict(soil)
    else:
        soil_reading = soil

    forecast, weather = get_weather_decision(
        city=city,
        lat=lat,
        lon=lon,
        place=place,
        base_minutes=base_minutes,
        flow_gpm=flow_gpm,
        efficiency=efficiency,
        zone_area_sqft=zone_area_sqft,
    )
    soil_decision = analyze_soil(soil_reading, base_minutes=base_minutes)
    final = merge_decisions(weather, soil_decision, flow_gpm=flow_gpm)
    return forecast, weather, soil_decision, final
