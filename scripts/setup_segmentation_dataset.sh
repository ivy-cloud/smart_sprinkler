#!/bin/bash
# Link YOLO train/valid/test folders into ml/vision/segmentation (dataset is not in git).
set -euo pipefail

SEG="$(cd "$(dirname "$0")/.." && pwd)/ml/vision/segmentation"
DOCS_ROOT="${SMART_SPRINKLER_DOCS:-$HOME/smart_sprinkler_docs}"
SRC="$DOCS_ROOT/code/Video_Segmentation"

if [ ! -d "$SRC/train/images" ]; then
  echo "Dataset not found at: $SRC/train/images"
  echo "Set SMART_SPRINKLER_DOCS to your smart_sprinkler_docs path, or copy Video_Segmentation there."
  exit 1
fi

cd "$SEG"
for split in train valid test; do
  if [ -e "$split" ]; then
    echo "Already present: $split"
  else
    ln -s "$SRC/$split" "$split"
    echo "Linked $split -> $SRC/$split"
  fi
done

echo "Done. Run: cd ml/vision/segmentation && python3 train.py"
