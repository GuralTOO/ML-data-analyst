"""FastAPI HTTP wrapper around DatasetChatAgent.

Exposes a Server-Sent Events stream for the React frontend (frontend/src/lib/api.js).

Run:
    .venv/bin/python -m backend.server                    # localhost:5001
    .venv/bin/uvicorn backend.server:app --port 5001      # with --reload, etc.

The Vite dev server proxies /api/* → http://localhost:5001 (see vite.config.js).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

# Load .env from project root BEFORE importing the agent (which reads keys at construction time)
load_dotenv()

# Required keys — fail fast at import, mirroring main.py
_REQUIRED = ["OPEN_ROUTER_API_KEY", "NIMBLE_API_KEY", "HF_TOKEN"]
_missing = [k for k in _REQUIRED if not os.environ.get(k)]
if _missing:
    sys.exit(f"Missing env vars: {_missing}")

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_core import Event, EventType, get_event_bus

from backend.agents import agent_registry
from backend.agents.dataset_analysis import DatasetAnalysisAgent
from backend.agents.persistence import agent_store
from backend.agents.session_manager import dataset_chat_sessions
from backend.agents.sub_agent_manager import (
    dataset_work_locks,
    dataset_sub_session_id,
    extract_repo_id,
    extract_repo_id_from_prompt,
    sub_agent_sessions,
)

logger = logging.getLogger("backend.server")
# Quiet noisy framework chatter so our own logs are readable
logging.getLogger("agent_core").setLevel(logging.WARNING)


# ---- Lifespan ----

@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("server starting; backend ready")
    yield
    logger.info("server shutting down; closing sessions")
    dataset_chat_sessions.clear_all()
    sub_agent_sessions.clear_all()
    dataset_work_locks.clear()
    agent_registry.clear()


app = FastAPI(title="dataset-finder", lifespan=lifespan)

# Dev convenience: allow the Vite dev origin even when the proxy isn't in play
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4000", "http://localhost:4001"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Session Management ----
#
# Root agents are cached per chat session and backed by local SQLite
# conversation storage. A per-session lock serializes turns so one agent history
# cannot be mutated by two requests at the same time.


# ---- Request/response shapes ----

class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None


class RenameRequest(BaseModel):
    title: str | None = None


_SESSION_ID_PREFIX = "chat"
_AUTO_TITLE_MAX_LEN = 80


def _new_session_id() -> str:
    """Mint a session id of the form chat_<hex8>. Short enough to read in logs."""
    return f"{_SESSION_ID_PREFIX}_{uuid.uuid4().hex[:12]}"


def _derive_title(query: str) -> str:
    """First line of the user's opening message, capped to ~80 chars."""
    first_line = (query or "").strip().splitlines()[0] if query else ""
    if len(first_line) <= _AUTO_TITLE_MAX_LEN:
        return first_line
    return first_line[: _AUTO_TITLE_MAX_LEN - 1].rstrip() + "…"


def _history_to_ui_messages(history: list[Any]) -> list[dict[str, Any]]:
    """Reduce an OpenAI-style history into the UI's user/assistant pairs.

    Drops the system prompt, tool result messages, and intermediate assistant
    iterations whose only content is tool_calls. The UI doesn't yet rehydrate
    tool-call activity on past turns — that arrives with the sub-agent work.
    """
    ui: list[dict[str, Any]] = []
    for idx, msg in enumerate(history):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "user" and isinstance(content, str) and content.strip():
            ui.append({"id": f"msg_{idx}", "role": "user", "content": content})
        elif role == "assistant" and isinstance(content, str) and content.strip():
            ui.append({"id": f"msg_{idx}", "role": "assistant", "content": content, "activity": []})
    return ui


def _sub_agents_to_ui(session_id: str) -> list[dict[str, Any]]:
    """Return cached/persisted dataset agents for session rehydration."""
    by_id: dict[str, dict[str, Any]] = {}

    def _apply_live_state(sub_session_id: str, base: dict[str, Any]) -> dict[str, Any]:
        live = sub_agent_sessions.live_state(sub_session_id)
        if live is None:
            return base
        messages = list(base.get("messages") or [])
        if live.prompt:
            last = messages[-1] if messages else None
            if not (last and last.get("role") == "user" and last.get("content") == live.prompt):
                messages.append({
                    "id": f"live_{sub_session_id}_u",
                    "role": "user",
                    "content": live.prompt,
                })
        if live.status != "running" and live.result:
            last = messages[-1] if messages else None
            if not (last and last.get("role") == "assistant" and last.get("content") == live.result):
                messages.append({
                    "id": f"live_{sub_session_id}_a",
                    "role": "assistant",
                    "content": live.result,
                    "activity": [],
                })
        return {
            **base,
            "repoId": base.get("repoId") or live.repo_id or extract_repo_id(sub_session_id) or sub_session_id,
            "task": base.get("task") or live.prompt,
            "status": live.status,
            "openedAt": live.opened_at_ms,
            "completedAt": live.completed_at_ms,
            "messages": messages,
            "currentActivity": live.current_activity if live.status == "running" else [],
            "currentText": live.current_text if live.status == "running" else "",
        }

    for row in agent_store.list_sub_agent_histories(session_id):
        sub_session_id = row["session_id"]
        messages = _history_to_ui_messages(row.get("history") or [])
        first_user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        # Old-format ids (`<chat>:dataset_analysis:<uuid>`) don't carry the
        # repo_id. The first user message is the task prompt which embeds a
        # JSON context block + a "Task: org/name …" preamble — the smarter
        # parser pulls the right slug from there (loose regex picks up
        # spurious paths like `data/foo.parquet` from the JSON otherwise).
        repo_id = (
            extract_repo_id(sub_session_id)
            or extract_repo_id_from_prompt(first_user)
            or sub_session_id.split(":")[-1]
        )
        by_id[sub_session_id] = {
            "id": sub_session_id,
            "repoId": repo_id,
            "task": first_user,
            "status": "success",
            "openedAt": row.get("updated_at"),
            "completedAt": row.get("updated_at"),
            "messages": messages,
            "currentActivity": [],
            "currentText": "",
        }
        by_id[sub_session_id] = _apply_live_state(sub_session_id, by_id[sub_session_id])
    for managed in sub_agent_sessions.list_for_chat(session_id):
        history = agent_store.load(managed.sub_session_id, "dataset_analysis")
        messages = _history_to_ui_messages(history)
        first_user = next((m["content"] for m in messages if m.get("role") == "user"), managed.task or "")
        by_id[managed.sub_session_id] = {
            **by_id.get(managed.sub_session_id, {}),
            "id": managed.sub_session_id,
            "repoId": managed.repo_id or extract_repo_id(managed.sub_session_id) or managed.sub_session_id,
            "task": first_user,
            "status": "success",
            "messages": messages,
            "currentActivity": [],
            "currentText": "",
        }
        by_id[managed.sub_session_id] = _apply_live_state(
            managed.sub_session_id,
            by_id[managed.sub_session_id],
        )
    return list(by_id.values())


def _sub_agent_to_ui(session_id: str, sub_session_id: str) -> dict[str, Any] | None:
    """Return one sub-agent entry in the same shape as session rehydration."""
    for item in _sub_agents_to_ui(session_id):
        if item.get("id") == sub_session_id:
            return item
    return None


def _restore_dataset_sub_agent(chat_id: str, sub_session_id: str):
    """Resolve or recreate a persistent dataset-analysis sub-agent by stable id."""
    if not sub_session_id.startswith(f"{chat_id}:"):
        raise HTTPException(status_code=400, detail="sub_session_id does not belong to chat_id")

    managed = sub_agent_sessions.get(sub_session_id)
    if managed is not None:
        return managed

    # Try to resolve the dataset from the id (new format) first.
    repo_id = extract_repo_id(sub_session_id)
    expected_id = dataset_sub_session_id(chat_id, repo_id) if repo_id else None

    # Old-format ids (`<chat>:dataset_analysis:<uuid>`) don't carry the repo
    # in the id at all — recover it from the persisted task prompt so we can
    # rehydrate the agent under its legacy id without forking history.
    if not repo_id or expected_id != sub_session_id:
        history = agent_store.load(sub_session_id, "dataset_analysis")
        for msg in history:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            parsed = extract_repo_id_from_prompt(content)
            if parsed:
                repo_id = parsed
                break

    if not repo_id:
        raise HTTPException(status_code=404, detail="sub-agent not found (not yet spawned or evicted)")

    def _new_agent(stable_sub_session_id: str) -> DatasetAnalysisAgent:
        # Passing session_id replays prior history out of the shared
        # conversation store, so the new agent picks up where the killed one
        # left off (whether the original id was old-format or new-format).
        return DatasetAnalysisAgent(
            session_id=stable_sub_session_id,
            conversation_store=agent_store,
        )

    # If the id matches the deterministic pattern, use the dataset-keyed
    # factory so anyone else asking for the same repo finds this agent too.
    if dataset_sub_session_id(chat_id, repo_id) == sub_session_id:
        managed, _ = sub_agent_sessions.get_or_create_dataset_agent(
            chat_session_id=chat_id,
            repo_id=repo_id,
            agent_factory=_new_agent,
        )
        return managed

    # Legacy id — register the rehydrated agent under its existing id so
    # follow-up turns and event feeds keep using the same key the rest of
    # the system has persisted against.
    agent = _new_agent(sub_session_id)
    return sub_agent_sessions.register(
        sub_session_id=sub_session_id,
        chat_session_id=chat_id,
        agent=agent,
        repo_id=repo_id,
    )


# ---- Event → SSE translation ----

def _sse_frame(payload: dict[str, Any]) -> str:
    """Encode one SSE message. Each frame ends with the standard double-newline."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _identity_payload(event: Event, entry) -> dict[str, Any]:
    """Identity fields the UI uses to route events to the right surface.

    `agent_id` + `agent_type` tell the frontend who fired the event; the root
    chat agent ('dataset_chat') lights up the main thread, while child
    agents ('dataset_analysis') light up tabs in the right rail. We also
    forward `agent_session_id` (from the registry entry, when present)
    because it is the stable per-sub-agent key the frontend uses for tabs.
    """
    return {
        "agent_id": getattr(event, "agent", None),
        "agent_type": getattr(event, "agent_type", None),
        "parent_agent": getattr(event, "parent_agent", None),
        "agent_session_id": entry.agent_session_id if entry else None,
    }


def _event_to_frame(event: Event, entry=None) -> dict[str, Any] | None:
    """Translate an agent_core Event into the {type, payload} shape the frontend expects.

    Returns None for events we don't surface to the UI (e.g. MODEL_THINKING).
    """
    etype = event.type.value if hasattr(event.type, "value") else event.type
    details = event.details or {}
    identity = _identity_payload(event, entry)

    if etype == EventType.AGENT_START.value:
        # Surfaced for both root and sub-agents. The frontend uses sub-agent
        # AGENT_START to open a tab the moment delegation kicks off.
        return {
            "type": "agent_start",
            "payload": {
                **identity,
                "prompt": details.get("prompt"),
                "model": details.get("model"),
            },
        }

    if etype == EventType.AGENT_END.value:
        # `details.result` carries the sub-agent's full report string — the
        # frontend renders it into the corresponding tab's Report section.
        status_str = event.status.value if hasattr(event.status, "value") else str(event.status)
        success = status_str != "failed"
        return {
            "type": "agent_end",
            "payload": {
                **identity,
                "success": success,
                "result": details.get("result"),
                "error": details.get("error"),
                "token_usage": details.get("token_usage"),
            },
        }

    if etype == EventType.TOOL_START.value:
        return {
            "type": "tool_start",
            "payload": {
                **identity,
                "id": event.tool_call_id,
                "tool": event.tool,
                "args_hint": details.get("args_hint"),
            },
        }

    if etype == EventType.TOOL_END.value:
        # The status enum reports COMPLETED / FAILED — map to frontend's success/error.
        status_str = event.status.value if hasattr(event.status, "value") else str(event.status)
        ui_status = "error" if status_str == "failed" else "success"
        return {
            "type": "tool_end",
            "payload": {
                **identity,
                "id": event.tool_call_id,
                "tool": event.tool,
                "status": ui_status,
                "result_summary": details.get("result_summary"),
                "error": details.get("error"),
            },
        }

    # MODEL_THINKING, CONTEXT_UPDATE, ERROR — skip for now. The UI doesn't render them.
    return None


# ---- /api/chat/stream ----

@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    session_id = req.session_id or _new_session_id()
    turn_id = uuid.uuid4().hex
    managed = dataset_chat_sessions.get_or_create(session_id)
    agent = managed.agent

    # First user turn on a session → derive a title from the query so the
    # sidebar has something readable to show. Existing titles are preserved.
    agent_store.record_session(session_id, default_title=_derive_title(req.query))

    # Per-request event queue. The event-bus subscriber and on_text_delta both
    # push frames into this queue; the SSE generator pulls them out.
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    bus = get_event_bus()

    # Buffer for streamed text deltas. agent_core's `on_text_delta` fires for
    # every model iteration — including intermediates that exist solely to
    # decide which tool to call next. Those iterations are followed by a
    # MODEL_THINKING event, which is our signal to drop the accumulated chunks.
    # The final iteration (the one that actually answers the user) emits no
    # MODEL_THINKING, so its buffer survives until run() returns and we flush
    # it as a single text_delta frame to the client.
    text_buffer: list[str] = []

    def _clear_text_buffer() -> None:
        text_buffer.clear()

    def _flush_text_buffer() -> None:
        if not text_buffer:
            return
        text = "".join(text_buffer)
        text_buffer.clear()
        queue.put_nowait({"type": "text_delta", "payload": {"delta": text}})

    def _event_session_entry(event: Event):
        entry = agent_registry.get_entry(getattr(event, "agent", None))
        if entry is not None:
            return entry.chat_session_id, entry
        parent_entry = agent_registry.get_entry(getattr(event, "parent_agent", None))
        if parent_entry is not None:
            return parent_entry.chat_session_id, parent_entry
        return None, None

    def _on_event(event: Event) -> None:
        event_session_id, entry = _event_session_entry(event)
        if event_session_id != session_id:
            return
        if entry and entry.turn_id and entry.turn_id != turn_id:
            return
        etype = event.type.value if hasattr(event.type, "value") else event.type
        try:
            agent_store.record_event(
                session_id,
                event,
                agent_session_id=entry.agent_session_id if entry else None,
            )
        except Exception:
            logger.exception("failed to persist agent event %s", etype)
        # Intermediate text just got finalized as "thinking" — drop the buffered
        # deltas before any client sees them.
        if etype == EventType.MODEL_THINKING.value:
            loop.call_soon_threadsafe(_clear_text_buffer)
            return
        frame = _event_to_frame(event, entry=entry)
        if frame is None:
            return
        if entry and entry.agent_session_id:
            sub_agent_sessions.record_frame(entry.agent_session_id, frame)
        if client_connected.is_set():
            loop.call_soon_threadsafe(queue.put_nowait, frame)

    def _on_text_delta(delta: str) -> None:
        if client_connected.is_set():
            loop.call_soon_threadsafe(text_buffer.append, delta)

    bus.subscribe(_on_event)
    client_connected = threading.Event()
    client_connected.set()
    run_started = threading.Event()
    cancel_requested = threading.Event()

    def _run_agent() -> None:
        try:
            with managed.lock:
                if cancel_requested.is_set():
                    return
                agent_registry.register(
                    agent.instance_id,
                    session_id,
                    agent=agent,
                    agent_session_id=session_id,
                    turn_id=turn_id,
                )
                try:
                    agent._current_turn_id = turn_id
                    run_started.set()
                    agent.run(req.query, streaming=True, on_text_delta=_on_text_delta)
                    agent_store.save_snapshot(session_id, agent.name, agent._history)
                finally:
                    if getattr(agent, "_current_turn_id", None) == turn_id:
                        agent._current_turn_id = None
                    agent_registry.unregister(agent.instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent.run failed: %s", exc)
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "agent_end", "payload": {"agent_type": agent.name, "success": False, "error": str(exc)}},
            )
        finally:
            bus.unsubscribe(_on_event)
            # Flush the final-iteration text (anything not wiped by MODEL_THINKING)
            # before the sentinel closes the stream.
            if client_connected.is_set():
                loop.call_soon_threadsafe(_flush_text_buffer)
            # Sentinel: tells the generator to stop reading.
            if client_connected.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, None)

    # Hand off to a worker thread so agent.run() (sync, blocking) doesn't pin the event loop.
    # run_in_executor returns a Future directly — uvloop refuses to wrap it in create_task.
    agent_future = loop.run_in_executor(None, _run_agent)

    async def event_generator():
        # Echo the session_id first so the client can persist it for follow-ups.
        yield _sse_frame({"type": "session", "payload": {"session_id": session_id}})
        try:
            while True:
                if await request.is_disconnected():
                    logger.info("client disconnected; detaching stream for %s", session_id)
                    client_connected.clear()
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies + load balancers from killing the stream
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield _sse_frame(item)
        finally:
            client_connected.clear()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )


# ---- /api/sessions ----
#
# Session list / detail / delete / rename. Backed by the SQLite chat_sessions
# table plus the conversations replay history. The sidebar populates from
# /api/sessions; opening a session calls /api/sessions/:id to rehydrate the
# message thread. New sessions are usually minted lazily on the first
# /api/chat/stream turn — POST /api/sessions is for the explicit "new chat"
# button when we want an id before the user types.


@app.get("/api/sessions")
async def list_sessions() -> dict[str, Any]:
    rows = agent_store.list_sessions()
    return {"sessions": rows}


@app.post("/api/sessions")
async def create_session() -> dict[str, Any]:
    session_id = _new_session_id()
    agent_store.record_session(session_id)
    return {"session_id": session_id, "title": None}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    meta = agent_store.get_session(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="session not found")
    history = agent_store.get_root_history(session_id)
    return {
        **meta,
        "messages": _history_to_ui_messages(history),
        "sub_agents": _sub_agents_to_ui(session_id),
    }


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    meta = agent_store.get_session(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="session not found")
    # Evict the cached in-memory agent first so any in-flight turn cancels
    # cleanly, then wipe the persisted rows (history, events, runs, snapshots,
    # session metadata) in one transaction. Cascade to any sub-agents the root
    # has spawned so we don't leak in-memory child instances.
    dataset_chat_sessions.clear(session_id, clear_persistent=False)
    sub_agent_sessions.clear_for_chat(session_id)
    agent_store.clear_session(session_id)
    return {"ok": True}


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, req: RenameRequest) -> dict[str, Any]:
    meta = agent_store.get_session(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="session not found")
    title = (req.title or "").strip() or None
    agent_store.set_session_title(session_id, title)
    refreshed = agent_store.get_session(session_id)
    return refreshed or {"session_id": session_id, "title": title}


# ---- /api/sessions/:chat_id/sub-agents/:sub_session_id/turn ----
#
# User turn against a persistent dataset sub-agent. Sub-agents are created or
# reused when the root agent calls message(agent_DS:org/name, ...); this endpoint
# addresses the same cached agent for user-initiated follow-up questions.


class SubAgentTurnRequest(BaseModel):
    query: str


@app.post("/api/sessions/{chat_id}/sub-agents/{sub_session_id:path}/turn")
async def sub_agent_turn(
    chat_id: str,
    sub_session_id: str,
    req: SubAgentTurnRequest,
    request: Request,
):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    managed = _restore_dataset_sub_agent(chat_id, sub_session_id)

    agent = managed.agent
    turn_id = uuid.uuid4().hex

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    bus = get_event_bus()

    def _on_event(event: Event) -> None:
        # Only forward events fired by this specific sub-agent. Sibling
        # sub-agents under the same chat must not bleed into this stream.
        entry = agent_registry.get_entry(getattr(event, "agent", None))
        if entry is None or entry.agent_session_id != sub_session_id:
            return
        if entry.turn_id and entry.turn_id != turn_id:
            return
        etype = event.type.value if hasattr(event.type, "value") else event.type
        try:
            agent_store.record_event(chat_id, event, agent_session_id=sub_session_id)
        except Exception:
            logger.exception("failed to persist sub-agent event %s", etype)
        if etype == EventType.MODEL_THINKING.value:
            return
        frame = _event_to_frame(event, entry=entry)
        if frame is None:
            return
        sub_agent_sessions.record_frame(sub_session_id, frame)
        if client_connected.is_set():
            loop.call_soon_threadsafe(queue.put_nowait, frame)

    bus.subscribe(_on_event)
    client_connected = threading.Event()
    client_connected.set()
    run_started = threading.Event()
    cancel_requested = threading.Event()

    def _run_agent() -> None:
        try:
            repo_id = managed.repo_id or extract_repo_id(managed.task)
            if not repo_id:
                raise RuntimeError("sub-agent is missing its dataset repo_id")
            with dataset_work_locks.acquire(
                repo_id,
                chat_session_id=chat_id,
                sub_session_id=sub_session_id,
            ):
                with managed.lock:
                    if cancel_requested.is_set():
                        return
                    agent_registry.register(
                        agent.instance_id,
                        chat_id,
                        agent=agent,
                        agent_session_id=sub_session_id,
                        parent_agent=getattr(agent, "_parent_agent", None),
                        turn_id=turn_id,
                    )
                    try:
                        run_started.set()
                        agent.run(req.query)
                    finally:
                        agent_registry.unregister(agent.instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("sub-agent run failed: %s", exc)
            frame = {
                "type": "agent_end",
                "payload": {
                    "agent_id": getattr(agent, "instance_id", None),
                    "agent_session_id": sub_session_id,
                    "agent_type": getattr(agent, "name", None),
                    "success": False,
                    "error": str(exc),
                },
            }
            sub_agent_sessions.record_frame(sub_session_id, frame)
            if client_connected.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, frame)
        finally:
            bus.unsubscribe(_on_event)
            if client_connected.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, None)

    agent_future = loop.run_in_executor(None, _run_agent)

    async def event_generator():
        # Echo identity up-front so the client can pin the stream to a tab
        # before any agent events arrive.
        yield _sse_frame({
            "type": "session",
            "payload": {
                "session_id": chat_id,
                "agent_session_id": sub_session_id,
                "agent_type": getattr(agent, "name", None),
            },
        })
        try:
            while True:
                if await request.is_disconnected():
                    client_connected.clear()
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield _sse_frame(item)
        finally:
            client_connected.clear()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---- /api/sessions/:chat_id/sub-agents/:sub_session_id/events ----
#
# Read-only live feed for an existing persistent sub-agent. This lets a
# re-opened browser tab attach to work that is already running in the backend
# without starting another turn or sharing mutable agent state with the UI.


@app.get("/api/sessions/{chat_id}/sub-agents/{sub_session_id:path}/events")
async def sub_agent_events(
    chat_id: str,
    sub_session_id: str,
    request: Request,
):
    managed = _restore_dataset_sub_agent(chat_id, sub_session_id)

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    bus = get_event_bus()

    def _on_event(event: Event) -> None:
        entry = agent_registry.get_entry(getattr(event, "agent", None))
        if entry is None or entry.chat_session_id != chat_id:
            return
        if entry.agent_session_id != sub_session_id:
            return
        etype = event.type.value if hasattr(event.type, "value") else event.type
        if etype == EventType.MODEL_THINKING.value:
            return
        frame = _event_to_frame(event, entry=entry)
        if frame is None:
            return
        sub_agent_sessions.record_frame(sub_session_id, frame)
        loop.call_soon_threadsafe(queue.put_nowait, frame)
        if frame.get("type") == "agent_end":
            loop.call_soon_threadsafe(queue.put_nowait, None)

    bus.subscribe(_on_event)

    async def event_generator():
        try:
            yield _sse_frame({
                "type": "session",
                "payload": {
                    "session_id": chat_id,
                    "agent_session_id": sub_session_id,
                    "agent_type": getattr(managed.agent, "name", None),
                },
            })
            snapshot = _sub_agent_to_ui(chat_id, sub_session_id)
            if snapshot is not None:
                yield _sse_frame({
                    "type": "sub_agent_snapshot",
                    "payload": {"sub_agent": snapshot},
                })
                if snapshot.get("status") != "running":
                    return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    live = sub_agent_sessions.live_state(sub_session_id)
                    if live is not None and live.status != "running":
                        snapshot = _sub_agent_to_ui(chat_id, sub_session_id)
                        if snapshot is not None:
                            yield _sse_frame({
                                "type": "sub_agent_snapshot",
                                "payload": {"sub_agent": snapshot},
                            })
                        break
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield _sse_frame(item)
        finally:
            bus.unsubscribe(_on_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---- /api/health ----

@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "sessions": dataset_chat_sessions.session_count(),
        "uptime_s": int(time.time() - _STARTED_AT),
    }


_STARTED_AT = time.time()


# ---- CLI entry ----

def main() -> int:
    import uvicorn
    uvicorn.run(
        "backend.server:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5001")),
        log_level="info",
        reload=bool(int(os.environ.get("RELOAD", "0"))),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
