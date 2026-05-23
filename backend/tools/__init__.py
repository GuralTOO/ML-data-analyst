"""Tools the agents expose to the LLM.

Layout mirrors the approach.md workflow:

    search.py       — Nimble-backed dataset discovery (visible to user)
    hf.py           — Hugging Face Hub metadata / dataset card / preview rows
    extract.py      — single-URL content extraction via Nimble
    clickhouse.py   — ClickHouse-backed deep analysis (flexible per dataset)

Each public tool function is decorated with safe_tool so failures become
{"error": ...} dicts instead of raising — the model can read the error
and recover instead of seeing tracebacks.
"""
from backend.tools.clickhouse import (
    analyze_hf_dataset,
    query_hf_dataset_with_clickhouse,
)
from backend.tools.extract import nimble_extract_url
from backend.tools.hf import (
    get_hf_dataset_card,
    get_hf_dataset_info,
    preview_hf_dataset,
)
from backend.tools.search import (
    nimble_ai_search_hf,
    nimble_serp_search_hf,
    nimble_web_search,
)


# Tools available to the root chat agent. ClickHouse primitives are deliberately
# not exposed here; the root delegates deep work to DatasetAnalysisAgent.
ROOT_TOOLS = [
    # Discovery (Nimble) — atomic primitives; agent orchestrates parallel calls
    nimble_serp_search_hf,     # call MULTIPLE in parallel with varied keywords
    nimble_ai_search_hf,       # call ONCE alongside, for semantic intent
    # Uniform content extraction — fan out in parallel after picking candidates
    nimble_extract_url,
    # HF metadata
    get_hf_dataset_info,
    get_hf_dataset_card,
    preview_hf_dataset,
    # General web (papers, leaderboards, etc.)
    nimble_web_search,
]

# Tools available to the dedicated dataset-analysis agent.
DATASET_ANALYSIS_TOOLS = [
    analyze_hf_dataset,
    query_hf_dataset_with_clickhouse,
    preview_hf_dataset,
]

# Backward-compatible name used by DatasetChatAgent.
ALL_TOOLS = ROOT_TOOLS

__all__ = [
    "ALL_TOOLS",
    "DATASET_ANALYSIS_TOOLS",
    "ROOT_TOOLS",
    "analyze_hf_dataset",
    "get_hf_dataset_card",
    "get_hf_dataset_info",
    "nimble_ai_search_hf",
    "nimble_extract_url",
    "nimble_serp_search_hf",
    "nimble_web_search",
    "preview_hf_dataset",
    "query_hf_dataset_with_clickhouse",
]
