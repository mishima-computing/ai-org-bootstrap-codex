#!/usr/bin/env python3
"""Run independent controller contracts in parallel worktrees and merge scoped results."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import concurrent.futures
import fnmatch
import shutil
import subprocess
import tempfile

import controller_workflow as workflow  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def _check_git(repo: Path, *args: str) -> str:
    cp = _git(repo, *args)
    if cp.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo} (exit {cp.returncode}): {cp.stderr.strip()}"
        )
    return cp.stdout


def _report_to_dict(report) -> dict:
    if hasattr(report, "to_dict"):
        return report.to_dict()
    if isinstance(report, dict):
        return dict(report)
    return {"ok": False, "unresolved_failures": [f"unexpected report type: {type(report).__name__}"]}


def _allowed(path: str, contract: dict) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in contract.get("files_allowed_to_change", []))


def _safe_rel(path: str) -> bool:
    p = Path(path)
    return not p.is_absolute() and ".." not in p.parts


def _changed_allowed_files(worktree: Path, contract: dict) -> list[str]:
    changed = _check_git(worktree, "diff", "--name-only", "HEAD").splitlines()
    return sorted(path for path in changed if _safe_rel(path) and _allowed(path, contract))


def _run_one(worktree: Path, contract: dict, run_id: str, carrier_runner, run_kwargs: dict) -> dict:
    try:
        report = workflow.run_contract(
            worktree,
            contract,
            run_id,
            carrier_runner=carrier_runner,
            **run_kwargs,
        )
        return _report_to_dict(report)
    except Exception as exc:
        return {
            "contract_role": contract.get("role", ""),
            "ok": False,
            "unresolved_failures": [f"{type(exc).__name__}: {exc}"],
        }


def _copy_back(src_root: Path, dst_root: Path, rel_path: str) -> None:
    src = src_root / rel_path
    dst = dst_root / rel_path
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    elif dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()


def run_parallel(repo, contracts, run_id_prefix, *, max_workers=4, carrier_runner=None, **run_kwargs) -> dict:
    """Run disjoint carrier contracts concurrently and merge their allowed diffs."""
    repo = Path(repo).resolve()
    contracts = list(contracts)
    max_workers = int(max_workers)
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")

    worktrees = []
    results = [None] * len(contracts)
    changed_by_index: list[list[str]] = [[] for _ in contracts]

    try:
        for i, contract in enumerate(contracts):
            worktree = Path(tempfile.mkdtemp(prefix=f"controller-parallel-{run_id_prefix}-{i}-"))
            _check_git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
            worktrees.append(worktree)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _run_one,
                    worktrees[i],
                    contracts[i],
                    f"{run_id_prefix}-{i}",
                    carrier_runner,
                    run_kwargs,
                ): i
                for i in range(len(contracts))
            }
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                results[i] = future.result()
                changed_by_index[i] = _changed_allowed_files(worktrees[i], contracts[i])

        owners: dict[str, list[int]] = {}
        for i, paths in enumerate(changed_by_index):
            for path in paths:
                owners.setdefault(path, []).append(i)

        conflicts = sorted(path for path, idxs in owners.items() if len(idxs) > 1)
        conflict_set = set(conflicts)
        merged_files = []
        for path in sorted(owners):
            if path in conflict_set:
                continue
            owner = owners[path][0]
            _copy_back(worktrees[owner], repo, path)
            merged_files.append(path)

        ok = all(bool((result or {}).get("ok")) for result in results) and not conflicts
        return {"ok": ok, "results": results, "merged_files": merged_files, "conflicts": conflicts}
    finally:
        for worktree in worktrees:
            _git(repo, "worktree", "remove", "--force", str(worktree))


def _self_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="controller-parallel-self-test-"))
    try:
        repo = root / "repo"
        repo.mkdir()
        _check_git(repo, "init")
        (repo / "a.txt").write_bytes(b"base a\n")
        (repo / "b.txt").write_bytes(b"base b\n")
        _check_git(repo, "add", "a.txt", "b.txt")
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Controller Parallel Self Test",
                "-c",
                "user.email=selftest@example.invalid",
                "commit",
                "-m",
                "initial",
            ],
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )

        def fake_carrier_runner(repo, prompt, sandbox, *, timeout, retries, out_dir):
            rel = prompt.strip()
            Path(repo, rel).write_bytes(f"written by {rel}\n".encode("utf-8"))
            return {"ok": True, "attempts": [{"exit_code": 0}]}

        contracts = [
            {
                "role": "writer-a",
                "prompt": "a.txt",
                "sandbox": "workspace-write",
                "files_allowed_to_change": ["a.txt"],
            },
            {
                "role": "writer-b",
                "prompt": "b.txt",
                "sandbox": "workspace-write",
                "files_allowed_to_change": ["b.txt"],
            },
        ]
        result = run_parallel(
            repo,
            contracts,
            "self-test",
            max_workers=2,
            carrier_runner=fake_carrier_runner,
            include_builtin_gates=False,
        )
        assert result["ok"] is True, result
        assert result["merged_files"] == ["a.txt", "b.txt"], result
        assert (repo / "a.txt").read_bytes() == b"written by a.txt\n"
        assert (repo / "b.txt").read_bytes() == b"written by b.txt\n"
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    _self_test()
    print("controller_parallel self-test ok")
