from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from ai_org_bootstrap.pack import find_repo_root
from ai_org_bootstrap.registry import load_runtime_registry


FORBIDDEN_PATH_PARTS = {"." + "clau" + "de", "." + "anti" + "gravity"}
FORBIDDEN_TEXT = [
    "Clau" + "de",
    "Anthro" + "pic",
    "clau" + "de",
    "anthro" + "pic",
    "Anti" + "gravity",
    "anti" + "gravity",
]


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    registry_path = root / "registry" / "runtime-registry.yaml"
    if not registry_path.is_file():
        return ["registry/runtime-registry.yaml is required."]

    for path in root.rglob("*"):
        # .agent-runs/ is gitignored runtime scratch, never published; out of scope for
        # this tracked-pack purity scan (excluding it is scoping, not a ban relaxation).
        if ".git" in path.parts or ".agent-runs" in path.parts:
            continue
        if any(part in FORBIDDEN_PATH_PARTS for part in path.parts):
            errors.append(f"forbidden non-Codex path present: {path.relative_to(root)}")
        if path.is_file() and _is_text_file(path):
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_TEXT:
                if token in text:
                    errors.append(f"forbidden non-Codex token {token!r} in {rel}")

    try:
        entries = load_runtime_registry(registry_path)
    except Exception as exc:
        return [f"runtime registry failed to parse: {exc}"]

    agent_ids = {entry.agent_id for entry in entries}
    expected_agents = {
        "functional-ci-action-writer",
        "security-ci-action-writer",
        "nonfunctional-ci-action-writer",
        "aggressive-designer",
        "conservative-designer",
        "genius",
        "aufheben-designer",
        "implementer",
        "linon",
    }
    if agent_ids != expected_agents:
        errors.append(f"runtime registry agents mismatch: {sorted(agent_ids)}")

    for entry in entries:
        for field_name, rel in {
            "role": entry.role,
            "adapter": entry.adapter,
            "schema": entry.schema,
        }.items():
            if not (root / rel).is_file():
                errors.append(f"{entry.agent_id} {field_name} path missing: {rel}")
        if not entry.adapter.startswith(".codex/agents/"):
            errors.append(f"{entry.agent_id} adapter must be under .codex/agents/")
        if entry.output_to and entry.output_to not in agent_ids:
            errors.append(f"{entry.agent_id} output_to unknown agent: {entry.output_to}")

    for schema_path in sorted((root / "schemas").glob("*.json")):
        try:
            json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"schema is not valid JSON: {schema_path.relative_to(root)}: {exc}")

    for script in ["scripts/merge-gate.py", "scripts/validate-bootstrap-pack.py"]:
        if not (root / script).is_file():
            errors.append(f"required script missing: {script}")

    return errors


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pyc"}:
        return False
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)
    root = find_repo_root(args.root)
    errors = validate(root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Codex-only AI Org Bootstrap pack validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
