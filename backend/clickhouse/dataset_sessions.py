"""Idle lifecycle tracking for per-dataset processing containers.

The session file records durable state for a selected dataset and schedules
cleanup inside the running Python process. Worker containers use deterministic
names so the cleanup path can stop exactly the matching dataset container after
the configured idle TTL.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import threading
from typing import Any


BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
SESSION_DIR = BACKEND_DIR / "dataset_sessions"
DEFAULT_IDLE_TTL_SECONDS = 10 * 60

_TIMERS: dict[str, threading.Timer] = {}
_LOCK = threading.Lock()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def _iso(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_name(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or "dataset"


def container_name_for_dataset(repo_id: str) -> str:
    digest = hashlib.sha1(repo_id.encode("utf-8")).hexdigest()[:10]
    slug = _safe_name(repo_id)[:42].strip("-")
    return f"dataset-{slug}-{digest}"


def _session_path(repo_id: str) -> pathlib.Path:
    digest = hashlib.sha1(repo_id.encode("utf-8")).hexdigest()[:16]
    return SESSION_DIR / f"{_safe_name(repo_id)[:80]}-{digest}.json"


def _read_session(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def _write_session(path: pathlib.Path, session: dict[str, Any]) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2), encoding="utf-8")


def _base_session(repo_id: str, path: pathlib.Path, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    session = dict(existing or {})
    session["repo_id"] = repo_id
    session["container_name"] = session.get("container_name") or container_name_for_dataset(repo_id)
    session["session_file"] = str(path)
    return session


def _docker_stop(container_name: str) -> dict[str, Any]:
    """Stop the named Docker container if it exists.

    This is intentionally narrow: only the deterministic container name for our
    dataset session is targeted. Missing Docker or a missing container is a
    non-fatal condition because worker containers are not required for all runs.
    """
    inspect = subprocess.run(
        ["docker", "container", "inspect", container_name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if inspect.returncode != 0:
        return {"attempted": False, "container_found": False, "message": "container not found"}

    stop = subprocess.run(
        ["docker", "stop", container_name],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return {
        "attempted": True,
        "container_found": True,
        "returncode": stop.returncode,
        "stdout": stop.stdout.strip(),
        "stderr": stop.stderr.strip(),
    }


def cleanup_expired_dataset_sessions(
    *,
    now: dt.datetime | None = None,
    stop_containers: bool = True,
) -> list[dict[str, Any]]:
    """Stop containers whose session has been idle past its expiry."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    now = now or _now()
    ejected = []

    for path in SESSION_DIR.glob("*.json"):
        session = _read_session(path)
        if not session:
            continue
        expires_at = _parse_iso(session.get("expires_at"))
        if not expires_at or expires_at > now:
            continue
        if session.get("state") == "ejected":
            continue

        docker_result = None
        if stop_containers and session.get("container_name"):
            try:
                docker_result = _docker_stop(session["container_name"])
            except Exception as exc:  # noqa: BLE001
                docker_result = {"attempted": True, "error": f"{type(exc).__name__}: {exc}"}

        session["state"] = "ejected"
        session["ejected_at"] = _iso(now)
        session["eject_reason"] = "idle_timeout"
        session["last_docker_stop"] = docker_result
        _write_session(path, session)
        ejected.append(session)

    return ejected


def _schedule_cleanup(repo_id: str, ttl_seconds: int) -> None:
    def run_cleanup() -> None:
        cleanup_expired_dataset_sessions()

    with _LOCK:
        old_timer = _TIMERS.pop(repo_id, None)
        if old_timer:
            old_timer.cancel()
        timer = threading.Timer(ttl_seconds + 1, run_cleanup)
        timer.daemon = True
        timer.start()
        _TIMERS[repo_id] = timer


def touch_dataset_session(
    repo_id: str,
    *,
    interaction: str,
    ttl_seconds: int = DEFAULT_IDLE_TTL_SECONDS,
    schedule_cleanup: bool = True,
) -> dict[str, Any]:
    """Mark a dataset as active and reset its idle ejection timer."""
    cleanup_expired_dataset_sessions()
    now = _now()
    path = _session_path(repo_id)
    existing = _read_session(path) or {}
    session = _base_session(repo_id, path, existing)
    created_at = session.get("created_at") or _iso(now)
    interactions = int(existing.get("interactions", 0)) + 1
    expires_at = now + dt.timedelta(seconds=ttl_seconds)

    session.update(
        {
            "state": "active",
            "created_at": created_at,
            "last_interaction_at": _iso(now),
            "last_interaction": interaction,
            "expires_at": _iso(expires_at),
            "idle_ttl_seconds": ttl_seconds,
            "interactions": interactions,
        }
    )
    for stale_key in ("ejected_at", "eject_reason", "last_docker_stop"):
        session.pop(stale_key, None)
    _write_session(path, session)
    if schedule_cleanup:
        _schedule_cleanup(repo_id, ttl_seconds)
    return session


def update_dataset_session(
    repo_id: str,
    updates: dict[str, Any],
    *,
    create: bool = False,
) -> dict[str, Any] | None:
    """Merge durable metadata into a dataset session.

    Worker lifecycle code uses this to persist Docker status without losing
    the idle timers maintained by touch_dataset_session.
    """
    path = _session_path(repo_id)
    existing = _read_session(path)
    if existing is None and not create:
        return None
    session = _base_session(repo_id, path, existing)
    session.update(updates)
    _write_session(path, session)
    return session


def mark_dataset_session_ejected(
    repo_id: str,
    *,
    reason: str,
    docker_result: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Mark a dataset session ejected after an idle or manual stop."""
    now = now or _now()
    session = update_dataset_session(
        repo_id,
        {
            "state": "ejected",
            "ejected_at": _iso(now),
            "eject_reason": reason,
            "last_docker_stop": docker_result,
        },
        create=True,
    )
    assert session is not None
    return session


def get_dataset_session(repo_id: str) -> dict[str, Any] | None:
    return _read_session(_session_path(repo_id))
