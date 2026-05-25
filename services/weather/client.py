#!/usr/bin/env python3
"""
Weather forecast helpers for smart sprinkler (Open-Meteo, no API key).

Library: from services.weather import load_forecast
CLI: python3 scripts/fetch_weather.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
IP_LOCATION_URL = "http://ip-api.com/json/?fields=status,lat,lon,city,regionName,country"

RAIN_WEATHER_CODES = {
    51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99,
}


@dataclass
class Place:
    name: str
    latitude: float
    longitude: float
    timezone: str | None = None


@dataclass
class HourForecast:
    time: str
    humidity_pct: float | None
    precip_mm: float | None
    precip_probability_pct: float | None
    weather_code: int | None
    is_rain_code: bool

    @property
    def rain_likely(self) -> bool:
        return (
            (self.precip_mm or 0) > 0.05
            or (self.precip_probability_pct or 0) >= 50
            or self.is_rain_code
        )


@dataclass
class WeatherForecast:
    place: Place
    timezone: str
    fetched_at: str
    hourly: list[HourForecast]

    def summary_24h(self) -> dict[str, Any]:
        rows = self.hourly[:24]
        return _summarize_rows(rows)


def http_get_json(url: str, timeout: float = 20.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "smart-sprinkler/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def geocode_city(city: str) -> Place:
    candidates = [city.strip()]
    if "," in city:
        candidates.append(city.split(",", 1)[0].strip())

    for name in candidates:
        params = urllib.parse.urlencode(
            {"name": name, "count": 1, "language": "en", "format": "json"}
        )
        data = http_get_json(f"{GEOCODE_URL}?{params}")
        results = data.get("results") or []
        if results:
            r = results[0]
            label = ", ".join(
                x for x in [r.get("name"), r.get("admin1"), r.get("country")] if x
            )
            return Place(
                name=label,
                latitude=float(r["latitude"]),
                longitude=float(r["longitude"]),
                timezone=r.get("timezone"),
            )

    raise ValueError(f'No location found for city: "{city}"')


def guess_place_from_ip() -> Place:
    data = http_get_json(IP_LOCATION_URL)
    if data.get("status") != "success":
        raise ValueError("Could not guess location from IP.")
    label = ", ".join(
        x
        for x in [data.get("city"), data.get("regionName"), data.get("country")]
        if x
    )
    return Place(
        name=label or "IP location",
        latitude=float(data["lat"]),
        longitude=float(data["lon"]),
    )


def resolve_place_from_args(
    *,
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    auto_location: bool = False,
) -> Place:
    if lat is not None and lon is not None:
        name = city or f"{lat:.4f}, {lon:.4f}"
        return Place(name=name, latitude=lat, longitude=lon)

    city = city or os.environ.get("WEATHER_CITY")
    if city:
        return geocode_city(city)

    env_lat = os.environ.get("WEATHER_LAT")
    env_lon = os.environ.get("WEATHER_LON")
    if env_lat and env_lon:
        return Place(
            name=f"{env_lat}, {env_lon}",
            latitude=float(env_lat),
            longitude=float(env_lon),
        )

    if auto_location:
        return guess_place_from_ip()

    raise ValueError(
        "Provide city, lat/lon, WEATHER_CITY, WEATHER_LAT/WEATHER_LON, or auto_location."
    )


def fetch_forecast_raw(
    place: Place, *, forecast_days: int = 2
) -> dict[str, Any]:
    params = {
        "latitude": place.latitude,
        "longitude": place.longitude,
        "hourly": ",".join(
            [
                "precipitation",
                "precipitation_probability",
                "relative_humidity_2m",
                "weather_code",
            ]
        ),
        "forecast_days": forecast_days,
        "timezone": "auto",
    }
    query = urllib.parse.urlencode(params)
    return http_get_json(f"{FORECAST_URL}?{query}")


def parse_hourly(data: dict[str, Any], max_hours: int | None = None) -> list[HourForecast]:
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    humidities = hourly.get("relative_humidity_2m") or []
    precip = hourly.get("precipitation") or []
    precip_prob = hourly.get("precipitation_probability") or []
    codes = hourly.get("weather_code") or []

    limit = len(times) if max_hours is None else min(max_hours, len(times))
    rows: list[HourForecast] = []
    for i in range(limit):
        code = codes[i] if i < len(codes) else None
        rows.append(
            HourForecast(
                time=times[i],
                humidity_pct=humidities[i] if i < len(humidities) else None,
                precip_mm=precip[i] if i < len(precip) else None,
                precip_probability_pct=precip_prob[i] if i < len(precip_prob) else None,
                weather_code=code,
                is_rain_code=code in RAIN_WEATHER_CODES if code is not None else False,
            )
        )
    return rows


def load_forecast(
    place: Place,
    *,
    forecast_days: int = 2,
    max_hours: int | None = None,
) -> WeatherForecast:
    raw = fetch_forecast_raw(place, forecast_days=forecast_days)
    hourly = parse_hourly(raw, max_hours=max_hours)
    return WeatherForecast(
        place=place,
        timezone=raw.get("timezone") or place.timezone or "local",
        fetched_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        hourly=hourly,
    )


def _summarize_rows(rows: list[HourForecast]) -> dict[str, Any]:
    humid = [r.humidity_pct for r in rows if r.humidity_pct is not None]
    precip = [r.precip_mm or 0.0 for r in rows]
    precip_prob = [r.precip_probability_pct or 0 for r in rows]
    rain_hours = [r for r in rows if r.rain_likely]

    avg_h = sum(humid) / len(humid) if humid else None

    return {
        "hours": len(rows),
        "total_precip_mm": round(sum(precip), 2),
        "max_precip_probability_pct": max(precip_prob) if precip_prob else 0,
        "rain_likely_hours": len(rain_hours),
        "humidity_avg_pct": round(avg_h, 1) if avg_h is not None else None,
        "humidity_min_pct": min(humid) if humid else None,
        "humidity_max_pct": max(humid) if humid else None,
    }


def format_table(rows: list[HourForecast]) -> str:
    lines = [
        f"{'Time':<20} {'Humidity':>9} {'Rain%':>7} {'Precip':>8} {'Rain?':>6}",
        "-" * 54,
    ]
    for r in rows:
        t = r.time.replace("T", " ")[:16]
        h = f"{r.humidity_pct:.0f}%" if r.humidity_pct is not None else "n/a"
        rp = (
            f"{r.precip_probability_pct:.0f}%"
            if r.precip_probability_pct is not None
            else "n/a"
        )
        pm = f"{r.precip_mm:.2f}mm" if r.precip_mm is not None else "n/a"
        rain = "yes" if r.rain_likely else "no"
        lines.append(f"{t:<20} {h:>9} {rp:>7} {pm:>8} {rain:>6}")
    return "\n".join(lines)


def forecast_to_dict(forecast: WeatherForecast, hours: int = 24) -> dict[str, Any]:
    rows = forecast.hourly[:hours]
    return {
        "location": asdict(forecast.place),
        "timezone": forecast.timezone,
        "fetched_at": forecast.fetched_at,
        "summary": _summarize_rows(rows),
        "hourly": [
            {
                "time": r.time,
                "humidity_pct": r.humidity_pct,
                "precipitation_mm": r.precip_mm,
                "precipitation_probability_pct": r.precip_probability_pct,
                "weather_code": r.weather_code,
                "rain_likely": r.rain_likely,
            }
            for r in rows
        ],
    }


