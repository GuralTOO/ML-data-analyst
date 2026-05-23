// REST client for the chat-sessions surface served by backend/server.py.
// Streams (chat turns, sub-agent turns) live in api.js; this module is the
// CRUD half: list / create / get / delete / rename root chat sessions.

const API_BASE = '/api';

async function jsonOrThrow(resp, action) {
  if (!resp.ok) {
    throw new Error(`${action} failed: ${resp.status}`);
  }
  return resp.json();
}

/** List all sessions, newest-updated first.
 *  Each row: { session_id, title, created_at, updated_at, run_count } */
export async function listSessions() {
  const resp = await fetch(`${API_BASE}/sessions`);
  const data = await jsonOrThrow(resp, 'list sessions');
  return data.sessions || [];
}

/** Create an empty session and return { session_id, title }. Useful for the
 *  "+ New chat" button when we want an id before the user has typed. */
export async function createSession() {
  const resp = await fetch(`${API_BASE}/sessions`, { method: 'POST' });
  return jsonOrThrow(resp, 'create session');
}

/** Fetch session metadata + rehydrated message thread.
 *  Returns { session_id, title, created_at, updated_at, messages, sub_agents }. */
export async function getSession(sessionId) {
  const resp = await fetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}`);
  return jsonOrThrow(resp, 'get session');
}

/** Delete a session and all its persisted history. */
export async function deleteSession(sessionId) {
  const resp = await fetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  });
  return jsonOrThrow(resp, 'delete session');
}

/** Set or clear the session title. Pass null/empty to clear. */
export async function renameSession(sessionId, title) {
  const resp = await fetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  return jsonOrThrow(resp, 'rename session');
}
