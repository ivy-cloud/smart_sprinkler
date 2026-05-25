# Firmware

Production ESP32/STM32 sketches. Legacy originals remain in `pre_code/ESP32_TASK/` for reference.

## Layout

```text
firmware/
  common/              shared headers (stubs)
  gateway/
    sensor_gateway.ino   placeholder — heli_tx_blue
    relay_gateway.ino    placeholder — heli_rc_blue
    hp_tk_tx/              serial angle -> BLE (ESP32A)
      hp_tk_tx.ino
  actuator/
    hp_tk_rx/              BLE -> servo GPIO 13 (ESP32B)
      hp_tk_rx.ino
  perception/
    lidar_node.ino       placeholder — uart / plane
```

## Sprinkler angle path (hp_tk)

| Board | Sketch | Role |
|-------|--------|------|
| **ESP32A** | `gateway/hp_tk_tx/hp_tk_tx.ino` | Read angle from USB serial (`0`–`180` + newline), forward over BLE |
| **ESP32B** | `actuator/hp_tk_rx/hp_tk_rx.ino` | BLE server `ESP32_Servo_Controller`, drive servo on pin **13** |

**Open in Arduino IDE:** File → Open → pick the folder (`hp_tk_tx` or `hp_tk_rx`), not the parent `firmware/` folder.

**Libraries (ESP32 board package):**

- `hp_tk_rx`: [ESP32Servo](https://github.com/madhephaestus/ESP32Servo) + BLE
- `hp_tk_tx`: BLE only

**Typical flow:** Laptop/Python → USB serial → **hp_tk_tx** → BLE → **hp_tk_rx** → nozzle angle.

Irrigation ON/OFF + duration from Python: [docs/RUNBOOK.md](../docs/RUNBOOK.md) (`analyze_soil.py` / API). Duration commands on the actuator are future work (see [firmware_consolidation.md](../docs/firmware_consolidation.md)).

## Placeholders (not yet ported)

| File | Legacy source |
|------|----------------|
| `gateway/sensor_gateway.ino` | `heli_tx_blue` |
| `gateway/relay_gateway.ino` | `heli_rc_blue` |
| `perception/lidar_node.ino` | `uart`, `plane` |

## Python decision layer

`services/irrigation/` — [docs/irrigation_api.md](../docs/irrigation_api.md)

## Consolidation plan

[docs/firmware_consolidation.md](../docs/firmware_consolidation.md)
