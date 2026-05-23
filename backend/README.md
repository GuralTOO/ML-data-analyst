# Backend ClickHouse Setup

This backend uses local ClickHouse for dataset understanding experiments.

Install backend dependencies from the project root:

```bash
.venv/bin/python -m pip install -r backend/requirements.txt
```

`agent-core` is installed from the public GitHub repository
`https://github.com/NGXT-Inc/agent_core.git`, not from a local checkout.

Optionally start a persistent local ClickHouse server:

```bash
cd backend
docker compose up -d clickhouse
```

Check it:

```bash
curl http://localhost:8123/ping
```

This is optional for most dataset-understanding work. If the server is not
running, the tools fall back to `backend/bin/clickhouse local`.

Profile the first Hugging Face dataset:

```bash
cd ..
.venv/bin/python -m backend.clickhouse.profile_hf_dataset TuringEnterprises/Open-MM-RL
```

The profiler reads Hugging Face Dataset Viewer metadata, finds converted Parquet files, and asks local ClickHouse to inspect those remote Parquet files. The default `auto` mode full-profiles small config/splits and switches to sample-first profiling for large ones.

If Docker Desktop is not running, install the standalone ClickHouse binary in `backend/bin`:

```bash
cd backend/bin
curl https://clickhouse.com/ | sh
```

The profiler automatically falls back to `backend/bin/clickhouse local`, which is enough for remote-read profiling and does not require a server.

Force sample-only or full profiling:

```bash
.venv/bin/python -m backend.clickhouse.profile_hf_dataset sensenova/SenseNova-SI-8M --mode sample
.venv/bin/python -m backend.clickhouse.profile_hf_dataset TuringEnterprises/Open-MM-RL --mode full
```

Reusable ClickHouse logic lives under `backend/clickhouse`. The main chat agent
does not receive these primitives directly. It receives one messaging tool:

- `message(agent="agent_DS:org/name", message="...")` — sends instructions or
  follow-up questions to the persistent `DatasetAnalysisAgent` for that dataset.
  The backend creates the dataset agent if needed, otherwise it appends to the
  existing agent conversation.

That specialist agent receives a deliberately small ClickHouse surface from
`backend/tools`:

- `analyze_hf_dataset` — broad dataset understanding: schema, split sizes,
  samples, safe ClickHouse profiling, column roles/stats, and profile artifact.
- `query_hf_dataset_with_clickhouse` — scalpel SQL for targeted follow-up after
  analysis identifies a config, split, and schema.

The `backend/tools/profile_hf_dataset.py` path remains as a compatibility CLI
wrapper, but agent-visible tools are registered through `backend/tools/__init__.py`.

## Agent session management

Root chat sessions are managed by `backend/agents/session_manager.py`. Each chat
session has one cached `DatasetChatAgent`, one local SQLite conversation store,
and one per-session lock. That lock serializes turns for the same chat session so
two requests cannot mutate the same agent history concurrently.

Sub-agent events are routed through `backend/agents/agent_registry.py`. The root
agent registers for the duration of a turn, and each dataset has one persistent
`DatasetAnalysisAgent` per root chat session with a stable child session id:

```text
<chat_session_id>:agent_DS:<normalized_repo_id>
```

The child is cached after it runs, so later messages from either the main agent
or the user continue the same conversation. The active child is registered only
while a turn is running, which lets SSE events route to the correct UI tab.
Dataset analysis turns also acquire a process-local lock keyed by normalized
Hugging Face `repo_id`, so two sub-agents cannot actively work the same dataset
at the same time.

When a browser reopens an existing chat, `GET /api/sessions/:id` returns the
persisted root messages plus all known dataset sub-agents. If any child is still
running, the frontend attaches to
`GET /api/sessions/:chat_id/sub-agents/:sub_session_id/events`, a read-only SSE
feed that streams that child agent's live tool and completion events without
starting a new turn.

Local durable state lives in `backend/agent_state/agent_sessions.sqlite3`:

- `conversations` stores agent-core replay history.
- `agent_runs` stores one row per agent run.
- `agent_events` stores streamed agent-core events.
- `conversation_snapshots` stores post-turn root snapshots.

Dataset container sessions are tracked under `backend/dataset_sessions`. Every
ClickHouse-backed interaction resets a 10 minute idle timer for that dataset.
When the timer expires, the lifecycle helper marks the session ejected and
stops the deterministic per-dataset Docker container name if such a container
exists. This keeps future per-dataset worker containers warm briefly without
leaving them running indefinitely.

Per-dataset workers use `clickhouse/clickhouse-server:latest` by default and
run `clickhouse local` through `docker exec`. Automatic profiling will try a
worker only when the image is already available locally, then fall back to the
host server or `backend/bin/clickhouse local`. Worker start/status/eject helpers
remain internal backend lifecycle controls rather than normal agent tools.
