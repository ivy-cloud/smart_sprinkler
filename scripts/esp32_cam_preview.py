import time
from pathlib import Path

import cv2
import numpy as np
import requests

# =========================
# ESP32-CAM capture URL
# Set to your ESP32-CAM IP address
# =========================
# CAPTURE_URL = "http://192.168.0.210/capture"
# CAPTURE_URL = "http://192.168.31.45/capture"
CAPTURE_URL = "http://192.168.0.188/capture"  # By bzan: change to ESP32-CAM IP; connect camera to board + laptop, use Arduino serial for IP

# HTTP request timeout (seconds)
TIMEOUT = 3

# Display scale factor (1.0 = original size, 2.0 = 2x zoom)
DISPLAY_SCALE = 2.0

# Initial rotation; only 0, 90, 180, 270 are supported
INITIAL_ROTATION_DEGREES = 0
ROTATION_OPTIONS = (0, 90, 180, 270)

# Directory for saved screenshots
SAVE_DIR = Path("captures")


def rotate_frame(frame, degrees):
    """Rotate an OpenCV frame clockwise by 0, 90, 180, or 270 degrees."""
    if degrees == 0:
        return frame
    if degrees == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"Unsupported rotation: {degrees}")


def next_rotation(current_degrees):
    current_index = ROTATION_OPTIONS.index(current_degrees)
    return ROTATION_OPTIONS[(current_index + 1) % len(ROTATION_OPTIONS)]


def main():
    rotation_degrees = INITIAL_ROTATION_DEGREES
    if rotation_degrees not in ROTATION_OPTIONS:
        raise ValueError("INITIAL_ROTATION_DEGREES must be 0, 90, 180, or 270")

    print("Connecting to ESP32-S3-CAM ...")
    print(f"Capture URL: {CAPTURE_URL}")
    print("Keys:")
    print("  q -> quit")
    print("  s -> save screenshot")
    print("  r -> cycle rotation: 0/90/180/270")

    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    last_time = time.time()
    fps = 0.0
    frame_count = 0
    frame_for_save = None

    window_name = "ESP32-S3-CAM Preview"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    while True:
        try:
            response = requests.get(CAPTURE_URL, timeout=TIMEOUT)

            if response.status_code != 200:
                print(f"Request failed, status code: {response.status_code}")
                time.sleep(0.2)
                continue

            img_array = np.frombuffer(response.content, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

            if frame is None:
                print("Failed to decode image")
                time.sleep(0.2)
                continue

            frame = rotate_frame(frame, rotation_degrees)
            frame_count += 1

            current_time = time.time()
            dt = current_time - last_time
            if dt > 0:
                fps = 1.0 / dt
            last_time = current_time

            h, w = frame.shape[:2]

            cv2.putText(
                frame,
                f"FPS: {fps:.2f}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            cv2.putText(
                frame,
                f"Size: {w}x{h}",
                (10, 65),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            cv2.putText(
                frame,
                f"Rotation: {rotation_degrees}",
                (10, 100),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            frame_for_save = frame.copy()

            frame_show = cv2.resize(
                frame,
                None,
                fx=DISPLAY_SCALE,
                fy=DISPLAY_SCALE,
                interpolation=cv2.INTER_LINEAR,
            )

            cv2.imshow(window_name, frame_show)

        except requests.exceptions.RequestException as e:
            print(f"Network request failed: {e}")
            time.sleep(0.5)
            continue

        except Exception as e:
            print(f"Runtime error: {e}")
            time.sleep(0.5)
            continue

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break

        if key == ord("s"):
            if frame_for_save is None:
                print("No frame available to save")
                continue

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            save_path = SAVE_DIR / f"esp32_cam_{timestamp}_rot{rotation_degrees}.jpg"
            cv2.imwrite(str(save_path), frame_for_save)
            print(f"Screenshot saved: {save_path}")

        elif key == ord("r"):
            rotation_degrees = next_rotation(rotation_degrees)
            print(f"Rotation: {rotation_degrees}")

    cv2.destroyAllWindows()
    print("Exited")


if __name__ == "__main__":
    main()
