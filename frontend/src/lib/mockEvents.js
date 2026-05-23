// Canned event sequence that mirrors what the real backend SSE stream
// will look like. Lets the UI run end-to-end before /api/chat/stream
// exists. Swap for the real stream once the backend HTTP wrapper lands.

const DELAY_BETWEEN = 280;

function uid(prefix) {
  return `${prefix}_${Math.random().toString(36).slice(2, 8)}`;
}

const FINAL_TEXT = `Found 3 strong candidates for a multimodal STEM benchmark on Hugging Face.

**Top pick:** TIGER-Lab/MMLU-Pro is a 12K-question multi-domain reasoning benchmark — broad coverage, well-cited, but text-only.

For your multimodal angle, **TuringEnterprises/Open-MM-RL** and **sensenova/SenseNova-SI-8M** are the two repos that surfaced in both the SERP and AI search pipelines — strong signal. SenseNova has 8M scientific-reasoning instances with images; Open-MM-RL pairs problems with verifiable answers and is smaller (~50K rows).

| Dataset | Rows | Modality | Verifiable |
|---|---|---|---|
| TuringEnterprises/Open-MM-RL | ~50K | Image + Text | ✅ |
| sensenova/SenseNova-SI-8M | 8M | Image + Text | partial |
| TIGER-Lab/MMLU-Pro | 12K | Text only | ✅ |

I'd start by deep-diving \`TuringEnterprises/Open-MM-RL\` — small enough to fully profile, verifiable answers match your evaluator workflow. Click "Analyze" on the card above to spin up its dedicated worker.`;

export async function* mockMainChatStream({ query }) {
  const evt = (type, payload) => ({ type, payload });

  yield evt('agent_start', { agent_type: 'dataset_chat' });
  await sleep(DELAY_BETWEEN);

  // Parallel discovery fan-out
  const serp1 = uid('t'), serp2 = uid('t'), serp3 = uid('t'), ai = uid('t');
  yield evt('tool_start', { id: serp1, tool: 'nimble_serp_search_hf', args_hint: 'multimodal STEM benchmark physics math' });
  yield evt('tool_start', { id: serp2, tool: 'nimble_serp_search_hf', args_hint: 'visual question answering science PhD' });
  yield evt('tool_start', { id: serp3, tool: 'nimble_serp_search_hf', args_hint: 'multimodal verifiable reward dataset' });
  yield evt('tool_start', { id: ai,    tool: 'nimble_ai_search_hf', args_hint: query.slice(0, 80) });
  await sleep(900);
  yield evt('tool_end', { id: serp1, tool: 'nimble_serp_search_hf', status: 'success', result_summary: '8 HF dataset hits' });
  yield evt('tool_end', { id: serp2, tool: 'nimble_serp_search_hf', status: 'success', result_summary: '6 HF dataset hits' });
  yield evt('tool_end', { id: serp3, tool: 'nimble_serp_search_hf', status: 'success', result_summary: '7 HF dataset hits' });
  yield evt('tool_end', { id: ai,    tool: 'nimble_ai_search_hf', status: 'success', result_summary: '10 HF dataset hits (4 overlap with SERP)' });
  await sleep(DELAY_BETWEEN);

  // Metadata fan-out
  const info1 = uid('t'), info2 = uid('t'), info3 = uid('t');
  yield evt('tool_start', { id: info1, tool: 'get_hf_dataset_info', args_hint: 'TuringEnterprises/Open-MM-RL' });
  yield evt('tool_start', { id: info2, tool: 'get_hf_dataset_info', args_hint: 'sensenova/SenseNova-SI-8M' });
  yield evt('tool_start', { id: info3, tool: 'get_hf_dataset_info', args_hint: 'TIGER-Lab/MMLU-Pro' });
  await sleep(700);
  yield evt('tool_end', { id: info1, tool: 'get_hf_dataset_info', status: 'success', result_summary: '50K rows, image+text, MIT license' });
  yield evt('tool_end', { id: info2, tool: 'get_hf_dataset_info', status: 'success', result_summary: '8M rows, multimodal, apache-2.0' });
  yield evt('tool_end', { id: info3, tool: 'get_hf_dataset_info', status: 'success', result_summary: '12K rows, text-only, MIT license' });
  await sleep(DELAY_BETWEEN);

  // Card reads for top 2
  const card1 = uid('t'), card2 = uid('t');
  yield evt('tool_start', { id: card1, tool: 'get_hf_dataset_card', args_hint: 'TuringEnterprises/Open-MM-RL' });
  yield evt('tool_start', { id: card2, tool: 'get_hf_dataset_card', args_hint: 'sensenova/SenseNova-SI-8M' });
  await sleep(800);
  yield evt('tool_end', { id: card1, tool: 'get_hf_dataset_card', status: 'success', result_summary: '5.2KB card' });
  yield evt('tool_end', { id: card2, tool: 'get_hf_dataset_card', status: 'success', result_summary: '4.8KB card' });
  await sleep(DELAY_BETWEEN);

  // Stream the final response one chunk at a time
  for (const chunk of streamWords(FINAL_TEXT, 5)) {
    yield evt('text_delta', { delta: chunk });
    await sleep(35);
  }

  yield evt('agent_end', { agent_type: 'dataset_chat', success: true });
}

// Mock per-dataset sub-agent activity for the modal demo.
export async function* mockDatasetAgentStream({ repoId, query }) {
  const evt = (type, payload) => ({ type, payload });
  yield evt('agent_start', { agent_type: `dataset:${repoId}` });
  await sleep(400);

  const t1 = uid('t'), t2 = uid('t'), t3 = uid('t');
  yield evt('tool_start', { id: t1, tool: 'start_hf_dataset_clickhouse_worker', args_hint: repoId });
  await sleep(700);
  yield evt('tool_end', { id: t1, tool: 'start_hf_dataset_clickhouse_worker', status: 'success', result_summary: 'worker warm (clickhouse-local)' });

  yield evt('tool_start', { id: t2, tool: 'inspect_hf_dataset_structure', args_hint: repoId });
  await sleep(900);
  yield evt('tool_end', { id: t2, tool: 'inspect_hf_dataset_structure', status: 'success', result_summary: '1 config / 2 splits / 12 columns' });

  yield evt('tool_start', { id: t3, tool: 'profile_hf_dataset', args_hint: `${repoId} (mode=auto)` });
  await sleep(1400);
  yield evt('tool_end', { id: t3, tool: 'profile_hf_dataset', status: 'success', result_summary: '50K rows profiled, 4 image columns' });

  const reply = query
    ? `For your question "${query}":\n\nThe profile shows 4 image columns and a verifiable answer column. ~50K examples split 80/10/10. Domain distribution favors physics (62%) over math (38%).`
    : `Worker warm. The dataset has 4 image columns and a verifiable answer field. ~50K rows across 1 config / 2 splits. Ready for follow-up questions — try "show me the domain distribution" or "what answer types are in the test split?".`;
  for (const chunk of streamWords(reply, 4)) {
    yield evt('text_delta', { delta: chunk });
    await sleep(30);
  }
  yield evt('agent_end', { agent_type: `dataset:${repoId}`, success: true });
}

// --- helpers ---

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function* streamWords(text, wordsPerChunk = 4) {
  const tokens = text.match(/\s+|\S+/g) || [];
  let buf = '';
  let words = 0;
  for (const tok of tokens) {
    buf += tok;
    if (/\S/.test(tok)) {
      words++;
      if (words >= wordsPerChunk) { yield buf; buf = ''; words = 0; }
    }
  }
  if (buf) yield buf;
}
