# Firmware (placeholders)

Production firmware layout. **Working code still lives in `pre_code/ESP32_TASK/`** until these are implemented.

## Layout

```text
firmware/
  common/           shared headers (stubs)
  gateway/          sensor uplink + optional relay
  actuator/         servo / valve node
  perception/       lidar / camera (future)
```

## Placeholder sketches

| File | Replaces (pre_code/ESP32_TASK) | Status |
|------|------------------------|--------|
| `gateway/sensor_gateway.ino` | `heli_tx_blue` | placeholder |
| `gateway/relay_gateway.ino` | `heli_rc_blue` | placeholder |
| `actuator/sprinkler_node.ino` | `hp_tk_rx` | placeholder |
| `perception/lidar_node.ino` | `uart`, `plane` | placeholder |

## Decision layer (implemented)

Python merge + API: `services/irrigation/` — see [docs/irrigation_api.md](../docs/irrigation_api.md).

## Consolidation plan

[firmware_consolidation.md](../docs/firmware_consolidation.md)
