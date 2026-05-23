"""Map active agent instances to chat sessions.

The agent-core event bus emits only agent instance IDs. The HTTP stream needs a
small registry so events from root agents and any nested sub-agents can be
routed back to the user-facing chat session.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


_MAX_AGE_SECONDS = 60 * 60


@dataclass(frozen=True)
class AgentRegistryEntry:
    chat_session_id: str
    agent_session_id: str | None
    parent_agent: str | None
    turn_id: str | None
    registered_at: float


_lock = threading.Lock()
_entries: dict[str, AgentRegistryEntry] = {}
_agents: dict[str, Any] = {}


def _sweep(now: float | None = None) -> None:
    """Drop leaked entries. Caller must hold _lock."""
    cutoff = (now if now is not None else time.monotonic()) - _MAX_AGE_SECONDS
    stale = [
        instance_id
        for instance_id, entry in _entries.items()
        if entry.registered_at < cutoff
    ]
    for instance_id in stale:
        _entries.pop(instance_id, None)
        _agents.pop(instance_id, None)


def register(
    instance_id: str,
    chat_session_id: str,
    *,
    agent: Any = None,
    agent_session_id: str | None = None,
    parent_agent: str | None = None,
    turn_id: str | None = None,
) -> None:
    """Register an active agent instance for event routing."""
    if not instance_id or not chat_session_id:
        return
    with _lock:
        _sweep()
        _entries[instance_id] = AgentRegistryEntry(
            chat_session_id=chat_session_id,
            agent_session_id=agent_session_id,
            parent_agent=parent_agent,
            turn_id=turn_id,
            registered_at=time.monotonic(),
        )
        if agent is not None:
            _agents[instance_id] = agent


def unregister(instance_id: str) -> AgentRegistryEntry | None:
    """Remove an active agent instance."""
    with _lock:
        entry = _entries.pop(instance_id, None)
        _agents.pop(instance_id, None)
        return entry


def lookup(instance_id: str | None) -> str | None:
    """Return the chat session for an agent instance, if active."""
    if not instance_id:
        return None
    with _lock:
        entry = _entries.get(instance_id)
        return entry.chat_session_id if entry else None


def get_entry(instance_id: str | None) -> AgentRegistryEntry | None:
    """Return metadata for an agent instance, if active."""
    if not instance_id:
        return None
    with _lock:
        return _entries.get(instance_id)


def get_agent(instance_id: str | None):
    """Return the live agent object for an instance, if registered."""
    if not instance_id:
        return None
    with _lock:
        return _agents.get(instance_id)


def find_agent_by_session(chat_session_id: str):
    """Return any active agent object for a chat session."""
    with _lock:
        for instance_id, entry in _entries.items():
            if entry.chat_session_id == chat_session_id:
                agent = _agents.get(instance_id)
                if agent is not None:
                    return agent
    return None


def clear() -> None:
    """Clear all registry state. Primarily for tests and shutdown."""
    with _lock:
        _entries.clear()
        _agents.clear()
