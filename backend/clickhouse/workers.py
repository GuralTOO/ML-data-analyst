"""Docker worker lifecycle for per-dataset ClickHouse processing.

The worker container is intentionally lightweight and idle-warm: it provides a
per-dataset process boundary for ClickHouse-local queries, shared cache/output
mounts, and deterministic cleanup through dataset_sessions.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Any

from backend.clickhouse.dataset_sessions import (
    DEFAULT_IDLE_TTL_SECONDS,
    get_dataset_session,
    mark_dataset_session_ejected,
    touch_dataset_session,
    update_dataset_session,
)


BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_WORKER_IMAGE = os.environ.get(
    "CLICKHOUSE_DATASET_WORKER_IMAGE",
    "clickhouse/clickhouse-server:latest",
)
DEFAULT_WORKER_MEMORY = os.environ.get("CLICKHOUSE_DATASET_WORKER_MEMORY", "2g")
DEFAULT_WORKER_CPUS = os.environ.get("CLICKHOUSE_DATASET_WORKER_CPUS", "2")
DEFAULT_PIDS_LIMIT = os.environ.get("CLICKHOUSE_DATASET_WORKER_PIDS_LIMIT", "512")
WORKER_CACHE_DIR = BACKEND_DIR / "clickhouse" / "cache"
WORKER_PROFILE_DIR = BACKEND_DIR / "profiles"


class DatasetWorkerError(RuntimeError):
    """Raised for expected dataset worker lifecycle failures."""


def _docker(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DatasetWorkerError("Docker CLI is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise DatasetWorkerError(f"Docker command timed out: docker {' '.join(args)}") from exc


def _result_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "docker command failed").strip()


def _docker_available() -> tuple[bool, str | None]:
    try:
        result = _docker(["info", "--format", "{{json .ServerVersion}}"], timeout=10)
    except DatasetWorkerError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, _result_error(result)
    return True, None


def _image_exists(image: str) -> bool:
    result = _docker(["image", "inspect", image], timeout=10)
    return result.returncode == 0


def _container_status(container_name: str) -> dict[str, Any]:
    result = _docker(["container", "inspect", container_name], timeout=10)
    if result.returncode != 0:
        return {
            "exists": False,
            "running": False,
            "container_name": container_name,
            "message": "container not found",
        }
    try:
        info = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise DatasetWorkerError(f"Could not parse Docker inspect output for {container_name}") from exc

    state = info.get("State") or {}
    config = info.get("Config") or {}
    return {
        "exists": True,
        "running": bool(state.get("Running")),
        "status": state.get("Status"),
        "container_name": container_name,
        "container_id": (info.get("Id") or "")[:12],
        "image": config.get("Image"),
        "created": info.get("Created"),
        "labels": config.get("Labels") or {},
        "exit_code": state.get("ExitCode"),
        "error": state.get("Error") or None,
    }


def _update_worker_status(repo_id: str, status: dict[str, Any]) -> None:
    update_dataset_session(
        repo_id,
        {
            "worker": status,
            "worker_image": status.get("image") or DEFAULT_WORKER_IMAGE,
        },
        create=True,
    )


def get_dataset_worker_status(repo_id: str) -> dict[str, Any]:
    """Return durable session state plus live Docker status when available."""
    session = get_dataset_session(repo_id) or touch_dataset_session(
        repo_id,
        interaction="worker_status",
        schedule_cleanup=False,
    )
    available, docker_error = _docker_available()
    live_status: dict[str, Any]
    if not available:
        live_status = {
            "exists": False,
            "running": False,
            "container_name": session["container_name"],
            "docker_available": False,
            "error": docker_error,
        }
    else:
        live_status = _container_status(session["container_name"])
        live_status["docker_available"] = True
    _update_worker_status(repo_id, live_status)
    return {
        "repo_id": repo_id,
        "session": get_dataset_session(repo_id),
        "worker": live_status,
    }


def ensure_dataset_worker(
    repo_id: str,
    *,
    image: str = DEFAULT_WORKER_IMAGE,
    pull_image: bool = False,
    ttl_seconds: int = DEFAULT_IDLE_TTL_SECONDS,
    interaction: str = "worker_ensure",
    touch_session: bool = True,
) -> dict[str, Any]:
    """Ensure a per-dataset Docker worker exists and is running.

    By default this will not pull a missing image; agent tools can opt into a
    pull explicitly when the user selects a dataset for deep processing.
    """
    session = get_dataset_session(repo_id)
    if touch_session or session is None:
        session = touch_dataset_session(repo_id, interaction=interaction, ttl_seconds=ttl_seconds)
    available, docker_error = _docker_available()
    if not available:
        status = {
            "ok": False,
            "started": False,
            "docker_available": False,
            "container_name": session["container_name"],
            "error": docker_error,
        }
        _update_worker_status(repo_id, status)
        return {"repo_id": repo_id, "session": get_dataset_session(repo_id), "worker": status}

    live_status = _container_status(session["container_name"])
    if live_status.get("running"):
        live_status.update({"ok": True, "started": False, "docker_available": True})
        _update_worker_status(repo_id, live_status)
        return {"repo_id": repo_id, "session": get_dataset_session(repo_id), "worker": live_status}

    if live_status.get("exists"):
        start = _docker(["start", session["container_name"]], timeout=30)
        if start.returncode != 0:
            live_status.update(
                {
                    "ok": False,
                    "started": False,
                    "docker_available": True,
                    "error": _result_error(start),
                }
            )
            _update_worker_status(repo_id, live_status)
            return {"repo_id": repo_id, "session": get_dataset_session(repo_id), "worker": live_status}
        live_status = _container_status(session["container_name"])
        live_status.update({"ok": True, "started": True, "docker_available": True})
        _update_worker_status(repo_id, live_status)
        return {"repo_id": repo_id, "session": get_dataset_session(repo_id), "worker": live_status}

    if not pull_image and not _image_exists(image):
        status = {
            "ok": False,
            "started": False,
            "docker_available": True,
            "container_name": session["container_name"],
            "image": image,
            "error": (
                f"Worker image {image!r} is not available locally. "
                "Call the start tool with pull_image=True to pull it."
            ),
        }
        _update_worker_status(repo_id, status)
        return {"repo_id": repo_id, "session": get_dataset_session(repo_id), "worker": status}

    WORKER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    WORKER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    run = _docker(
        [
            "run",
            "-d",
            "--name",
            session["container_name"],
            "--label",
            "dataset-understanding=true",
            "--label",
            f"dataset-understanding.repo_id={repo_id}",
            "--memory",
            DEFAULT_WORKER_MEMORY,
            "--cpus",
            DEFAULT_WORKER_CPUS,
            "--pids-limit",
            DEFAULT_PIDS_LIMIT,
            "--workdir",
            "/work",
            "-v",
            f"{WORKER_CACHE_DIR}:/work/cache",
            "-v",
            f"{WORKER_PROFILE_DIR}:/work/profiles",
            "--entrypoint",
            "sleep",
            image,
            "infinity",
        ],
        timeout=180,
    )
    if run.returncode != 0:
        status = {
            "ok": False,
            "started": False,
            "docker_available": True,
            "container_name": session["container_name"],
            "image": image,
            "error": _result_error(run),
        }
        _update_worker_status(repo_id, status)
        return {"repo_id": repo_id, "session": get_dataset_session(repo_id), "worker": status}

    live_status = _container_status(session["container_name"])
    live_status.update({"ok": True, "started": True, "docker_available": True})
    _update_worker_status(repo_id, live_status)
    return {"repo_id": repo_id, "session": get_dataset_session(repo_id), "worker": live_status}


def stop_dataset_worker(repo_id: str, *, reason: str = "manual_eject") -> dict[str, Any]:
    """Stop a dataset worker and mark the session ejected."""
    session = get_dataset_session(repo_id) or touch_dataset_session(
        repo_id,
        interaction="worker_stop",
        schedule_cleanup=False,
    )
    available, docker_error = _docker_available()
    if not available:
        docker_result = {
            "attempted": False,
            "container_found": False,
            "docker_available": False,
            "error": docker_error,
        }
    else:
        status = _container_status(session["container_name"])
        if not status.get("exists"):
            docker_result = {
                "attempted": False,
                "container_found": False,
                "docker_available": True,
                "message": "container not found",
            }
        else:
            stop = _docker(["stop", session["container_name"]], timeout=30)
            docker_result = {
                "attempted": True,
                "container_found": True,
                "docker_available": True,
                "returncode": stop.returncode,
                "stdout": stop.stdout.strip(),
                "stderr": stop.stderr.strip(),
            }
    ejected = mark_dataset_session_ejected(repo_id, reason=reason, docker_result=docker_result)
    return {"repo_id": repo_id, "session": ejected, "docker": docker_result}


def run_clickhouse_local_in_worker(
    repo_id: str,
    sql: str,
    *,
    timeout: int = 180,
    pull_image: bool = False,
    touch_session: bool = True,
) -> str:
    """Run clickhouse-local inside the dataset worker container."""
    ensured = ensure_dataset_worker(
        repo_id,
        pull_image=pull_image,
        interaction="worker_query",
        touch_session=touch_session,
    )
    worker = ensured.get("worker") or {}
    if not worker.get("ok") or not worker.get("running"):
        raise DatasetWorkerError(worker.get("error") or "dataset worker is not running")

    result = _docker(
        [
            "exec",
            worker["container_name"],
            "clickhouse",
            "local",
            "--max_http_get_redirects=10",
            "--query",
            sql,
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise DatasetWorkerError(f"ClickHouse worker query failed: {_result_error(result)}")
    return result.stdout
