# Vision angle experiment (two frames + fallback 90°)

Use this folder for **two test photos** when running `scripts/vision_angle_experiment.py`.

## Get two frames from the dataset

The training images live outside git (~600MB). Copy two test frames, e.g.:

```bash
# From repo root — adjust source path if your docs tree differs
SRC=../smart_sprinkler_docs/code/Video_Segmentation/test/images
mkdir -p examples/vision

# Pick any two jpg/png frames; rename for clarity
cp "$SRC"/0001.jpg examples/vision/frame_grass_left.jpg
cp "$SRC"/0002.jpg examples/vision/frame_grass_right.jpg
```

Pick one frame where **grass is mostly on the left** of the picture and one where it is **mostly on the right** so the computed angles differ.

## Run the experiment

**1. Preview angles (no USB)**

```bash
python3 scripts/vision_angle_experiment.py \
  --image-a examples/vision/frame_grass_left.jpg \
  --image-b examples/vision/frame_grass_right.jpg \
  --dry-run
```

Expected:

| Case | Image | Typical angle |
|------|--------|----------------|
| `no_image (fallback)` | none | **90°** (always, no YOLO) |
| `image_a` | left grass | lower (e.g. 20–50°) |
| `image_b` | right grass | higher (e.g. 130–160°) |

**2. No images → only 90°**

```bash
python3 scripts/vision_angle_experiment.py --dry-run
```

**3. Send each angle to hp_tk_tx**

Power **hp_tk_rx**, reset **hp_tk_tx** (BLE connected), close Arduino Serial Monitor, then:

```bash
python3 scripts/vision_angle_experiment.py --list-ports

python3 scripts/vision_angle_experiment.py \
  --image-a examples/vision/frame_grass_left.jpg \
  --image-b examples/vision/frame_grass_right.jpg \
  --port /dev/cu.usbserial-XXXX \
  --pause 4
```

Sequence on serial: `0` (park) → **90** (no-image case) → **angle A** → **angle B** → `0` (park).

**4. Vision-only angle test (single frame)**

```bash
python3 scripts/predict_grass_angle.py examples/vision/frame_grass_left.jpg
```

## Requirements

- `pip install -r ml/requirements.txt` (ultralytics + torch)
- Trained weights: `ml/vision/segmentation/runs/segment/train/weights/best.pt`  
  or `SMART_SPRINKLER_VISION_WEIGHTS=/path/to/best.pt`
- Without weights, image cases fall back to **90°** like the no-image case.

## Calibration

If left/right angles are swapped or misaligned, add `--invert-x`, `--angle-offset`, or `--angle-scale` (see `ml/vision/segmentation/README.md`).
