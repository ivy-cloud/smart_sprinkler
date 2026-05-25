#!/usr/bin/env python3
"""
HTTP API for irrigation decisions (weather + soil + merged).

  pip install -r requirements.txt
  python3 scripts/api_server.py
  python3 scripts/api_server.py --port 8080
"""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

import uvicorn
from services.api import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Irrigation decision HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
