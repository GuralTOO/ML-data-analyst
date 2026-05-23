// Backend HTTP client for the agent SSE streams.
// The dev server proxies /api/* to localhost:5001 (see vite.config.js).
//
// PROTOCOL — root agent stream
//   POST /api/chat/stream  { query, session_id? }
//     → SSE of { type, payload } frames.
//     The first frame is always { type: 'session', payload: { session_id } }
//     so unsaved clients can capture the server-minted id.
//
// PROTOCOL — per-sub-agent follow-up stream
//   POST /api/sessions/:chat_id/sub-agents/:sub_session_id/turn  { query }
//     → SSE scoped to one dataset-analysis sub-agent. Same frame shape.
//
// PROTOCOL — per-sub-agent live feed
//   GET /api/sessions/:chat_id/sub-agents/:sub_session_id/events
//     → read-only SSE for an existing/running dataset-analysis sub-agent.
//
// Each event payload carries identity fields the UI uses for routing:
//   { agent_id, agent_type, parent_agent, agent_session_id, ... }
// - agent_type === 'dataset_chat'      → root activity feed
// - agent_type === 'dataset_analysis'  → persistent dataset-agent tab keyed by agent_session_id

const API_BASE = '/api';

/**
 * Send a message to the root DatasetChatAgent. Returns an async iterator of
 * parsed events.
 */
export async function* streamMainChat({ query, sessionId, signal }) {
  const resp = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify({ query, session_id: sessionId }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`chat stream failed: ${resp.status}`);
  }
  yield* parseSseStream(resp.body);
}

/**
 * Run a turn against a persistent dataset sub-agent. Stream events flow back
 * with the same shape as the main chat stream but scoped to one
 * agent_session_id.
 */
export async function* streamSubAgentTurn({ chatId, subSessionId, query, signal }) {
  const path = `${API_BASE}/sessions/${encodeURIComponent(chatId)}`
    + `/sub-agents/${encodeURIComponent(subSessionId)}/turn`;
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify({ query }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`sub-agent stream failed: ${resp.status}`);
  }
  yield* parseSseStream(resp.body);
}

/**
 * Subscribe to an existing dataset sub-agent without starting a new turn.
 * Used when a session is reopened while a specialist is still running.
 */
export async function* streamSubAgentEvents({ chatId, subSessionId, signal }) {
  const path = `${API_BASE}/sessions/${encodeURIComponent(chatId)}`
    + `/sub-agents/${encodeURIComponent(subSessionId)}/events`;
  const resp = await fetch(path, {
    method: 'GET',
    headers: { accept: 'text/event-stream' },
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`sub-agent live feed failed: ${resp.status}`);
  }
  yield* parseSseStream(resp.body);
}

// ---------------- Internals ----------------

async function* parseSseStream(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLine = frame.split('\n').find(l => l.startsWith('data:'));
      if (!dataLine) continue;
      const json = dataLine.slice(5).trim();
      if (!json || json === '[DONE]') continue;
      try { yield JSON.parse(json); } catch { /* skip malformed frame */ }
    }
  }
}
