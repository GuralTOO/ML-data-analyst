# API

The frontend talks to the backend through REST for session CRUD and Server-Sent Events for agent turns.

Base URL in development:

```text
frontend http://localhost:4000
backend  http://localhost:5001
proxy    /api/* -> http://localhost:5001
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/chat/stream` | Run one root chat turn and stream events. |
| `GET` | `/api/sessions` | List root chat sessions. |
| `POST` | `/api/sessions` | Create an empty chat session. |
| `GET` | `/api/sessions/{session_id}` | Rehydrate messages and dataset agents. |
| `PATCH` | `/api/sessions/{session_id}` | Rename or clear a session title. |
| `DELETE` | `/api/sessions/{session_id}` | Delete a session and child histories. |
| `POST` | `/api/sessions/{chat_id}/sub-agents/{sub_session_id}/turn` | Run a follow-up turn against one dataset agent. |
| `GET` | `/api/sessions/{chat_id}/sub-agents/{sub_session_id}/events` | Attach read-only to an already-running dataset agent. |
| `GET` | `/api/health` | Health, uptime, and cached session count. |

`sub_session_id` can contain `:` and `/`, so clients should URL-encode it as one path value.

## Root Turn

Request:

```json
{
  "query": "Find multimodal STEM benchmark datasets",
  "session_id": "chat_abc123"
}
```

`session_id` is optional. If omitted, the server mints one and sends it as the first SSE frame:

```json
{
  "type": "session",
  "payload": { "session_id": "chat_abc123" }
}
```

## SSE Frames

Every data frame is:

```text
data: {"type":"...", "payload": {...}}
```

Keep-alives are comments:

```text
: keep-alive
```

Common frame types:

| Type | Meaning |
| --- | --- |
| `session` | Stream identity. Always first on turn streams. |
| `agent_start` | Agent began a run. Dataset-agent starts open tabs in the UI. |
| `tool_start` | Tool call began. |
| `tool_end` | Tool call completed or failed. |
| `text_delta` | Streamed assistant text for the root agent. |
| `agent_end` | Agent run completed or failed. |
| `sub_agent_snapshot` | Current stored/live state when attaching to an existing dataset agent. |

Tool frame:

```json
{
  "type": "tool_start",
  "payload": {
    "agent_type": "dataset_chat",
    "agent_session_id": "chat_abc123",
    "id": "tool-call-id",
    "tool": "nimble_serp_search_hf",
    "args_hint": "site:huggingface.co/datasets STEM benchmark"
  }
}
```

Dataset specialist completion:

```json
{
  "type": "agent_end",
  "payload": {
    "agent_type": "dataset_analysis",
    "agent_session_id": "chat_abc123:agent_DS:org/name",
    "success": true,
    "result": "Concise dataset report..."
  }
}
```

## Session Detail Shape

`GET /api/sessions/{session_id}` returns:

```json
{
  "session_id": "chat_abc123",
  "title": "Find multimodal STEM benchmark datasets",
  "created_at": "2026-05-23 15:00:00",
  "updated_at": "2026-05-23 15:04:00",
  "messages": [
    { "id": "msg_1", "role": "user", "content": "..." },
    { "id": "msg_2", "role": "assistant", "content": "...", "activity": [] }
  ],
  "sub_agents": [
    {
      "id": "chat_abc123:agent_DS:org/name",
      "repoId": "org/name",
      "status": "success",
      "messages": []
    }
  ]
}
```

## Frontend Clients

- Streams: `frontend/src/lib/api.js`
- Session CRUD: `frontend/src/lib/sessionsApi.js`
- State routing: `frontend/src/hooks/useAgentChat.js`

