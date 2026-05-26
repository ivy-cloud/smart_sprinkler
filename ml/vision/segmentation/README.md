# Grass / scene segmentation (YOLO)

Segments CCTV-style frames into **car, grass, road, water** — used to find lawn areas for targeted irrigation (proposal “see before you spray”).

## Scripts

| File | Purpose |
|------|---------|
| `train.py` | Fine-tune YOLO11n-seg on `data.yaml` |
| `convert_coco.py` | Convert COCO labels to YOLO segment format |
| `data.yaml` | Class names and image paths |

## Dataset (not in git)

Training images (~600MB) live in the source tree:

`../../smart_sprinkler_docs/code/Video_Segmentation/`

Copy or symlink before training:

```bash
# from repo root — example symlink
ln -s ../../smart_sprinkler_docs/code/Video_Segmentation/train ml/vision/segmentation/train
ln -s ../../smart_sprinkler_docs/code/Video_Segmentation/valid ml/vision/segmentation/valid
ln -s ../../smart_sprinkler_docs/code/Video_Segmentation/test ml/vision/segmentation/test
```

Pretrained weights (optional, gitignored): copy `yolo11n-seg.pt` from the same docs folder into this directory.

## Train

```bash
pip install -r ml/requirements.txt
cd ml/vision/segmentation
python3 train.py
```

Runs appear under `runs/segment/` (Ultralytics default).

## Inference (after training)

### Angle from grass (integrated)

1. Run YOLO11-seg on a **camera frame** (jpg/png).
2. Pick the **largest `grass` box** above confidence threshold.
3. Use the box **horizontal center** `cx` vs image width → nozzle angle **0–180°**  
   (`angle = round((cx / width) * 180)`, optional `--invert-x` if your mount is mirrored).
4. If no grass (or tiny region), use **fallback angle** (default 90°).

```bash
# Standalone test (needs trained weights under runs/segment/.../best.pt)
python3 scripts/predict_grass_angle.py path/to/frame.jpg --json

# Full stack: soil + weather decision + vision aim + hp_tk serial
python3 scripts/irrigation_to_hp_tk.py \
  --csv "12.1,0.4,0.0,28,22.5,41" --city "San Jose" \
  --image path/to/frame.jpg --port /dev/cu.usbserial-XXXX
```

Library: `services.irrigation.aim_from_image()` / `hp_tk_angle_from_decision()`.

Weights search order: `SMART_SPRINKLER_VISION_WEIGHTS` env → `runs/segment/train/weights/best.pt` → `yolo11n-seg.pt`.

### Bench calibration

Camera and servo are mounted separately; tune so **left/center/right in the photo**
matches **nozzle 0°/90°/180°**:

```text
angle = 90 + angle_scale × (linear_from_image − 90) + angle_offset
```

```bash
python3 scripts/predict_grass_angle.py frame.jpg --angle-offset -5 --angle-scale 1.1
python3 scripts/irrigation_to_hp_tk.py ... --image frame.jpg --angle-offset 0 --angle-scale 1.0 --invert-x
```

See `angle.py` (`centroid_to_nozzle_angle`) and `--angle-offset` / `--angle-scale` on the CLIs.

### Low-level Ultralytics

```python
from ultralytics import YOLO
model = YOLO("runs/segment/train/weights/best.pt")
results = model.predict("frame.jpg")
```
