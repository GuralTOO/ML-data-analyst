"""ClickHouse-backed dataset analysis tools.

Per approach.md: ClickHouse is the interaction layer for the analysis agent.
The agent-facing surface is intentionally small: one broad analysis tool and
one targeted SQL scalpel. Lower-level lifecycle/profile helpers stay internal.
"""
from __future__ import annotations

from backend.tools._common import safe_tool
from backend.clickhouse.hf import (
    analyze_hf_dataset as _analyze_hf_dataset,
    inspect_hf_dataset_structure as _inspect_hf_dataset_structure,
    profile_hf_dataset as _profile_hf_dataset,
    query_hf_dataset_with_clickhouse as _query_hf_dataset_with_clickhouse,
)
from backend.clickhouse.workers import (
    ensure_dataset_worker as _ensure_dataset_worker,
    get_dataset_worker_status as _get_dataset_worker_status,
    stop_dataset_worker as _stop_dataset_worker,
)


@safe_tool
def analyze_hf_dataset(
    repo_id: str,
    configs: str | None = None,
    splits: str | None = None,
    depth: str = "auto",
    sample_limit: int = 3,
) -> dict:
    """Broadly analyze one selected Hugging Face dataset with ClickHouse.

    This is the default deep-analysis tool. It handles structure inspection,
    safe profiling, sample rows, column roles/stats, profile artifact writing,
    and worker/session lifecycle internally.

    depth:
      - "auto": full-profile small splits, sample-profile large splits.
      - "sample": never full-scan remote Parquet.
      - "full": full-profile selected splits when the user explicitly wants it.

    Use the returned schema, sample rows, and suggested_followup_queries to
    decide whether a targeted SQL query is needed next.
    """
    return _analyze_hf_dataset(
        repo_id,
        configs=configs,
        splits=splits,
        depth=depth,
        sample_limit=sample_limit,
    )


# Internal/compatibility wrappers. They are intentionally not registered in
# backend.tools.ALL_TOOLS because the agent should not manage this complexity.


@safe_tool
def inspect_hf_dataset_structure(
    repo_id: str,
    configs: str | None = None,
    splits: str | None = None,
    sample_limit: int = 2,
) -> dict:
    return _inspect_hf_dataset_structure(
        repo_id,
        configs=configs,
        splits=splits,
        sample_limit=sample_limit,
    )


@safe_tool
def start_hf_dataset_clickhouse_worker(
    repo_id: str,
    pull_image: bool = True,
) -> dict:
    """Start or resume the dedicated Docker worker for a selected dataset.

    Use this when the user selects a dataset for deep processing. The worker is
    kept warm for 10 minutes after the last ClickHouse-backed interaction, then
    the session lifecycle stops it automatically. pull_image=True allows the
    first run to pull the configured ClickHouse worker image if needed.
    """
    return _ensure_dataset_worker(repo_id, pull_image=pull_image)


@safe_tool
def get_hf_dataset_clickhouse_worker_status(repo_id: str) -> dict:
    """Check the current session and Docker worker status for one dataset."""
    return _get_dataset_worker_status(repo_id)


@safe_tool
def eject_hf_dataset_clickhouse_worker(repo_id: str) -> dict:
    """Stop the dedicated Docker worker for a dataset and mark it ejected."""
    return _stop_dataset_worker(repo_id)


@safe_tool
def profile_hf_dataset(
    repo_id: str,
    mode: str = "auto",
    configs: str | None = None,
    splits: str | None = None,
    sample_limit: int = 3,
) -> dict:
    """Internal compatibility wrapper for the lower-level profiler."""
    profile = _profile_hf_dataset(
        repo_id,
        mode=mode,
        configs=configs,
        splits=splits,
        sample_limit=sample_limit,
    )
    for sp in profile.get("split_profiles", []):
        sp.pop("sample_rows", None)
    return profile


@safe_tool
def query_hf_dataset_with_clickhouse(
    repo_id: str,
    config: str,
    split: str,
    select_sql: str,
    limit: int = 20,
    allow_large: bool = False,
) -> dict:
    """Scalpel tool: run one constrained SQL SELECT against a dataset split.

    Use this when you need:
      • aggregations: COUNT, SUM, AVG, MIN, MAX
      • GROUP BY / DISTINCT
      • filtering with WHERE on specific columns
      • inspecting a specific row or small filtered slice
      • derived columns or computed answers

    REQUIRED preflight: analyze_hf_dataset first — you need the valid
    (config, split) names and schema to write correct SQL.

    select_sql MUST contain {table} as the FROM target. Example:
      SELECT domain, count() AS n FROM {table} GROUP BY domain ORDER BY n DESC

    The tool rejects non-SELECT SQL, direct table functions, and semicolons,
    enforces LIMIT ≤ 100, and refuses large remote splits unless allow_large=True.
    """
    return _query_hf_dataset_with_clickhouse(
        repo_id,
        config=config,
        split=split,
        select_sql=select_sql,
        limit=limit,
        allow_large=allow_large,
    )
