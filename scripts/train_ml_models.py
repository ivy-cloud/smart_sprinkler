#!/usr/bin/env python3
"""Train soil MLP artifacts used by services/irrigation/ml_inference.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    scripts = [
        ROOT / "ml/soil/binary/train.py",
        ROOT / "ml/soil/regression/train.py",
    ]
    for script in scripts:
        print(f"\n=== {script.relative_to(ROOT)} ===")
        rc = subprocess.call([sys.executable, str(script)], cwd=ROOT)
        if rc != 0:
            return rc
    print("\nDone. Artifacts in ml/soil/*/artifacts/ (gitignored).")
    print("Test: python3 scripts/analyze_soil.py --csv '12.1,0.4,0.0,28,22.5,41' --city 'San Jose'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
