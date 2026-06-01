#!/usr/bin/env python3
"""
Read live soil sensor lines from ESP32 USB serial and run ML + irrigation decision.

Works with:
  - soil_node_rx:     [SOIL] Humidity: 0.0%   Temperature: 23.4 C   ...
  - soil_node_rx:     [CSV] 0,0,0,0.0,23.4,0.0
  - SoilNode TX USB:  湿度: 0.0%   温度: 25.8℃   ...  (Chinese)
  - heli CSV:         12.1,0.4,0.0,28,22.5,41

When BLE/RX reports soil temp 0 (common), patch from another source:
  --temp-port   second USB serial (e.g. SoilNode TX) for 温度 / Temperature
  --city        also enables Open-Meteo air temp fallback (disable: --no-temp-from-weather)

Examples:
  python3 scripts/soil_serial_ml.py --list-ports
  python3 scripts/soil_serial_ml.py --port /dev/cu.usbmodem12301 --city "San Jose"
  python3 scripts/soil_serial_ml.py --port /dev/cu.RX --temp-port /dev/cu.TX --city "San Jose"
  python3 scripts/soil_serial_ml.py --port /dev/cu.usbmodem12301 --city "San Jose" --json
  python3 scripts/soil_serial_ml.py --port /dev/cu.usbmodem12301 --soil-only --no-weather

Requires: pip install pyserial
Close Arduino Serial Monitor before running.
"""

from __future__ import annotations

import argparse
import json
import re
import select
import sys
import time
from dataclasses import dataclass

import _bootstrap  # noqa: F401

from hp_tk_serial import list_serial_ports, open_hp_tk_serial
from services.irrigation import analyze_soil_api, get_final_decision_api
from services.weather.client import load_forecast, resolve_place_from_args

SOIL_TEMP_MISSING_C = 0.05
DEFAULT_TEMP_STALE_S = 120.0
DEFAULT_WEATHER_REFRESH_S = 600.0
FRAME_IDLE_S = 0.25


def parse_sensor_line(raw: str) -> str | None:
    """Return 6-field CSV for SoilReading.from_csv_line, or None."""
    line = raw.strip()
    if not line or line.startswith("ESP-ROM") or line.startswith("BOOT-"):
        return None
    if line.startswith("[SOIL]"):
        line = line[6:].strip()
    if line.startswith("[CSV]"):
        line = line[5:].strip()
    if line.startswith("From STM32:"):
        line = line[11:].strip()

    parts = [p.strip() for p in line.split(",")]
    if len(parts) == 6:
        try:
            [float(p) for p in parts]
            return ",".join(parts)
        except ValueError:
            pass

    if "humidity" in line.lower() or "moisture" in line.lower():
        return _english_soil_line_to_csv(line)

    if "湿度" in line or "温度" in line:
        return _chinese_soil_line_to_csv(line)

    return None


def parse_temperature_from_line(raw: str) -> float | None:
    """Extract soil/air temperature from a serial line (TX USB or [SOIL] text)."""
    line = raw.strip()
    if not line or line.startswith("ESP-ROM") or line.startswith("BOOT-"):
        return None
    if line.startswith("[SOIL]"):
        line = line[6:].strip()
    if line.startswith("[CSV]"):
        line = line[5:].strip()

    parts = [p.strip() for p in line.split(",")]
    if len(parts) == 6:
        try:
            temp = float(parts[4])
            if -40.0 <= temp <= 80.0:
                return temp
        except ValueError:
            pass

    for key in ("温度", "Temperature", "Soil temp"):
        m = re.search(rf"{re.escape(key)}:\s*([\d.]+)", line, re.IGNORECASE)
        if not m:
            continue
        try:
            temp = float(m.group(1))
        except ValueError:
            continue
        if -40.0 <= temp <= 80.0:
            return temp

    return None


def soil_temp_missing(csv_line: str, *, threshold: float = SOIL_TEMP_MISSING_C) -> bool:
    parts = [p.strip() for p in csv_line.split(",")]
    if len(parts) != 6:
        return True
    try:
        return float(parts[4]) <= threshold
    except ValueError:
        return True


def patch_csv_soil_temp(csv_line: str, temp_c: float) -> str:
    parts = [p.strip() for p in csv_line.split(",")]
    if len(parts) != 6:
        return csv_line
    parts[4] = f"{temp_c:.1f}"
    return ",".join(parts)


class TemperatureSupplement:
    """Fill missing soil temp from TX serial and/or Open-Meteo air temperature."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._serial_temp_c: float | None = None
        self._serial_at = 0.0
        self._weather_temp_c: float | None = None
        self._weather_at = 0.0
        self._place = None
        if args.temp_from_weather and (args.city or args.lat is not None):
            try:
                self._place = resolve_place_from_args(
                    city=args.city, lat=args.lat, lon=args.lon
                )
            except ValueError:
                self._place = None

    def note_serial(self, temp_c: float) -> None:
        if temp_c <= SOIL_TEMP_MISSING_C:
            return
        self._serial_temp_c = temp_c
        self._serial_at = time.monotonic()

    def _refresh_weather_temp(self) -> float | None:
        if self._place is None:
            return None
        now = time.monotonic()
        if (
            self._weather_temp_c is not None
            and now - self._weather_at < self.args.weather_refresh_seconds
        ):
            return self._weather_temp_c
        try:
            forecast = load_forecast(self._place, forecast_days=1, max_hours=1)
            row = forecast.hourly[0] if forecast.hourly else None
            temp = row.temperature_c if row else None
        except Exception as exc:
            print(f"Warning: weather temp fetch failed: {exc}", file=sys.stderr)
            return self._weather_temp_c
        if temp is None:
            return self._weather_temp_c
        self._weather_temp_c = float(temp)
        self._weather_at = now
        return self._weather_temp_c

    def apply(self, csv_line: str) -> tuple[str, str | None]:
        if not soil_temp_missing(csv_line):
            return csv_line, None

        now = time.monotonic()
        if (
            self._serial_temp_c is not None
            and now - self._serial_at <= self.args.temp_stale_seconds
        ):
            return (
                patch_csv_soil_temp(csv_line, self._serial_temp_c),
                f"tx-serial ({self._serial_temp_c:.1f}°C)",
            )

        if self.args.temp_from_weather:
            air = self._refresh_weather_temp()
            if air is not None and air > SOIL_TEMP_MISSING_C:
                return patch_csv_soil_temp(csv_line, air), f"weather-air ({air:.1f}°C)"

        return csv_line, None

    def apply_to_reading(self, reading: dict) -> tuple[dict, str | None]:
        temp = reading.get("soil_temp_c")
        if temp is not None and float(temp) > SOIL_TEMP_MISSING_C:
            return reading, None

        csv_line = reading_to_csv_line(reading)
        patched, src = self.apply(csv_line)
        if not src:
            return reading, None

        parts = patched.split(",")
        out = dict(reading)
        out["soil_temp_c"] = float(parts[4])
        return out, src


@dataclass
class SoilFrame:
    """Merged soil reading from [SOIL] + [CSV] + [EC] lines (one RX print block)."""

    moisture: float | None = None
    temp_c: float | None = None
    salinity_uS_cm: float | None = None
    conductivity_uS_cm: float | None = None
    voltage: float | None = None
    current: float | None = None
    flow_rate_l_min: float | None = None
    water_level_pct: float | None = None
    humidity_pct: float | None = None
    touched: bool = False

    def merge_numeric(self, key: str, value: float | None) -> None:
        if value is None:
            return
        setattr(self, key, value)
        self.touched = True

    def has_ml_signal(self) -> bool:
        return (
            (self.salinity_uS_cm is not None and self.salinity_uS_cm > 0)
            or (self.conductivity_uS_cm is not None and self.conductivity_uS_cm > 0)
            or (self.moisture is not None and self.moisture > SOIL_TEMP_MISSING_C)
            or (self.humidity_pct is not None and self.humidity_pct > SOIL_TEMP_MISSING_C)
            or (self.water_level_pct is not None and self.water_level_pct > SOIL_TEMP_MISSING_C)
            or (self.temp_c is not None and self.temp_c > SOIL_TEMP_MISSING_C)
            or (self.voltage is not None and self.voltage > 0)
        )

    def to_reading_dict(self) -> dict:
        moisture = self.moisture
        if moisture is None:
            moisture = self.water_level_pct
        if moisture is None:
            moisture = self.humidity_pct
        if moisture is None:
            moisture = 0.0

        temp = self.temp_c if self.temp_c is not None else 0.0
        reading: dict = {
            "water_level_pct": self.water_level_pct if self.water_level_pct is not None else moisture,
            "humidity_pct": self.humidity_pct if self.humidity_pct is not None else moisture,
            "soil_temp_c": temp,
        }
        if self.voltage is not None:
            reading["voltage"] = self.voltage
        if self.current is not None:
            reading["current"] = self.current
        if self.flow_rate_l_min is not None:
            reading["flow_rate_l_min"] = self.flow_rate_l_min
        if self.salinity_uS_cm is not None:
            reading["salinity_uS_cm"] = self.salinity_uS_cm
        if self.conductivity_uS_cm is not None:
            reading["conductivity_uS_cm"] = self.conductivity_uS_cm
        return reading


class SoilFrameAccumulator:
    """RX prints [SOIL], [CSV], [EC] back-to-back — merge before ML."""

    def __init__(self) -> None:
        self._frame = SoilFrame()
        self._last_update = 0.0

    def _reset(self) -> None:
        self._frame = SoilFrame()
        self._last_update = 0.0

    def _complete(self) -> dict | None:
        if not self._frame.touched or not self._frame.has_ml_signal():
            self._reset()
            return None
        reading = self._frame.to_reading_dict()
        self._reset()
        return reading

    def ingest(self, raw: str) -> dict | None:
        line = raw.strip()
        if not line or line.startswith("ESP-ROM") or line.startswith("BOOT-"):
            return None

        self._last_update = time.monotonic()
        complete_now = False

        if line.startswith("[EC]"):
            ec = parse_ec_line(line)
            if ec.get("salinity_uS_cm") is not None:
                self._frame.merge_numeric("salinity_uS_cm", ec["salinity_uS_cm"])
            if ec.get("conductivity_uS_cm") is not None:
                self._frame.merge_numeric("conductivity_uS_cm", ec["conductivity_uS_cm"])
            complete_now = True
        elif line.startswith("[SOIL]"):
            merge_soil_text_fields(self._frame, line[6:].strip())
        elif line.startswith("[CSV]"):
            merge_csv_fields(self._frame, line[5:].strip())
        elif line.startswith("From STM32:"):
            merge_csv_fields(self._frame, line[11:].strip())
        else:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 6 and _looks_like_sensor_csv(parts):
                merge_csv_fields(self._frame, line)
                if _heli_style_csv(parts):
                    complete_now = True
            elif _looks_like_soil_text(line):
                merge_soil_text_fields(self._frame, line)
                complete_now = True

        if complete_now:
            return self._complete()
        return None

    def take_if_idle(self, idle_s: float = FRAME_IDLE_S) -> dict | None:
        if not self._frame.touched or self._last_update == 0.0:
            return None
        if time.monotonic() - self._last_update < idle_s:
            return None
        return self._complete()


def reading_to_csv_line(reading: dict) -> str:
    wl = reading.get("water_level_pct", reading.get("humidity_pct", 0.0)) or 0.0
    hum = reading.get("humidity_pct", wl) or wl
    temp = reading.get("soil_temp_c", 0.0) or 0.0
    volt = reading.get("voltage", 0.0) or 0.0
    curr = reading.get("current", 0.0) or 0.0
    flow = reading.get("flow_rate_l_min", 0.0) or 0.0
    return f"{volt:.1f},{curr:.1f},{flow:.1f},{wl:.1f},{temp:.1f},{hum:.1f}"


def parse_ec_line(line: str) -> dict:
    out: dict = {}
    for key in ("salinity_uS_cm", "conductivity_uS_cm"):
        m = re.search(rf"{re.escape(key)}=([\d.]+)", line, re.IGNORECASE)
        if m:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                pass
    return out


def merge_soil_text_fields(frame: SoilFrame, line: str) -> None:
    for attr, key in (
        ("moisture", "Humidity"),
        ("moisture", "Moisture"),
        ("temp_c", "Temperature"),
        ("temp_c", "Soil temp"),
        ("salinity_uS_cm", "Salinity"),
        ("conductivity_uS_cm", "Conductivity"),
        ("moisture", "湿度"),
        ("temp_c", "温度"),
        ("salinity_uS_cm", "盐分"),
        ("conductivity_uS_cm", "电导率"),
    ):
        val = _grab_float(line, key)
        if val is not None:
            frame.merge_numeric(attr, val)


def merge_csv_fields(frame: SoilFrame, line: str) -> None:
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != 6:
        return
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return

    frame.merge_numeric("voltage", values[0])
    frame.merge_numeric("current", values[1])
    frame.merge_numeric("flow_rate_l_min", values[2])
    if values[3] > SOIL_TEMP_MISSING_C:
        frame.merge_numeric("water_level_pct", values[3])
        frame.merge_numeric("moisture", values[3])
    if values[4] > SOIL_TEMP_MISSING_C:
        frame.merge_numeric("temp_c", values[4])
    if values[5] > SOIL_TEMP_MISSING_C:
        frame.merge_numeric("humidity_pct", values[5])
        if frame.moisture is None or frame.moisture <= SOIL_TEMP_MISSING_C:
            frame.merge_numeric("moisture", values[5])


def _looks_like_sensor_csv(parts: list[str]) -> bool:
    try:
        [float(p) for p in parts]
        return True
    except ValueError:
        return False


def _heli_style_csv(parts: list[str]) -> bool:
    """STM32/heli_tx CSV has real voltage — not the RX placeholder 0,0,0,..."""
    try:
        return float(parts[0]) > 1.0 or float(parts[3]) > SOIL_TEMP_MISSING_C
    except ValueError:
        return False


def _looks_like_soil_text(line: str) -> bool:
    lower = line.lower()
    return (
        "humidity" in lower
        or "moisture" in lower
        or "temperature" in lower
        or "salinity" in lower
        or "conductivity" in lower
        or "湿度" in line
        or "温度" in line
        or "盐分" in line
        or "电导率" in line
    )


def print_ml_inputs(reading: dict, temp_src: str | None) -> None:
    csv_line = reading_to_csv_line(reading)
    temp_note = f"  (temp from {temp_src})" if temp_src else ""
    print(f">> legacy CSV: {csv_line}{temp_note}")
    sal = reading.get("salinity_uS_cm")
    ec = reading.get("conductivity_uS_cm")
    hum = reading.get("humidity_pct")
    temp = reading.get("soil_temp_c")
    sal_s = f"{sal:.0f}" if sal is not None else "—"
    ec_s = f"{ec:.0f}" if ec is not None else "—"
    hum_s = f"{hum:.1f}" if hum is not None else "—"
    temp_s = f"{temp:.1f}" if temp is not None else "—"
    print(
        f">> ML inputs:   humidity={hum_s}%  temp={temp_s}°C  "
        f"salinity={sal_s} μS/cm  EC={ec_s} μS/cm"
    )


def _grab_float(line: str, key: str) -> float | None:
    m = re.search(rf"{re.escape(key)}:\s*([\d.]+)", line, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _english_soil_line_to_csv(line: str) -> str | None:
    moisture = _grab_float(line, "Humidity")
    if moisture is None:
        moisture = _grab_float(line, "Moisture")

    temp = _grab_float(line, "Temperature")
    if temp is None:
        temp = _grab_float(line, "Soil temp")

    if moisture is None and temp is None:
        return None

    wl = moisture if moisture is not None else 0.0
    st = temp if temp is not None else 0.0
    hum = wl
    return f"0,0,0,{wl:.1f},{st:.1f},{hum:.1f}"


def _chinese_soil_line_to_csv(line: str) -> str | None:
    moisture = _grab_float(line, "湿度")
    temp = _grab_float(line, "温度")
    if moisture is None and temp is None:
        return None

    wl = moisture if moisture is not None else 0.0
    st = temp if temp is not None else 0.0
    hum = wl
    return f"0,0,0,{wl:.1f},{st:.1f},{hum:.1f}"


def print_decision(payload: dict) -> None:
    print("--- decision ---")
    print(f"  sprinkler_on:     {payload.get('sprinkler_on')}")
    print(f"  duration_minutes: {payload.get('duration_minutes')}")
    if payload.get("skip_reason"):
        print(f"  skip_reason:      {payload.get('skip_reason')}")
    if payload.get("days_to_next_watering") is not None:
        print(f"  next_water_days:  {payload.get('days_to_next_watering')} (ML)")
    ml = payload.get("ml") or {}
    if ml.get("prob_needs_water") is not None:
        print(f"  ml_needs_water:   {ml.get('prob_needs_water'):.2f}")
    if ml.get("notes"):
        for note in ml["notes"]:
            print(f"  ml_note:          {note}")
    print()


def run(args: argparse.Namespace) -> int:
    last_fingerprint: tuple | None = None
    last_run = 0.0
    temp_supplement = TemperatureSupplement(args)
    accumulator = SoilFrameAccumulator()

    ser_temp = open_hp_tk_serial(args.temp_port, baud=args.baud) if args.temp_port else None
    try:
        with open_hp_tk_serial(args.port, baud=args.baud) as ser:
            print(f"Listening on {args.port} @ {args.baud} (Ctrl+C to stop)")
            if args.temp_port:
                print(f"Temperature from {args.temp_port} (SoilNode TX USB)")
            elif args.temp_from_weather and temp_supplement._place:
                print(
                    f"Missing soil temp -> Open-Meteo air @ {temp_supplement._place.name}"
                )
            print("ML uses merged [SOIL]+[CSV]+[EC] (not raw zero CSV alone)")
            print(f"Mode: {'soil-only' if args.soil_only else 'weather+soil+ML'}")
            if args.city:
                print(f"Location: {args.city}")
            print()

            readers: list = [ser]
            if ser_temp is not None:
                readers.append(ser_temp)

            def process_reading(reading: dict) -> None:
                nonlocal last_fingerprint, last_run

                reading, temp_src = temp_supplement.apply_to_reading(reading)
                fingerprint = (
                    reading.get("humidity_pct"),
                    reading.get("soil_temp_c"),
                    reading.get("salinity_uS_cm"),
                    reading.get("conductivity_uS_cm"),
                )

                now = time.monotonic()
                if fingerprint == last_fingerprint and (now - last_run) < args.min_interval:
                    return
                if (now - last_run) < args.min_interval:
                    return

                last_fingerprint = fingerprint
                last_run = now
                print_ml_inputs(reading, temp_src)

                use_ml = not args.no_ml
                if args.soil_only:
                    payload = analyze_soil_api(
                        reading,
                        base_minutes=args.base_minutes,
                        city=args.city,
                        lat=args.lat,
                        lon=args.lon,
                        use_ml=use_ml,
                    )
                else:
                    payload = get_final_decision_api(
                        city=args.city,
                        lat=args.lat,
                        lon=args.lon,
                        sensor=reading,
                        base_minutes=args.base_minutes,
                        flow_gpm=args.flow_gpm,
                        use_ml=use_ml,
                    )

                if args.json:
                    print(json.dumps(payload, indent=2))
                else:
                    print_decision(payload)

            while True:
                ready, _, _ = select.select(readers, [], [], 0.5)

                if ser_temp is not None and ser_temp in ready:
                    raw_temp = (
                        ser_temp.readline()
                        .decode("utf-8", errors="ignore")
                        .strip()
                    )
                    if raw_temp:
                        t = parse_temperature_from_line(raw_temp)
                        if t is not None:
                            temp_supplement.note_serial(t)
                            if args.verbose:
                                print(f"<< [temp-port] {raw_temp} -> {t:.1f}°C")

                if ser in ready:
                    raw = ser.readline().decode("utf-8", errors="ignore").strip()
                    if raw:
                        if args.verbose:
                            print(f"<< {raw}")
                        completed = accumulator.ingest(raw)
                        if completed is not None:
                            process_reading(completed)

                idle_reading = accumulator.take_if_idle()
                if idle_reading is not None:
                    process_reading(idle_reading)
    finally:
        if ser_temp is not None:
            ser_temp.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Realtime serial soil data -> ML irrigation decision"
    )
    parser.add_argument("--port", help="ESP32 USB serial (RX or TX board)")
    parser.add_argument(
        "--temp-port",
        help="Second USB serial for soil temperature (e.g. SoilNode TX when --port is BLE RX)",
    )
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--city", help="City for weather + ML regression (recommended)")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument(
        "--temp-from-weather",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When soil temp is 0/missing, use Open-Meteo air temp if --city/--lat set (default: on)",
    )
    parser.add_argument(
        "--temp-stale-seconds",
        type=float,
        default=DEFAULT_TEMP_STALE_S,
        help=f"Max age for --temp-port reading (default {DEFAULT_TEMP_STALE_S:.0f}s)",
    )
    parser.add_argument(
        "--weather-refresh-seconds",
        type=float,
        default=DEFAULT_WEATHER_REFRESH_S,
        help=f"Refresh interval for air-temp fallback (default {DEFAULT_WEATHER_REFRESH_S:.0f}s)",
    )
    parser.add_argument("--base-minutes", type=float, default=20)
    parser.add_argument("--flow-gpm", type=float, default=8.0)
    parser.add_argument(
        "--min-interval",
        type=float,
        default=10.0,
        help="Seconds between predictions (default 10)",
    )
    parser.add_argument(
        "--soil-only",
        action="store_true",
        help="Soil rules + ML only (no weather merge)",
    )
    parser.add_argument(
        "--no-weather",
        action="store_true",
        help="Alias for --soil-only",
    )
    parser.add_argument("--no-ml", action="store_true", help="Rules only, no ML models")
    parser.add_argument("--json", action="store_true", help="Print full JSON each decision")
    parser.add_argument("--verbose", action="store_true", help="Print every serial line")
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List serial ports and exit",
    )
    args = parser.parse_args()
    if args.no_weather:
        args.soil_only = True

    if args.list_ports:
        for device in list_serial_ports():
            print(device)
        return 0

    if not args.port:
        parser.error("--port is required (or use --list-ports)")

    if args.temp_port and args.temp_port == args.port:
        parser.error("--temp-port must differ from --port")

    if not args.soil_only and not args.city and args.lat is None:
        print(
            "Warning: no --city/--lat; weather merge needs location. "
            "Use --soil-only or pass --city.",
            file=sys.stderr,
        )

    try:
        run(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
