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

```python
from ultralytics import YOLO
model = YOLO("runs/segment/train/weights/best.pt")
results = model.predict("frame.jpg")
# Grass mask → centroid / area → nozzle angle
```
