"""CLI wrapper for the reusable Hugging Face + ClickHouse profiler."""

from __future__ import annotations

import argparse
import json

from backend.clickhouse.hf import profile_hf_dataset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_id", help="Hugging Face dataset id, e.g. TuringEnterprises/Open-MM-RL")
    parser.add_argument("--mode", choices=["auto", "sample", "full"], default="auto")
    parser.add_argument("--config", action="append", dest="configs")
    parser.add_argument("--split", action="append", dest="splits")
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--max-full-scan-bytes", type=int, default=100_000_000)
    parser.add_argument("--max-full-scan-rows", type=int, default=100_000)
    parser.add_argument("--no-write-artifact", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the full JSON profile")
    args = parser.parse_args()

    profile = profile_hf_dataset(
        args.repo_id,
        mode=args.mode,
        configs=args.configs,
        splits=args.splits,
        sample_limit=args.sample_limit,
        max_full_scan_bytes=args.max_full_scan_bytes,
        max_full_scan_rows=args.max_full_scan_rows,
        write_artifact=not args.no_write_artifact,
    )

    if args.json:
        print(json.dumps(profile, indent=2))
        return 0

    print(f"Dataset: {profile['repo_id']}")
    print(f"Requested mode: {profile['requested_mode']}")
    print(f"ClickHouse mode: {profile['clickhouse_mode']}")
    if profile.get("profile_path"):
        print(f"Profile written: {profile['profile_path']}")
    for split_profile in profile["split_profiles"]:
        print(
            "- "
            f"{split_profile['config']}/{split_profile['split']}: "
            f"{split_profile['profile_mode']} profile, "
            f"rows={split_profile.get('num_rows')}, "
            f"bytes={split_profile.get('parquet_bytes')}, "
            f"columns={len(split_profile.get('columns') or [])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
