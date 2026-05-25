"""Shared defaults for irrigation scheduling."""

DEFAULT_BASE_MINUTES = 20
DEFAULT_FLOW_GPM = 8.0
DEFAULT_EFFICIENCY = 0.80
DEFAULT_ZONE_AREA_SQFT = 1000

RAIN_PROB_SKIP_PCT = 50
RAIN_PROB_REDUCE_PCT = 30
TOMORROW_PRECIP_SKIP_MM = 2.0
TONIGHT_WINDOW_START = 22
TONIGHT_WINDOW_END = 6

# Soil thresholds (tune with field calibration)
SOIL_WET_WATER_LEVEL_PCT = 70
SOIL_DRY_WATER_LEVEL_PCT = 35
SOIL_WET_HUMIDITY_PCT = 78
SOIL_DRY_HUMIDITY_PCT = 45
SOIL_CRITICAL_WATER_LEVEL_PCT = 20
SOIL_MIN_RUN_MINUTES = 8
SOIL_MAX_RUN_MINUTES = 45

HUMIDITY_BANDS: list[tuple[float, float, float, str]] = [
    (0, 35, 1.25, "very_dry"),
    (35, 50, 1.00, "dry"),
    (50, 65, 0.75, "moderate"),
    (65, 80, 0.50, "humid"),
    (80, 101, 0.25, "very_humid"),
]
