#!/usr/bin/env python3
"""
Interactive VOFA supervisor: live weather + fake vision + soil thread → stop/continue watering.

  • Fetches current local/Open-Meteo weather and prints a summary.
  • Cycles fake camera frames every few seconds (default 3s) with new aim angles.
  • Background thread reads soil from gateway USB (firewater: / soil_hum: lines).
  • Main loop merges weather + live soil; prints START/STOP when you change the probe by hand.

Close VOFA+ / Serial Monitor before using --port.

Examples:
  python3 scripts/vofa_supervisor_live.py --city "San Jose" --dry-run
  python3 scripts/vofa_supervisor_live.py --auto-location --fake-image dry_left --dry-run
  python3 scripts/vofa_supervisor_live.py --port /dev/cu.usbmodemXXX --city "San Jose" \\
      --fake-image dry_left --motor 120 --vision-interval 3
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass, field

import _bootstrap  # noqa: F401

from hp_tk_serial import list_serial_ports, open_hp_tk_serial
from services.irrigation import get_final_decision_api
from services.weather.client import (
    _summarize_rows,
    format_table,
    load_forecast,
    resolve_place_from_args,
)
from vofa_supervisor_demo import firewater_to_soil_csv, parse_firewater, send_aim, send_vofa


# Lawn aim waypoints (yaw/pitch in gateway servo range 0–180°).
# Ordered left → right for ping-pong sweep while pump is ON.
AIM_WAYPOINT_ORDER: tuple[str, ...] = (
    "far_left",
    "left",
    "center_near",
    "center_far",
    "right",
    "far_right",
)

# Yaw ~20°↔160° (pan) and pitch ~35°↔145° (tilt) so both axes move clearly.
AIM_WAYPOINTS: dict[str, dict] = {
    "far_left": {
        "title": "Far left lawn sector",
        "yaw": 160,
        "pitch": 40,
        "lines": [
            "+------------------------------+",
            "|  FAKE CAMERA  [DRY far L]  |",
            "| *                            |",
            "|  ========================    |",
            "|         lawn / grass         |",
            "+------------------------------+",
        ],
    },
    "left": {
        "title": "Left lawn sector",
        "yaw": 130,
        "pitch": 120,
        "lines": [
            "+------------------------------+",
            "|  FAKE CAMERA  [DRY left]   |",
            "|      *                       |",
            "|  ========================    |",
            "|         lawn / grass         |",
            "+------------------------------+",
        ],
    },
    "center_near": {
        "title": "Center near (mid range)",
        "yaw": 100,
        "pitch": 50,
        "lines": [
            "+------------------------------+",
            "|  FAKE CAMERA [DRY center]  |",
            "|         *                    |",
            "|  ========================    |",
            "|         lawn / grass         |",
            "+------------------------------+",
        ],
    },
    "center_far": {
        "title": "Center far (deep range)",
        "yaw": 90,
        "pitch": 140,
        "lines": [
            "+------------------------------+",
            "|  FAKE CAMERA [DRY far ctr] |",
            "|            *                 |",
            "|  ========================    |",
            "|         lawn / grass         |",
            "+------------------------------+",
        ],
    },
    "right": {
        "title": "Right lawn sector",
        "yaw": 50,
        "pitch": 45,
        "lines": [
            "+------------------------------+",
            "|  FAKE CAMERA [DRY right]   |",
            "|                    *         |",
            "|  ========================    |",
            "|         lawn / grass         |",
            "+------------------------------+",
        ],
    },
    "far_right": {
        "title": "Far right lawn sector",
        "yaw": 20,
        "pitch": 130,
        "lines": [
            "+------------------------------+",
            "|  FAKE CAMERA [DRY far R]   |",
            "|                          *   |",
            "|  ========================    |",
            "|         lawn / grass         |",
            "+------------------------------+",
        ],
    },
}

# Backward-compatible names from earlier demos.
AIM_ALIASES: dict[str, str] = {
    "dry_left": "left",
    "dry_right": "right",
    "dry_center": "center_far",
}

# Legacy export for docs/tests.
FAKE_FRAMES = AIM_WAYPOINTS


def resolve_waypoint_key(name: str) -> str:
    key = AIM_ALIASES.get(name, name)
    if key not in AIM_WAYPOINTS:
        known = sorted(set(AIM_WAYPOINTS) | set(AIM_ALIASES))
        raise SystemExit(f"Unknown aim waypoint {name!r}. Choose: {', '.join(known)}")
    return key


def parse_aim_sequence(spec: str | None) -> list[str]:
    """Comma-separated waypoint keys, e.g. left,center_near,right,center_near."""
    if not spec:
        return list(AIM_WAYPOINT_ORDER)
    keys = [resolve_waypoint_key(part.strip()) for part in spec.split(",") if part.strip()]
    if len(keys) < 2:
        raise SystemExit("--aim-sequence needs at least 2 waypoints for ping-pong")
    return keys


def format_waypoint_table(keys: list[str]) -> str:
    parts = [f"{k} Y={AIM_WAYPOINTS[k]['yaw']} P={AIM_WAYPOINTS[k]['pitch']}" for k in keys]
    return "  |  ".join(parts)


@dataclass
class VisionSnapshot:
    """Latest fake ML frame and nozzle aim (thread-safe copy via lock)."""

    frame_key: str = "dry_left"
    yaw: int = 115
    pitch: int = 68
    updated_at: float = 0.0
    frame_count: int = 0


@dataclass
class SoilSnapshot:
    """Latest soil reading from gateway serial (thread-safe copy via lock)."""

    csv_line: str = "0,0,0,50,22.0,50"
    humidity: float = 50.0
    temp_c: float = 22.0
    ph: float = 7.0
    raw_line: str = ""
    updated_at: float = 0.0
    line_count: int = 0


@dataclass
class SupervisorState:
    soil: SoilSnapshot = field(default_factory=SoilSnapshot)
    vision: VisionSnapshot = field(default_factory=VisionSnapshot)
    soil_lock: threading.Lock = field(default_factory=threading.Lock)
    vision_lock: threading.Lock = field(default_factory=threading.Lock)
    serial_lock: threading.Lock = field(default_factory=threading.Lock)
    stop_event: threading.Event = field(default_factory=threading.Event)
    wakeup: threading.Event = field(default_factory=threading.Event)
    watering: bool = False
    last_sprinkler_on: bool | None = None
    last_skip_reason: str | None = None
    last_printed_humidity: float | None = None
    last_sent_yaw: int | None = None
    last_sent_pitch: int | None = None


@dataclass
class ThresholdConfig:
    """Hysteresis: water when dry, stop when wet, hold in between."""

    dry_below: float = 50.0   # at or below → START watering
    wet_above: float = 60.0   # at or above → STOP watering

    def validate(self) -> None:
        if self.dry_below >= self.wet_above:
            raise SystemExit(
                f"--dry-below ({self.dry_below}) must be less than --wet-above ({self.wet_above})"
            )


def evaluate_threshold(
    humidity: float,
    cfg: ThresholdConfig,
    *,
    currently_on: bool | None,
) -> tuple[bool, str, str]:
    """Return (sprinkler_on, source, reason)."""
    if humidity <= cfg.dry_below:
        return (
            True,
            "threshold_dry",
            f"Soil {humidity:.1f}% <= dry limit {cfg.dry_below:.1f}% → WATER",
        )
    if humidity >= cfg.wet_above:
        return (
            False,
            "threshold_wet",
            f"Soil {humidity:.1f}% >= wet limit {cfg.wet_above:.1f}% → STOP",
        )
    if currently_on is None:
        mid = (cfg.dry_below + cfg.wet_above) / 2.0
        on = humidity < mid
        return (
            on,
            "threshold_init",
            f"Soil {humidity:.1f}% in band ({cfg.dry_below}-{cfg.wet_above}); "
            f"starting as {'WATER' if on else 'SKIP'}",
        )
    hold = "ON" if currently_on else "OFF"
    return (
        currently_on,
        "threshold_hold",
        f"Soil {humidity:.1f}% between limits — holding {hold} (hysteresis)",
    )


def threshold_payload(on: bool, source: str, reason: str) -> dict:
    return {
        "sprinkler_on": on,
        "duration_minutes": 8 if on else 0,
        "decision_source": source,
        "skip_reason": None if on else reason,
        "notes": [reason],
    }


def parse_gateway_soil_line(line: str, snap: SoilSnapshot) -> bool:
    """Update snapshot from firewater: or soil_*: lines. Returns True if updated."""
    line = line.strip()
    if not line or line.startswith("ESP-ROM") or line.startswith("BOOT-"):
        return False

    fw = parse_firewater(line)
    if fw is not None:
        snap.humidity = float(fw["humidity"])
        snap.temp_c = float(fw["temp_c"])
        snap.ph = float(fw.get("ph") or snap.ph)
        snap.csv_line = firewater_to_soil_csv(fw)
        snap.raw_line = line
        snap.updated_at = time.time()
        snap.line_count += 1
        return True

    if line.startswith("soil_hum:"):
        try:
            snap.humidity = float(line.split(":", 1)[1].strip())
            snap.csv_line = f"0,0,0,{snap.humidity:.1f},{snap.temp_c:.1f},{snap.humidity:.1f}"
            snap.raw_line = line
            snap.updated_at = time.time()
            snap.line_count += 1
            return True
        except ValueError:
            return False

    if line.startswith("soil_temp:"):
        try:
            snap.temp_c = float(line.split(":", 1)[1].strip())
            parts = [p.strip() for p in snap.csv_line.split(",")]
            if len(parts) == 6:
                parts[4] = f"{snap.temp_c:.1f}"
                snap.csv_line = ",".join(parts)
            snap.raw_line = line
            snap.updated_at = time.time()
            return True
        except ValueError:
            return False

    return False


def soil_reader_thread(ser, state: SupervisorState) -> None:
    """Read gateway serial in background; update shared soil snapshot."""
    while not state.stop_event.is_set():
        try:
            with state.serial_lock:
                raw = ser.readline()
        except Exception as exc:
            print(f"\n[soil-thread] serial error: {exc}")
            break
        if not raw:
            continue
        try:
            line = raw.decode("utf-8", errors="ignore")
        except Exception:
            continue
        with state.soil_lock:
            if not parse_gateway_soil_line(line, state.soil):
                continue
            hum = state.soil.humidity
            changed = state.last_printed_humidity is None or abs(hum - state.last_printed_humidity) >= 0.5
            if changed:
                state.last_printed_humidity = hum
                print(
                    f"\n[soil] NEW reading: humidity={hum:.1f}%  temp={state.soil.temp_c:.1f}°C  "
                    f"(#{state.soil.line_count})  ← touch/wet probe to move this value"
                )
                state.wakeup.set()


def init_vision_snapshot(frame_key: str) -> VisionSnapshot:
    key = resolve_waypoint_key(frame_key)
    frame = AIM_WAYPOINTS[key]
    return VisionSnapshot(
        frame_key=key,
        yaw=int(frame["yaw"]),
        pitch=int(frame["pitch"]),
        updated_at=time.time(),
        frame_count=1,
    )


def apply_vision_frame(state: SupervisorState, key: str) -> int:
    """Update shared vision snapshot from a waypoint key; return frame count."""
    frame = AIM_WAYPOINTS[key]
    yaw = int(frame["yaw"])
    pitch = int(frame["pitch"])
    with state.vision_lock:
        state.vision.frame_key = key
        state.vision.yaw = yaw
        state.vision.pitch = pitch
        state.vision.updated_at = time.time()
        state.vision.frame_count += 1
        frame_num = state.vision.frame_count
    print_vision_update(key, yaw=yaw, pitch=pitch, frame_num=frame_num)
    state.wakeup.set()
    return frame_num


def next_ping_pong_index(idx: int, n: int, direction: int) -> tuple[int, int]:
    """Step through 0..n-1 and back (e.g. 0,1,2,1,0,1,2,…)."""
    if n <= 1:
        return idx, direction
    nxt = idx + direction
    if nxt >= n:
        return n - 2, -1
    if nxt < 0:
        return 1, 1
    return nxt, direction


def vision_simulator_thread(
    state: SupervisorState,
    frame_keys: list[str],
    *,
    start_index: int,
    interval_s: float,
    mode: str,
) -> None:
    """Emit fake ML frames; supervisor retargets nozzle aim while pump is ON."""
    idx = start_index
    direction = 1 if start_index < len(frame_keys) - 1 else -1
    while not state.stop_event.wait(timeout=max(0.1, interval_s)):
        if mode == "pingpong":
            idx, direction = next_ping_pong_index(idx, len(frame_keys), direction)
        else:
            idx = (idx + 1) % len(frame_keys)
        apply_vision_frame(state, frame_keys[idx])


def print_weather_block(*, city: str | None, lat: float | None, lon: float | None, auto: bool):
    place = resolve_place_from_args(city=city, lat=lat, lon=lon, auto_location=auto)
    forecast = load_forecast(place, forecast_days=2, max_hours=24)
    rows = forecast.hourly[:12]
    summary = _summarize_rows(rows)

    print()
    print("=" * 70)
    print("  LIVE WEATHER (Open-Meteo)")
    print("=" * 70)
    print(f"  Location:  {place.name}")
    print(f"  Coords:    {place.latitude:.4f}, {place.longitude:.4f}")
    print(f"  Timezone:  {forecast.timezone}")
    print(f"  Fetched:   {forecast.fetched_at}")
    print()
    print("  Next ~12 h (humidity & rain):")
    print(format_table(rows))
    print()
    print("  Summary (24h window):")
    print(f"    Total precip:     {summary.get('total_precip_mm')} mm")
    print(f"    Max rain prob:    {summary.get('max_precip_probability_pct')}%")
    print(
        f"    Humidity avg:     {summary.get('humidity_avg_pct')}% "
        f"(min {summary.get('humidity_min_pct')}% / max {summary.get('humidity_max_pct')}%)"
    )
    print(f"    ET0 avg:          {summary.get('et0_avg_mm')} mm")
    print(f"    VPD avg:          {summary.get('vpd_avg_kpa')} kPa")
    print("=" * 70)
    print()
    return place, forecast


def print_fake_vision(frame_key: str) -> tuple[int, int]:
    key = resolve_waypoint_key(frame_key)
    frame = AIM_WAYPOINTS[key]

    yaw = int(frame["yaw"])
    pitch = int(frame["pitch"])

    print("=" * 70)
    print("  FAKE VISION / ML AIM (demo — not a real camera yet)")
    print("=" * 70)
    print(f"  Waypoint:  {key}" + (f" (alias {frame_key})" if key != frame_key else ""))
    print(f"  Title:     {frame['title']}")
    print()
    for ln in frame["lines"]:
        print(f"  {ln}")
    print()
    print("  ML interpretation:")
    print(f"    Dry patch centroid → nozzle aim")
    print(f"    Yaw (Y):   {yaw}°  — horizontal aim")
    print(f"    Pitch (P): {pitch}° — vertical aim")
    print()
    print("  (Later: replace with YOLO + real ESP32-CAM frame)")
    print("=" * 70)
    print()
    return yaw, pitch


def print_vision_update(frame_key: str, *, yaw: int, pitch: int, frame_num: int) -> None:
    """Compact banner when a new fake ML frame arrives in the live loop."""
    frame = AIM_WAYPOINTS[frame_key]
    print()
    print("-" * 70)
    print(f"  [vision] NEW frame #{frame_num}: {frame_key} — {frame['title']}")
    print(f"           aim adjust → Y={yaw}°  P={pitch}°")
    for ln in frame["lines"][1:5]:
        print(f"  {ln}")
    print("-" * 70)


def evaluate_decision(
    *,
    city: str | None,
    lat: float | None,
    lon: float | None,
    soil_csv: str,
    use_ml: bool,
) -> dict:
    return get_final_decision_api(
        city=city,
        lat=lat,
        lon=lon,
        sensor=soil_csv,
        use_ml=use_ml,
    )


def print_status_line(
    *,
    snap: SoilSnapshot,
    payload: dict,
    watering: bool,
    vision: VisionSnapshot,
    cfg: ThresholdConfig | None = None,
) -> None:
    hum = snap.humidity
    on = bool(payload.get("sprinkler_on"))
    src = payload.get("decision_source", "?")
    pump_s = "ON " if watering else "OFF"
    dec_s = "WATER" if on else "SKIP "
    ts = time.strftime("%H:%M:%S")
    age = time.time() - snap.updated_at if snap.updated_at else None
    age_s = f"{age:.0f}s ago" if age is not None else "waiting for probe…"
    v_age = time.time() - vision.updated_at if vision.updated_at else None
    v_age_s = f"{v_age:.0f}s ago" if v_age is not None else "—"
    thresh = ""
    if cfg is not None:
        thresh = f"  limits: dry<={cfg.dry_below:.0f}% wet>={cfg.wet_above:.0f}%"
    print(
        f"[{ts}] soil={hum:5.1f}%{thresh}  decision={dec_s}  pump={pump_s}  "
        f"Y/P={vision.yaw}/{vision.pitch} ({vision.frame_key})  src={src}  "
        f"soil_rx={age_s}  vision_rx={v_age_s}"
    )
    reason = payload.get("skip_reason") or (payload.get("notes") or [None])[0]
    if reason and (not on or src.startswith("threshold")):
        print(f"         → {reason}")


def set_aim(
    ser,
    *,
    yaw: int,
    pitch: int,
    state: SupervisorState,
) -> bool:
    """Send yaw/pitch only. Returns True if serial was sent."""
    if state.last_sent_yaw == yaw and state.last_sent_pitch == pitch:
        return False
    with state.serial_lock:
        send_aim(ser, yaw, pitch)
    state.last_sent_yaw = yaw
    state.last_sent_pitch = pitch
    return True


def announce_vision_aim(
    *,
    vision: VisionSnapshot,
    actuate: bool,
    watering: bool,
    sent: bool,
) -> None:
    print()
    if watering:
        print("  >>> AIM UPDATE — new dry-patch target (watering active)")
    else:
        print("  >>> NEW FRAME — dry patch detected (pump off, aim unchanged)")
    print(f"      Frame:  {vision.frame_key}  (#{vision.frame_count})")
    print(f"      Y/P:    {vision.yaw} / {vision.pitch}")
    if not watering:
        print("      Serial: skipped — adjust aim only while pump is ON")
    elif actuate:
        print("      Serial: " + ("yaw/pitch sent to gateway" if sent else "unchanged angles"))
    else:
        print("      Serial: dry-run — no yaw/pitch commands")
    print()


def set_watering(
    ser,
    *,
    on: bool,
    yaw: int,
    pitch: int,
    motor: int,
    state: SupervisorState,
) -> None:
    with state.serial_lock:
        send_vofa(ser, "C", 0)
        send_aim(ser, yaw, pitch)
        if on:
            send_vofa(ser, "motor", motor)
            send_vofa(ser, "pump", 1)
            state.watering = True
        else:
            send_vofa(ser, "motor", 0)
            send_vofa(ser, "pump", 0)
            state.watering = False
    state.last_sent_yaw = yaw
    state.last_sent_pitch = pitch


def announce_transition(
    *,
    now_on: bool,
    payload: dict,
    snap: SoilSnapshot,
    yaw: int,
    pitch: int,
    actuate: bool,
) -> None:
    bar = "!" * 70
    if now_on:
        print()
        print(bar)
        print("  >>> START WATERING — soil/weather says IRRIGATE")
        print(bar)
    else:
        print()
        print(bar)
        print("  >>> STOP WATERING — soil/weather says SKIP")
        print(bar)
    print(f"  Soil CSV:     {snap.csv_line}")
    print(f"  Humidity:     {snap.humidity:.1f}%")
    print(f"  Temp:         {snap.temp_c:.1f}°C")
    print(f"  Decision:     {payload.get('decision_source')}")
    print(f"  Duration:     {payload.get('duration_minutes')} min (production label)")
    if payload.get("skip_reason"):
        print(f"  Skip reason:  {payload.get('skip_reason')}")
    print(f"  Aim Y/P:      {yaw} / {pitch}")
    if actuate:
        print("  Actuation:    sending pump/motor commands to gateway")
    else:
        print("  Actuation:    dry-run — no serial commands")
    print(bar)
    print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Live VOFA supervisor: weather + fake vision + soil thread"
    )
    p.add_argument("--list-ports", action="store_true")
    p.add_argument("--port", help="Gateway USB (VOFA must be closed)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--city", help='City e.g. "San Jose, CA"')
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--auto-location", action="store_true", help="Use IP geolocation for weather")
    p.add_argument(
        "--fake-image",
        default="left",
        help=(
            "Starting aim waypoint or alias "
            f"({', '.join(AIM_WAYPOINT_ORDER)}; aliases: dry_left, dry_right, dry_center)"
        ),
    )
    p.add_argument(
        "--aim-sequence",
        metavar="KEYS",
        help=(
            "Comma-separated waypoint keys for ping-pong "
            "(default: all 6 sectors left→right). "
            "Example: left,center_near,right,center_near"
        ),
    )
    p.add_argument(
        "--vision-interval",
        type=float,
        default=3.0,
        help="Seconds between fake ML frames / aim updates (default 3; 0=static)",
    )
    p.add_argument(
        "--vision-mode",
        choices=("pingpong", "cycle"),
        default="pingpong",
        help="pingpong=oscillate back/forth through frames (default); cycle=loop one way",
    )
    p.add_argument(
        "--static-vision",
        action="store_true",
        help="Do not cycle fake frames; keep --fake-image angles only",
    )
    p.add_argument("--motor", type=int, default=120, help="Motor speed 0-180 when watering")
    p.add_argument(
        "--mode",
        choices=("threshold", "merge"),
        default="threshold",
        help="threshold=live soil limits (default); merge=weather+ML rules",
    )
    p.add_argument(
        "--dry-below",
        type=float,
        default=50.0,
        help="Threshold mode: start watering when soil reading <= this (default 50)",
    )
    p.add_argument(
        "--wet-above",
        type=float,
        default=60.0,
        help="Threshold mode: stop watering when soil reading >= this (default 60)",
    )
    p.add_argument(
        "--wait-soil",
        type=float,
        default=15.0,
        help="Seconds to wait for first firewater:/soil_hum: before using defaults",
    )
    p.add_argument("--poll", type=float, default=0.25, help="Max wait between checks (seconds)")
    p.add_argument("--no-ml", action="store_true", help="Soil rules only, no ML models")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print weather/vision and decisions; do not send pump/motor (still reads soil if --port)",
    )
    p.add_argument(
        "--status-every",
        type=float,
        default=5.0,
        help="Print status line every N seconds even if decision unchanged",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_ports:
        print("Serial ports:")
        for port in list_serial_ports():
            print(f"  {port}")
        return 0

    if not args.city and not args.lat and not args.auto_location:
        args.city = "San Jose"

    place, _forecast = print_weather_block(
        city=args.city,
        lat=args.lat,
        lon=args.lon,
        auto=args.auto_location,
    )
    # Use resolved coordinates for irrigation merge (important for --auto-location).
    decision_city = args.city if not args.auto_location and args.lat is None else None
    decision_lat = args.lat if args.lat is not None else place.latitude
    decision_lon = args.lon if args.lon is not None else place.longitude
    location_name = place.name
    yaw, pitch = print_fake_vision(args.fake_image)
    live_vision = not args.static_vision and args.vision_interval > 0
    start_key = resolve_waypoint_key(args.fake_image)
    frame_keys = parse_aim_sequence(args.aim_sequence)
    try:
        vision_start_idx = frame_keys.index(start_key)
    except ValueError:
        raise SystemExit(
            f"--fake-image {args.fake_image!r} ({start_key}) must appear in --aim-sequence"
        ) from None
    thresh = ThresholdConfig(dry_below=args.dry_below, wet_above=args.wet_above)
    thresh.validate()

    print("=" * 70)
    print("  LIVE SUPERVISOR LOOP")
    print("=" * 70)
    print(f"  Weather city:   {location_name}")
    print(f"  Start aim:      {start_key} → Y={yaw} P={pitch}")
    print("  Aim waypoints:  " + format_waypoint_table(frame_keys))
    if live_vision:
        seq = " → ".join(frame_keys[vision_start_idx:] + frame_keys[:vision_start_idx])
        if args.vision_mode == "pingpong":
            rev = " ← ".join(reversed(frame_keys))
            print(f"  Vision sweep:   every {args.vision_interval:.1f}s — ping-pong")
            print(f"                  {seq} ← … → {rev.split(' ← ')[0]}")
        else:
            print(f"  Vision sweep:   every {args.vision_interval:.1f}s — {seq} (loop)")
        print("                  yaw/pitch sent only while pump is ON")
    else:
        print("  Vision cycle:   static (single frame)")
    print(f"  Decision mode:  {args.mode}")
    if args.mode == "threshold":
        print(f"  Soil thresholds (hysteresis):")
        print(f"    WATER when reading <= {thresh.dry_below:.1f}%  (dry hand / dry probe)")
        print(f"    STOP  when reading >= {thresh.wet_above:.1f}%  (wet hand / wet probe)")
        print(f"    Between limits: keep current pump state (no flapping)")
    print(f"  React speed:    immediate on each new soil serial line")
    if args.port:
        print(f"  Gateway port:   {args.port}")
        print("  Soil thread:    reading firewater: / soil_hum: from gateway")
    else:
        print("  Gateway port:   (none — add --port for live probe demo)")
    if args.dry_run:
        print("  Mode:           DRY-RUN (no pump/motor commands)")
    elif args.port:
        print("  Mode:           ACTUATE pump/motor on threshold crossings")
    print("  Ctrl+C to exit")
    print("=" * 70)
    print()

    state = SupervisorState()
    state.vision = init_vision_snapshot(args.fake_image)
    ser = None
    reader: threading.Thread | None = None
    vision_thread: threading.Thread | None = None

    if args.mode == "threshold" and not args.port:
        print("NOTE: threshold mode needs --port for live probe readings.")
        print("      Without --port you only see the default soil value (50%).")
        print()

    if live_vision:
        vision_thread = threading.Thread(
            target=vision_simulator_thread,
            args=(state, frame_keys),
            kwargs={
                "start_index": vision_start_idx,
                "interval_s": args.vision_interval,
                "mode": args.vision_mode,
            },
            name="vision-sim",
            daemon=True,
        )
        vision_thread.start()
        print(f"[main] vision simulator started — new frame every {args.vision_interval:.1f}s")
        print()

    if args.port:
        print(f"Opening {args.port} …")
        ser = open_hp_tk_serial(args.port, baud=args.baud)
        time.sleep(0.5)
        reader = threading.Thread(
            target=soil_reader_thread,
            args=(ser, state),
            name="soil-reader",
            daemon=True,
        )
        reader.start()
        print("[main] soil reader thread started — waiting for firewater:/soil_hum: …")
        print()

    last_status = 0.0
    actuate = ser is not None and not args.dry_run
    loop_start = time.time()
    last_processed_vision_count = state.vision.frame_count

    def soil_snapshot() -> SoilSnapshot:
        with state.soil_lock:
            return SoilSnapshot(
                csv_line=state.soil.csv_line,
                humidity=state.soil.humidity,
                temp_c=state.soil.temp_c,
                ph=state.soil.ph,
                raw_line=state.soil.raw_line,
                updated_at=state.soil.updated_at,
                line_count=state.soil.line_count,
            )

    def vision_snapshot() -> VisionSnapshot:
        with state.vision_lock:
            return VisionSnapshot(
                frame_key=state.vision.frame_key,
                yaw=state.vision.yaw,
                pitch=state.vision.pitch,
                updated_at=state.vision.updated_at,
                frame_count=state.vision.frame_count,
            )

    def decide(snap: SoilSnapshot) -> dict:
        if args.mode == "merge":
            return evaluate_decision(
                city=decision_city,
                lat=decision_lat,
                lon=decision_lon,
                soil_csv=snap.csv_line,
                use_ml=not args.no_ml,
            )
        on, source, reason = evaluate_threshold(
            snap.humidity,
            thresh,
            currently_on=state.last_sprinkler_on,
        )
        return threshold_payload(on, source, reason)

    try:
        while True:
            state.wakeup.wait(timeout=max(0.05, args.poll))
            state.wakeup.clear()

            snap = soil_snapshot()
            vision = vision_snapshot()
            vision_updated = vision.frame_count > last_processed_vision_count
            if vision_updated:
                last_processed_vision_count = vision.frame_count
                sent = False
                if state.watering and actuate and ser is not None:
                    sent = set_aim(ser, yaw=vision.yaw, pitch=vision.pitch, state=state)
                announce_vision_aim(
                    vision=vision,
                    actuate=actuate,
                    watering=state.watering,
                    sent=sent,
                )

            if args.port and snap.line_count == 0:
                if time.time() - loop_start > args.wait_soil:
                    print("[main] no soil serial yet — is soil node + gateway powered?")
                    loop_start = time.time()
                if not vision_updated:
                    continue

            payload = decide(snap)
            now_on = bool(payload.get("sprinkler_on"))

            if state.last_sprinkler_on is None or now_on != state.last_sprinkler_on:
                announce_transition(
                    now_on=now_on,
                    payload=payload,
                    snap=snap,
                    yaw=vision.yaw,
                    pitch=vision.pitch,
                    actuate=actuate,
                )
                if actuate and ser is not None:
                    set_watering(
                        ser,
                        on=now_on,
                        yaw=vision.yaw,
                        pitch=vision.pitch,
                        motor=args.motor,
                        state=state,
                    )
                else:
                    state.watering = now_on
                state.last_sprinkler_on = now_on
                state.last_skip_reason = payload.get("skip_reason")

            now = time.time()
            if now - last_status >= args.status_every:
                print_status_line(
                    snap=snap,
                    payload=payload,
                    watering=state.watering,
                    vision=vision,
                    cfg=thresh if args.mode == "threshold" else None,
                )
                last_status = now

    except KeyboardInterrupt:
        print("\n[main] stopping …")
    finally:
        state.stop_event.set()
        if reader is not None:
            reader.join(timeout=1.0)
        if vision_thread is not None:
            vision_thread.join(timeout=1.0)
        final_vision = vision_snapshot()
        if ser is not None and actuate:
            try:
                set_watering(
                    ser,
                    on=False,
                    yaw=final_vision.yaw,
                    pitch=final_vision.pitch,
                    motor=0,
                    state=state,
                )
            except Exception:
                pass
            ser.close()
        print("[main] exit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
