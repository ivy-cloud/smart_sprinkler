#!/usr/bin/env python3
"""
VOFA gateway supervisor demo: weather + (fake) ML aim → pump/motor commands.

Laptop sits between VOFA/gateway USB serial and the decision layer:

  soil/firewater ──► decide (demo or live weather) ──► yaw/pitch/pump/motor

Demo scenarios hard-code weather + ML image results so you can bench-test
without Open-Meteo or a camera. Later swap fakes for live forecast / vision.

Examples:
  python3 scripts/vofa_supervisor_demo.py --list-ports
  python3 scripts/vofa_supervisor_demo.py --scenario water --dry-run
  python3 scripts/vofa_supervisor_demo.py --test-plan
  python3 scripts/vofa_supervisor_demo.py --run-all-dry
  python3 scripts/vofa_supervisor_demo.py --scenario water_critical --port /dev/cu.usbmodemXXX \\
      --spray-seconds 3
  python3 scripts/vofa_supervisor_demo.py --scenario live --city "San Jose" \\
      --csv "0,0,0,28,22.5,35" --dry-run

Close VOFA+ / Serial Monitor before opening --port (only one client per USB).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass

import _bootstrap  # noqa: F401

from hp_tk_serial import list_serial_ports, open_hp_tk_serial

from services.irrigation import get_final_decision_api


# firewater:temp,humidity,pH,servo_x,servo_y,motor_speed,pump
FIREWATER_PREFIX = "firewater:"


@dataclass
class SupervisorPlan:
    """Actuation plan after weather (+ fake ML) merge."""

    sprinkler_on: bool
    duration_minutes: float
    skip_reason: str | None
    decision_source: str
    yaw: int
    pitch: int
    motor_speed: int
    soil_csv: str
    notes: list[str]
    weather_label: str
    ml_label: str
    scenario: str = ""


SCENARIOS = {
    "water": {
        "help": "Clear weather + dry soil + fake ML aim → irrigate",
        "category": "WATER ON",
        "soil_csv": "0,0,0,28,24.0,32",
        "weather_ok": True,
        "weather_label": "FAKE clear / no rain skip",
        "ml_needs_water": True,
        "ml_label": "FAKE vision: dry patch detected",
        "yaw": 100,
        "pitch": 75,
        "duration_minutes": 8.0,
        "motor_speed": 120,
        "expect_summary": "Pump ON ~lab seconds; Y=100 P=75; motor=120",
    },
    "water_critical": {
        "help": "Very dry soil + clear weather → irrigate (stronger / longer plan)",
        "category": "WATER ON",
        "soil_csv": "0,0,0,15,26.0,18",
        "weather_ok": True,
        "weather_label": "FAKE clear / no rain skip",
        "ml_needs_water": True,
        "ml_label": "FAKE vision: critical dry zone center-left",
        "yaw": 95,
        "pitch": 80,
        "duration_minutes": 12.0,
        "motor_speed": 150,
        "expect_summary": "Pump ON; critical dry band; higher motor speed",
    },
    "water_moderate": {
        "help": "Moderate dry soil + clear weather → still irrigate",
        "category": "WATER ON",
        "soil_csv": "0,0,0,42,23.5,45",
        "weather_ok": True,
        "weather_label": "FAKE clear / no rain skip",
        "ml_needs_water": True,
        "ml_label": "FAKE vision: slightly dry lawn sector",
        "yaw": 110,
        "pitch": 70,
        "duration_minutes": 6.0,
        "motor_speed": 100,
        "expect_summary": "Pump ON; moderate moisture; shorter plan duration",
    },
    "rain_skip": {
        "help": "Hard rain skip → no irrigate (even if soil looks dry)",
        "category": "SKIP",
        "soil_csv": "0,0,0,25,23.0,30",
        "weather_ok": False,
        "weather_label": "FAKE rain skip (hard)",
        "ml_needs_water": True,
        "ml_label": "FAKE vision: would aim, but weather vetoes",
        "yaw": 90,
        "pitch": 90,
        "duration_minutes": 0.0,
        "motor_speed": 0,
        "skip_reason": "Demo rain skip: forecast rain — do not water.",
        "expect_summary": "Pump OFF; weather veto even though soil is dry",
    },
    "wet_soil": {
        "help": "Clear weather but soil already wet → no irrigate",
        "category": "SKIP",
        "soil_csv": "0,0,0,78,22.0,75",
        "weather_ok": True,
        "weather_label": "FAKE clear",
        "ml_needs_water": False,
        "ml_label": "FAKE vision: lawn looks wet / no dry patch",
        "yaw": 90,
        "pitch": 90,
        "duration_minutes": 0.0,
        "motor_speed": 0,
        "skip_reason": "Demo soil skip: moisture adequate.",
        "expect_summary": "Pump OFF; soil/ML says no need",
    },
}

# Extra live cases for --run-all-dry (not separate --scenario names)
LIVE_TEST_CASES = (
    {
        "name": "live_dry",
        "category": "WATER ON (live)",
        "soil_csv": "0,0,0,28,22.5,35",
        "expect_summary": "Real Open-Meteo + dry CSV → usually ON",
    },
    {
        "name": "live_wet",
        "category": "SKIP (live)",
        "soil_csv": "0,0,0,78,22.0,75",
        "expect_summary": "Real Open-Meteo + wet CSV → usually OFF",
    },
)


def print_test_plan_table() -> None:
    """Print bench experiment matrix (water ON + skip cases)."""
    print()
    print("=" * 78)
    print("  VOFA SUPERVISOR — BENCH TEST PLAN")
    print("=" * 78)
    print()
    print("  Phase A — dry-run (no hardware, verify inputs + decision + expected commands)")
    print("  Phase B — actuate ON cases on gateway USB (close VOFA+ first)")
    print("  Phase C — actuate SKIP cases (confirm pump stays OFF)")
    print()
    row = "#  {:<18} {:<14} {:<6}  {}"
    print(row.format("Scenario", "Category", "ON?", "What to verify"))
    print("  " + "-" * 74)
    n = 1
    for name, cfg in SCENARIOS.items():
        on = "YES" if cfg.get("weather_ok") and cfg.get("ml_needs_water") and not cfg.get("skip_reason") else "NO"
        if name.startswith("water"):
            on = "YES"
        print(f"  {n:<2} {name:<18} {cfg.get('category','?'):<14} {on:<6}  {cfg.get('expect_summary','')}")
        n += 1
    for case in LIVE_TEST_CASES:
        print(f"  {n:<2} {case['name']:<18} {case['category']:<14} {'?':<6}  {case['expect_summary']}")
        n += 1
    print()
    print("  Commands:")
    print("    python3 scripts/vofa_supervisor_demo.py --run-all-dry")
    print("    python3 scripts/vofa_supervisor_demo.py --scenario water --dry-run")
    print("    python3 scripts/vofa_supervisor_demo.py --scenario water_critical --port PORT --spray-seconds 3")
    print("    python3 scripts/vofa_supervisor_demo.py --scenario rain_skip --port PORT")
    print()
    print("  WATER ON cases (expect pump+motor active briefly):")
    for name, cfg in SCENARIOS.items():
        if cfg.get("category") == "WATER ON":
            print(f"    • {name}")
    print("  SKIP cases (expect pump OFF, motor 0):")
    for name, cfg in SCENARIOS.items():
        if cfg.get("category") == "SKIP":
            print(f"    • {name}")
    print("=" * 78)
    print()


def build_plan(
    scenario: str,
    *,
    soil_csv: str | None,
    city: str,
    use_ml: bool,
    yaw: int,
    pitch: int,
    motor: int,
) -> SupervisorPlan:
    if scenario == "live":
        csv_line = soil_csv or "0,0,0,28,22.5,35"
        plan = plan_from_live(
            city=city,
            soil_csv=csv_line,
            use_ml=use_ml,
            yaw=yaw,
            pitch=pitch,
            motor_speed=motor,
        )
    else:
        plan = plan_from_scenario(scenario, soil_csv_override=soil_csv)
        if yaw != 100:
            plan.yaw = yaw
        if pitch != 75:
            plan.pitch = pitch
        if motor != 120 and plan.sprinkler_on:
            plan.motor_speed = motor
    return plan


def run_all_dry(args: argparse.Namespace) -> int:
    """Run every test case dry-run and print a results summary."""
    lab_s = max(0.5, min(15.0, float(args.spray_seconds)))
    print_test_plan_table()
    print("--- RUN ALL (dry-run) ---")
    print()
    results: list[tuple[str, str, bool, str, int, int, int]] = []

    for name in SCENARIOS:
        plan = build_plan(
            name,
            soil_csv=None,
            city=args.city,
            use_ml=not args.no_ml,
            yaw=args.yaw,
            pitch=args.pitch,
            motor=args.motor,
        )
        on = "ON " if plan.sprinkler_on else "OFF"
        results.append((name, SCENARIOS[name]["category"], plan.sprinkler_on, on, plan.yaw, plan.pitch, plan.motor_speed))
        print(f"[{on.strip()}] {name}: Y={plan.yaw} P={plan.pitch} motor={plan.motor_speed}  "
              f"({plan.decision_source})")
        if plan.skip_reason:
            print(f"       skip: {plan.skip_reason}")

    for case in LIVE_TEST_CASES:
        plan = build_plan(
            "live",
            soil_csv=case["soil_csv"],
            city=args.city,
            use_ml=not args.no_ml,
            yaw=args.yaw,
            pitch=args.pitch,
            motor=args.motor,
        )
        on = "ON " if plan.sprinkler_on else "OFF"
        results.append((case["name"], case["category"], plan.sprinkler_on, on, plan.yaw, plan.pitch, plan.motor_speed))
        print(f"[{on.strip()}] {case['name']} csv={case['soil_csv']}: "
              f"decision={plan.sprinkler_on} Y={plan.yaw} P={plan.pitch}")
        if plan.skip_reason:
            print(f"       skip: {plan.skip_reason}")

    print()
    print("--- SUMMARY ---")
    print(f"  {'Case':<20} {'Category':<16} {'ON?':<5} {'Y/P':<10} {'Motor'}")
    print("  " + "-" * 60)
    for name, cat, on_bool, on_s, y, p, m in results:
        print(f"  {name:<20} {cat:<16} {on_s:<5} {y}/{p:<7} {m}")
    water_on = sum(1 for r in results if r[2])
    skip_n = len(results) - water_on
    print()
    print(f"  Total: {len(results)} cases — {water_on} WATER ON, {skip_n} SKIP/OFF")
    print()
    print("  Next — hardware WATER ON (close VOFA+, replace PORT):")
    for name, cat, on_bool, *_ in results:
        if not on_bool:
            continue
        if name.startswith("live"):
            case = next(c for c in LIVE_TEST_CASES if c["name"] == name)
            print(
                f"    python3 scripts/vofa_supervisor_demo.py --scenario live "
                f'--csv "{case["soil_csv"]}" --city "{args.city}" '
                f"--port PORT --spray-seconds {lab_s:.0f}"
            )
        else:
            print(
                f"    python3 scripts/vofa_supervisor_demo.py --scenario {name} "
                f"--port PORT --spray-seconds {lab_s:.0f}"
            )
    print()
    print("  Next — hardware SKIP (pump should stay OFF):")
    for name, cat, on_bool, *_ in results:
        if on_bool:
            continue
        if name.startswith("live"):
            case = next(c for c in LIVE_TEST_CASES if c["name"] == name)
            print(
                f"    python3 scripts/vofa_supervisor_demo.py --scenario live "
                f'--csv "{case["soil_csv"]}" --city "{args.city}" --port PORT'
            )
        else:
            print(f"    python3 scripts/vofa_supervisor_demo.py --scenario {name} --port PORT")
    print()
    return 0


def parse_soil_csv(csv_line: str) -> dict[str, float | None]:
    """Return labeled fields from heli-style 6-field CSV."""
    parts = [p.strip() for p in csv_line.split(",")]
    if len(parts) != 6:
        return {}
    out: dict[str, float | None] = {}
    labels = (
        "voltage",
        "current",
        "flow_l_min",
        "water_level_pct",
        "soil_temp_c",
        "humidity_pct",
    )
    for label, raw in zip(labels, parts, strict=True):
        try:
            out[label] = float(raw)
        except ValueError:
            out[label] = None
    return out


def expected_serial_commands(plan: SupervisorPlan, *, lab_seconds: float) -> list[str]:
    """Human-readable list of VOFA/gateway commands the supervisor will send."""
    cmds = ["C:0  (manual mode — supervisor owns pump/motor, not gateway auto B)"]
    cmds.append(f"yaw:{plan.yaw}")
    cmds.append(f"pitch:{plan.pitch}")
    if plan.sprinkler_on:
        cmds.append(f"motor:{plan.motor_speed}")
        cmds.append("pump:1")
        cmds.append(f"  … wait {lab_seconds:.1f} s (lab spray window)")
        cmds.append("pump:0")
        cmds.append("motor:0")
    else:
        cmds.append("motor:0")
        cmds.append("pump:0")
    return cmds


def expected_hardware(plan: SupervisorPlan, *, lab_seconds: float) -> list[str]:
    """What you should observe on the bench."""
    if plan.sprinkler_on:
        return [
            f"VOFA Y → {plan.yaw}, P → {plan.pitch} (nozzle aim)",
            f"Motor runs at ~{plan.motor_speed} for {lab_seconds:.1f} s",
            "Pump ON during that window, then OFF",
            f"Production plan would run ~{plan.duration_minutes:.0f} min; demo is shortened for safety",
        ]
    return [
        f"VOFA Y → {plan.yaw}, P → {plan.pitch} (aim only, or idle pose)",
        "Motor stays 0, pump stays OFF",
        f"Reason: {plan.skip_reason or 'decision OFF'}",
    ]


def print_run_header(args: argparse.Namespace) -> None:
    print()
    print("=" * 70)
    print("  VOFA SUPERVISOR DEMO")
    print("=" * 70)
    print(f"  Scenario:       {args.scenario}")
    if args.scenario in SCENARIOS:
        print(f"  About:          {SCENARIOS[args.scenario]['help']}")
    elif args.scenario == "live":
        print("  About:          Real Open-Meteo weather + soil rules/ML merge")
    print(f"  Mode:           {'DRY-RUN (no USB)' if args.dry_run else 'ACTUATE' if args.port else 'PLAN ONLY'}")
    if args.port:
        print(f"  Gateway port:   {args.port} @ {args.baud}")
    if args.read_firewater:
        print(f"  Soil source:    latest firewater: from gateway (wait {args.firewater_wait}s)")
    elif args.csv:
        print(f"  Soil source:    --csv override")
    else:
        print(f"  Soil source:    scenario default or live default")
    if args.scenario == "live":
        print(f"  Weather city:   {args.city}")
        print(f"  ML enabled:     {not args.no_ml}")
    print("=" * 70)
    print()


def print_inputs(
    plan: SupervisorPlan,
    *,
    args: argparse.Namespace,
    firewater: dict[str, float] | None = None,
) -> None:
    print("--- INPUT DATA ---")
    if firewater is not None:
        print("  From gateway firewater: line:")
        print(f"    temp={firewater['temp_c']:.1f}°C  humidity={firewater['humidity']:.1f}%  pH={firewater['ph']:.1f}")
        print(f"    servo_x={firewater['servo_x']:.0f}  servo_y={firewater['servo_y']:.0f}  "
              f"motor={firewater['motor_speed']:.0f}  pump={firewater['pump']:.0f}")
    print(f"  Weather input:  {plan.weather_label}")
    print(f"  ML / vision:    {plan.ml_label}")
    print(f"  Soil CSV:       {plan.soil_csv}")
    print("    (format: voltage,current,flow,water_level_pct,soil_temp_c,humidity_pct)")
    fields = parse_soil_csv(plan.soil_csv)
    if fields:
        wl = fields.get("water_level_pct")
        hum = fields.get("humidity_pct")
        temp = fields.get("soil_temp_c")
        wl_s = f"{wl:.1f}%" if wl is not None else "—"
        hum_s = f"{hum:.1f}%" if hum is not None else "—"
        temp_s = f"{temp:.1f}°C" if temp is not None else "—"
        print(f"    → water_level={wl_s}  humidity={hum_s}  soil_temp={temp_s}")
        if wl is not None:
            if wl < 35:
                band = "dry (likely needs water in demo scenarios)"
            elif wl > 65:
                band = "wet (likely skip in demo scenarios)"
            else:
                band = "moderate"
            print(f"    → moisture band (approx): {band}")
    if args.scenario != "live":
        cfg = SCENARIOS.get(args.scenario, {})
        print("  Demo scenario flags:")
        print(f"    weather_ok={cfg.get('weather_ok')}  ml_needs_water={cfg.get('ml_needs_water')}")
    print()


def print_decision(plan: SupervisorPlan, *, lab_seconds: float, dry_run: bool) -> None:
    print("--- DECISION ---")
    print(f"  Sprinkler ON:     {plan.sprinkler_on}")
    print(f"  Duration (plan):  {plan.duration_minutes:.1f} min  (production label)")
    print(f"  Lab spray time:   {lab_seconds:.1f} s  (bench safety cap)")
    print(f"  Decision source:  {plan.decision_source}")
    if plan.skip_reason:
        print(f"  Skip reason:      {plan.skip_reason}")
    print(f"  Aim Y / P:        {plan.yaw} / {plan.pitch}")
    print(f"  Motor speed:      {plan.motor_speed}")
    if plan.notes:
        print("  Notes:")
        for note in plan.notes:
            print(f"    - {note}")
    print()


def print_expected(plan: SupervisorPlan, *, lab_seconds: float, dry_run: bool) -> None:
    print("--- EXPECTED (if hardware connected) ---")
    for line in expected_hardware(plan, lab_seconds=lab_seconds):
        print(f"  • {line}")
    print()
    print("  Serial commands to gateway:")
    for cmd in expected_serial_commands(plan, lab_seconds=lab_seconds):
        print(f"    {cmd}")
    if dry_run:
        print()
        print("  (dry-run — commands above are NOT sent)")
    print()
    print("=" * 70)
    print()


def print_plan(plan: SupervisorPlan, *, lab_seconds: float, dry_run: bool, args: argparse.Namespace | None = None, firewater: dict[str, float] | None = None) -> None:
    """Full report: inputs, decision, and expected behavior."""
    if args is not None:
        print_run_header(args)
        print_inputs(plan, args=args, firewater=firewater)
    print_decision(plan, lab_seconds=lab_seconds, dry_run=dry_run)
    print_expected(plan, lab_seconds=lab_seconds, dry_run=dry_run)


def parse_firewater(line: str) -> dict[str, float] | None:
    raw = line.strip()
    if not raw.lower().startswith(FIREWATER_PREFIX):
        return None
    body = raw[len(FIREWATER_PREFIX) :].strip()
    parts = [p.strip() for p in body.split(",")]
    if len(parts) < 3:
        return None
    try:
        values = [float(p) for p in parts[:7]]
    except ValueError:
        return None
    while len(values) < 7:
        values.append(0.0)
    temp, hum, ph, servo_x, servo_y, motor, pump = values[:7]
    return {
        "temp_c": temp,
        "humidity": hum,
        "ph": ph,
        "servo_x": servo_x,
        "servo_y": servo_y,
        "motor_speed": motor,
        "pump": pump,
    }


def firewater_to_soil_csv(fw: dict[str, float]) -> str:
    """Map gateway firewater humidity/temp into heli-style 6-field CSV."""
    hum = float(fw.get("humidity") or 0.0)
    temp = float(fw.get("temp_c") or 0.0)
    # water_level and humidity both from soil humidity for rules merge
    return f"0,0,0,{hum:.1f},{temp:.1f},{hum:.1f}"


def read_latest_firewater(ser, *, wait_s: float = 3.0) -> dict[str, float] | None:
    deadline = time.time() + wait_s
    latest = None
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        try:
            line = raw.decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
        parsed = parse_firewater(line)
        if parsed is not None:
            latest = parsed
    return latest


def send_vofa(ser, key: str, value: int) -> None:
    line = f"{key}:{int(value)}\n"
    ser.write(line.encode("ascii", errors="ignore"))
    ser.flush()
    print(f"  >> {line.strip()}")


def send_aim(ser, yaw: int, pitch: int) -> None:
    """Send nozzle aim. Hardware axes are swapped vs VOFA names (yaw↔pitch)."""
    send_vofa(ser, "yaw", pitch)
    send_vofa(ser, "pitch", yaw)


def plan_from_scenario(name: str, *, soil_csv_override: str | None = None) -> SupervisorPlan:
    if name not in SCENARIOS:
        raise SystemExit(f"Unknown scenario {name!r}. Choose: {', '.join(SCENARIOS)}")
    cfg = SCENARIOS[name]
    soil_csv = soil_csv_override or cfg["soil_csv"]
    notes = [cfg["help"], cfg["weather_label"], cfg["ml_label"]]

    if not cfg["weather_ok"]:
        return SupervisorPlan(
            sprinkler_on=False,
            duration_minutes=0.0,
            skip_reason=cfg.get("skip_reason"),
            decision_source="demo_weather_rain_skip",
            yaw=int(cfg["yaw"]),
            pitch=int(cfg["pitch"]),
            motor_speed=0,
            soil_csv=soil_csv,
            notes=notes,
            weather_label=cfg["weather_label"],
            ml_label=cfg["ml_label"],
            scenario=name,
        )

    if not cfg["ml_needs_water"] or cfg.get("skip_reason"):
        return SupervisorPlan(
            sprinkler_on=False,
            duration_minutes=0.0,
            skip_reason=cfg.get("skip_reason") or "Demo: ML/soil says no water.",
            decision_source="demo_soil_or_ml_skip",
            yaw=int(cfg["yaw"]),
            pitch=int(cfg["pitch"]),
            motor_speed=0,
            soil_csv=soil_csv,
            notes=notes,
            weather_label=cfg["weather_label"],
            ml_label=cfg["ml_label"],
            scenario=name,
        )

    return SupervisorPlan(
        sprinkler_on=True,
        duration_minutes=float(cfg["duration_minutes"]),
        skip_reason=None,
        decision_source="demo_merged_water",
        yaw=int(cfg["yaw"]),
        pitch=int(cfg["pitch"]),
        motor_speed=int(cfg["motor_speed"]),
        soil_csv=soil_csv,
        notes=notes,
        weather_label=cfg["weather_label"],
        ml_label=cfg["ml_label"],
        scenario=name,
    )


def plan_from_live(
    *,
    city: str,
    soil_csv: str,
    use_ml: bool,
    yaw: int,
    pitch: int,
    motor_speed: int,
) -> SupervisorPlan:
    payload = get_final_decision_api(
        city=city,
        sensor=soil_csv,
        use_ml=use_ml,
    )
    on = bool(payload.get("sprinkler_on"))
    mins = float(payload.get("duration_minutes") or 0)
    notes = []
    if payload.get("decision_source"):
        notes.append(f"live decision_source={payload['decision_source']}")
    for n in (payload.get("notes") or [])[:4]:
        notes.append(str(n))
    return SupervisorPlan(
        sprinkler_on=on,
        duration_minutes=mins,
        skip_reason=payload.get("skip_reason"),
        decision_source=str(payload.get("decision_source") or "live"),
        yaw=yaw if on else 90,
        pitch=pitch if on else 90,
        motor_speed=motor_speed if on else 0,
        soil_csv=soil_csv,
        notes=notes,
        weather_label=f"LIVE Open-Meteo ({city})",
        ml_label="LIVE soil ML" if use_ml else "rules only (--no-ml)",
        scenario="live",
    )


def run_actuation(ser, plan: SupervisorPlan, *, lab_seconds: float) -> None:
    print("--- ACTUATION (sending to gateway) ---")
    # Always leave auto mode off so supervisor owns pump/motor.
    print("  Step 1: disable gateway auto-watering (C:0)")
    send_vofa(ser, "C", 0)

    if not plan.sprinkler_on:
        print("  Step 2: SKIP path — aim only, pump/motor OFF")
        send_aim(ser, plan.yaw, plan.pitch)
        send_vofa(ser, "motor", 0)
        send_vofa(ser, "pump", 0)
        print("  Done — no watering (decision OFF)")
        return

    print(f"  Step 2: aim nozzle Y={plan.yaw} P={plan.pitch}")
    send_aim(ser, plan.yaw, plan.pitch)
    time.sleep(0.4)
    print(f"  Step 3: start motor={plan.motor_speed} pump=1 for {lab_seconds:.1f} s")
    send_vofa(ser, "motor", plan.motor_speed)
    send_vofa(ser, "pump", 1)
    time.sleep(lab_seconds)
    print("  Step 4: stop pump and motor")
    send_vofa(ser, "pump", 0)
    send_vofa(ser, "motor", 0)
    print("  Done — watering cycle complete")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="VOFA gateway supervisor demo (weather + fake ML → actuate)")
    p.add_argument("--list-ports", action="store_true", help="List USB serial ports and exit")
    p.add_argument(
        "--test-plan",
        action="store_true",
        help="Print full bench test matrix (WATER ON + SKIP cases) and exit",
    )
    p.add_argument(
        "--run-all-dry",
        action="store_true",
        help="Dry-run all demo scenarios + live dry/wet; print summary table",
    )
    p.add_argument(
        "--scenario",
        choices=[*SCENARIOS.keys(), "live"],
        default="water",
        help="Demo plan (default: water). Use 'live' for real Open-Meteo + soil CSV/rules",
    )
    p.add_argument("--port", help="Gateway USB serial port (VOFA must be closed)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--dry-run", action="store_true", help="Print plan only; do not open serial")
    p.add_argument("--json", action="store_true", help="Also print plan as JSON")
    p.add_argument(
        "--spray-seconds",
        type=float,
        default=3.0,
        help="Lab irrigation window when ON (default 3s, safety cap)",
    )
    p.add_argument("--csv", help="Override soil CSV (6 fields). For live, required unless --read-firewater")
    p.add_argument("--city", default="San Jose", help="City for --scenario live")
    p.add_argument("--no-ml", action="store_true", help="Disable soil ML for --scenario live")
    p.add_argument("--yaw", type=int, default=100, help="Aim yaw for live ON (and overrides)")
    p.add_argument("--pitch", type=int, default=75, help="Aim pitch for live ON")
    p.add_argument("--motor", type=int, default=120, help="Motor speed 0-180 when watering")
    p.add_argument(
        "--read-firewater",
        action="store_true",
        help="Read latest firewater: line from gateway for soil humidity/temp",
    )
    p.add_argument("--firewater-wait", type=float, default=3.0, help="Seconds to wait for firewater")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_ports:
        ports = list_serial_ports()
        print("Serial ports:")
        for port in ports:
            print(f"  {port}")
        if not ports:
            print("  (none)")
        return 0

    if args.test_plan:
        print_test_plan_table()
        return 0

    if args.run_all_dry:
        return run_all_dry(args)

    lab_s = max(0.5, min(15.0, float(args.spray_seconds)))
    soil_csv = args.csv
    firewater: dict[str, float] | None = None

    ser = None
    if args.read_firewater:
        if not args.port:
            raise SystemExit("--read-firewater requires --port")
        if args.dry_run:
            raise SystemExit("--read-firewater cannot be combined with --dry-run")
        print(f"Opening {args.port} to read firewater …")
        ser = open_hp_tk_serial(args.port, baud=args.baud)
        time.sleep(0.5)
        fw = read_latest_firewater(ser, wait_s=args.firewater_wait)
        if fw is None:
            ser.close()
            raise SystemExit(
                "No firewater: line seen. Is soil node sending? "
                "Or pass --csv for a bench value."
            )
        firewater = fw
        soil_csv = firewater_to_soil_csv(fw)

    if args.scenario == "live":
        if not soil_csv:
            soil_csv = "0,0,0,28,22.5,35"
        plan = build_plan(
            "live",
            soil_csv=soil_csv,
            city=args.city,
            use_ml=not args.no_ml,
            yaw=args.yaw,
            pitch=args.pitch,
            motor=args.motor,
        )
    else:
        plan = build_plan(
            args.scenario,
            soil_csv=soil_csv,
            city=args.city,
            use_ml=not args.no_ml,
            yaw=args.yaw,
            pitch=args.pitch,
            motor=args.motor,
        )

    print_plan(plan, lab_seconds=lab_s, dry_run=args.dry_run, args=args, firewater=firewater)
    if args.json:
        print(json.dumps(plan.__dict__, indent=2))

    if args.dry_run:
        return 0

    if not args.port:
        raise SystemExit("Provide --port to actuate, or use --dry-run / --list-ports")

    if ser is None:
        print("Opening gateway serial …")
        print(f"  port={args.port}  (close VOFA+ / Serial Monitor first)")
        ser = open_hp_tk_serial(args.port, baud=args.baud)
        time.sleep(0.4)

    try:
        run_actuation(ser, plan, lab_seconds=lab_s)
    finally:
        ser.close()

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
