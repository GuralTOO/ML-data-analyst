# Dataset Finder

Local agentic search for finding and stress-testing Hugging Face datasets.

You chat with a discovery agent, it searches the live web, ranks real HF dataset repos, and marks each candidate as `DS:org/name`. Click a dataset chip and the app opens a persistent specialist agent that profiles the actual dataset through ClickHouse.

```mermaid
flowchart LR
  U["User"] --> UI["React chat UI"]
  UI -->|SSE| API["FastAPI backend"]
  API --> Root["DatasetChatAgent"]
  Root --> N["Nimble search + extract"]
  Root --> HF["Hugging Face metadata"]
  Root -->|message(agent_DS:org/name)| DS["DatasetAnalysisAgent"]
  DS --> CH["ClickHouse over HF Parquet"]
  API --> DB["SQLite session store"]
  API --> UI
```

## What It Does

- Finds datasets with parallel Nimble SERP calls plus one semantic AI search.
- Keeps every chat and dataset specialist persistent in local SQLite.
- Streams visible tool activity to the UI with Server-Sent Events.
- Gives selected datasets their own specialist agent, tab, memory, and ClickHouse-backed data checks.
- Uses ClickHouse to inspect remote Hugging Face Parquet without copying whole datasets locally.

## Stack

| Layer | Dependencies |
| --- | --- |
| Backend | Python 3.13, FastAPI, `agent-core`, OpenRouter, Nimble, Hugging Face Hub, SQLite. |
| Data analysis | ClickHouse server, per-dataset Docker workers, or `backend/bin/clickhouse local`. |
| Frontend | Vite 5, React 18, Tailwind 4, lucide-react. |

`agent-core` is installed from `git+https://github.com/NGXT-Inc/agent_core.git` through `backend/requirements.txt`.

## Quick Start

Prereqs: Python 3.13, Node 22+, npm, and optional Docker Desktop for a persistent ClickHouse server.

```bash
cp .env.example .env
# Fill in OPEN_ROUTER_API_KEY, NIMBLE_API_KEY, HF_TOKEN

.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m backend.server
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:4000](http://localhost:4000).

## Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPEN_ROUTER_API_KEY` | Yes | Model access for `deepseek/deepseek-v4-pro`. |
| `NIMBLE_API_KEY` | Yes | Live SERP, AI search, and rendered extraction. |
| `HF_TOKEN` | Yes | Hugging Face Hub and datasets-server access. |
| `PORT` | No | Backend port, default `5001`. |
| `RELOAD` | No | Set `1` for uvicorn reload. |
| `CLICKHOUSE_DATASET_WORKERS` | No | Enable per-dataset Docker workers, default on. |
| `CLICKHOUSE_DATASET_WORKER_PULL` | No | Allow automatic worker image pulls, default off. |

## Docs

- [Architecture](docs/architecture.md) - agents, persistence, routing, and concurrency.
- [API](docs/api.md) - HTTP endpoints and SSE frame shapes.
- [ClickHouse](docs/clickhouse.md) - how real-data profiling scales.
- [Nimble](docs/nimble.md) - live discovery, extraction, and parallel search.
- [Vision](docs/vision.md) - the product and engineering intent.

## Useful Commands

```bash
.venv/bin/python -m backend.main
.venv/bin/python -m backend.clickhouse.profile_hf_dataset TuringEnterprises/Open-MM-RL
cd backend && docker compose up -d clickhouse
cd frontend && npm run build
```

Generated local state and artifacts:

- `backend/agent_state/agent_sessions.sqlite3` - gitignored
- `backend/dataset_sessions/*.json` - gitignored
- `backend/clickhouse/cache/` - gitignored
- `backend/profiles/*.profile.json` - reusable profile artifacts
