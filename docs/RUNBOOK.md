# Irrigation decision runbook

Exact CLI commands that produce an **irrigation decision** (sprinkler ON/OFF, duration, skip reason). Run all commands from the **repository root** (`smart_sprinkler/`).

---

## Quick pick: which command do I need?

| I have… | I want… | Command |
|---------|---------|---------|
| Nothing (just location) | Weather-only ON/OFF + minutes | `sprinkler_schedule.py --city "…"` |
| Soil sensor CSV + location | **Final** decision (recommended) | `analyze_soil.py --csv "…" --city "…"` |
| **SoilNode BLE RX** on USB + location | **Live** decision from serial | `soil_serial_ml.py --port … --city "…"` |
| Soil CSV only | Soil rules (+ ML binary if trained) | `analyze_soil.py --csv "…" --soil-only` |
| HTTP / another app | Same as final decision | `api_server.py` then `POST /v1/irrigation/decision` |
| Debug forecast only | No decision (tables) | `fetch_weather.py` (see below) |

**Production path for your hardware:** `analyze_soil.py` with `--csv` from STM32/heli and `--city` (or `--lat` / `--lon`).

---

## 0. One-time setup

```bash
cd /path/to/smart_sprinkler

# API + irrigation services (use python3 -m pip if plain pip is not found)
python3 -m pip install -r requirements.txt
```

**Python version:** 3.10+ recommended. On **3.9**, `requirements.txt` includes `eval_type_backport` for Pydantic. If the API still fails, upgrade Python (`brew install python@3.12`).

```bash
# Optional ML (binary + regression)
python3 -m pip install -r ml/requirements.txt
python3 scripts/train_ml_models.py
```

Without ML training, rule-based decisions still work; output will note missing model files.

**Live BLE path:** flash `firmware/gateway/soil_node_rx/soil_node_rx.ino` on a second ESP32-S3 and use `scripts/soil_serial_ml.py` (see **§ H** below). Same ML artifacts apply when trained.

**Location** (pick one per run):

- `--city "San Jose, CA"`
- `--lat 37.34 --lon -121.89`
- `--auto-location` (IP guess; `fetch_weather` / `sprinkler_schedule` only)
- Environment: `WEATHER_CITY`, or `WEATHER_LAT` + `WEATHER_LON`

**Sensor CSV format** (6 values, no spaces inside numbers):

```text
voltage,current,flowRate,waterLevel,soilTemp,humidity
```

Example:

```text
12.1,0.4,0.0,28,22.5,41
```

---

## 1. Final irrigation decision (weather + soil + merge + ML)

**Triggers:** rules + rain skip + soil moisture + `min(weather, soil)` minutes + optional ML.

| Field | Meaning |
|-------|---------|
| `sprinkler_on` | Run valve now? |
| `duration_minutes` | How long to run today |
| `skip_reason` | Why OFF (if any) |
| `decision_source` | e.g. `merged_min_ml`, `weather_rain_skip` |
| `days_to_next_watering` | ML regression (if trained + `--city`) |

### Command (default: ML on)

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA"
```

### Same, machine-readable JSON

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --json
```

### Coordinates instead of city

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --lat 37.3382 --lon -121.8863
```

### Rules only (no ML)

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --no-ml
```

### Tune base run length and flow

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose" \
  --base-minutes 25 \
  --flow-gpm 8
```

**Requires:** `--city` or `--lat`/`--lon` (or env vars) for weather fetch. **Requires:** `--csv` for soil.

---

## 2. Weather-only irrigation decision (no soil sensor)

**Triggers:** Open-Meteo humidity/rain rules only — no CSV, no soil merge, no soil ML.

```bash
python3 scripts/sprinkler_schedule.py --city "San Jose, CA"
```

```bash
python3 scripts/sprinkler_schedule.py --city "San Jose, CA" --json
```

```bash
python3 scripts/sprinkler_schedule.py --lat 37.34 --lon -121.89
```

```bash
python3 scripts/sprinkler_schedule.py --auto-location
```

**Output keys:** `sprinkler_on`, `duration_minutes`, `duration`, `decision` (full weather object), `rain_checks`.

**ML:** not used.

---

## 3. Soil-only decision (no weather merge)

**Triggers:** soil moisture rules on CSV; optional ML binary (+ regression if `--city` and models trained). Does **not** apply rain hard-skip or `min(weather, soil)`.

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --soil-only
```

With weather fetch for ML regression (ET₀, VPD, rain):

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --soil-only \
  --city "San Jose, CA"
```

Rules only:

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --soil-only \
  --no-ml
```

**Note:** This is **not** the full production decision if rain is forecast — use section 1 for that.

---

## 4. Weather forecast + embedded schedule (rules, no soil)

**Triggers:** hourly forecast table; with `--with-schedule`, adds rule-based `sprinkler_on` / duration (same family as `sprinkler_schedule.py`, lighter output).

Forecast only (no irrigation decision):

```bash
python3 scripts/fetch_weather.py --city "San Jose, CA"
```

Forecast + rule-based ON/OFF and duration:

```bash
python3 scripts/fetch_weather.py --city "San Jose, CA" --with-schedule
```

JSON:

```bash
python3 scripts/fetch_weather.py --city "San Jose, CA" --with-schedule --json
```

**ML:** not used.

---

## 5. HTTP API (same decisions as CLI)

Start server:

```bash
pip install -r requirements.txt
python3 scripts/api_server.py
# default http://127.0.0.1:8765
```

Interactive docs: http://127.0.0.1:8765/docs

### Final decision (equivalent to section 1)

```bash
curl -s -X POST http://127.0.0.1:8765/v1/irrigation/decision \
  -H "Content-Type: application/json" \
  -d '{
    "city": "San Jose, CA",
    "base_minutes": 20,
    "flow_gpm": 8,
    "use_ml": true,
    "sensor": {
      "csv_line": "12.1,0.4,0.0,28,22.5,41"
    }
  }' | python3 -m json.tool
```

### Weather-only decision (equivalent to section 2)

```bash
curl -s -X POST http://127.0.0.1:8765/v1/weather/decision \
  -H "Content-Type: application/json" \
  -d '{"city": "San Jose, CA", "base_minutes": 20}' | python3 -m json.tool
```

### Soil analyze (equivalent to section 3)

```bash
curl -s -X POST http://127.0.0.1:8765/v1/soil/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "city": "San Jose, CA",
    "use_ml": true,
    "sensor": {"csv_line": "12.1,0.4,0.0,28,22.5,41"}
  }' | python3 -m json.tool
```

Set `"use_ml": false` for rules-only.

---

## 6. Commands that do **not** decide irrigation

| Command | Purpose |
|---------|---------|
| `python3 scripts/train_ml_models.py` | Train weights only; no sprinkler output |
| `python3 scripts/fetch_weather.py` (without `--with-schedule`) | Forecast tables only |
| `ml/soil/binary/infer.py` | Standalone ML test; not merged with rain rules |
| `ml/soil/regression/infer.py` | Standalone days prediction; not merged |

---

## Decision matrix (ML × path)

| Command | Weather rules | Soil rules | Merge (rain min) | ML |
|---------|---------------|------------|------------------|-----|
| `analyze_soil.py` + `--city` | ✓ | ✓ | ✓ | ✓ default |
| `analyze_soil.py` + `--no-ml` | ✓ | ✓ | ✓ | ✗ |
| `analyze_soil.py` + `--soil-only` | ✗ | ✓ | ✗ | ✓ if `--city` |
| `sprinkler_schedule.py` | ✓ | ✗ | ✗ | ✗ |
| `fetch_weather.py --with-schedule` | ✓ | ✗ | ✗ | ✗ |
| `POST /v1/irrigation/decision` | ✓ | ✓ | ✓ | ✓ default |
| `soil_serial_ml.py` + `--city` | ✓ | ✓ | ✓ | ✓ default |

---

## Typical workflows

### A. Laptop receives heli_tx CSV over Bluetooth

```bash
# Paste latest line into --csv
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --json
```

### E. Decision → hp_tk angle (angle 0 = stop on your wiring)

Laptop runs Python, USB serial talks to **hp_tk_tx**; tx forwards angle over BLE to **hp_tk_rx**.

```bash
python3 -m pip install pyserial
python3 scripts/irrigation_to_hp_tk.py --list-ports

# Preview decision + angle without serial
python3 scripts/irrigation_to_hp_tk.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --dry-run

# Send 0 (OFF) or 90 (ON) to hp_tk_tx
python3 scripts/irrigation_to_hp_tk.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --port /dev/cu.usbserial-XXXX \
  --angle-on 90
```

| `sprinkler_on` | Angle sent | Effect (if GPIO2 = valve) |
|----------------|------------|-------------------------|
| `false` | **0** | Stop / park |
| `true` | `--angle-on` (default 90) | Spray at that nozzle angle |

Duration is **not** sent to firmware yet (only ON/OFF via angle 0 vs non-zero).

**Optional YOLO aim** (trained `ml/vision/segmentation/runs/segment/train/weights/best.pt`):

```bash
python3 scripts/predict_grass_angle.py path/to/frame.jpg --json

python3 scripts/irrigation_to_hp_tk.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --image path/to/frame.jpg \
  --port /dev/cu.usbserial-XXXX
```

Without `--image`, ON uses fixed `--angle-on` (default 90). Calibration: `--angle-offset`, `--angle-scale`, `--invert-x`.

### F. Timed spray experiment (0 → angle → wait → 0)

For bench tests, spray time is **decision duration converted to seconds, capped between 1 and 10 s** (not full minutes).

```bash
python3 scripts/hp_tk_spray_experiment.py --list-ports

python3 scripts/hp_tk_spray_experiment.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --dry-run

python3 scripts/hp_tk_spray_experiment.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --port /dev/cu.usbserial-XXXX \
  --angle 30 \
  --image path/to/frame.jpg \
  --min-seconds 1 \
  --max-seconds 10
```

| Phase | Angle | Wait |
|-------|-------|------|
| Start | 0 | `--settle-seconds` (default 1 s) |
| Spray | `--angle` or YOLO from `--image` | `min(10, max(1, duration_minutes×60))` or `--spray-seconds` |
| End | 0 | `--settle-seconds` |

If `sprinkler_on` is false, only **angle 0** is sent.

### G. Vision angle experiment (two images vs fallback 90°)

Compare **no image** (always fallback **90°**, no YOLO) vs two frames (grass left vs right → different angles), then send each value to **hp_tk_tx**.

Setup: copy two test frames to `examples/vision/` — see [examples/vision/README.md](../examples/vision/README.md).

```bash
# Angles only (no USB)
python3 scripts/vision_angle_experiment.py \
  --image-a examples/vision/frame_grass_left.jpg \
  --image-b examples/vision/frame_grass_right.jpg \
  --dry-run

# No images → single case, 90°
python3 scripts/vision_angle_experiment.py --dry-run

# Send 0 → 90 → angle_a → angle_b → 0 on hp_tk_tx
python3 scripts/vision_angle_experiment.py \
  --image-a examples/vision/frame_grass_left.jpg \
  --image-b examples/vision/frame_grass_right.jpg \
  --port /dev/cu.usbserial-XXXX --pause 4
```

### H. SoilNode BLE receiver → live ML decision (ESP32-S3 RX + laptop)

Field **SoilNode TX** advertises over BLE (Nordic UART notify). A second ESP32-S3 runs **`firmware/gateway/soil_node_rx/soil_node_rx.ino`**, connects to the TX, and prints English soil lines on USB. The laptop runs **`scripts/soil_serial_ml.py`** for realtime weather + soil merge + ML.

**Hardware**

| Board | Firmware | USB to laptop |
|-------|----------|---------------|
| SoilNode TX (field) | Vendor `SoilNode` | Optional (real soil temp on TX serial) |
| ESP32-S3 RX | `soil_node_rx.ino` | **Required** for live path |

**Flash RX (Arduino IDE)**

| Setting | Value |
|---------|--------|
| Sketch | `firmware/gateway/soil_node_rx/soil_node_rx.ino` |
| Board | ESP32S3 Dev Module |
| USB CDC On Boot | **Enabled** |
| Serial Monitor | 115200 (close before running Python) |

Set `TARGET_MAC` in the sketch to your TX MAC if needed (default in repo).

**TX must be powered**; its serial boot log should include `BLE advertising started.`

**Expected RX serial (after connect)**

```text
[BLE] Connected to SoilNode
[BLE] Ready — soil data will appear below
[SOIL] Humidity: 0.0%   Temperature: 0.0 C   Salinity: 24 uS/cm   Conductivity: 30 uS/cm
[CSV] 0,0,0,0.0,0.0,0.0
[EC] salinity_uS_cm=24 conductivity_uS_cm=30
```

BLE often sends **binary** frames: salinity/EC are real; **humidity** and **soil temp** may be 0 on the wire. The Python script merges `[SOIL]` + `[CSV]` + `[EC]` (does not feed the bare zero CSV alone to ML) and can fill missing temp from forecast or a second USB port.

**One-time Python deps**

```bash
python3 -m pip install pyserial
python3 -m pip install -r requirements.txt
# Optional ML weights:
python3 -m pip install -r ml/requirements.txt
python3 scripts/train_ml_models.py
```

**Live decision (recommended)**

Close **Arduino Serial Monitor** on the RX port first.

```bash
python3 scripts/soil_serial_ml.py --list-ports

python3 scripts/soil_serial_ml.py \
  --port /dev/cu.usbmodem12201 \
  --city "San Jose"
```

Replace the port with your RX `cu.usbmodem*` device from `--list-ports`.

**Optional: soil temp from TX USB** (second cable to field board)

```bash
python3 scripts/soil_serial_ml.py \
  --port /dev/cu.usbmodem12201 \
  --temp-port /dev/cu.usbmodem58FC0382281 \
  --city "San Jose"
```

When soil temp is 0 or missing and `--city` (or `--lat`/`--lon`) is set, the script uses **Open-Meteo air temperature** as a proxy (`--temp-from-weather`, default on). Disable with `--no-temp-from-weather`.

**Example output**

```text
Listening on /dev/cu.usbmodem12201 @ 115200 (Ctrl+C to stop)
Missing soil temp -> Open-Meteo air @ San Jose, California, United States
ML uses merged [SOIL]+[CSV]+[EC] (not raw zero CSV alone)

>> legacy CSV: 0.0,0.0,0.0,0.0,12.1,0.0  (temp from weather-air (12.1°C))
>> ML inputs:   humidity=0.0%  temp=12.1°C  salinity=24 μS/cm  EC=30 μS/cm
--- decision ---
  sprinkler_on:     True
  duration_minutes: 15
  ml_needs_water:   0.25
  ml_note:          ML binary: likely already watered (p=0.75).
```

| Log field | Meaning |
|-----------|---------|
| `ML inputs` | Values actually passed to ML (salinity/EC from `[EC]`, temp patched if needed) |
| `legacy CSV` | Six-field row for rule engine (voltage,current,flow,level,temp,humidity) |
| `temp from weather-air` | Soil temp missing over BLE; air temp from forecast used |

**Other flags**

```bash
# Every serial line + JSON payload
python3 scripts/soil_serial_ml.py --port /dev/cu.usbmodem12201 --city "San Jose" --verbose --json

# Soil rules + ML only (no weather merge for duration)
python3 scripts/soil_serial_ml.py --port /dev/cu.usbmodem12201 --soil-only --city "San Jose"
```

**USB sanity check:** if RX serial shows only ROM text, flash `firmware/gateway/serial_usb_test/serial_usb_test.ino` or enable **USB CDC On Boot**.

### B. No sensor yet — test weather policy

```bash
python3 scripts/sprinkler_schedule.py --city "San Jose, CA"
```

### C. Cron daily check

```bash
cd /path/to/smart_sprinkler && \
python3 scripts/analyze_soil.py --csv "$SENSOR_LINE" --city "San Jose" --json \
  >> /var/log/sprinkler_decisions.jsonl
```

### D. Rules-only fallback (no torch / no artifacts)

```bash
python3 scripts/analyze_soil.py --csv "12.1,0.4,0.0,28,22.5,41" --city "San Jose" --no-ml
```

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Provide city, lat/lon, …` | Add `--city` or `--lat`/`--lon` for any merged or weather command |
| `Expected 6 comma-separated sensor values` | Fix `--csv` format (six numbers) |
| ML notes: “no trained weights” | Run `python3 scripts/train_ml_models.py` |
| ML notes: “install torch” | `pip install -r ml/requirements.txt` |
| Network / 502 from API | Check internet; Open-Meteo must be reachable |
| `soil_serial_ml.py`: port is busy | Close Arduino Serial Monitor; `lsof /dev/cu.usbmodem*` |
| RX serial: no `[SOIL]` after Ready | TX powered? BLE advertising? Check MAC in sketch |
| ML inputs: humidity=0 | BLE binary path often lacks moisture; rules may differ from ML |
| ML inputs: temp from weather-air | Expected when BLE temp is 0; use `--temp-port` for TX USB soil temp |

---

## I. VOFA supervisor demo (weather + fake ML → gateway)

Laptop script decides irrigate yes/no, then sends VOFA-style commands to **`gateway_ctrl`** USB (`yaw:` / `pitch:` / `motor:` / `pump:`). Demo scenarios hard-code weather + fake vision so you can bench-test without Open-Meteo or a camera.

**Axis note:** supervisor `send_aim()` swaps yaw↔pitch on the wire (`yaw:` gets pitch value and `pitch:` gets yaw) to match the gimbal wiring. Logs still show logical Y=pan / P=tilt.

### Test plan (WATER ON + SKIP)

| # | Scenario | Category | Expect |
|---|----------|----------|--------|
| 1 | `water` | **WATER ON** | Pump ON ~3s; Y=100 P=75; motor=120 |
| 2 | `water_critical` | **WATER ON** | Very dry soil; higher motor; Y=95 P=80 |
| 3 | `water_moderate` | **WATER ON** | Moderate dry; shorter plan; Y=110 P=70 |
| 4 | `rain_skip` | SKIP | Pump OFF — weather rain veto |
| 5 | `wet_soil` | SKIP | Pump OFF — soil/ML says wet enough |
| 6 | `live` + dry CSV | **WATER ON** (usually) | Real Open-Meteo + `0,0,0,28,22.5,35` |
| 7 | `live` + wet CSV | SKIP (usually) | Real Open-Meteo + `0,0,0,78,22.0,75` |

**Phase A — dry-run all cases (no hardware):**

```bash
python3 scripts/vofa_supervisor_demo.py --test-plan
python3 scripts/vofa_supervisor_demo.py --run-all-dry
```

**Phase B — actuate WATER ON cases** (close VOFA+ first):

```bash
python3 scripts/vofa_supervisor_demo.py --list-ports

python3 scripts/vofa_supervisor_demo.py --scenario water \
  --port /dev/cu.usbmodemXXXX --spray-seconds 3

python3 scripts/vofa_supervisor_demo.py --scenario water_critical \
  --port /dev/cu.usbmodemXXXX --spray-seconds 3

python3 scripts/vofa_supervisor_demo.py --scenario water_moderate \
  --port /dev/cu.usbmodemXXXX --spray-seconds 3

python3 scripts/vofa_supervisor_demo.py --scenario live --city "San Jose" \
  --csv "0,0,0,28,22.5,35" --port /dev/cu.usbmodemXXXX --spray-seconds 3
```

**Phase C — actuate SKIP cases** (pump must stay OFF):

```bash
python3 scripts/vofa_supervisor_demo.py --scenario rain_skip \
  --port /dev/cu.usbmodemXXXX

python3 scripts/vofa_supervisor_demo.py --scenario wet_soil \
  --port /dev/cu.usbmodemXXXX

python3 scripts/vofa_supervisor_demo.py --scenario live --city "San Jose" \
  --csv "0,0,0,78,22.0,75" --port /dev/cu.usbmodemXXXX
```

Single-case dry-run with full input/decision/expected output:

```bash
python3 scripts/vofa_supervisor_demo.py --scenario water --dry-run
python3 scripts/vofa_supervisor_demo.py --scenario water_critical --dry-run
python3 scripts/vofa_supervisor_demo.py --scenario rain_skip --dry-run
```

Optional: soil from latest `firewater:` line on the gateway:

```bash
python3 scripts/vofa_supervisor_demo.py --scenario live --city "San Jose" \
  --port /dev/cu.usbmodemXXXX --read-firewater --spray-seconds 3
```

### J. Interactive live demo (weather + fake vision + soil hand test)

Continuous supervisor: prints **live Open-Meteo weather**, cycles **fake ML camera frames** (default every **3s**) with live aim angles, reads **soil in a background thread**, and **START/STOP** watering when you change the probe by hand.

```bash
# Preview weather + cycling fake images + decisions (no USB)
python3 scripts/vofa_supervisor_live.py --city "San Jose" --dry-run

# Same with IP geolocation for weather
python3 scripts/vofa_supervisor_live.py --auto-location --fake-image dry_left --dry-run

# Full demo — close VOFA+ first; probe + nozzle aim both update live
python3 scripts/vofa_supervisor_live.py --port /dev/cu.usbmodemXXXX \\
  --city "San Jose" --fake-image dry_left --motor 120 \\
  --dry-below 50 --wet-above 60 --vision-interval 3

# Static aim (no frame cycling)
python3 scripts/vofa_supervisor_live.py --port /dev/cu.usbmodemXXXX \\
  --city "San Jose" --static-vision

# Dry-run but still read live soil from gateway
python3 scripts/vofa_supervisor_live.py --port /dev/cu.usbmodemXXXX \
  --city "San Jose" --dry-run
```

| `--fake-image` | Sector | Aim Y / P |
|----------------|--------|-----------|
| `far_left` | Far left | 160 / 40 |
| `left` (`dry_left`) | Left | 130 / 120 |
| `center_near` | Center near | 100 / 50 |
| `center_far` (`dry_center`) | Center far | 90 / 140 |
| `right` (`dry_right`) | Right | 50 / 45 |
| `far_right` | Far right | 20 / 130 |

**Live vision (default):** every `--vision-interval` seconds (default **3**), angles **ping-pong** through **6 lawn waypoints** (servo range 0–180°) while the pump is ON:

| Waypoint | Yaw | Pitch |
|----------|-----|-------|
| `far_left` | 160 | 40 |
| `left` | 130 | 120 |
| `center_near` | 100 | 50 |
| `center_far` | 90 | 140 |
| `right` | 50 | 45 |
| `far_right` | 20 | 130 |

Each step changes **both** pan (~20°↔160°) and tilt (~40°↔140°) so the gimbal moves on two axes while watering.

Sweep: `far_left ↔ … ↔ far_right ↔ …` (back and forth). Custom path: `--aim-sequence left,center_near,right,center_near`. Aliases: `dry_left`→`left`, `dry_right`→`right`, `dry_center`→`center_far`.

Watch for `>>> AIM UPDATE` (angles while watering), `>>> NEW FRAME` (frame seen but pump off), and `>>> START WATERING` / `>>> STOP WATERING` (soil threshold crossings).

**Threshold mode (default)** — reacts on every new `firewater:` line:

| Reading | Action |
|---------|--------|
| `<= --dry-below` (default 50) | **START** pump/motor |
| `>= --wet-above` (default 60) | **STOP** pump/motor |
| between | hold current state (hysteresis) |

Tune for your probe: `--dry-below 45 --wet-above 55` (sensitive) or `--dry-below 40 --wet-above 70` (wider band).

Use `--mode merge` for full weather+ML rules instead of simple thresholds.

---

## Related docs

- [irrigation_api.md](./irrigation_api.md) — API fields and merge rules  
- [irrigation_schedule_design.md](./irrigation_schedule_design.md) — humidity bands, GPM assumptions  
- [ml_overview.md](./ml_overview.md) — what binary/regression models do  
- [architecture.md](./architecture.md) — full system map  
