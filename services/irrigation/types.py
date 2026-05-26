"""Shared datatypes for irrigation decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SoilReading:
    """Sensor row aligned with heli_tx CSV: voltage,current,flow,level,temp,humidity."""

    voltage: float | None = None
    current: float | None = None
    flow_rate_l_min: float | None = None
    water_level_pct: float | None = None
    soil_temp_c: float | None = None
    humidity_pct: float | None = None
    salinity_uS_cm: float | None = None
    conductivity_uS_cm: float | None = None

    @classmethod
    def from_csv_line(cls, line: str) -> "SoilReading":
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 6:
            raise ValueError("Expected 6 comma-separated sensor values")

        def f(i: int) -> float | None:
            try:
                return float(parts[i])
            except ValueError:
                return None

        return cls(
            voltage=f(0),
            current=f(1),
            flow_rate_l_min=f(2),
            water_level_pct=f(3),
            soil_temp_c=f(4),
            humidity_pct=f(5),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SoilReading":
        aliases = {
            "flow_rate_l_min": ("flow_rate_l_min", "flowRate", "flow_rate"),
            "water_level_pct": ("water_level_pct", "waterLevel", "water_level"),
            "soil_temp_c": ("soil_temp_c", "soilTemp", "soil_temp"),
            "humidity_pct": ("humidity_pct", "humidity"),
            "salinity_uS_cm": ("salinity_uS_cm", "salinity"),
            "conductivity_uS_cm": ("conductivity_uS_cm", "conductivity", "ec"),
        }

        def pick(*keys: str) -> float | None:
            for k in keys:
                if k in data and data[k] is not None:
                    return float(data[k])
            return None

        return cls(
            voltage=pick("voltage"),
            current=pick("current"),
            flow_rate_l_min=pick(*aliases["flow_rate_l_min"]),
            water_level_pct=pick(*aliases["water_level_pct"]),
            soil_temp_c=pick(*aliases["soil_temp_c"]),
            humidity_pct=pick(*aliases["humidity_pct"]),
            salinity_uS_cm=pick(*aliases["salinity_uS_cm"]),
            conductivity_uS_cm=pick(*aliases["conductivity_uS_cm"]),
        )


@dataclass
class WeatherDecision:
    source: str = "weather"
    sprinkler_on: bool = False
    duration_minutes: int = 0
    duration_seconds: int = 0
    duration: str = "0 min (off)"
    duration_factor: float = 0.0
    estimated_gallons: float = 0.0
    estimated_depth_mm: float = 0.0
    base_minutes: int = 20
    flow_gpm: float = 8.0
    efficiency: float = 0.8
    humidity_avg_pct: float | None = None
    humidity_band: str = "unknown"
    skip_reason: str | None = None
    rain_checks: dict[str, Any] = field(default_factory=dict)
    recommended_start: str = "22:00 local"
    notes: list[str] = field(default_factory=list)


@dataclass
class SoilDecision:
    source: str = "soil"
    needs_water: bool = False
    sprinkler_on: bool = False
    duration_minutes: int = 0
    duration_seconds: int = 0
    duration: str = "0 min (off)"
    duration_factor: float = 1.0
    moisture_band: str = "unknown"
    skip_reason: str | None = None
    notes: list[str] = field(default_factory=list)
    reading: SoilReading | None = None
    ml_prob_needs_water: float | None = None
    ml_prob_watered: float | None = None
    ml_used: bool = False


@dataclass
class FinalIrrigationDecision:
    sprinkler_on: bool
    duration_minutes: int
    duration_seconds: int
    duration: str
    estimated_gallons: float
    estimated_depth_mm: float
    recommended_start: str
    decision_source: str
    skip_reason: str | None
    notes: list[str] = field(default_factory=list)
    weather: WeatherDecision | None = None
    soil: SoilDecision | None = None
    days_to_next_watering: float | None = None
    ml: dict[str, Any] | None = None
    nozzle_angle_deg: int | None = None
    vision: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sprinkler_on": self.sprinkler_on,
            "duration_minutes": self.duration_minutes,
            "duration_seconds": self.duration_seconds,
            "duration": self.duration,
            "estimated_gallons": self.estimated_gallons,
            "estimated_depth_mm": self.estimated_depth_mm,
            "recommended_start": self.recommended_start,
            "decision_source": self.decision_source,
            "skip_reason": self.skip_reason,
            "notes": self.notes,
            "days_to_next_watering": self.days_to_next_watering,
            "ml": self.ml,
            "nozzle_angle_deg": self.nozzle_angle_deg,
            "vision": self.vision,
            "weather": asdict(self.weather) if self.weather else None,
            "soil": asdict(self.soil) if self.soil else None,
        }


def duration_label(minutes: int) -> tuple[int, int, str]:
    seconds = max(0, minutes) * 60
    if minutes > 0:
        return minutes, seconds, f"{minutes} min ({seconds} sec)"
    return 0, 0, "0 min (off)"
