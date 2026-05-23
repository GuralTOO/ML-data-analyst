"""Local SQLite persistence for agent sessions.

This is intentionally smaller than the Papyrus event sink. It gives the backend
durable conversation history and a queryable run/event log without adding cloud
dependencies.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from typing import Any

from agent_core.core.persistence import deserialize_message, serialize_message


BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_AGENT_STATE_DIR = BACKEND_DIR / "agent_state"
DEFAULT_DB_PATH = DEFAULT_AGENT_STATE_DIR / "agent_sessions.sqlite3"


def _jsonable(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def _short_text(value: Any, limit: int = 1000) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(_jsonable(value))
    return text if len(text) <= limit else text[:limit] + "... [truncated]"


class LocalSQLiteAgentStore:
    """Conversation store plus append-only-ish event/run tables."""

    def __init__(self, db_path: str | pathlib.Path = DEFAULT_DB_PATH):
        self.db_path = pathlib.Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active_runs: dict[str, str] = {}
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Idempotent migration for databases created before the title column
            # existed. ALTER TABLE ADD COLUMN errors if the column is already
            # present — catch the OperationalError and move on.
            try:
                conn.execute("ALTER TABLE chat_sessions ADD COLUMN title TEXT")
            except sqlite3.OperationalError:
                pass
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    history TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (session_id, agent_type)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_session_id TEXT,
                    agent_id TEXT NOT NULL,
                    parent_agent TEXT,
                    agent_type TEXT,
                    prompt TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at REAL,
                    ended_at REAL,
                    result_summary TEXT,
                    token_usage TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    session_id TEXT NOT NULL,
                    agent_session_id TEXT,
                    agent_id TEXT NOT NULL,
                    parent_agent TEXT,
                    agent_type TEXT,
                    event_type TEXT NOT NULL,
                    status TEXT,
                    tool TEXT,
                    tool_call_id TEXT,
                    details TEXT NOT NULL,
                    timestamp REAL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    history TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def record_session(self, session_id: str, *, default_title: str | None = None) -> None:
        """Insert a chat session if missing; refresh updated_at on hit.

        If ``default_title`` is provided and the session has no title yet, it
        is filled in. Existing titles are never overwritten — use
        ``set_session_title`` for explicit renames.
        """
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions (session_id, title)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE
                    SET updated_at = CURRENT_TIMESTAMP,
                        title = COALESCE(chat_sessions.title, excluded.title)
                """,
                (session_id, default_title),
            )

    def set_session_title(self, session_id: str, title: str | None) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions (session_id, title)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE
                    SET title = excluded.title,
                        updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, title),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT session_id, title, created_at, updated_at
                FROM chat_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return one row per root chat session, newest-updated first.

        ``save()`` from agent_core touches chat_sessions for any session id it
        sees — including the synthetic ids assigned to sub-agents
        (``chat_xxx:dataset_analysis:yyy``). Those rows must not appear in
        the sidebar, so we filter to ids without a colon (the format
        guarantees roots have none).
        """
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    cs.session_id,
                    cs.title,
                    cs.created_at,
                    cs.updated_at,
                    (
                        SELECT COUNT(*)
                        FROM agent_runs ar
                        WHERE ar.session_id = cs.session_id
                          AND ar.agent_type = 'dataset_chat'
                    ) AS run_count
                FROM chat_sessions cs
                WHERE cs.session_id NOT LIKE '%:%'
                ORDER BY cs.updated_at DESC
                """,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_root_history(self, session_id: str) -> list[Any]:
        """Convenience alias: load the root agent's conversation history."""
        return self.load(session_id, "dataset_chat")

    def list_sub_agent_sessions(self, chat_session_id: str) -> list[dict[str, Any]]:
        """Return one row per persisted dataset-analysis sub-agent under a chat.

        Sub-agent session ids follow the convention
        ``<chat_session_id>:dataset_analysis:<short_uuid>``, so a prefix glob
        against ``chat_sessions`` enumerates them.
        """
        prefix_glob = f"{chat_session_id}:dataset_analysis:%"
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT session_id, created_at, updated_at
                FROM chat_sessions
                WHERE session_id LIKE ?
                ORDER BY created_at ASC
                """,
                (prefix_glob,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_run(self, session_id: str, agent_type: str) -> dict[str, Any] | None:
        """Most-recent agent_runs row for a (session_id, agent_type) pair."""
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT run_id, status, started_at, ended_at, result_summary, prompt
                FROM agent_runs
                WHERE session_id = ? AND agent_type = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (session_id, agent_type),
            ).fetchone()
        return dict(row) if row else None

    def list_sub_agent_histories(self, chat_session_id: str) -> list[dict[str, Any]]:
        """Return persisted dataset-analysis conversations for one chat.

        Matches every sub-agent under this chat regardless of id convention.
        Older sessions used ``<chat>:dataset_analysis:<uuid>``; the current
        convention is ``<chat>:agent_DS:<repo>``. The agent_type filter is
        what actually scopes us to dataset-analysis specialists, so a
        permissive prefix glob is safe and lets the sidebar rehydrate sessions
        that predate the deterministic naming.
        """
        prefix = f"{chat_session_id}:%"
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT session_id, agent_type, history, updated_at
                FROM conversations
                WHERE session_id LIKE ?
                  AND agent_type = 'dataset_analysis'
                ORDER BY updated_at ASC
                """,
                (prefix,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                history = [
                    deserialize_message(item)
                    for item in json.loads(row["history"])
                ]
            except Exception:
                history = []
            out.append({
                "session_id": row["session_id"],
                "agent_type": row["agent_type"],
                "history": history,
                "updated_at": row["updated_at"],
            })
        return out

    def load(self, session_id: str, agent_type: str) -> list[Any]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT history FROM conversations WHERE session_id = ? AND agent_type = ?",
                (session_id, agent_type),
            ).fetchone()
        if not row:
            return []
        try:
            return [deserialize_message(item) for item in json.loads(row["history"])]
        except Exception:
            return []

    def save(self, session_id: str, agent_type: str, history: list[Any]) -> None:
        serialized = json.dumps([serialize_message(item) for item in history], default=str)
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO conversations (session_id, agent_type, history)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id, agent_type)
                DO UPDATE SET history = excluded.history, updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, agent_type, serialized),
            )
            conn.execute(
                """
                INSERT INTO chat_sessions (session_id)
                VALUES (?)
                ON CONFLICT(session_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                """,
                (session_id,),
            )

    def clear(self, session_id: str, agent_type: str) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                "DELETE FROM conversations WHERE session_id = ? AND agent_type = ?",
                (session_id, agent_type),
            )

    def clear_session(self, session_id: str) -> None:
        """Delete all persisted state for a chat session AND all its sub-agents.

        Sub-agents persist under derived ids prefixed by ``session_id:`` (e.g.
        ``chat_abc:dataset_analysis:f91a20cd``). The cascade matches both the
        exact id and the prefix-with-colon pattern to evict child rows too.
        """
        prefix_glob = f"{session_id}:%"
        with self._lock, self._connection() as conn:
            for table in (
                "conversations",
                "conversation_snapshots",
                "agent_events",
                "agent_runs",
                "chat_sessions",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE session_id = ? OR session_id LIKE ?",
                    (session_id, prefix_glob),
                )

    def save_snapshot(self, session_id: str, agent_type: str, history: list[Any]) -> None:
        serialized = json.dumps([serialize_message(item) for item in history], default=str)
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO conversation_snapshots (session_id, agent_type, history)
                VALUES (?, ?, ?)
                """,
                (session_id, agent_type, serialized),
            )

    def record_event(
        self,
        session_id: str,
        event: Any,
        *,
        agent_session_id: str | None = None,
    ) -> str | None:
        """Persist one agent-core event and maintain a lightweight run row."""
        event_type = _enum_value(getattr(event, "type", None)) or "unknown"
        status = _enum_value(getattr(event, "status", None))
        agent_id = getattr(event, "agent", "") or ""
        parent_agent = getattr(event, "parent_agent", None)
        agent_type = getattr(event, "agent_type", None)
        details = getattr(event, "details", None) or {}
        timestamp = getattr(event, "timestamp", None)

        with self._lock:
            run_id = self._active_runs.get(agent_id)
            if event_type == "agent_start":
                run_id = str(uuid.uuid4())
                self._active_runs[agent_id] = run_id
            details_json = json.dumps(_jsonable(details), ensure_ascii=True)
            with self._connection() as conn:
                if event_type == "agent_start" and run_id:
                    conn.execute(
                        """
                        INSERT INTO agent_runs (
                            run_id, session_id, agent_session_id, agent_id,
                            parent_agent, agent_type, prompt, status, started_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
                        """,
                        (
                            run_id,
                            session_id,
                            agent_session_id,
                            agent_id,
                            parent_agent,
                            agent_type,
                            _short_text(details.get("prompt")),
                            timestamp,
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO agent_events (
                        run_id, session_id, agent_session_id, agent_id,
                        parent_agent, agent_type, event_type, status, tool,
                        tool_call_id, details, timestamp
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        session_id,
                        agent_session_id,
                        agent_id,
                        parent_agent,
                        agent_type,
                        event_type,
                        status,
                        getattr(event, "tool", None),
                        getattr(event, "tool_call_id", None),
                        details_json,
                        timestamp,
                    ),
                )
                if event_type == "agent_end" and run_id:
                    token_usage = details.get("token_usage")
                    conn.execute(
                        """
                        UPDATE agent_runs
                           SET status = ?,
                               ended_at = ?,
                               result_summary = ?,
                               token_usage = ?
                         WHERE run_id = ?
                        """,
                        (
                            "failed" if status == "failed" else "completed",
                            timestamp,
                            _short_text(details.get("result") or details.get("error")),
                            json.dumps(_jsonable(token_usage), ensure_ascii=True)
                            if token_usage
                            else None,
                            run_id,
                        ),
                    )
                    self._active_runs.pop(agent_id, None)
        return run_id

    def count_rows(self, table: str) -> int:
        if table not in {
            "chat_sessions",
            "conversations",
            "agent_runs",
            "agent_events",
            "conversation_snapshots",
        }:
            raise ValueError(f"unsupported table: {table}")
        with self._lock, self._connection() as conn:
            row = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()
            return int(row["n"])


agent_store = LocalSQLiteAgentStore()
