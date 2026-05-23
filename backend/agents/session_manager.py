"""Thread-safe root-agent session management."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.agent import DatasetChatAgent
from backend.agents.persistence import LocalSQLiteAgentStore, agent_store


@dataclass
class ManagedAgentSession:
    session_id: str
    agent: Any
    conversation_store: LocalSQLiteAgentStore
    lock: threading.RLock


class DatasetChatSessionManager:
    """Cache root agents and serialize turns per user-facing session."""

    def __init__(
        self,
        *,
        store: LocalSQLiteAgentStore = agent_store,
        agent_factory: Callable[..., Any] = DatasetChatAgent,
    ) -> None:
        self._store = store
        self._agent_factory = agent_factory
        self._sessions: dict[str, ManagedAgentSession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> ManagedAgentSession:
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing

            self._store.record_session(session_id)
            managed = ManagedAgentSession(
                session_id=session_id,
                agent=self._agent_factory(
                    session_id=session_id,
                    conversation_store=self._store,
                ),
                conversation_store=self._store,
                lock=threading.RLock(),
            )
            self._sessions[session_id] = managed
            return managed

    def lock_for(self, session_id: str) -> threading.RLock:
        return self.get_or_create(session_id).lock

    def clear(self, session_id: str, *, clear_persistent: bool = False) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
        if clear_persistent:
            self._store.clear_session(session_id)

    def clear_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)


dataset_chat_sessions = DatasetChatSessionManager()
