"""Hugging Face Hub metadata tools — info, card, preview, native search.

These tools talk to the HF Hub API directly via huggingface_hub. The
ClickHouse-backed analysis tools live in backend.tools.clickhouse.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

# Module-level import — huggingface_hub has a circular-import race when first
# loaded from multiple threads concurrently (which happens when the agent calls
# 4+ HF tools in parallel).
from huggingface_hub import HfApi, hf_hub_download

from backend.tools._common import safe_tool, trim


@safe_tool
def get_hf_dataset_info(repo_id: str) -> dict:
    """Hub-level metadata for a dataset: who made it, when, license, popularity.

    Source: HF Hub repo API (HfApi.dataset_info) — NOT the parquet data.
    Returns repo facts: author, downloads, likes, tags, license, dates, file
    list, card_data (the YAML frontmatter from the README).

    When to use this vs analyze_hf_dataset:
      • get_hf_dataset_info           → "Is this dataset reputable / well-maintained?"
                                         (popularity, license, age, author)
      • analyze_hf_dataset            → "What's IN this dataset?"
                                         (configs, splits, columns, sizes, samples,
                                          ClickHouse profile)

    Cheap (~100ms). Call right after discovery to score adoption signals before
    deciding which candidates merit deeper inspection.
    """
    api = HfApi(token=os.environ.get("HF_TOKEN"))
    info = api.dataset_info(repo_id)
    card_data = None
    if getattr(info, "card_data", None) is not None:
        try:
            card_data = info.card_data.to_dict()
        except Exception:
            card_data = str(info.card_data)
    return {
        "id": info.id,
        "author": getattr(info, "author", None),
        "downloads": getattr(info, "downloads", None),
        "likes": getattr(info, "likes", None),
        "created_at": str(getattr(info, "created_at", "") or ""),
        "last_modified": str(getattr(info, "last_modified", "") or ""),
        "tags": getattr(info, "tags", None) or [],
        "card_data": card_data,
        "files": [s.rfilename for s in (getattr(info, "siblings", None) or [])][:50],
    }


@safe_tool
def get_hf_dataset_card(repo_id: str) -> dict:
    """Download the README/dataset card for an HF dataset.

    The card is the human-written description (purpose, structure, license,
    citations). Use this to deeply understand a shortlisted candidate.
    Returns {repo_id, card_markdown} with markdown truncated to ~6KB.
    """
    try:
        path = hf_hub_download(
            repo_id=repo_id,
            filename="README.md",
            repo_type="dataset",
            token=os.environ.get("HF_TOKEN"),
        )
        with open(path, encoding="utf-8") as f:
            card = f.read()
        return {"repo_id": repo_id, "card_markdown": trim(card, 6000)}
    except Exception as exc:
        return {"repo_id": repo_id, "error": f"could not fetch card: {exc}"}


@safe_tool
def preview_hf_dataset(
    repo_id: str,
    config: str = "default",
    split: str = "train",
    limit: int = 5,
) -> dict:
    """Fetch sample rows from a dataset — the DEFAULT for "just show me rows".

    Source: HF datasets-server /rows endpoint. NOT ClickHouse. No worker needed,
    no SQL required, no preflight required for single-config datasets.

    When to use this vs query_hf_dataset_with_clickhouse:
      • preview_hf_dataset                 → "Just show me a few example rows"
                                              ALWAYS prefer this for that purpose.
      • query_hf_dataset_with_clickhouse   → "Answer a question with SQL"
                                              (aggregations, filters, GROUP BY)

    For multi-config datasets, call analyze_hf_dataset first to pick the
    right (config, split). Defaults to config="default", split="train" — these
    work for single-config datasets but 404 on multi-config without explicit args.
    """
    qs = urllib.parse.urlencode({
        "dataset": repo_id, "config": config, "split": split,
        "offset": 0, "length": min(max(limit, 1), 20),
    })
    url = f"https://datasets-server.huggingface.co/rows?{qs}"
    headers = {}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"repo_id": repo_id, "error": f"datasets-server: {exc}"}
    rows = data.get("rows", [])
    # Aggressively truncate any long string fields so the response stays small
    for r in rows:
        row = r.get("row") or {}
        for k, v in list(row.items()):
            if isinstance(v, str) and len(v) > 400:
                row[k] = v[:400] + f"…[+{len(v) - 400}]"
    return {
        "repo_id": repo_id,
        "config": config,
        "split": split,
        "num_rows_total": data.get("num_rows_total"),
        "features": data.get("features"),
        "rows": rows,
    }
