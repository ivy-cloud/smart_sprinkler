#!/usr/bin/env python3
"""
HTTP API for irrigation decisions (weather + soil + merged).

Run:
  pip install -r requirements.txt
  python3 scripts/api_server.py

Endpoints:
  GET  /health
  POST /v1/weather/decision
  POST /v1/soil/analyze
  POST /v1/irrigation/decision   ← combined final decision
"""

from __future__ import annotations

from typing import Any

from services.irrigation import (
    analyze_soil_api,
    get_final_decision_api,
    get_weather_decision_api,
)

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
    import uvicorn
except ImportError as exc:
    raise SystemExit(
        "Missing dependencies. Install with: pip install -r requirements.txt"
    ) from exc


class LocationBody(BaseModel):
    city: str | None = None
    lat: float | None = None
    lon: float | None = None


class ScheduleParams(BaseModel):
    base_minutes: float = 20
    flow_gpm: float = 8.0
    efficiency: float = 0.8
    zone_area_sqft: float = 1000


class WeatherRequest(LocationBody, ScheduleParams):
    pass


class SoilSensorBody(BaseModel):
    voltage: float | None = None
    current: float | None = None
    flow_rate_l_min: float | None = Field(default=None, alias="flowRate")
    water_level_pct: float | None = Field(default=None, alias="waterLevel")
    soil_temp_c: float | None = Field(default=None, alias="soilTemp")
    humidity_pct: float | None = Field(default=None, alias="humidity")
    csv_line: str | None = Field(
        default=None,
        description="Alternative: 'voltage,current,flow,level,temp,humidity'",
    )

    model_config = {"populate_by_name": True}


class SoilRequest(BaseModel):
    sensor: SoilSensorBody
    base_minutes: float = 20


class FinalDecisionRequest(LocationBody, ScheduleParams):
    sensor: SoilSensorBody


app = FastAPI(
    title="Smart Sprinkler Irrigation API",
    version="1.0.0",
    description="Weather + soil merged irrigation ON/OFF and duration.",
)


def _sensor_payload(body: SoilSensorBody) -> dict[str, Any] | str:
    if body.csv_line:
        return body.csv_line.strip()
    data = body.model_dump(by_alias=False, exclude={"csv_line"})
    if all(v is None for v in data.values()):
        raise HTTPException(status_code=400, detail="Provide sensor fields or csv_line")
    return {k: v for k, v in data.items() if v is not None}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/weather/decision")
def weather_decision(req: WeatherRequest) -> dict[str, Any]:
    try:
        return get_weather_decision_api(
            city=req.city,
            lat=req.lat,
            lon=req.lon,
            base_minutes=req.base_minutes,
            flow_gpm=req.flow_gpm,
            efficiency=req.efficiency,
            zone_area_sqft=req.zone_area_sqft,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/soil/analyze")
def soil_analyze(req: SoilRequest) -> dict[str, Any]:
    try:
        return analyze_soil_api(
            _sensor_payload(req.sensor),
            base_minutes=req.base_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/irrigation/decision")
def irrigation_decision(req: FinalDecisionRequest) -> dict[str, Any]:
    try:
        return get_final_decision_api(
            city=req.city,
            lat=req.lat,
            lon=req.lon,
            sensor=_sensor_payload(req.sensor),
            base_minutes=req.base_minutes,
            flow_gpm=req.flow_gpm,
            efficiency=req.efficiency,
            zone_area_sqft=req.zone_area_sqft,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


