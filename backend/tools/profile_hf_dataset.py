"""Compatibility wrapper for the backend ClickHouse profiler CLI."""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.clickhouse.profile_hf_dataset import main


if __name__ == "__main__":
    raise SystemExit(main())
