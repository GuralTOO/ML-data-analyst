"""Search tools — atomic Nimble-backed primitives the agent orchestrates.

Per approach.md: the agent (not the tool) fans out searches in parallel,
inspects the merged results, and picks top candidates. These tools are
intentionally atomic and small — one call = one search.

Composition pattern (the agent learns this from the docstrings + system prompt):
    nimble_serp_search_hf("humaneval code generation")  ⎫
    nimble_serp_search_hf("mbpp python eval")           ⎬ in parallel
    nimble_serp_search_hf("code benchmark test cases")  ⎪
    nimble_ai_search_hf("small benchmark for code-gen with verifiable tests") ⎭
        │
        ▼ (agent merges/dedupes/picks top ~10 in its own reasoning)
        │
    nimble_extract_url(url1)  ⎫
    nimble_extract_url(url2)  ⎬ in parallel — uniform markdown for every dataset
    ...                       ⎭
"""
from __future__ import annotations

import math

from backend.tools._common import (
    hit_to_dataset_row,
    nimble_client,
    nimble_organic_results,
    safe_tool,
)


# ---- Internal helpers ----


def _round_up_to_10(n: int, *, cap: int = 40) -> int:
    """Nimble's Google SERP API requires num_results be a multiple of 10."""
    return min(cap, max(10, math.ceil(n / 10) * 10))


def _dedupe_by_repo_id(rows: list[dict]) -> list[dict]:
    """Keep the first (best-positioned) row per repo_id; preserve order."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        rid = r["repo_id"]
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


# ---- Atomic search tools ----


@safe_tool
def nimble_serp_search_hf(query: str, max_results: int = 10) -> list[dict]:
    """Run ONE Google search via Nimble's SERP API; return HF dataset hits only.

    USE THIS AS A PRIMITIVE — call it MULTIPLE TIMES IN PARALLEL with varied
    keyword combinations to widen recall. Google's keyword matching is brittle,
    so different phrasings surface different datasets. A typical discovery
    pattern is 3–5 parallel SERP calls each exploring a different angle.

    Query tips:
      • Include `site:huggingface.co/datasets` to restrict to HF dataset pages.
        Example: `site:huggingface.co/datasets humaneval code generation`
        (This tool prepends it automatically if missing.)
      • Use short, varied keyword combos — Google is a keyword matcher, not a
        semantic engine. For a multimodal STEM benchmark try parallel queries:
            "multimodal STEM benchmark physics math"
            "visual question answering science PhD"
            "multimodal verifiable reward dataset"
            "image-grounded math physics evaluation"
      • Pair with ONE nimble_ai_search_hf call (semantic understanding) for
        complementary coverage — SERP catches what keyword-matching surfaces;
        AI search catches semantic intent.

    Args:
        query: keyword query for Google. site:huggingface.co/datasets is
            prepended automatically if not already present.
        max_results: how many HF-dataset hits to return (default 10, max 40).
            Non-dataset URLs (blogs, models, spaces) are filtered out before
            counting, so the actual return list may be slightly shorter.

    Returns:
        list of {repo_id, url, title, snippet, position}
        — already filtered to canonical HF dataset URLs (org/name).
    """
    max_results = max(0, int(max_results))
    if max_results == 0:
        return []
    if "site:huggingface.co" not in query.lower():
        full_query = f"site:huggingface.co/datasets {query}"
    else:
        full_query = query

    nimble = nimble_client()
    res = nimble.serp.run(
        search_engine="google_search",
        query=full_query,
        num_results=_round_up_to_10(max_results),
        parse=True,
    )
    organic = nimble_organic_results(res)
    hits = [row for r in organic if (row := hit_to_dataset_row(r)) is not None]
    return _dedupe_by_repo_id(hits)[:max_results]


@safe_tool
def nimble_ai_search_hf(query: str, max_results: int = 10) -> list[dict]:
    """Run ONE Nimble AI search; return HF dataset hits only.

    Unlike nimble_serp_search_hf (raw Google keyword matching), this uses
    Nimble's AI search layer — it interprets natural-language intent. Write
    queries describing WHAT the user wants, not keyword combos.

    Examples of good queries:
      "small benchmark for evaluating code generation with verifiable unit tests"
      "multimodal STEM dataset with PhD-level physics and math problems"
      "open-ended question answering dataset with image inputs"

    USE THIS AS A COMPLEMENT to parallel nimble_serp_search_hf calls — AI
    search catches semantic intent that keyword search misses, and vice versa.
    Typical pattern: ONE nimble_ai_search_hf call alongside 3-5 parallel
    nimble_serp_search_hf calls.

    Args:
        query: natural-language description of what the user wants.
            (`huggingface.co/datasets` is prepended as a soft hint to bias
            toward dataset pages.)
        max_results: how many HF-dataset hits to return (default 10, max 20).
            Non-dataset URLs are filtered before counting.

    Returns:
        list of {repo_id, url, title, snippet, position}
        — already filtered to canonical HF dataset URLs (org/name).
    """
    max_results = max(0, int(max_results))
    if max_results == 0:
        return []
    if "huggingface.co" not in query.lower():
        full_query = f"huggingface.co/datasets {query}"
    else:
        full_query = query

    nimble = nimble_client()
    res = nimble.search(
        query=full_query,
        focus="general",
        include_domains=["huggingface.co"],
        search_depth="lite",
        max_results=min(max(max_results * 2, 5), 20),  # over-fetch for filtering
    )
    d = res.model_dump() if hasattr(res, "model_dump") else res
    results = d.get("results") or []
    hits = [row for r in results if (row := hit_to_dataset_row(r)) is not None]
    return _dedupe_by_repo_id(hits)[:max_results]


@safe_tool
def nimble_web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the live web (Google SERP) via Nimble — NO domain filtering.

    Use for general web research — papers (arxiv), leaderboards, blog posts,
    GitHub repos, anything outside Hugging Face. For finding HF datasets
    specifically, use nimble_serp_search_hf or nimble_ai_search_hf instead.

    Args:
        query: any Google query.
        max_results: 1-10, default 5.

    Returns:
        list of {title, url, snippet} — unfiltered.
    """
    max_results = max(0, int(max_results))
    if max_results == 0:
        return []
    nimble = nimble_client()
    res = nimble.serp.run(
        search_engine="google_search",
        query=query,
        num_results=_round_up_to_10(max_results, cap=20),
        parse=True,
    )
    organic = nimble_organic_results(res)
    return [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "snippet": r.get("snippet") or r.get("description"),
        }
        for r in organic[:max_results]
    ]
