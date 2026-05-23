# Frontend

Vite + React UI for Dataset Finder: chat thread, session sidebar, and persistent dataset-agent workspace.

```bash
cd frontend
npm install
npm run dev      # http://localhost:4000
```

The dev server proxies `/api/*` to `http://localhost:5001`.

Important files:

- `src/hooks/useAgentChat.js` - session state, SSE ingestion, dataset-agent tabs.
- `src/lib/api.js` - streaming client.
- `src/lib/sessionsApi.js` - session CRUD client.
- `src/components/MarkdownContent.jsx` - `DS:org/name` clickable chips.
- `src/components/DatasetWorkspace.jsx` - right-side specialist workspace.

Use `?mock=1` for the canned offline stream in `src/lib/mockEvents.js`.

See the root [README](../README.md), [Architecture](../docs/architecture.md), and [API](../docs/api.md).
