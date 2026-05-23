// Display labels for the agent's tools. Names match those registered in
// backend/tools/__init__.py.
export const TOOL_LABELS = {
  // Discovery (Nimble)
  nimble_serp_search_hf: 'Google search for HF datasets',
  nimble_ai_search_hf: 'Nimble AI search',
  nimble_web_search: 'Web search',
  nimble_extract_url: 'Reading page',

  // HF metadata
  search_hf_datasets: 'HF native search',
  get_hf_dataset_info: 'Dataset info',
  get_hf_dataset_card: 'Reading dataset card',
  preview_hf_dataset: 'Previewing rows',

  // ClickHouse analysis
  inspect_hf_dataset_structure: 'Inspecting structure',
  profile_hf_dataset: 'Profiling dataset',
  query_hf_dataset_with_clickhouse: 'Querying via ClickHouse',
  start_hf_dataset_clickhouse_worker: 'Starting worker',
  get_hf_dataset_clickhouse_worker_status: 'Worker status',
  eject_hf_dataset_clickhouse_worker: 'Stopping worker',
};

// Tool name → kind, for routing icons + activity grouping.
// 'dataset_agent' is the synthetic "tool" we use when the main agent spawns
// a per-dataset sub-agent — it has no backend tool of the same name, but
// the activity feed renders it as a sub-agent card.
export const SUB_AGENT_TOOLS = new Set(['dataset_agent']);
