"""Open-Meteo weather forecast client."""

from services.weather.client import (
    HourForecast,
    Place,
    WeatherForecast,
    fetch_forecast_raw,
    forecast_to_dict,
    format_table,
    geocode_city,
    guess_place_from_ip,
    load_forecast,
    parse_hourly,
    resolve_place_from_args,
)

__all__ = [
    "Place",
    "HourForecast",
    "WeatherForecast",
    "load_forecast",
    "resolve_place_from_args",
    "forecast_to_dict",
    "format_table",
    "geocode_city",
    "guess_place_from_ip",
    "fetch_forecast_raw",
    "parse_hourly",
]
