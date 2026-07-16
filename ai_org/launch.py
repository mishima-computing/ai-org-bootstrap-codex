"""Own the AI Org run entry point and detach pull workers from caller teardown.

Memento: run12 lost a freshly detached pipeline Python to SIGKILL inside its
first minute, with no traceback, because the launching shell and child still had
a job-control lifetime race. Linger sleeps in shell scripts were only a
band-aid: the precedent facet "process supervision versus linger" says sleeps
narrow the window while session/process-group separation closes the class of
failure, so this module uses the PEP 3143 double-fork + setsid pattern instead.

Memento: workspace is disposable, knowledge is permanent. A no-path launch
creates a fresh git workspace under ~/aiorg_workspaces, while an explicit path
means reuse exactly that repo. The precedent store is never per-workspace:
--store keeps defaulting to the canonical persistent brain at
~/aiorg_assets/engineering_precedent_store/org.sqlite3, which must not be deleted and grows
across runs.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from ai_org import git_wrapper, patchwork_queue
from ai_org.patchwork_queue import submit as submission


DEFAULT_REFERENCE_STORE = "~/aiorg_assets/engineering_precedent_store/org.sqlite3"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ai_org.launch",
        description="Submit an AI Org request and start a detached patch series pull worker.",
    )
    parser.add_argument(
        "launch_args",
        nargs="+",
        metavar="ARG",
        help="request text, or target repository followed by request text",
    )
    parser.add_argument("--store", help="AI_ORG_REFERENCE_STORE path for the worker")
    parser.add_argument("--artifacts-dir", help="directory for run.log, progress.json, and intake_result.json")
    parser.add_argument("--_selftest", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        repo, request_text, workspace_created = _resolve_launch_target(args.launch_args)
    except ValueError as exc:
        parser.error(str(exc))
        raise AssertionError("unreachable")

    store = Path(args.store).expanduser().resolve() if args.store else None
    artifacts_dir = (
        Path(args.artifacts_dir).expanduser().resolve()
        if args.artifacts_dir
        else _default_artifacts_dir(repo)
    )
    log_path = artifacts_dir / "run.log"
    progress_path = artifacts_dir / "progress.json"
    result_path = artifacts_dir / "intake_result.json"

    try:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        submission.submit(repo, request_text)
        worker_pid = _spawn_detached_worker(
            repo=repo,
            store=store,
            log_path=log_path,
            progress_path=progress_path,
            result_path=result_path,
            selftest=args._selftest,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "run": "started",
                "worker_pid": worker_pid,
                "repo": str(repo),
                "workspace": str(repo),
                "workspace_created": workspace_created,
                "log": str(log_path),
                "progress": str(progress_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def _resolve_launch_target(launch_args: Sequence[str]) -> tuple[Path, str, bool]:
    if len(launch_args) == 1:
        request_text = launch_args[0]
        return _create_fresh_workspace(request_text), request_text, True
    if len(launch_args) == 2:
        repo = Path(launch_args[0]).expanduser().resolve()
        return repo, launch_args[1], False
    raise ValueError("expected request text, or target repository followed by request text")


def _create_fresh_workspace(request_text: str) -> Path:
    root = Path.home() / "aiorg_workspaces"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = _request_slug(request_text)
    repo = _unique_workspace_path(root, f"{stamp}-{slug}")
    repo.mkdir()
    _initialize_workspace_repo(repo)
    return repo.resolve()


def _request_slug(request_text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", request_text.lower())
    if not words:
        return "request"
    slug = "-".join(words[:5])
    return slug[:48].strip("-") or "request"


def _unique_workspace_path(root: Path, base_name: str) -> Path:
    candidate = root / base_name
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base_name}-{suffix}"
        suffix += 1
    return candidate


def _initialize_workspace_repo(repo: Path) -> None:
    _git(repo, "init")
    # Workspace repos carry the explicit engine identity (env-overridable,
    # noreply-style); ambient git config is forbidden engine-wide (see git_wrapper).
    identity = git_wrapper.engine_identity()
    _git(repo, "config", "user.name", identity["name"])
    _git(repo, "config", "user.email", identity["email"])
    (repo / "README.md").write_text("AI Org workspace\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "workspace: initialize")
    _git(repo, "branch", "-M", "main")


def _default_artifacts_dir(repo: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return repo / ".ai-org" / "launch" / f"{stamp}-{os.getpid()}"


def _spawn_detached_worker(
    *,
    repo: Path,
    store: Path | None,
    log_path: Path,
    progress_path: Path,
    result_path: Path,
    selftest: bool,
) -> int:
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid:
        os.close(write_fd)
        payload = _read_pipe_json(read_fd)
        os.close(read_fd)
        _, status = os.waitpid(pid, 0)
        if not payload:
            raise RuntimeError(f"detached worker handshake failed with status {status}")
        if "error" in payload:
            raise RuntimeError(str(payload["error"]))
        return int(payload["worker_pid"])

    os.close(read_fd)
    try:
        os.setsid()
        worker_pid = os.fork()
    except BaseException as exc:
        try:
            _write_pipe_json(write_fd, {"error": f"{type(exc).__name__}: {exc}"})
        except BaseException:
            pass
        os._exit(1)

    if worker_pid:
        _write_pipe_json(write_fd, {"worker_pid": worker_pid})
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    _run_worker(
        repo=repo,
        store=store,
        log_path=log_path,
        progress_path=progress_path,
        result_path=result_path,
        selftest=selftest,
    )
    os._exit(0)


def _run_worker(
    *,
    repo: Path,
    store: Path | None,
    log_path: Path,
    progress_path: Path,
    result_path: Path,
    selftest: bool,
) -> None:
    os.setsid()
    _redirect_stdio(log_path)
    if store is not None:
        os.environ["AI_ORG_REFERENCE_STORE"] = str(store)
    elif "AI_ORG_REFERENCE_STORE" not in os.environ:
        os.environ["AI_ORG_REFERENCE_STORE"] = str(Path(DEFAULT_REFERENCE_STORE).expanduser())

    _write_json(progress_path, {"status": "worker_started", "pid": os.getpid()})
    print(
        json.dumps(
            {
                "event": "launch.worker.started",
                "pid": os.getpid(),
                "ppid": os.getppid(),
                "sid": os.getsid(0),
                "pgid": os.getpgrp(),
                "repo": str(repo),
            },
            sort_keys=True,
        ),
        flush=True,
    )

    try:
        if selftest:
            result = _run_selftest(progress_path)
        else:
            result = patchwork_queue.pull(repo, progress_path=progress_path)
    except BaseException as exc:
        error_result = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "pid": os.getpid()}
        _write_json(progress_path, error_result)
        _write_json(result_path, error_result)
        raise

    _write_json(result_path, _json_safe(result))
    print(json.dumps({"event": "launch.worker.completed", "result_path": str(result_path)}, sort_keys=True), flush=True)


def _run_selftest(progress_path: Path) -> dict[str, Any]:
    marker_path = progress_path.with_name("selftest_marker.json")
    _write_json(progress_path, {"status": "selftest_started", "pid": os.getpid()})
    time.sleep(2.0)
    marker = {
        "status": "selftest_done",
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "sid": os.getsid(0),
        "pgid": os.getpgrp(),
    }
    _write_json(marker_path, marker)
    _write_json(progress_path, {"status": "selftest_done", "marker": str(marker_path), "pid": os.getpid()})
    return {"status": "selftest_done", "marker": str(marker_path), **marker}


def _redirect_stdio(log_path: Path) -> None:
    devnull_fd = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(devnull_fd, 0)
    finally:
        if devnull_fd > 2:
            os.close(devnull_fd)

    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
    finally:
        if log_fd > 2:
            os.close(log_fd)


def _read_pipe_json(fd: int) -> dict[str, Any] | None:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
    if not chunks:
        return None
    return json.loads(b"".join(chunks).decode("utf-8"))


def _write_pipe_json(fd: int, payload: Mapping[str, Any]) -> None:
    os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return repr(value)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
