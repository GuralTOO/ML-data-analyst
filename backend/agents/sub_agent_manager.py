"""Persistent dataset-analysis sub-agent session management.

Each dataset actor is scoped to one root chat session and one Hugging Face
dataset. The stable sub-session id is:

    <chat_session_id>:agent_DS:<normalized_repo_id>

Both the root agent and the user-facing HTTP endpoint address the same cached
agent through this id, so turns append to one conversation instead of creating
throwaway child agents.
"""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


_HF_DATASET_URL_RE = re.compile(
    r"https?://(?:www\.)?huggingface\.co/datasets/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
)
_HF_REPO_ID_RE = re.compile(
    r"(?:DS:)?\b([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)\b"
)


def normalize_repo_id(repo_id: str | None) -> str | None:
    """Return a stable lock key for a Hugging Face dataset repo id."""
    if not repo_id:
        return None
    value = str(repo_id).strip().strip("`'\"")
    if value.startswith("DS:"):
        value = value[3:]
    url_match = _HF_DATASET_URL_RE.search(value)
    if url_match:
        value = url_match.group(1)
    value = value.split("?", 1)[0].split("#", 1)[0].strip("/")
    match = _HF_REPO_ID_RE.search(value)
    if not match:
        return None
    return match.group(1).lower()


def dataset_agent_name(repo_id: str) -> str:
    dataset_key = normalize_repo_id(repo_id)
    if not dataset_key:
        raise ValueError(f"invalid Hugging Face dataset repo_id: {repo_id!r}")
    return f"agent_DS:{dataset_key}"


def dataset_sub_session_id(chat_session_id: str, repo_id: str) -> str:
    if not chat_session_id:
        raise ValueError("chat_session_id is required for dataset sub-agents")
    return f"{chat_session_id}:{dataset_agent_name(repo_id)}"


def extract_repo_id(value: Any) -> str | None:
    """Best-effort repo id extraction from a task string or context JSON."""
    if value is None:
        return None
    if isinstance(value, str):
        url_match = _HF_DATASET_URL_RE.search(value)
        if url_match:
            return url_match.group(1)
        match = _HF_REPO_ID_RE.search(value)
        return match.group(1) if match else None
    if isinstance(value, dict):
        priority_keys = ("repo_id", "dataset_id", "dataset")
        for key in priority_keys:
            repo_id = extract_repo_id(value.get(key))
            if repo_id:
                return repo_id
        for nested in value.values():
            repo_id = extract_repo_id(nested)
            if repo_id:
                return repo_id
    if isinstance(value, (list, tuple)):
        for nested in value:
            repo_id = extract_repo_id(nested)
            if repo_id:
                return repo_id
    return None


# Matches the canonical task prompt shape the root agent constructs:
#   "Context:\n{...JSON...}\n\nTask: <free text>"
# Group 1 captures the JSON block; group 2 captures the trailing task text.
_TASK_PROMPT_RE = re.compile(
    r"^\s*Context:\s*(\{.*?\})\s*\n\s*\n\s*Task:\s*(.*)$",
    re.DOTALL,
)
# "Task: org/name" without preceding context — used as a secondary hit.
_TASK_REPO_RE = re.compile(
    r"Task:\s+([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)\b"
)


def extract_repo_id_from_prompt(text: str | None) -> str | None:
    """Robust repo-id extraction for the task prompts the root agent emits.

    The loose ``extract_repo_id`` regex matches any ``slash/path`` token and
    is brittle when the prompt embeds JSON that contains paths like
    ``data/astronomy.parquet`` or letter sequences like multi-choice options
    (``A/B/C/D``). This helper prefers structured signals:

    1. Parse the ``Context:\n{...}\n\nTask:`` JSON block and recurse via
       ``extract_repo_id`` so the dict-aware path finds ``repo_id`` etc.
    2. Look for a ``Task: org/name`` prefix on the trailing task line.
    3. Fall back to the loose regex over the whole string.
    """
    if not text:
        return None
    import json as _json

    m = _TASK_PROMPT_RE.match(text)
    if m:
        try:
            ctx = _json.loads(m.group(1))
        except _json.JSONDecodeError:
            ctx = None
        if ctx is not None:
            from_ctx = extract_repo_id(ctx)
            if from_ctx:
                return from_ctx
        task_line = m.group(2).strip()
        head = task_line.split()[0].rstrip(":,.;") if task_line else ""
        if "/" in head and _HF_REPO_ID_RE.fullmatch(head):
            return head

    m = _TASK_REPO_RE.search(text)
    if m:
        return m.group(1)

    return extract_repo_id(text)


@dataclass
class ManagedSubAgentSession:
    sub_session_id: str
    chat_session_id: str
    agent: Any
    lock: threading.RLock
    repo_id: str | None = None
    dataset_key: str | None = None
    task: str | None = None


@dataclass
class LiveSubAgentState:
    sub_session_id: str
    chat_session_id: str
    repo_id: str | None = None
    status: str = "running"
    opened_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    completed_at_ms: int | None = None
    prompt: str = ""
    result: str = ""
    error: str | None = None
    current_activity: list[dict[str, Any]] = field(default_factory=list)
    current_text: str = ""


class SubAgentSessionManager:
    """Cache dataset-analysis sub-agents and serialize turns per sub-agent.

    Two requests against the *same* sub-agent are serialized so its history
    can't be mutated concurrently. Separate dataset-work locks below also
    serialize different sub-agents that address the same dataset.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ManagedSubAgentSession] = {}
        self._live_states: dict[str, LiveSubAgentState] = {}
        self._lock = threading.Lock()

    def register(
        self,
        *,
        sub_session_id: str,
        chat_session_id: str,
        agent: Any,
        repo_id: str | None = None,
        task: str | None = None,
    ) -> ManagedSubAgentSession:
        """Insert a sub-agent into the cache. Idempotent — re-register updates
        the metadata but keeps the existing agent/lock so an in-flight turn
        is not disturbed."""
        with self._lock:
            existing = self._sessions.get(sub_session_id)
            if existing is not None:
                if repo_id and not existing.repo_id:
                    existing.repo_id = repo_id
                    existing.dataset_key = normalize_repo_id(repo_id)
                if task and not existing.task:
                    existing.task = task
                return existing
            managed = ManagedSubAgentSession(
                sub_session_id=sub_session_id,
                chat_session_id=chat_session_id,
                agent=agent,
                lock=threading.RLock(),
                repo_id=repo_id,
                dataset_key=normalize_repo_id(repo_id),
                task=task,
            )
            self._sessions[sub_session_id] = managed
            return managed

    def get_or_create_dataset_agent(
        self,
        *,
        chat_session_id: str,
        repo_id: str,
        agent_factory,
        task: str | None = None,
    ) -> tuple[ManagedSubAgentSession, bool]:
        """Return the persistent dataset agent for (chat_session_id, repo_id).

        ``agent_factory`` is called with the deterministic sub-session id only
        when no in-memory agent exists yet. The stable id lets the new agent
        reload prior conversation from the durable conversation store.
        """
        sub_session_id = dataset_sub_session_id(chat_session_id, repo_id)
        dataset_key = normalize_repo_id(repo_id)
        with self._lock:
            existing = self._sessions.get(sub_session_id)
            if existing is not None:
                if task and not existing.task:
                    existing.task = task
                if repo_id and not existing.repo_id:
                    existing.repo_id = repo_id
                existing.dataset_key = existing.dataset_key or dataset_key
                return existing, False
            agent = agent_factory(sub_session_id)
            managed = ManagedSubAgentSession(
                sub_session_id=sub_session_id,
                chat_session_id=chat_session_id,
                agent=agent,
                lock=threading.RLock(),
                repo_id=repo_id,
                dataset_key=dataset_key,
                task=task,
            )
            self._sessions[sub_session_id] = managed
            return managed, True

    def get_by_dataset(
        self,
        *,
        chat_session_id: str,
        repo_id: str,
    ) -> ManagedSubAgentSession | None:
        return self.get(dataset_sub_session_id(chat_session_id, repo_id))

    def get(self, sub_session_id: str) -> ManagedSubAgentSession | None:
        with self._lock:
            return self._sessions.get(sub_session_id)

    def list_for_chat(self, chat_session_id: str) -> list[ManagedSubAgentSession]:
        """Return all cached sub-agents under one chat session."""
        prefix = f"{chat_session_id}:"
        with self._lock:
            return [
                managed
                for sid, managed in self._sessions.items()
                if managed.chat_session_id == chat_session_id or sid.startswith(prefix)
            ]

    def live_state(self, sub_session_id: str) -> LiveSubAgentState | None:
        with self._lock:
            return self._live_states.get(sub_session_id)

    def record_frame(self, sub_session_id: str, frame: dict[str, Any]) -> None:
        """Remember the latest visible UI state for a dataset sub-agent."""
        frame_type = frame.get("type")
        payload = frame.get("payload") or {}
        if not sub_session_id or payload.get("agent_type") != "dataset_analysis":
            return
        now_ms = int(time.time() * 1000)
        with self._lock:
            managed = self._sessions.get(sub_session_id)
            state = self._live_states.get(sub_session_id)
            if state is None:
                state = LiveSubAgentState(
                    sub_session_id=sub_session_id,
                    chat_session_id=managed.chat_session_id if managed else sub_session_id.split(":", 1)[0],
                    repo_id=(managed.repo_id if managed else extract_repo_id(sub_session_id)),
                    opened_at_ms=now_ms,
                )
                self._live_states[sub_session_id] = state

            if frame_type == "agent_start":
                state.status = "running"
                state.completed_at_ms = None
                state.error = None
                state.prompt = payload.get("prompt") or state.prompt
                state.repo_id = state.repo_id or extract_repo_id(state.prompt) or extract_repo_id(sub_session_id)
                state.current_activity = []
                state.current_text = ""
                return

            if frame_type == "tool_start":
                state.status = "running"
                tool_id = payload.get("id")
                if tool_id and any(item.get("id") == tool_id for item in state.current_activity):
                    return
                state.current_activity.append({
                    "id": tool_id,
                    "tool": payload.get("tool"),
                    "args_hint": payload.get("args_hint"),
                    "status": "running",
                    "started_at": now_ms,
                })
                return

            if frame_type == "tool_end":
                tool_id = payload.get("id")
                updated = False
                for item in state.current_activity:
                    if item.get("id") == tool_id:
                        item.update({
                            "status": payload.get("status") or "success",
                            "result_summary": payload.get("result_summary"),
                            "error": payload.get("error"),
                            "duration_ms": now_ms - int(item.get("started_at") or now_ms),
                        })
                        updated = True
                        break
                if not updated:
                    state.current_activity.append({
                        "id": tool_id,
                        "tool": payload.get("tool"),
                        "status": payload.get("status") or "success",
                        "result_summary": payload.get("result_summary"),
                        "error": payload.get("error"),
                        "started_at": now_ms,
                    })
                return

            if frame_type == "text_delta":
                state.current_text += payload.get("delta") or ""
                return

            if frame_type == "agent_end":
                success = payload.get("success") is not False
                state.status = "success" if success else "error"
                state.completed_at_ms = now_ms
                state.result = payload.get("result") or state.result
                state.error = payload.get("error")
                state.current_activity = []
                state.current_text = ""

    def clear(self, sub_session_id: str) -> None:
        with self._lock:
            self._sessions.pop(sub_session_id, None)
            self._live_states.pop(sub_session_id, None)

    def clear_for_chat(self, chat_session_id: str) -> int:
        """Evict every sub-agent under one chat session.

        Cascaded when the chat session itself is deleted so we don't leak the
        underlying agent instances. Returns the eviction count.
        """
        prefix = f"{chat_session_id}:"
        with self._lock:
            stale = [
                sid
                for sid, managed in self._sessions.items()
                if managed.chat_session_id == chat_session_id or sid.startswith(prefix)
            ]
            for sid in stale:
                self._sessions.pop(sid, None)
                self._live_states.pop(sid, None)
            return len(stale)

    def clear_all(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._live_states.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


@dataclass
class ActiveDatasetWork:
    dataset_key: str
    repo_id: str
    chat_session_id: str | None
    sub_session_id: str | None
    started_at: float


class DatasetWorkLockManager:
    """Serialize dataset-analysis turns that address the same HF dataset."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._active: dict[str, ActiveDatasetWork] = {}
        self._lock = threading.Lock()

    def lock_for(self, repo_id: str) -> threading.RLock:
        dataset_key = normalize_repo_id(repo_id)
        if not dataset_key:
            raise ValueError(f"invalid Hugging Face dataset repo_id: {repo_id!r}")
        with self._lock:
            lock = self._locks.get(dataset_key)
            if lock is None:
                lock = threading.RLock()
                self._locks[dataset_key] = lock
            return lock

    @contextmanager
    def acquire(
        self,
        repo_id: str,
        *,
        chat_session_id: str | None = None,
        sub_session_id: str | None = None,
    ) -> Iterator[ActiveDatasetWork]:
        dataset_key = normalize_repo_id(repo_id)
        if not dataset_key:
            raise ValueError(f"invalid Hugging Face dataset repo_id: {repo_id!r}")
        lock = self.lock_for(repo_id)
        lock.acquire()
        active = ActiveDatasetWork(
            dataset_key=dataset_key,
            repo_id=repo_id,
            chat_session_id=chat_session_id,
            sub_session_id=sub_session_id,
            started_at=time.monotonic(),
        )
        with self._lock:
            self._active[dataset_key] = active
        try:
            yield active
        finally:
            with self._lock:
                if self._active.get(dataset_key) is active:
                    self._active.pop(dataset_key, None)
            lock.release()

    def active_for(self, repo_id: str) -> ActiveDatasetWork | None:
        dataset_key = normalize_repo_id(repo_id)
        if not dataset_key:
            return None
        with self._lock:
            return self._active.get(dataset_key)

    def clear(self) -> None:
        with self._lock:
            self._active.clear()
            self._locks.clear()


sub_agent_sessions = SubAgentSessionManager()
dataset_work_locks = DatasetWorkLockManager()
