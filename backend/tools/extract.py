"""URL extraction tool — fetch one or many pages as clean markdown via Nimble."""
from __future__ import annotations

import concurrent.futures
import sys

from backend.tools._common import nimble_client, safe_tool, trim


_DEFAULT_MAX_CHARS = 6000
_MIN_MAX_CHARS = 100
_MAX_PARALLEL_EXTRACTS = 10
_PER_EXTRACT_TIMEOUT_SEC = 60
_MAX_URLS_PER_CALL = 20


def _extract_one(url: str, max_chars: int) -> dict:
    """Extract a single URL via Nimble. Raises on failure — caller handles."""
    nimble = nimble_client()
    res = nimble.extract(
        url=url,
        formats=["markdown"],
        render=True,
        markdown_backend="main_content",
    )
    data = getattr(res, "data", None)
    md = None
    if data is not None:
        md = getattr(data, "markdown", None)
    elif isinstance(res, dict):
        md = (res.get("data") or {}).get("markdown")
    # Treat empty/whitespace-only markdown as a failure rather than silent success.
    # Otherwise the agent sees {url, markdown: None} and can't tell what happened.
    if not md or not md.strip():
        raise ValueError("empty markdown returned (page blank, blocked, or non-text)")
    return {"url": url, "markdown": trim(md, max_chars)}


@safe_tool
def nimble_extract_url(urls: list[str], max_chars: int = _DEFAULT_MAX_CHARS) -> list[dict]:
    """Extract clean markdown from one or MORE URLs in parallel via Nimble (JS-rendered).

    ALWAYS pass a LIST of URLs — even for a single URL, wrap it: ["url"].
    Extracts run in PARALLEL (up to 10 concurrent). Per-URL failures DON'T sink
    the batch: failed URLs come back as {url, error}, successful ones as
    {url, markdown}. Order matches the input list (after dedup).

    Input hygiene applied automatically (visible in stderr; first result also
    carries `_warnings` when any apply):
      • Duplicate URLs deduped (kept in first-occurrence order)
      • Non-string / empty entries dropped
      • Inputs exceeding 20 URLs are truncated to the first 20

    USE THIS as the uniformization step in the discovery flow:
      1. nimble_serp_search_hf (×N in parallel) + nimble_ai_search_hf (×1) surface
         candidate dataset URLs
      2. Merge, dedupe by repo_id, pick the top ~10 in your reasoning
      3. Call nimble_extract_url(urls=[url1, url2, ..., url10]) ONCE
      4. Every dataset now has the same markdown shape → show to the user

    This single-batch pattern is what gives the UI uniform "datasets being analyzed"
    cards. Calling extract one-URL-at-a-time defeats that consistency goal AND
    wastes latency.

    Also useful for non-HF pages: paper abstracts (arxiv), leaderboards, blogs,
    anything surfaced by nimble_web_search.

    Args:
        urls: list of URLs to extract. JS-rendered, so React-driven pages (HF
            dataset cards) work. Hard cap of 20 URLs per call.
        max_chars: per-URL markdown truncation. Default 6000, min 100.

    Returns:
        list of dicts, one per UNIQUE input URL after hygiene:
          • {url, markdown}                       on success
          • {url, error}                          on per-URL failure (including
                                                   empty markdown from blocked
                                                   or non-text pages)
        The first result may also carry `_warnings: [str, ...]` describing any
        input hygiene applied. Empty input returns [].
    """
    if not urls:
        return []

    # Clamp truncation budget
    max_chars = max(_MIN_MAX_CHARS, int(max_chars))

    # Input hygiene: drop non-strings/empties, dedup preserving order, then truncate.
    cleaned = [u for u in urls if isinstance(u, str) and u.strip()]
    deduped = list(dict.fromkeys(cleaned))

    warnings: list[str] = []
    if len(cleaned) < len(urls):
        warnings.append(
            f"dropped {len(urls) - len(cleaned)} non-string/empty URL(s)"
        )
    if len(deduped) < len(cleaned):
        warnings.append(
            f"deduped {len(cleaned) - len(deduped)} duplicate URL(s)"
        )
    if len(deduped) > _MAX_URLS_PER_CALL:
        warnings.append(
            f"input had {len(deduped)} URLs; only first {_MAX_URLS_PER_CALL} processed"
        )
        deduped = deduped[:_MAX_URLS_PER_CALL]

    for w in warnings:
        print(f"[tools] nimble_extract_url: {w}", file=sys.stderr)

    if not deduped:
        return []

    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_MAX_PARALLEL_EXTRACTS, len(deduped))
    ) as ex:
        futures = [ex.submit(_extract_one, url, max_chars) for url in deduped]
        for url, future in zip(deduped, futures):
            try:
                results.append(future.result(timeout=_PER_EXTRACT_TIMEOUT_SEC))
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "url": url,
                    "error": f"{type(exc).__name__}: {exc}",
                })

    # Attach warnings to the first result so the agent sees them in-band.
    if warnings and results:
        results[0] = {**results[0], "_warnings": warnings}

    return results
