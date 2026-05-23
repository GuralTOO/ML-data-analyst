# Backend

FastAPI, SSE, SQLite persistence, agent sessions, Nimble tools, Hugging Face tools, and ClickHouse-backed dataset analysis.

Run from the repo root:

```bash
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m backend.server
```

Smoke test:

```bash
.venv/bin/python -m backend.main
```

See the root [README](../README.md) and docs:

- [Architecture](../docs/architecture.md)
- [API](../docs/api.md)
- [ClickHouse](../docs/clickhouse.md)
- [Nimble](../docs/nimble.md)
