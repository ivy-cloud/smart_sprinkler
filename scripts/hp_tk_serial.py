"""USB serial helpers for hp_tk_tx (ESP32A gateway)."""

from __future__ import annotations


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise SystemExit("Install pyserial: python3 -m pip install pyserial") from exc
    return [p.device for p in list_ports.comports()]


def _import_serial():
    try:
        import serial
    except ImportError as exc:
        raise SystemExit("Install pyserial: python3 -m pip install pyserial") from exc
    return serial


def open_hp_tk_serial(port: str, *, baud: int = 115200):
    """Open tx USB; dsrdtr=False avoids resetting ESP32 on macOS."""
    serial = _import_serial()
    try:
        return serial.Serial(
            port,
            baud,
            timeout=2,
            dsrdtr=False,
            rtscts=False,
        )
    except serial.SerialException as exc:
        err = str(exc).lower()
        errno = getattr(exc, "errno", None)
        if errno == 16 or "busy" in err:
            raise SystemExit(
                f"Cannot open {port}: port is busy.\n"
                "Close Arduino Serial Monitor or other apps on this port, then retry."
            ) from exc
        if errno == 2 or "no such file" in err:
            found = ", ".join(list_serial_ports()) or "(none — is hp_tk_tx USB plugged in?)"
            raise SystemExit(
                f"Cannot open {port}: port not found.\n"
                f"Current ports: {found}\n"
                "Run: python3 scripts/vision_angle_experiment.py --list-ports"
            ) from exc
        raise


def write_angle(ser, angle: int) -> None:
    if not 0 <= angle <= 180:
        raise ValueError(f"angle must be 0-180, got {angle}")
    ser.write(f"{angle}\n".encode("ascii"))
    ser.flush()


def send_angle(port: str, angle: int, *, baud: int = 115200) -> None:
    with open_hp_tk_serial(port, baud=baud) as ser:
        write_angle(ser, angle)
