"""ClickHouse-backed dataset understanding internals.

Agent-exposed wrappers live in ``backend.tools``. This package contains the
implementation and command-line helpers used by those wrappers.
"""

from backend.clickhouse.hf import (
    DatasetToolError,
    analyze_hf_dataset,
    inspect_hf_dataset_structure,
    profile_hf_dataset,
    query_hf_dataset_with_clickhouse,
)
from backend.clickhouse.workers import (
    ensure_dataset_worker,
    get_dataset_worker_status,
    stop_dataset_worker,
)

__all__ = [
    "DatasetToolError",
    "analyze_hf_dataset",
    "ensure_dataset_worker",
    "get_dataset_worker_status",
    "inspect_hf_dataset_structure",
    "profile_hf_dataset",
    "query_hf_dataset_with_clickhouse",
    "stop_dataset_worker",
]
