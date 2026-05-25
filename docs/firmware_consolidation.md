# Firmware consolidation plan

How the scattered `pre_code/ESP32_TASK/*.ino` files combine into maintainable production firmware.

## Short answer

**Yes, combine — but into 3 sketches + shared headers, not one file.**

```text
pre_code/ESP32_TASK (9 prototypes)  →  firmware/ (3 roles + common)
```

---

## What gets merged vs kept separate

### Merge into shared code (`firmware/common/`)

These were **copy-pasted** across `heli_tx_blue` and `heli_rc_blue`:

- `SensorData` struct
- CSV parser (5 commas, 6 fields)
- Print helpers
- Pin/baud `#define`s

### One sketch per physical board

| Board | Sketch | Legacy sources |
|-------|--------|----------------|
| **Sensor gateway** | `gateway/sensor_gateway.ino` | `heli_tx_blue` |
| **Relay gateway** (optional) | `gateway/relay_gateway.ino` | `heli_rc_blue` |
| **Sprinkler actuator** | `actuator/hp_tk_rx/hp_tk_rx.ino` | `hp_tk_rx` (+ duration command TODO) |
| **Angle relay (serial→BLE)** | `gateway/hp_tk_tx/hp_tk_tx.ino` | `hp_tk_tx` |
| **Lidar node** (optional) | `perception/lidar_node.ino` (TODO) | `uart` + `plane` |
| **Manual input** (dev) | stay in `pre_code/ESP32_TASK/4pi_shoubing` or drop | joystick |

### Do NOT merge into one chip

| Pair | Why |
|------|-----|
| `heli_tx` + `heli_rc` | Two locations in the field; client vs server |
| `sensor_gateway` + `hp_tk_rx` | Sensor box vs sprinkler head |
| `uart` lidar + soil gateway | Different mounts; CPU load |

---

## Command protocol (actuator)

After Python merge API returns `duration_minutes` and optional angle:

| BLE / serial write | Meaning |
|------------------|---------|
| `RUN:15` | Open valve / run 15 minutes |
| `STOP` | Stop |
| `45` | Set nozzle servo to 45° |

Laptop bridge (future): small script reads API JSON → sends BLE commands.

---

## Relationship to Python layer

```text
         ┌─────────────────┐
         │ irrigation/     │
         │ merge.py        │
         └────────┬────────┘
                  │ JSON
                  v
         ┌─────────────────┐
         │ api_server.py   │
         └────────┬────────┘
                  │ HTTP or script
                  v
    ┌─────────────┴─────────────┐
    │                           │
    v                           v
sensor_gateway.ino      hp_tk_rx.ino
(uplink CSV)            (RUN + angle)
```

Firmware does **not** run the merge logic initially — the **laptop/edge** does. Later you can embed a subset on ESP32 with WiFi + lighter rules.

---

## Migration checklist

1. [x] Define `firmware/` layout and placeholder sketches
2. [x] Stub `common/` headers
3. [ ] Implement `sensor_parser.h` from `heli_tx_blue`
4. [ ] Implement gateway / actuator / perception sketches
5. [ ] PlatformIO or Arduino project per role
6. [ ] Retire duplicate code in `pre_code/ESP32_TASK/` when verified

---

## PlatformIO structure (recommended next step)

```ini
[platformio]
default_envs = gateway, actuator

[env:gateway]
build_src_filter = +<gateway/sensor_gateway.ino> +<common/*>

[env:actuator]
lib_deps = ESP32Servo
build_src_filter = +<actuator/hp_tk_rx/> +<common/*>
```

Single repo, multiple `env` targets — cleaner than one giant `.ino`.

See [firmware/README.md](../firmware/README.md) for wiring and build notes.
