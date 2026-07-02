"""Patch/implement stage.

This stage is intentionally shaped as git-read -> codex -> git-write.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

from ai_org.rfc.field_registry import (
    RFC_VIEW_FIELDS,
    STRING_ARRAY_FIELDS,
    STRING_FIELDS,
    validate_tech_stack,
    validate_user_experience_requirements,
)

RFC_FIELDS = RFC_VIEW_FIELDS


def run(
    repo,
    rfc_id_or_branch: str,
    rfc_path: str = "rfc.json",
    branch: str | None = None,
    feedback=None,
    attempt: int = 1,
) -> dict:
    """Implement the RFC committed on ai-org/rfc/<id> and return contribution branch metadata."""
    repo_path = Path(repo).resolve()
    rfc = _read_rfc_from_git(repo_path, rfc_id_or_branch, rfc_path)
    if not rfc["ok"]:
        return rfc

    rfc_data = rfc["rfc"]
    technical_approach = _read_technical_approach_from_git(repo_path, rfc_id_or_branch)
    if not technical_approach["ok"]:
        return technical_approach
    rfc_id = _rfc_id(rfc_id_or_branch)
    suffix = "" if attempt == 1 else f"-a{attempt}"
    branch_name = branch or f"ai-org/contrib/{rfc_id}{suffix}"
    if _branch_exists(repo_path, branch_name):
        return _failure(f"branch already exists: {branch_name}", branch=branch_name)

    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-patch-"))
    worktree = temp_dir / "worktree"
    out_file = temp_dir / "codex-output.txt"
    try:
        # git is done in python; codex --sandbox workspace-write edits the worktree but
        # CANNOT write .git (PROVEN: .git/index.lock permission denied).
        worktree_result = _git_run(
            repo_path,
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree),
            _default_branch(repo_path),
        )
        if worktree_result.returncode != 0:
            return _failure(
                "git worktree add failed",
                branch=branch_name,
                stderr=worktree_result.stderr,
                stdout=worktree_result.stdout,
            )

        prompt = _prompt(rfc_data, technical_approach["technical_approach"], technical_approach["domain_spec_files"])
        if feedback is not None:
            prompt += (
                "\nA previous attempt was rejected by acceptance with these blockers: "
                f"{feedback}. Address them.\n"
            )
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "workspace-write",
            "-C",
            str(worktree),
            "-o",
            str(out_file),
            prompt,
        ]
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        # codex can exit non-zero / write no -o file -> check returncode AND out_file
        # existence before reading (fail closed).
        if completed.returncode != 0 or not out_file.exists():
            return _failure(
                "codex implementation failed",
                branch=branch_name,
                returncode=completed.returncode,
                stderr=completed.stderr,
                stdout=completed.stdout,
                output_exists=out_file.exists(),
            )

        status = _git_run(worktree, "status", "--porcelain", "-uall")
        if status.returncode != 0:
            return _failure(
                "git status failed after codex",
                branch=branch_name,
                stderr=status.stderr,
                stdout=status.stdout,
            )
        if not status.stdout.strip():
            return _failure("codex produced no edits", branch=branch_name)

        add = _git_run(
            worktree,
            "add",
            "-A",
            "--",
            ".",
            ":(exclude)__pycache__",
            ":(exclude)**/__pycache__",
            ":(exclude)*.pyc",
            ":(exclude)**/*.pyc",
        )
        if add.returncode != 0:
            return _failure(
                "git add failed",
                branch=branch_name,
                stderr=add.stderr,
                stdout=add.stdout,
            )

        commit = _git_run(worktree, "commit", "-m", f"patch: {rfc_data['working_title']}")
        if commit.returncode != 0:
            return _failure(
                "git commit failed",
                branch=branch_name,
                stderr=commit.stderr,
                stdout=commit.stdout,
            )

        sha = _git(worktree, "rev-parse", "HEAD").strip()
        return {
            "ok": True,
            "branch": branch_name,
            "commit": sha,
        }
    finally:
        if worktree.exists():
            _git_run(repo_path, "worktree", "remove", "--force", str(worktree))
        shutil.rmtree(temp_dir, ignore_errors=True)


def _read_rfc_from_git(repo: Path, rfc_id_or_branch: str, rfc_path: str) -> dict:
    rfc_branch = _rfc_branch(rfc_id_or_branch)
    result = _git_run(repo, "show", f"{rfc_branch}:{rfc_path}")
    if result.returncode != 0:
        return _failure(
            f"{rfc_path} missing at {rfc_branch}",
            stderr=result.stderr,
            stdout=result.stdout,
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return _failure(f"{rfc_path} at {rfc_branch} is not parseable JSON: {exc}")

    if not _is_common_8(data):
        return _failure(f"{rfc_path} at {rfc_branch} must contain exactly the RFC field registry fields")

    return {"ok": True, "rfc": {field: data[field] for field in RFC_FIELDS}}


def _read_technical_approach_from_git(repo: Path, rfc_id_or_branch: str) -> dict:
    rfc_branch = _rfc_branch(rfc_id_or_branch)
    result = _git_run(repo, "show", f"{rfc_branch}:technical-approach.json")
    if result.returncode != 0:
        return _failure(
            f"technical-approach.json missing at {rfc_branch}",
            stderr=result.stderr,
            stdout=result.stdout,
        )
    try:
        approach = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return _failure(f"technical-approach.json at {rfc_branch} is not parseable JSON: {exc}")
    if not isinstance(approach, dict):
        return _failure(f"technical-approach.json at {rfc_branch} must contain a JSON object")
    domain_files = _read_domain_spec_files(repo, rfc_branch, approach)
    return {"ok": True, "technical_approach": approach, "domain_spec_files": domain_files}


def _read_domain_spec_files(repo: Path, branch: str, approach: Mapping[str, Any]) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for file_ref in _domain_spec_file_refs(approach):
        result = _git_run(repo, "show", f"{branch}:{file_ref}")
        if result.returncode != 0:
            files[file_ref] = {"error": "missing referenced domain specification file"}
            continue
        try:
            files[file_ref] = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            files[file_ref] = {"error": f"invalid JSON: {exc}"}
    return files


def _domain_spec_file_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, Mapping):
        file_ref = value.get("file_ref")
        if isinstance(file_ref, str) and file_ref.startswith("domain-spec/") and file_ref not in refs:
            refs.append(file_ref)
        for child in value.values():
            refs.extend(ref for ref in _domain_spec_file_refs(child) if ref not in refs)
    elif isinstance(value, list):
        for child in value:
            refs.extend(ref for ref in _domain_spec_file_refs(child) if ref not in refs)
    return refs


def _prompt(
    rfc: dict[str, Any],
    technical_approach: Mapping[str, Any] | None = None,
    domain_spec_files: Mapping[str, Any] | None = None,
) -> str:
    text = "Implement the RFC in this repository.\nEdit the working tree only. Do not commit.\n\n" + _format_rfc(rfc)
    domain_text = _format_domain_specification(technical_approach or {}, domain_spec_files or {})
    if domain_text:
        text += "\n\nContributor domain specification:\n" + domain_text
    return text


def _rfc_id(rfc_id_or_branch: str) -> str:
    return _rfc_branch(rfc_id_or_branch).removeprefix("ai-org/rfc/")


def _branch_exists(repo: Path, branch: str) -> bool:
    result = _git_run(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    return result.returncode == 0


def _rfc_branch(rfc_id_or_branch: str) -> str:
    if rfc_id_or_branch.startswith("refs/heads/"):
        return rfc_id_or_branch.removeprefix("refs/heads/")
    if rfc_id_or_branch.startswith("ai-org/rfc/"):
        return rfc_id_or_branch
    return f"ai-org/rfc/{rfc_id_or_branch}"


def _is_common_8(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(RFC_FIELDS)
        and all(isinstance(value[field], str) for field in STRING_FIELDS)
        and all(
            isinstance(value[field], list) and all(isinstance(item, str) for item in value[field])
            for field in STRING_ARRAY_FIELDS
        )
        and validate_tech_stack(value.get("tech_stack"))
        and validate_user_experience_requirements(value.get("user_experience_requirements"))
    )


def _format_rfc(rfc: dict[str, Any]) -> str:
    lines: list[str] = []
    for field in RFC_FIELDS:
        value = rfc[field]
        if isinstance(value, list):
            rendered = _format_alternatives(value)
        elif isinstance(value, dict):
            rendered = json.dumps(value, sort_keys=True, ensure_ascii=True)
        else:
            rendered = str(value)
        lines.append(f"{field}:\n{rendered}")
    return "\n\n".join(lines) + "\n"


def _format_alternatives(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def _format_domain_specification(
    technical_approach: Mapping[str, Any],
    domain_spec_files: Mapping[str, Any],
) -> str:
    domain = _find_domain_specification(technical_approach)
    if not domain:
        return ""
    aspects = domain.get("aspects")
    if not isinstance(aspects, list) or not aspects:
        return ""
    lines: list[str] = []
    for aspect in aspects:
        if not isinstance(aspect, Mapping):
            continue
        lines.append(f"- {aspect.get('aspect_name', '')} ({aspect.get('applicability', '')})")
        body = str(aspect.get("specification_body") or "").strip()
        if body:
            lines.append(f"  specification_body: {body}")
        quantities = aspect.get("quantities")
        if isinstance(quantities, list) and quantities:
            lines.append("  quantities:")
            for quantity in quantities:
                if isinstance(quantity, Mapping):
                    lines.append(
                        f"    - {quantity.get('name', '')}: {quantity.get('value', '')} {quantity.get('unit', '')}".rstrip()
                    )
        tables = aspect.get("tables")
        if isinstance(tables, list) and tables:
            lines.append("  tables:")
            for table in tables:
                if isinstance(table, Mapping):
                    rows = table.get("rows")
                    lines.append(f"    - {table.get('table_name', '')}: {len(rows) if isinstance(rows, list) else 0} rows")
        externalized = aspect.get("externalized_tables")
        if isinstance(externalized, list) and externalized:
            lines.append("  externalized_tables:")
            for table in externalized:
                if isinstance(table, Mapping):
                    lines.append(
                        f"    - {table.get('table_name', '')}: {table.get('row_count', 0)} rows in {table.get('file_ref', '')}"
                    )
    if domain_spec_files:
        lines.append("")
        lines.append("Full externalized domain tables:")
        for file_ref, payload in domain_spec_files.items():
            lines.append(f"{file_ref}:")
            lines.append(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True))
    return "\n".join(lines).strip()


def _find_domain_specification(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        found = value.get("domain_specification")
        if isinstance(found, Mapping):
            return found
        for child in value.values():
            nested = _find_domain_specification(child)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _find_domain_specification(child)
            if nested is not None:
                return nested
    return None


def _default_branch(repo: Path) -> str:
    origin_head = _git_run(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if origin_head.returncode == 0:
        ref = origin_head.stdout.strip()
        if ref.startswith("origin/"):
            return ref

    current = _git_run(repo, "symbolic-ref", "--short", "HEAD")
    if current.returncode == 0 and current.stdout.strip():
        return current.stdout.strip()

    raise RuntimeError("could not determine repository default branch")


def _git(repo: Path, *args: str) -> str:
    result = _git_run(repo, *args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout


def _git_run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _failure(message: str, **extra) -> dict:
    result = {"ok": False, "error": message}
    result.update({key: value for key, value in extra.items() if value not in (None, "")})
    return result
