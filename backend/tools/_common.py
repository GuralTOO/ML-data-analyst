"""Shared helpers used across the agent-exposed tool modules.

Not exposed to the LLM — these are implementation details.
"""
from __future__ import annotations

import functools
import os
import re
import sys
from typing import Callable


# ---- Generic helpers ----


def trim(text: str | None, limit: int = 3000) -> str | None:
    """Truncate long strings; preserve None."""
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n…[truncated, {len(text) - limit} more chars]"


# ---- Tool wrapping ----


def safe_tool(fn: Callable) -> Callable:
    """Catch exceptions and return {"error": ...} so the model can recover.

    Without this, agent_core.logger.exception() dumps full tracebacks
    on every model-hallucinated repo_id or transient HTTP failure.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}", "tool": fn.__name__}

    return wrapper


# ---- Nimble client + result extraction ----


def nimble_client():
    """Return a Nimble SDK client. Imported lazily — the SDK is fine threaded."""
    from nimble_python import Nimble
    return Nimble(api_key=os.environ["NIMBLE_API_KEY"])


def nimble_organic_results(res) -> list[dict]:
    """Extract organic results from a `nimble.serp.run` response.

    The SDK returns a pydantic model nested as:
        res.data.parsing.entities.OrganicResult  -> list of {url, title, snippet, ...}
    This helper normalizes to dicts and tolerates both pydantic and raw-dict shapes.
    """
    if hasattr(res, "model_dump"):
        d = res.model_dump()
    elif isinstance(res, dict):
        d = res
    else:
        return []
    data = d.get("data") or {}
    parsing = data.get("parsing") or {}
    entities = parsing.get("entities") or {}
    organic = entities.get("OrganicResult") or []
    return organic if isinstance(organic, list) else []


# ---- HF dataset URL matching ----

HF_DATASET_URL_RE = re.compile(
    r"huggingface\.co/datasets/([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)?)",
    re.IGNORECASE,
)


def hit_to_dataset_row(r: dict, position_key: str = "position") -> dict | None:
    """Pull a {repo_id, url, title, snippet, position} row out of a search hit
    if its URL points to a real HF dataset, else None.

    Used by both pipelines in search.nimble_find_hf_datasets so the dedup
    table sees a single normalized shape regardless of which Nimble surface
    produced the hit.
    """
    url = r.get("url") or r.get("link") or ""
    m = HF_DATASET_URL_RE.search(url)
    if not m:
        return None
    repo_id = m.group(1)
    if "/" not in repo_id:  # skip legacy single-segment datasets like /datasets/squad
        return None
    pos = r.get(position_key)
    if pos is None:
        pos = (r.get("metadata") or {}).get("position")
    try:
        pos = int(pos)
    except (TypeError, ValueError):
        pos = 999
    return {
        "repo_id": repo_id,
        "url": f"https://huggingface.co/datasets/{repo_id}",
        "title": r.get("title"),
        "snippet": r.get("snippet") or r.get("description"),
        "position": pos,
    }


def safe_future(future, default, *, label: str = ""):
    """Resolve a `concurrent.futures.Future`; return `default` on any exception.

    Prints a one-line error to stderr (always) and a full traceback if
    DISCOVERY_DEBUG=1 is set. Used by the dual-pipeline tools so that one
    pipeline's hiccup doesn't sink the whole call.
    """
    try:
        return future.result(timeout=45)
    except Exception as exc:
        import traceback
        print(
            f"[tools] {label} failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        if os.environ.get("DISCOVERY_DEBUG"):
            traceback.print_exc(file=sys.stderr)
        return default
