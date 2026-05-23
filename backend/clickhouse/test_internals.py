from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest.mock import patch

from backend.clickhouse import dataset_sessions
from backend.clickhouse.hf import DatasetToolError, _full_scan_safe, _validate_select_sql


class ClickHouseSqlGuardTests(unittest.TestCase):
    def test_accepts_select_with_table_placeholder(self) -> None:
        sql = "SELECT domain, count() AS n FROM {table} GROUP BY domain ORDER BY n DESC"
        self.assertEqual(_validate_select_sql(sql), sql)

    def test_rejects_non_read_only_sql(self) -> None:
        with self.assertRaises(DatasetToolError):
            _validate_select_sql("DROP TABLE {table}")

    def test_rejects_direct_table_functions(self) -> None:
        with self.assertRaises(DatasetToolError):
            _validate_select_sql("SELECT * FROM url('https://example.com/data.parquet', 'Parquet')")

    def test_rejects_missing_placeholder(self) -> None:
        with self.assertRaises(DatasetToolError):
            _validate_select_sql("SELECT count()")

    def test_allows_format_column_name(self) -> None:
        sql = "SELECT format, count() AS n FROM {table} GROUP BY format"
        self.assertEqual(_validate_select_sql(sql), sql)

    def test_rejects_format_output_clause(self) -> None:
        with self.assertRaises(DatasetToolError):
            _validate_select_sql("SELECT count() FROM {table} FORMAT JSON")


class FullScanPolicyTests(unittest.TestCase):
    def test_unknown_size_is_not_full_scan_safe(self) -> None:
        safe, reason = _full_scan_safe({"num_rows": None, "parquet_bytes": None}, 100, 100)
        self.assertFalse(safe)
        self.assertIn("unknown", reason)

    def test_small_known_split_is_full_scan_safe(self) -> None:
        safe, _ = _full_scan_safe({"num_rows": 10, "parquet_bytes": 20}, 100, 100)
        self.assertTrue(safe)


class DatasetSessionTests(unittest.TestCase):
    def test_touch_preserves_worker_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(dataset_sessions, "SESSION_DIR", pathlib.Path(temp_dir)):
                repo_id = "org/example"
                dataset_sessions.touch_dataset_session(
                    repo_id,
                    interaction="profile:auto",
                    schedule_cleanup=False,
                )
                dataset_sessions.update_dataset_session(
                    repo_id,
                    {"worker": {"ok": True}, "worker_image": "clickhouse/example:latest"},
                )

                session = dataset_sessions.touch_dataset_session(
                    repo_id,
                    interaction="query:default/train",
                    schedule_cleanup=False,
                )

        self.assertEqual(session["worker"], {"ok": True})
        self.assertEqual(session["worker_image"], "clickhouse/example:latest")
        self.assertEqual(session["state"], "active")

    def test_mark_ejected_sets_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(dataset_sessions, "SESSION_DIR", pathlib.Path(temp_dir)):
                session = dataset_sessions.mark_dataset_session_ejected(
                    "org/example",
                    reason="manual_eject",
                    docker_result={"attempted": True},
                )

        self.assertEqual(session["state"], "ejected")
        self.assertEqual(session["eject_reason"], "manual_eject")
        self.assertEqual(session["last_docker_stop"], {"attempted": True})


class AgentToolSurfaceTests(unittest.TestCase):
    def test_root_agent_delegates_clickhouse_to_specialist(self) -> None:
        from backend.tools import ALL_TOOLS, DATASET_ANALYSIS_TOOLS
        from backend.agent import DatasetChatAgent
        from backend.agents.dataset_analysis import DatasetAnalysisAgent

        root_tool_names = {tool.__name__ for tool in ALL_TOOLS}
        runtime_root_tool_names = {
            tool.__name__
            for tool in DatasetChatAgent(session_id="test-root-surface")._tool_functions
        }
        dataset_tool_names = {tool.__name__ for tool in DATASET_ANALYSIS_TOOLS}
        child_tool_names = {
            tool.__name__
            for tool in DatasetAnalysisAgent(session_id="test-surface")._tool_functions
        }

        self.assertNotIn("analyze_hf_dataset", root_tool_names)
        self.assertNotIn("query_hf_dataset_with_clickhouse", root_tool_names)
        self.assertIn("message", runtime_root_tool_names)
        self.assertNotIn("dataset_analysis_agent", runtime_root_tool_names)
        self.assertIn("analyze_hf_dataset", dataset_tool_names)
        self.assertIn("query_hf_dataset_with_clickhouse", dataset_tool_names)
        self.assertIn("preview_hf_dataset", dataset_tool_names)
        self.assertEqual(
            child_tool_names,
            {
                "analyze_hf_dataset",
                "query_hf_dataset_with_clickhouse",
                "preview_hf_dataset",
            },
        )


if __name__ == "__main__":
    unittest.main()
