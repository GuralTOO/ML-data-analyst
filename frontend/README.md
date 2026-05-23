# Frontend

Vite + React UI for the dataset-finder agent. Visual patterns ported from
the Papyrus chat surface (`SubAgentCard`, `MarkdownContent`, JSON syntax
highlighting, tool tree).

## Run

```bash
cd frontend
npm install
npm run dev      # http://localhost:4000
```

The dev server proxies `/api/*` → `http://localhost:5001` (where a backend
HTTP wrapper around `DatasetChatAgent` is expected to live).

While no backend HTTP server exists yet, the UI runs against a mock event
stream in `src/lib/mockEvents.js`. Toggle with the `?mock=1` query param
(default) or `?mock=0` once the real backend is up.

## Structure

- `src/components/ChatPanel.jsx` — main agent chat thread + activity feed
- `src/components/DatasetAgentModal.jsx` — per-dataset sub-agent modal
- `src/components/MarkdownContent.jsx` — lightweight markdown renderer (ported)
- `src/components/JsonBlock.jsx` — JSON syntax highlighter (ported)
- `src/hooks/useAgentChat.js` — message + activity + dataset state machine
- `src/lib/api.js` — backend HTTP client (currently stubbed)
- `src/lib/mockEvents.js` — canned event sequence for offline dev

## Wiring to the backend

The backend currently only exposes `python -m backend.main` as a CLI smoke
test. To turn it into an HTTP server the UI can hit:

1. Add a FastAPI (or Flask) endpoint at `POST /api/chat` that takes
   `{ query, session_id? }` and returns a streaming response.
2. Bridge `agent_core`'s `EventBus` + `on_text_delta` into Server-Sent
   Events (SSE) — each backend event becomes one SSE message matching the
   protocol in `src/lib/api.js` (`event_type`, `payload`).
3. Add a parallel endpoint per dataset sub-agent for the
   `DatasetAgentModal` chat surface.
