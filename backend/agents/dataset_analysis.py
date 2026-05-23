"""Dedicated persistent dataset-analysis agent.

The root chat agent and the user-facing UI both communicate with stable
per-dataset child agents. This keeps search/recommendation separate from
ClickHouse-backed dataset understanding while preserving continuity inside each
dataset actor.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from agent_core import Agent, OpenRouterProvider

from backend.agents import agent_registry
from backend.agents.sub_agent_manager import (
    dataset_work_locks,
    extract_repo_id,
    sub_agent_sessions,
)
from backend.tools.clickhouse import (
    analyze_hf_dataset as _analyze_hf_dataset,
    query_hf_dataset_with_clickhouse as _query_hf_dataset_with_clickhouse,
)
from backend.tools.hf import preview_hf_dataset as _preview_hf_dataset


DATASET_ANALYSIS_PROMPT = """\
You are a specialist dataset-analysis agent.

Your job is to understand one selected Hugging Face dataset deeply enough to
tell the root agent whether it is useful, what is inside it, and what risks or
follow-up checks matter.

You do not search for datasets. You analyze the dataset given in the task or
context.

Required workflow:
  1. Identify the Hugging Face repo_id from the task or context.
  2. Call analyze_hf_dataset(repo_id, depth="auto") first unless the context
     already includes a fresh analysis result.
  3. Use query_hf_dataset_with_clickhouse only as a scalpel after you know the
     valid config, split, and schema. Use it for specific checks such as label
     distribution, null/empty fields, duplicate IDs, suspicious values, row-level
     inspection, or domain balance.
  4. Use preview_hf_dataset when you need a few more raw examples.

Use bounded targeted checks as needed, but do not repeat equivalent SQL queries.

Be conservative with full scans. Use depth="sample" for obviously large
datasets unless the task explicitly asks for a full scan.

Return a concise structured answer with:
  - dataset id
  - configs/splits inspected
  - schema and semantic roles
  - important sample observations
  - quality/coverage risks
  - targeted SQL checks you ran and what they showed
  - recommended next steps

Do not add storage, provenance, planned-size, or licensing claims unless they
are directly present in tool output or supplied context. For image/media columns,
state only what the schema and samples show; distinguish bytes, paths, URLs, and
unknown storage explicitly.
"""


class DatasetAnalysisAgent(Agent):
    """Specialist agent for selected-dataset deep analysis."""

    name = "dataset_analysis"
    DEFAULT_MODEL = "deepseek/deepseek-v4-pro"
    DEFAULT_STREAMING = False
    MAX_ITERATIONS = 50
    MAX_PARALLEL_TOOLS = 5
    system_prompt = DATASET_ANALYSIS_PROMPT

    def __init__(
        self,
        session_id: str | None = None,
        conversation_store=None,
        parent_agent: str | None = None,
        cancel_event=None,
    ) -> None:
        provider = OpenRouterProvider(
            app_name="hackathon-dataset-analysis",
            response_cache=True,
            response_cache_ttl_seconds=300,
        )
        super().__init__(
            provider=provider,
            parent_agent=parent_agent,
            session_id=session_id,
            conversation_store=conversation_store,
            cancel_event=cancel_event,
        )
        self.register_tool(self.analyze_hf_dataset)
        self.register_tool(self.query_hf_dataset_with_clickhouse)
        self.register_tool(self.preview_hf_dataset)

    def analyze_hf_dataset(
        self,
        repo_id: str,
        configs: str | None = None,
        splits: str | None = None,
        depth: str = "auto",
        sample_limit: int = 3,
    ) -> dict:
        """Broadly analyze one selected Hugging Face dataset with ClickHouse."""
        return _analyze_hf_dataset(
            repo_id,
            configs=configs,
            splits=splits,
            depth=depth,
            sample_limit=sample_limit,
        )

    def query_hf_dataset_with_clickhouse(
        self,
        repo_id: str,
        config: str,
        split: str,
        select_sql: str,
        limit: int = 20,
        allow_large: bool = False,
    ) -> dict:
        """Run one constrained read-only SQL SELECT against a dataset split."""
        return _query_hf_dataset_with_clickhouse(
            repo_id,
            config=config,
            split=split,
            select_sql=select_sql,
            limit=limit,
            allow_large=allow_large,
        )

    def preview_hf_dataset(
        self,
        repo_id: str,
        config: str = "default",
        split: str = "train",
        limit: int = 5,
    ) -> dict:
        """Fetch a few extra example rows from the HF datasets-server."""
        return _preview_hf_dataset(repo_id, config=config, split=split, limit=limit)


def make_dataset_analysis_tool(
    *,
    session_id: str | None = None,
    conversation_store=None,
    parent_agent: str | None = None,
    cancel_event=None,
    turn_id_getter: Callable[[], str | None] | None = None,
) -> Callable:
    return make_dataset_message_tool(
        session_id=session_id,
        conversation_store=conversation_store,
        parent_agent=parent_agent,
        cancel_event=cancel_event,
        turn_id_getter=turn_id_getter,
    )


def make_dataset_message_tool(
    *,
    session_id: str | None = None,
    conversation_store=None,
    parent_agent: str | None = None,
    cancel_event=None,
    turn_id_getter: Callable[[], str | None] | None = None,
) -> Callable:
    """Create the root-agent tool for messaging persistent dataset agents.

    The root agent only knows the target dataset-agent name and a message. The
    backend creates or reuses the persistent sub-agent, serializes turns for the
    same dataset, and routes events back to the UI through the registry.
    """

    def message(agent: str, message: str, context: str = "") -> str:
        """Send a message to a persistent dataset-analysis agent.

        Args:
            agent: Dataset agent name, e.g. agent_DS:org/name or DS:org/name.
            message: Instruction or follow-up question for that dataset agent.
            context: Optional JSON string with metadata, card snippets, configs,
                splits, or prior analysis.

        Returns:
            The dataset-analysis specialist's response.
        """

        if not session_id:
            return "message requires an active root chat session before it can address dataset agents."

        ctx = None
        if context:
            try:
                ctx = json.loads(context)
            except json.JSONDecodeError:
                ctx = {"raw_context": context}

        repo_id = extract_repo_id(agent) or extract_repo_id(ctx) or extract_repo_id(message)
        if not repo_id:
            return (
                "message requires a concrete Hugging Face dataset agent like "
                "agent_DS:org/name or DS:org/name."
            )

        def _new_agent(sub_session_id: str) -> DatasetAnalysisAgent:
            return DatasetAnalysisAgent(
                session_id=sub_session_id,
                conversation_store=conversation_store,
                parent_agent=parent_agent,
                cancel_event=cancel_event,
            )

        managed, created = sub_agent_sessions.get_or_create_dataset_agent(
            chat_session_id=session_id,
            repo_id=repo_id,
            agent_factory=_new_agent,
            task=message,
        )
        child = managed.agent
        child_session_id = managed.sub_session_id

        agent_registry.register(
            child.instance_id,
            session_id,
            agent=child,
            agent_session_id=child_session_id,
            parent_agent=parent_agent,
            turn_id=turn_id_getter() if turn_id_getter else None,
        )
        try:
            child_prompt = message
            if created:
                child_prompt = f"You are analyzing dataset {repo_id}.\n\n{message}"
            if ctx is not None:
                context_text = json.dumps(ctx, indent=2, sort_keys=True, default=str)
                child_prompt = f"Context:\n{context_text}\n\n{child_prompt}"
            with dataset_work_locks.acquire(
                repo_id,
                chat_session_id=session_id,
                sub_session_id=child_session_id,
            ):
                with managed.lock:
                    return child.run(child_prompt)
        finally:
            agent_registry.unregister(child.instance_id)

    return message
