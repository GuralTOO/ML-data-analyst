"""End-to-end smoke test for the DatasetChatAgent.

Loads .env, instantiates the agent (OpenRouter → DeepSeek V4-pro), runs a real
query with streaming, prints events as they fire, and dumps the final answer.

Run from project root:
    .venv/bin/python -m backend.main
"""
from __future__ import annotations

import os
import sys
import time

import logging

from dotenv import load_dotenv

# Load .env from project root regardless of cwd
load_dotenv()

# Quiet the agent_core "Tool X raised an exception" tracebacks — our tools
# already return {"error": ...} on failure, the model handles it gracefully.
logging.getLogger("agent_core").setLevel(logging.WARNING)

# Sanity-check required keys before we spin up anything
_REQUIRED = ["OPEN_ROUTER_API_KEY", "NIMBLE_API_KEY", "HF_TOKEN"]
missing = [k for k in _REQUIRED if not os.environ.get(k)]
if missing:
    sys.exit(f"Missing env vars: {missing}")

from agent_core import EventType, get_event_bus  # noqa: E402

from backend.agent import DatasetChatAgent  # noqa: E402


# ---- Event tracing — show what the agent is doing in real time ----

_t0 = time.time()


def _elapsed() -> str:
    return f"{time.time() - _t0:5.1f}s"


def _on_event(event) -> None:
    t = event.type
    agent_type = getattr(event, "agent_type", "") or ""
    tool = getattr(event, "tool", None)
    details = getattr(event, "details", None) or {}
    status = getattr(event, "status", None)

    if t == EventType.AGENT_START:
        print(f"\n[{_elapsed()}] AGENT_START  {agent_type}")
    elif t == EventType.AGENT_END:
        ok = details.get("success", True)
        print(f"\n[{_elapsed()}] AGENT_END    {agent_type}  ok={ok}")
    elif t == EventType.TOOL_START:
        hint = details.get("args_hint")
        hint_s = f" {hint!r}" if hint else ""
        print(f"[{_elapsed()}]   → {tool}{hint_s}")
    elif t == EventType.TOOL_END:
        summary = (details.get("result_summary") or details.get("error") or "")[:120]
        status_s = str(status) if status else ""
        print(f"[{_elapsed()}]   ← {tool}  {status_s}  → {summary!r}")
    elif t == EventType.MODEL_THINKING:
        text = (details.get("text") or "")[:100]
        if text.strip():
            print(f"[{_elapsed()}]   …thinking: {text!r}")


get_event_bus().subscribe(_on_event)


# ---- Streaming text printer ----

_streaming_active = False


def _on_text_delta(delta: str) -> None:
    global _streaming_active
    if not _streaming_active:
        print(f"\n[{_elapsed()}] ── streamed response ──")
        _streaming_active = True
    print(delta, end="", flush=True)


# ---- Run ----

DEFAULT_QUERY = (
    "I'm looking for a multimodal STEM benchmark dataset with verifiable answers. "
    "Ideally physics or math, with images. Find 2-3 strong options on Hugging Face "
    "and recommend the best fit."
)


def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or DEFAULT_QUERY
    print(f"=== DatasetChatAgent smoke test ===")
    print(f"Model: deepseek/deepseek-v4-pro (via OpenRouter)")
    print(f"Query: {query}\n")

    agent = DatasetChatAgent()
    response = agent.run(
        query,
        streaming=True,
        on_text_delta=_on_text_delta,
    )

    print(f"\n\n[{_elapsed()}] ── final response ──\n{response}\n")
    print(f"Total elapsed: {_elapsed()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
