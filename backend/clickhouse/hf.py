"""Hugging Face dataset inspection and ClickHouse interaction helpers.

These functions are the reusable ClickHouse/Hugging Face implementation. Agent
tools should import these through thin wrappers in ``backend.tools``.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from backend.clickhouse.dataset_sessions import get_dataset_session, touch_dataset_session
from backend.clickhouse.workers import run_clickhouse_local_in_worker


HF_DATASETS_SERVER = "https://datasets-server.huggingface.co"
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
CLICKHOUSE_BINARY = BACKEND_DIR / "bin" / "clickhouse"
DEFAULT_CLICKHOUSE_URL = "http://localhost:8123/"
DEFAULT_PROFILE_DIR = BACKEND_DIR / "profiles"

DEFAULT_MAX_FULL_SCAN_BYTES = 100_000_000
DEFAULT_MAX_FULL_SCAN_ROWS = 100_000
DEFAULT_SAMPLE_LIMIT = 5
DEFAULT_MAX_PROFILE_STRING = 500
DEFAULT_QUERY_LIMIT = 20
MAX_QUERY_LIMIT = 100
DEFAULT_MAX_QUERY_ROWS = 100_000


class DatasetToolError(RuntimeError):
    """Raised for expected dataset-tool failures."""


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return cleaned or "dataset"


def compact_value(value: Any, key: str | None = None, *, max_string: int = DEFAULT_MAX_PROFILE_STRING) -> Any:
    if key == "bytes" and isinstance(value, str):
        return f"<{len(value)} chars omitted>"
    if isinstance(value, str):
        if len(value) <= max_string:
            return value
        return value[:max_string] + f"... <truncated {len(value) - max_string} chars>"
    if isinstance(value, list):
        return [compact_value(item, max_string=max_string) for item in value[:5]]
    if isinstance(value, dict):
        return {
            item_key: compact_value(item_value, item_key, max_string=max_string)
            for item_key, item_value in value.items()
        }
    return value


def _normalize_filter(value: str | list[str] | tuple[str, ...] | None) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    else:
        parts = [str(part).strip() for part in value]
    cleaned = {part for part in parts if part}
    return cleaned or None


def _fetch_json(path: str, params: dict[str, Any], *, timeout: int = 60) -> dict[str, Any]:
    url = f"{HF_DATASETS_SERVER}/{path}?{urllib.parse.urlencode(params)}"
    headers = {}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise DatasetToolError(f"HF datasets-server {path} failed with HTTP {exc.code}: {body}") from exc


def _rows(repo_id: str, config: str, split: str, limit: int) -> dict[str, Any]:
    return _fetch_json(
        "rows",
        {
            "dataset": repo_id,
            "config": config,
            "split": split,
            "offset": 0,
            "length": min(max(int(limit), 1), 20),
        },
        timeout=60,
    )


def _features_by_name(features: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {feature.get("name"): feature for feature in (features or []) if feature.get("name")}


def _type_contains_image(feature_type: Any) -> bool:
    if isinstance(feature_type, dict):
        if feature_type.get("_type") == "Image":
            return True
        return any(_type_contains_image(value) for value in feature_type.values())
    if isinstance(feature_type, list):
        return any(_type_contains_image(value) for value in feature_type)
    return False


def _semantic_role(name: str, clickhouse_type: str, feature: dict[str, Any] | None) -> str:
    lower = name.lower()
    feature_type = (feature or {}).get("type")
    if _type_contains_image(feature_type) or "image" in lower or "bytes Nullable(String)" in clickhouse_type:
        return "image"
    if lower in {"id", "uid"} or lower.endswith("_id"):
        return "identifier"
    if lower in {"label", "labels", "answer", "target", "class"}:
        return "label_or_answer"
    if any(token in lower for token in ("conversation", "messages", "chat")):
        return "conversation"
    if any(token in lower for token in ("question", "prompt", "instruction", "text", "caption")):
        return "text"
    if any(token in lower for token in ("path", "url", "src", "file")):
        return "path_or_url"
    if clickhouse_type.startswith("Array("):
        return "array"
    return "unknown"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sql_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _url_table(file_url: str) -> str:
    return f"url({_sql_string(file_url)}, 'Parquet')"


def _table_expr(files: list[dict[str, Any]]) -> str:
    if not files:
        raise DatasetToolError("No parquet files available for selected config/split")
    if len(files) == 1:
        return _url_table(files[0]["url"])
    selects = [f"SELECT * FROM {_url_table(file_info['url'])}" for file_info in files]
    return "(" + " UNION ALL ".join(selects) + ")"


class ClickHouseRunner:
    """Run queries against a dataset worker, server, or clickhouse-local."""

    def __init__(
        self,
        *,
        repo_id: str | None = None,
        url: str = DEFAULT_CLICKHOUSE_URL,
        binary: pathlib.Path = CLICKHOUSE_BINARY,
        prefer_worker: bool | None = None,
        worker_pull_image: bool | None = None,
    ) -> None:
        self.repo_id = repo_id
        self.url = url
        self.binary = binary
        self.prefer_worker = _env_flag("CLICKHOUSE_DATASET_WORKERS", True) if prefer_worker is None else prefer_worker
        self.worker_pull_image = (
            _env_flag("CLICKHOUSE_DATASET_WORKER_PULL", False)
            if worker_pull_image is None
            else worker_pull_image
        )
        self.mode: str | None = None
        self.worker: dict[str, Any] | None = None
        self.worker_error: str | None = None

    def ensure_available(self) -> str:
        if self.mode:
            return self.mode
        if self.repo_id and self.prefer_worker:
            try:
                self._worker_query("SELECT 1", timeout=60)
                self.mode = "worker"
                return self.mode
            except Exception as exc:  # noqa: BLE001
                self.worker_error = str(exc)
        try:
            self._server_query("SELECT 1", timeout=10)
            self.mode = "server"
            return self.mode
        except Exception:
            pass
        if not self.binary.exists():
            raise DatasetToolError(
                f"ClickHouse is unavailable. Start Docker Compose or install {self.binary}"
            )
        self._local_query("SELECT 1", timeout=30)
        self.mode = "local"
        return self.mode

    def query(self, sql: str, *, timeout: int = 180) -> str:
        mode = self.ensure_available()
        if mode == "server":
            return self._server_query(sql, timeout=timeout)
        if mode == "worker":
            return self._worker_query(sql, timeout=timeout)
        return self._local_query(sql, timeout=timeout)

    def json(self, sql: str, *, timeout: int = 180) -> dict[str, Any]:
        return json.loads(self.query(f"{sql}\nFORMAT JSON", timeout=timeout))

    def _server_query(self, sql: str, *, timeout: int) -> str:
        settings = urllib.parse.urlencode(
            {
                "max_http_get_redirects": 10,
                "max_execution_time": max(1, math.ceil(timeout * 0.9)),
            }
        )
        request = urllib.request.Request(
            self.url + "?" + settings,
            data=sql.encode("utf-8"),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise DatasetToolError(f"ClickHouse query failed: {body}") from exc

    def _local_query(self, sql: str, *, timeout: int) -> str:
        completed = subprocess.run(
            [
                str(self.binary),
                "local",
                "--max_http_get_redirects=10",
                "--query",
                sql,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            raise DatasetToolError(f"ClickHouse local query failed: {completed.stderr}")
        return completed.stdout

    def _worker_query(self, sql: str, *, timeout: int) -> str:
        if not self.repo_id:
            raise DatasetToolError("repo_id is required for dataset worker queries")
        try:
            return run_clickhouse_local_in_worker(
                self.repo_id,
                sql,
                timeout=timeout,
                pull_image=self.worker_pull_image,
                touch_session=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.worker_error = str(exc)
            raise

    def runtime(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "worker_enabled": bool(self.repo_id and self.prefer_worker),
            "worker_error": self.worker_error,
        }


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _dataset_index(repo_id: str) -> dict[str, Any]:
    size = _fetch_json("size", {"dataset": repo_id})
    splits = _fetch_json("splits", {"dataset": repo_id})
    parquet = _fetch_json("parquet", {"dataset": repo_id})

    split_sizes = {}
    for item in ((size.get("size") or {}).get("splits") or []):
        split_sizes[(item.get("config"), item.get("split"))] = item

    files_by_split: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for file_info in parquet.get("parquet_files") or []:
        key = (file_info.get("config"), file_info.get("split"))
        files_by_split.setdefault(key, []).append(file_info)

    return {
        "repo_id": repo_id,
        "size": size.get("size") or {},
        "splits": splits.get("splits") or [],
        "parquet": parquet,
        "split_sizes": split_sizes,
        "files_by_split": files_by_split,
    }


def _selected_split_keys(
    index: dict[str, Any],
    configs: str | list[str] | tuple[str, ...] | None,
    splits: str | list[str] | tuple[str, ...] | None,
) -> list[tuple[str, str]]:
    config_filter = _normalize_filter(configs)
    split_filter = _normalize_filter(splits)
    keys = []
    for item in index["splits"]:
        config = item.get("config")
        split = item.get("split")
        if config_filter and config not in config_filter:
            continue
        if split_filter and split not in split_filter:
            continue
        keys.append((config, split))
    return keys


def _split_numbers(index: dict[str, Any], config: str, split: str) -> dict[str, int | None]:
    split_size = index["split_sizes"].get((config, split)) or {}
    files = index["files_by_split"].get((config, split), [])
    return {
        "num_rows": split_size.get("num_rows"),
        "num_columns": split_size.get("num_columns"),
        "parquet_bytes": split_size.get("num_bytes_parquet_files")
        if split_size.get("num_bytes_parquet_files") is not None
        else sum(int(file_info.get("size") or 0) for file_info in files),
        "memory_bytes": split_size.get("num_bytes_memory"),
    }


def _full_scan_safe(numbers: dict[str, int | None], max_full_scan_bytes: int, max_full_scan_rows: int) -> tuple[bool, str]:
    rows = numbers.get("num_rows")
    bytes_ = numbers.get("parquet_bytes")
    if rows is None and bytes_ is None:
        return False, "row count and parquet byte count are unknown"
    if bytes_ is not None and bytes_ > max_full_scan_bytes:
        return False, f"parquet bytes {bytes_} exceed threshold {max_full_scan_bytes}"
    if rows is not None and rows > max_full_scan_rows:
        return False, f"rows {rows} exceed threshold {max_full_scan_rows}"
    return True, "under full-scan thresholds"


def _describe(files: list[dict[str, Any]], runner: ClickHouseRunner) -> list[dict[str, Any]]:
    if not files:
        return []
    return runner.json(f"DESCRIBE TABLE {_url_table(files[0]['url'])}", timeout=120)["data"]


def _sample_rows(repo_id: str, config: str, split: str, sample_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        data = _rows(repo_id, config, split, sample_limit)
    except DatasetToolError as exc:
        return [], [{"error": str(exc)}]
    rows = []
    for item in data.get("rows") or []:
        rows.append(
            {
                "row_idx": item.get("row_idx"),
                "row": compact_value(item.get("row") or {}),
                "truncated_cells": item.get("truncated_cells") or [],
            }
        )
    return rows, data.get("features") or []


def _column_profiles_sample(
    schema: list[dict[str, Any]],
    sample_rows: list[dict[str, Any]],
    features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    feature_map = _features_by_name(features)
    row_values = [item.get("row") or {} for item in sample_rows]
    profiles = []
    for column in schema:
        name = column["name"]
        typ = column["type"]
        values = [row.get(name) for row in row_values]
        non_null = [value for value in values if value is not None]
        profile: dict[str, Any] = {
            "name": name,
            "type": typ,
            "semantic_role": _semantic_role(name, typ, feature_map.get(name)),
            "profile_mode": "sample",
            "sample_size": len(values),
            "nulls_sample": len(values) - len(non_null),
            "sample_values": compact_value(non_null[:5]),
        }
        if typ.startswith(("String", "Nullable(String)", "LowCardinality(String)", "Nullable(LowCardinality(String)")):
            lengths = [len(value) for value in non_null if isinstance(value, str)]
            profile["empty_sample"] = sum(1 for value in non_null if value == "")
            if lengths:
                profile["min_length_sample"] = min(lengths)
                profile["max_length_sample"] = max(lengths)
                profile["avg_length_sample"] = sum(lengths) / len(lengths)
        elif typ.startswith("Array("):
            lengths = [len(value) for value in non_null if isinstance(value, list)]
            if lengths:
                profile["min_items_sample"] = min(lengths)
                profile["max_items_sample"] = max(lengths)
                profile["avg_items_sample"] = sum(lengths) / len(lengths)
        profiles.append(profile)
    return profiles


def _column_profiles_full(
    files: list[dict[str, Any]],
    schema: list[dict[str, Any]],
    features: list[dict[str, Any]],
    runner: ClickHouseRunner,
) -> tuple[int, list[dict[str, Any]]]:
    expressions = ["count() AS row_count"]
    field_map: dict[int, list[tuple[str, str]]] = {}
    feature_map = _features_by_name(features)

    for idx, column in enumerate(schema):
        name = column["name"]
        typ = column["type"]
        ident = _sql_identifier(name)
        field_map[idx] = [("nulls", f"c{idx}_nulls")]
        expressions.append(f"countIf(isNull({ident})) AS c{idx}_nulls")

        if typ.startswith("Array("):
            additions = [
                ("min_items", f"min(length({ident}))"),
                ("max_items", f"max(length({ident}))"),
                ("avg_items", f"avg(length({ident}))"),
            ]
        elif typ.startswith(("String", "Nullable(String)", "LowCardinality(String)", "Nullable(LowCardinality(String)")):
            additions = [
                ("empty", f"countIf(length({ident}) = 0)"),
                ("avg_length", f"avg(length({ident}))"),
                ("length_quantiles", f"quantiles(0.5, 0.9, 0.99)(length({ident}))"),
                ("top_values", f"topK(10)({ident})"),
            ]
        elif any(token in typ for token in ("Int", "Float", "Decimal")):
            additions = [
                ("min", f"min({ident})"),
                ("max", f"max({ident})"),
                ("avg", f"avg({ident})"),
                ("quantiles", f"quantiles(0.5, 0.9, 0.99)({ident})"),
            ]
        else:
            additions = []

        for output_key, expression in additions:
            alias = f"c{idx}_{output_key}"
            expressions.append(f"{expression} AS {alias}")
            field_map[idx].append((output_key, alias))

    row = runner.json(f"SELECT {', '.join(expressions)} FROM {_table_expr(files)}", timeout=300)["data"][0]
    profiles = []
    for idx, column in enumerate(schema):
        name = column["name"]
        typ = column["type"]
        profile = {
            "name": name,
            "type": typ,
            "semantic_role": _semantic_role(name, typ, feature_map.get(name)),
            "profile_mode": "full",
        }
        for output_key, alias in field_map[idx]:
            profile[output_key] = compact_value(row.get(alias))
        profiles.append(profile)
    return int(row.get("row_count") or 0), profiles


def inspect_hf_dataset_structure(
    repo_id: str,
    *,
    configs: str | list[str] | tuple[str, ...] | None = None,
    splits: str | list[str] | tuple[str, ...] | None = None,
    sample_limit: int = 2,
    max_full_scan_bytes: int = DEFAULT_MAX_FULL_SCAN_BYTES,
    max_full_scan_rows: int = DEFAULT_MAX_FULL_SCAN_ROWS,
) -> dict[str, Any]:
    """Return cheap structure metadata and safe profiling recommendations."""
    dataset_session = touch_dataset_session(repo_id, interaction="inspect_structure")
    index = _dataset_index(repo_id)
    runner = ClickHouseRunner(repo_id=repo_id)
    clickhouse = {"available": False, "mode": None, "error": None, "worker_enabled": runner.prefer_worker}
    try:
        clickhouse["mode"] = runner.ensure_available()
        clickhouse["available"] = True
    except Exception as exc:  # noqa: BLE001
        clickhouse["error"] = str(exc)
    if runner.worker_error:
        clickhouse["worker_error"] = runner.worker_error
    dataset_session = get_dataset_session(repo_id) or dataset_session

    selected_keys = _selected_split_keys(index, configs, splits)
    if not selected_keys:
        raise DatasetToolError("No dataset splits matched the provided config/split filters")
    selected = []
    for config, split in selected_keys:
        files = index["files_by_split"].get((config, split), [])
        numbers = _split_numbers(index, config, split)
        safe, reason = _full_scan_safe(numbers, max_full_scan_bytes, max_full_scan_rows)
        sample_rows, features = _sample_rows(repo_id, config, split, sample_limit)
        schema = []
        schema_error = None
        if clickhouse["available"] and files:
            try:
                schema = _describe(files, runner)
            except Exception as exc:  # noqa: BLE001
                schema_error = str(exc)
        selected.append(
            {
                "config": config,
                "split": split,
                **numbers,
                "parquet_files": {
                    "count": len(files),
                    "filenames": [file_info.get("filename") for file_info in files[:5]],
                },
                "features": features,
                "clickhouse_schema": schema,
                "clickhouse_schema_error": schema_error,
                "sample_rows": sample_rows,
                "profile_policy": {
                    "recommended_mode": "full" if safe else "sample",
                    "full_scan_safe": safe,
                    "reason": reason,
                    "max_full_scan_bytes": max_full_scan_bytes,
                    "max_full_scan_rows": max_full_scan_rows,
                },
            }
        )

    dataset_size = index["size"].get("dataset") or {}
    return {
        "repo_id": repo_id,
        "dataset_size": dataset_size,
        "parquet_status": {
            "pending": index["parquet"].get("pending") or [],
            "failed": index["parquet"].get("failed") or [],
            "partial": bool(index["parquet"].get("partial")),
        },
        "clickhouse": clickhouse,
        "dataset_session": dataset_session,
        "splits": selected,
    }


def _write_profile(profile: dict[str, Any], output_dir: pathlib.Path = DEFAULT_PROFILE_DIR) -> pathlib.Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_name(profile['repo_id'])}.profile.json"
    output_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return output_path


def _compact_schema(schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": column.get("name"),
            "type": column.get("type"),
        }
        for column in schema
    ]


def _compact_column_profile(column: dict[str, Any]) -> dict[str, Any]:
    keep_keys = [
        "name",
        "type",
        "semantic_role",
        "profile_mode",
        "nulls",
        "nulls_sample",
        "sample_size",
        "empty",
        "empty_sample",
        "min",
        "max",
        "avg",
        "quantiles",
        "min_length_sample",
        "max_length_sample",
        "avg_length",
        "avg_length_sample",
        "length_quantiles",
        "min_items",
        "max_items",
        "avg_items",
        "min_items_sample",
        "max_items_sample",
        "avg_items_sample",
        "top_values",
        "sample_values",
    ]
    return {key: compact_value(column[key]) for key in keep_keys if key in column}


def _suggest_followup_queries(split_profile: dict[str, Any]) -> list[dict[str, str]]:
    columns = split_profile.get("columns") or []
    config = split_profile.get("config")
    split = split_profile.get("split")
    suggestions = [
        {
            "question": "How many rows are in this split?",
            "config": config,
            "split": split,
            "sql": "SELECT count() AS rows FROM {table}",
        }
    ]

    for column in columns:
        name = column.get("name")
        role = column.get("semantic_role")
        typ = column.get("type") or ""
        if not name:
            continue
        ident = _sql_identifier(name)
        if role in {"label_or_answer", "identifier", "text", "conversation"} or "String" in typ:
            suggestions.append(
                {
                    "question": f"What are the most common values in {name}?",
                    "config": config,
                    "split": split,
                    "sql": f"SELECT {ident}, count() AS n FROM {{table}} GROUP BY {ident} ORDER BY n DESC LIMIT 20",
                }
            )
        if len(suggestions) >= 5:
            break
    return suggestions


def profile_hf_dataset(
    repo_id: str,
    *,
    mode: str = "auto",
    configs: str | list[str] | tuple[str, ...] | None = None,
    splits: str | list[str] | tuple[str, ...] | None = None,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    max_full_scan_bytes: int = DEFAULT_MAX_FULL_SCAN_BYTES,
    max_full_scan_rows: int = DEFAULT_MAX_FULL_SCAN_ROWS,
    write_artifact: bool = True,
) -> dict[str, Any]:
    """Profile a Hugging Face dataset through local ClickHouse.

    mode="auto" full-scans only small selected config/splits. mode="sample"
    never scans full parquet data. mode="full" scans selected parquet files.
    """
    if mode not in {"auto", "sample", "full"}:
        raise DatasetToolError("mode must be one of: auto, sample, full")
    dataset_session = touch_dataset_session(repo_id, interaction=f"profile:{mode}")
    index = _dataset_index(repo_id)
    runner = ClickHouseRunner(repo_id=repo_id)
    clickhouse_mode = runner.ensure_available()
    split_profiles = []

    selected_keys = _selected_split_keys(index, configs, splits)
    if not selected_keys:
        raise DatasetToolError("No dataset splits matched the provided config/split filters")

    for config, split in selected_keys:
        files = index["files_by_split"].get((config, split), [])
        numbers = _split_numbers(index, config, split)
        safe, reason = _full_scan_safe(numbers, max_full_scan_bytes, max_full_scan_rows)
        sample_rows, features = _sample_rows(repo_id, config, split, sample_limit)
        schema = _describe(files, runner) if files else []

        actual_rows = numbers.get("num_rows")
        profile_error = None
        if not files:
            chosen_mode = "unavailable"
            columns = []
            profile_error = "No converted Parquet files are available for this config/split"
        else:
            chosen_mode = "full" if mode == "full" or (mode == "auto" and safe) else "sample"

        if chosen_mode == "full":
            actual_rows, columns = _column_profiles_full(files, schema, features, runner)
        elif chosen_mode == "sample":
            columns = _column_profiles_sample(schema, sample_rows, features)

        split_profiles.append(
            {
                "config": config,
                "split": split,
                **numbers,
                "actual_profiled_rows": actual_rows,
                "profile_mode": chosen_mode,
                "profile_policy_reason": reason,
                "profile_error": profile_error,
                "schema": schema,
                "features": features,
                "columns": columns,
                "sample_rows": sample_rows,
                "parquet_files": {
                    "count": len(files),
                    "filenames": [file_info.get("filename") for file_info in files[:5]],
                    "total_bytes": sum(int(file_info.get("size") or 0) for file_info in files),
                },
            }
        )

    profile = {
        "repo_id": repo_id,
        "profiled_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "requested_mode": mode,
        "clickhouse_mode": clickhouse_mode,
        "clickhouse_runtime": runner.runtime(),
        "dataset_session": get_dataset_session(repo_id) or dataset_session,
        "thresholds": {
            "max_full_scan_bytes": max_full_scan_bytes,
            "max_full_scan_rows": max_full_scan_rows,
        },
        "dataset_size": index["size"].get("dataset") or {},
        "parquet_status": {
            "pending": index["parquet"].get("pending") or [],
            "failed": index["parquet"].get("failed") or [],
            "partial": bool(index["parquet"].get("partial")),
        },
        "split_profiles": split_profiles,
    }
    if write_artifact:
        profile["profile_path"] = str(_write_profile(profile))
    return profile


def analyze_hf_dataset(
    repo_id: str,
    *,
    configs: str | list[str] | tuple[str, ...] | None = None,
    splits: str | list[str] | tuple[str, ...] | None = None,
    depth: str = "auto",
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Run the default broad ClickHouse analysis for a selected HF dataset.

    This is the high-level primitive the agent should normally use. It hides
    worker lifecycle, split safety policy, and profile artifact management.
    """
    if depth not in {"auto", "sample", "full"}:
        raise DatasetToolError("depth must be one of: auto, sample, full")
    profile = profile_hf_dataset(
        repo_id,
        mode=depth,
        configs=configs,
        splits=splits,
        sample_limit=sample_limit,
        write_artifact=True,
    )
    split_summaries = []
    for split_profile in profile.get("split_profiles") or []:
        column_profiles = [_compact_column_profile(column) for column in split_profile.get("columns") or []]
        semantic_roles: dict[str, list[str]] = {}
        for column in column_profiles:
            semantic_roles.setdefault(column.get("semantic_role") or "unknown", []).append(column.get("name"))
        split_summaries.append(
            {
                "config": split_profile.get("config"),
                "split": split_profile.get("split"),
                "num_rows": split_profile.get("num_rows"),
                "num_columns": split_profile.get("num_columns"),
                "parquet_bytes": split_profile.get("parquet_bytes"),
                "memory_bytes": split_profile.get("memory_bytes"),
                "profile_mode": split_profile.get("profile_mode"),
                "profile_policy_reason": split_profile.get("profile_policy_reason"),
                "profile_error": split_profile.get("profile_error"),
                "actual_profiled_rows": split_profile.get("actual_profiled_rows"),
                "parquet_files": split_profile.get("parquet_files"),
                "schema": _compact_schema(split_profile.get("schema") or []),
                "semantic_roles": semantic_roles,
                "columns": column_profiles,
                "sample_rows": split_profile.get("sample_rows") or [],
                "suggested_followup_queries": _suggest_followup_queries(split_profile),
            }
        )
    return {
        "repo_id": repo_id,
        "analyzed_at": profile.get("profiled_at"),
        "requested_depth": depth,
        "clickhouse_mode": profile.get("clickhouse_mode"),
        "clickhouse_runtime": profile.get("clickhouse_runtime"),
        "dataset_session": profile.get("dataset_session"),
        "dataset_size": profile.get("dataset_size"),
        "parquet_status": profile.get("parquet_status"),
        "thresholds": profile.get("thresholds"),
        "profile_path": profile.get("profile_path"),
        "splits": split_summaries,
    }


_FORBIDDEN_SQL = re.compile(
    r"\b(attach|alter|create|delete|detach|drop|grant|insert|kill|optimize|rename|revoke|set|system|truncate|update|use)\b",
    re.IGNORECASE,
)
_FORBIDDEN_TABLE_FUNCTIONS = re.compile(r"\b(file|hdfs|jdbc|mysql|odbc|postgresql|s3|url)\s*\(", re.IGNORECASE)
_FORMAT_CLAUSE = re.compile(r"\bFORMAT\s+[A-Za-z0-9_]+\s*$", re.IGNORECASE)


def _validate_select_sql(select_sql: str) -> str:
    sql = select_sql.strip()
    if not sql:
        raise DatasetToolError("select_sql cannot be empty")
    if ";" in sql:
        raise DatasetToolError("select_sql must be a single statement without semicolons")
    if _FORMAT_CLAUSE.search(sql):
        raise DatasetToolError("select_sql must not include FORMAT")
    lowered = sql.lower()
    if _FORBIDDEN_SQL.search(sql):
        raise DatasetToolError("select_sql must be read-only SELECT SQL")
    if _FORBIDDEN_TABLE_FUNCTIONS.search(sql):
        raise DatasetToolError("select_sql must query the provided {table} placeholder only")
    if not (lowered.startswith("select ") or lowered.startswith("with ")):
        raise DatasetToolError("select_sql must start with SELECT or WITH")
    if "{table}" not in sql:
        raise DatasetToolError("select_sql must include the {table} placeholder in its FROM clause")
    return sql


def query_hf_dataset_with_clickhouse(
    repo_id: str,
    *,
    config: str,
    split: str,
    select_sql: str,
    limit: int = DEFAULT_QUERY_LIMIT,
    max_query_bytes: int = DEFAULT_MAX_FULL_SCAN_BYTES,
    max_query_rows: int = DEFAULT_MAX_QUERY_ROWS,
    allow_large: bool = False,
) -> dict[str, Any]:
    """Run a constrained SELECT query over one HF dataset config/split."""
    dataset_session = touch_dataset_session(repo_id, interaction=f"query:{config}/{split}")
    clean_sql = _validate_select_sql(select_sql)
    limit = min(max(int(limit), 1), MAX_QUERY_LIMIT)
    index = _dataset_index(repo_id)
    files = index["files_by_split"].get((config, split), [])
    if not files:
        raise DatasetToolError(f"No parquet files found for {repo_id} config={config!r} split={split!r}")
    numbers = _split_numbers(index, config, split)
    parquet_bytes = int(numbers.get("parquet_bytes") or 0)
    rows = numbers.get("num_rows")
    if not allow_large and parquet_bytes > max_query_bytes:
        raise DatasetToolError(
            f"Selected split has {parquet_bytes} parquet bytes, exceeding query guard "
            f"{max_query_bytes}. Use inspect/profile sample output, choose a smaller config, "
            "or explicitly allow a large query."
        )
    if not allow_large and rows is not None and rows > max_query_rows:
        raise DatasetToolError(
            f"Selected split has {rows} rows, exceeding query guard {max_query_rows}. "
            "Use profile_hf_dataset(mode='sample'), choose a smaller config, "
            "or explicitly allow a large query."
        )
    runner = ClickHouseRunner(repo_id=repo_id)
    table = _table_expr(files)
    subquery = clean_sql.replace("{table}", table)
    result = runner.json(f"SELECT * FROM ({subquery}) LIMIT {limit}", timeout=120)
    return {
        "repo_id": repo_id,
        "config": config,
        "split": split,
        "clickhouse_mode": runner.mode,
        "clickhouse_runtime": runner.runtime(),
        "dataset_session": get_dataset_session(repo_id) or dataset_session,
        "limit": limit,
        "parquet_bytes": parquet_bytes,
        "max_query_rows": max_query_rows,
        "max_query_bytes": max_query_bytes,
        "query": clean_sql,
        "rows": compact_value(result.get("data") or []),
        "meta": result.get("meta") or [],
        "statistics": result.get("statistics") or {},
    }
