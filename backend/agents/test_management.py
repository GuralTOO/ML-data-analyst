from __future__ import annotations

import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_core import Event, EventStatus, EventType

from backend.agents import agent_registry
from backend.agents.dataset_analysis import make_dataset_message_tool
from backend.agents.persistence import LocalSQLiteAgentStore
from backend.agents.session_manager import DatasetChatSessionManager
from backend.agents.sub_agent_manager import (
    DatasetWorkLockManager,
    dataset_sub_session_id,
    extract_repo_id,
    sub_agent_sessions,
)


class FakeAgent:
    def __init__(self, *, session_id: str, conversation_store=None):
        self.session_id = session_id
        self.conversation_store = conversation_store
        self.instance_id = f"fake_{session_id}"
        self.name = "dataset_chat"


class SessionManagerTests(unittest.TestCase):
    def test_same_session_lock_serializes_turns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalSQLiteAgentStore(pathlib.Path(temp_dir) / "agents.sqlite3")
            manager = DatasetChatSessionManager(store=store, agent_factory=FakeAgent)
            lock = manager.lock_for("chat-a")
            first_started = threading.Event()
            order: list[str] = []

            def first() -> None:
                with lock:
                    order.append("first-start")
                    first_started.set()
                    time.sleep(0.05)
                    order.append("first-end")

            def second() -> None:
                first_started.wait(timeout=1)
                with manager.lock_for("chat-a"):
                    order.append("second")

            threads = [threading.Thread(target=first), threading.Thread(target=second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=1)

        self.assertEqual(order, ["first-start", "first-end", "second"])

    def test_different_session_locks_do_not_block_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalSQLiteAgentStore(pathlib.Path(temp_dir) / "agents.sqlite3")
            manager = DatasetChatSessionManager(store=store, agent_factory=FakeAgent)
            lock_a = manager.lock_for("chat-a")
            lock_b = manager.lock_for("chat-b")

            lock_a.acquire()
            try:
                acquired_b = lock_b.acquire(blocking=False)
                if acquired_b:
                    lock_b.release()
            finally:
                lock_a.release()

        self.assertTrue(acquired_b)


class DatasetAnalysisDelegationTests(unittest.TestCase):
    def tearDown(self) -> None:
        agent_registry.clear()
        sub_agent_sessions.clear_all()

    def test_message_tool_reuses_persistent_dataset_child(self) -> None:
        created = []

        class FakeChild:
            name = "dataset_analysis"

            def __init__(
                self,
                *,
                session_id: str,
                conversation_store=None,
                parent_agent=None,
                cancel_event=None,
            ):
                self.session_id = session_id
                self.conversation_store = conversation_store
                self.parent_agent = parent_agent
                self.cancel_event = cancel_event
                self.instance_id = f"child_{len(created)}"
                created.append(self)

            def run(self, task: str) -> str:
                entry = agent_registry.get_entry(self.instance_id)
                assert entry is not None
                assert entry.agent_session_id == self.session_id
                assert entry.turn_id == "turn-1"
                return f"{self.session_id}:{task}"

        with patch("backend.agents.dataset_analysis.DatasetAnalysisAgent", FakeChild):
            tool = make_dataset_message_tool(
                session_id="chat-1",
                parent_agent="dataset_chat_chat-1",
                turn_id_getter=lambda: "turn-1",
            )
            first = tool("agent_DS:org/a", "analyze repo")
            second = tool("agent_DS:org/a", "follow up")
            third = tool("agent_DS:org/b", "analyze another")

        self.assertEqual(len(created), 2)
        self.assertEqual(created[0].session_id, dataset_sub_session_id("chat-1", "org/a"))
        self.assertEqual(created[1].session_id, dataset_sub_session_id("chat-1", "org/b"))
        self.assertEqual(created[0].parent_agent, "dataset_chat_chat-1")
        self.assertIn("You are analyzing dataset org/a.", first)
        self.assertIn("follow up", second)
        self.assertNotIn("Context:", second)
        self.assertIn("You are analyzing dataset org/b.", third)
        self.assertIsNone(agent_registry.lookup(created[0].instance_id))
        self.assertIsNone(agent_registry.lookup(created[1].instance_id))


class DatasetWorkLockTests(unittest.TestCase):
    def test_extract_repo_id_from_task_context_and_url(self) -> None:
        self.assertEqual(extract_repo_id("analyze DS:Org/Data-1"), "Org/Data-1")
        self.assertEqual(extract_repo_id("agent_DS:Org/Data-1"), "Org/Data-1")
        self.assertEqual(
            extract_repo_id("https://huggingface.co/datasets/Org/Data-1/viewer/default/train"),
            "Org/Data-1",
        )
        self.assertEqual(
            extract_repo_id({"metadata": {"repo_id": "OtherOrg/OtherData"}}),
            "OtherOrg/OtherData",
        )

    def test_same_dataset_lock_serializes_work(self) -> None:
        manager = DatasetWorkLockManager()
        first_started = threading.Event()
        order: list[str] = []

        def first() -> None:
            with manager.acquire("Org/Data"):
                order.append("first-start")
                first_started.set()
                time.sleep(0.05)
                order.append("first-end")

        def second() -> None:
            first_started.wait(timeout=1)
            with manager.acquire("org/data"):
                order.append("second")

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertEqual(order, ["first-start", "first-end", "second"])

    def test_different_dataset_locks_do_not_block_each_other(self) -> None:
        manager = DatasetWorkLockManager()
        lock_a = manager.lock_for("org/a")
        lock_b = manager.lock_for("org/b")

        lock_a.acquire()
        try:
            acquired_b = lock_b.acquire(blocking=False)
            if acquired_b:
                lock_b.release()
        finally:
            lock_a.release()

        self.assertTrue(acquired_b)

    def test_sub_agent_live_state_tracks_visible_activity(self) -> None:
        sub_agent_sessions.clear_all()
        try:
            sub_agent_sessions.register(
                sub_session_id="chat-1:agent_DS:org/data",
                chat_session_id="chat-1",
                agent=object(),
                repo_id="org/data",
                task="analyze",
            )
            sub_agent_sessions.record_frame(
                "chat-1:agent_DS:org/data",
                {
                    "type": "agent_start",
                    "payload": {
                        "agent_type": "dataset_analysis",
                        "agent_session_id": "chat-1:agent_DS:org/data",
                        "prompt": "You are analyzing dataset org/data.\n\nAnalyze it.",
                    },
                },
            )
            sub_agent_sessions.record_frame(
                "chat-1:agent_DS:org/data",
                {
                    "type": "tool_start",
                    "payload": {
                        "agent_type": "dataset_analysis",
                        "agent_session_id": "chat-1:agent_DS:org/data",
                        "id": "tool-1",
                        "tool": "analyze_hf_dataset",
                        "args_hint": "org/data",
                    },
                },
            )

            live = sub_agent_sessions.live_state("chat-1:agent_DS:org/data")

            self.assertIsNotNone(live)
            self.assertEqual(live.status, "running")
            self.assertEqual(live.repo_id, "org/data")
            self.assertEqual(live.current_activity[0]["tool"], "analyze_hf_dataset")
        finally:
            sub_agent_sessions.clear_all()


class AgentRegistryTests(unittest.TestCase):
    def tearDown(self) -> None:
        agent_registry.clear()

    def test_registry_routes_nested_agents_to_chat_session(self) -> None:
        agent_registry.register("root", "chat-1", agent_session_id="chat-1")
        agent_registry.register(
            "child",
            "chat-1",
            agent_session_id="chat-1:agent_DS:org/data",
            parent_agent="root",
        )
        agent_registry.register(
            "grandchild",
            "chat-1",
            agent_session_id="chat-1:agent_DS:org/data:worker",
            parent_agent="child",
        )

        self.assertEqual(agent_registry.lookup("root"), "chat-1")
        self.assertEqual(agent_registry.lookup("child"), "chat-1")
        self.assertEqual(agent_registry.lookup("grandchild"), "chat-1")
        self.assertEqual(
            agent_registry.get_entry("grandchild").agent_session_id,
            "chat-1:agent_DS:org/data:worker",
        )


class LocalPersistenceTests(unittest.TestCase):
    def test_store_persists_conversation_events_runs_and_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalSQLiteAgentStore(pathlib.Path(temp_dir) / "agents.sqlite3")
            store.record_session("chat-1")
            history = [{"role": "user", "content": "hello"}]
            store.save("chat-1", "dataset_chat", history)
            store.save_snapshot("chat-1", "dataset_chat", history)

            start = Event(
                type=EventType.AGENT_START,
                agent="agent-1",
                agent_type="dataset_chat",
                details={"prompt": "hello"},
                timestamp=1.0,
            )
            end = Event(
                type=EventType.AGENT_END,
                agent="agent-1",
                agent_type="dataset_chat",
                status=EventStatus.COMPLETED,
                details={
                    "result": "done",
                    "token_usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
                timestamp=2.0,
            )
            store.record_event("chat-1", start, agent_session_id="chat-1")
            store.record_event("chat-1", end, agent_session_id="chat-1")

            loaded = store.load("chat-1", "dataset_chat")
            row_counts = {
                table: store.count_rows(table)
                for table in [
                    "chat_sessions",
                    "conversations",
                    "conversation_snapshots",
                    "agent_runs",
                    "agent_events",
                ]
            }

        self.assertEqual(loaded, history)
        self.assertEqual(row_counts["chat_sessions"], 1)
        self.assertEqual(row_counts["conversations"], 1)
        self.assertEqual(row_counts["conversation_snapshots"], 1)
        self.assertEqual(row_counts["agent_runs"], 1)
        self.assertEqual(row_counts["agent_events"], 2)


if __name__ == "__main__":
    unittest.main()
