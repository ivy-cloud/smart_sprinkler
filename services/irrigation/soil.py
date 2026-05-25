"""Soil sensor analysis for irrigation need (rules + optional ML)."""

from __future__ import annotations

from .config import (
    DEFAULT_BASE_MINUTES,
    SOIL_CRITICAL_WATER_LEVEL_PCT,
    SOIL_DRY_HUMIDITY_PCT,
    SOIL_DRY_WATER_LEVEL_PCT,
    SOIL_MAX_RUN_MINUTES,
    SOIL_MIN_RUN_MINUTES,
    SOIL_WET_HUMIDITY_PCT,
    SOIL_WET_WATER_LEVEL_PCT,
)
from .ml_inference import MlSoilInsights, analyze_ml
from .types import SoilDecision, SoilReading, duration_label

from services.weather.client import WeatherForecast


def _moisture_band(water_level: float | None, humidity: float | None) -> str:
    wl = water_level if water_level is not None else 50.0
    hum = humidity if humidity is not None else 50.0
    avg = (wl + hum) / 2.0
    if avg >= SOIL_WET_WATER_LEVEL_PCT:
        return "wet"
    if avg >= 55:
        return "moist"
    if avg >= SOIL_DRY_WATER_LEVEL_PCT:
        return "moderate"
    if avg >= SOIL_CRITICAL_WATER_LEVEL_PCT:
        return "dry"
    return "critical_dry"


def analyze_soil(
    reading: SoilReading,
    *,
    base_minutes: float = DEFAULT_BASE_MINUTES,
    forecast: WeatherForecast | None = None,
    use_ml: bool = True,
) -> tuple[SoilDecision, MlSoilInsights | None]:
    wl = reading.water_level_pct
    hum = reading.humidity_pct
    band = _moisture_band(wl, hum)
    notes: list[str] = []
    skip_reason: str | None = None
    factor = 1.0

    if wl is not None and wl >= SOIL_WET_WATER_LEVEL_PCT:
        skip_reason = f"Soil water level high ({wl:.1f}%); skip watering."
    elif hum is not None and hum >= SOIL_WET_HUMIDITY_PCT:
        skip_reason = f"Soil/air humidity high ({hum:.1f}%); skip watering."

    if skip_reason is None:
        if band == "critical_dry":
            factor = 1.35
            notes.append("Critical dry soil; increasing run time.")
        elif band == "dry":
            factor = 1.15
        elif band == "moderate":
            factor = 1.0
        elif band == "moist":
            factor = 0.6
            notes.append("Moist soil; reduced run time.")
        elif band == "wet":
            factor = 0.0

    raw_minutes = base_minutes * factor
    duration_minutes = int(round(max(0, min(raw_minutes, SOIL_MAX_RUN_MINUTES))))

    needs_water = band in {"critical_dry", "dry", "moderate"} and skip_reason is None
    if band == "moist" and skip_reason is None:
        needs_water = duration_minutes > 0

    if band == "critical_dry" and skip_reason is None:
        duration_minutes = max(duration_minutes, SOIL_MIN_RUN_MINUTES)

    sprinkler_on = needs_water and duration_minutes > 0 and skip_reason is None
    if skip_reason:
        duration_minutes = 0
        sprinkler_on = False

    ml_insights: MlSoilInsights | None = None
    ml_prob_needs: float | None = None
    ml_prob_watered: float | None = None
    ml_used = False

    if use_ml and forecast is not None:
        ml_insights = analyze_ml(reading, forecast)
        notes.extend(ml_insights.notes or [])
        ml_prob_needs = ml_insights.prob_needs_water
        ml_prob_watered = ml_insights.prob_watered
        ml_used = ml_insights.binary_available

        is_critical = (
            wl is not None and wl <= SOIL_CRITICAL_WATER_LEVEL_PCT
        ) or band == "critical_dry"

        if (
            ml_insights.ml_skip_watering
            and skip_reason is None
            and not is_critical
            and band in {"moist", "moderate", "wet"}
        ):
            skip_reason = (
                f"ML indicates soil likely watered recently "
                f"(p_watered={ml_prob_watered:.2f}); skipping with rules."
            )
            duration_minutes = 0
            sprinkler_on = False
            needs_water = False
            notes.append("ML + rules: defer irrigation (not critically dry).")

        elif (
            ml_insights.ml_boost_watering
            and skip_reason is None
            and not sprinkler_on
            and band in {"dry", "moderate", "critical_dry"}
        ):
            needs_water = True
            sprinkler_on = True
            duration_minutes = max(duration_minutes, SOIL_MIN_RUN_MINUTES)
            notes.append("ML + rules: boost irrigation (model sees dry soil).")
            ml_used = True

    mins, secs, label = duration_label(duration_minutes)

    decision = SoilDecision(
        needs_water=needs_water,
        sprinkler_on=sprinkler_on,
        duration_minutes=mins,
        duration_seconds=secs,
        duration=label,
        duration_factor=round(factor, 2),
        moisture_band=band,
        skip_reason=skip_reason,
        notes=notes,
        reading=reading,
        ml_prob_needs_water=ml_prob_needs,
        ml_prob_watered=ml_prob_watered,
        ml_used=ml_used,
    )
    return decision, ml_insights
