"""Fail-closed contract for the durable-body migration CI boundary.

The catalog report proves repository state.  This module separately proves
that CI executes every closure proof, in order, instead of merely leaving the
commands available to developers.  It intentionally parses only the small
``jobs.*.steps`` surface needed by this repository and has no YAML dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


@dataclass(frozen=True, slots=True)
class RequiredProof:
    name: str
    commands: tuple[str, ...]
    exact_run: str


REQUIRED_PROOFS = (
    RequiredProof(
        "Verify migration-closure CI contract",
        ("python -m ai_org.migration_closure_ci",),
        "python -m ai_org.migration_closure_ci",
    ),
    RequiredProof(
        "Verify zero-gap durable-body coverage",
        ("python -m ai_org.registered_body_io",),
        'python -m ai_org.registered_body_io > "$RUNNER_TEMP/durable-body-coverage-report-v1.json"',
    ),
    RequiredProof(
        "Verify frozen root codec contract",
        (
            "go test . -run",
            "Test(FirstKindAdmissionProofV1|PublicContractV1|ModuleAndDependencyIntegrityManifestV1|FlatReviewV1GoldenAndStableIDLocality)",
        ),
        "go test . -run '^Test(FirstKindAdmissionProofV1|PublicContractV1|ModuleAndDependencyIntegrityManifestV1|FlatReviewV1GoldenAndStableIDLocality)$'",
    ),
    RequiredProof(
        "Verify frozen and sibling codec packages",
        ("go test ./...",),
        "go test ./...",
    ),
    RequiredProof(
        "Build installed codec bundle",
        ("go build -o", "./cmd/ai-org-cuecodec"),
        'mkdir -p "$RUNNER_TEMP/ai-org-codec-bin" '
        'go build -o "$RUNNER_TEMP/ai-org-codec-bin/ai-org-cuecodec" ./cmd/ai-org-cuecodec '
        'echo "$RUNNER_TEMP/ai-org-codec-bin" >> "$GITHUB_PATH"',
    ),
    RequiredProof(
        "Verify installed authority-bundle handshake",
        ("BodyCodecClient", "ai-org-cuecodec", ".handshake()"),
        "python -c 'import os; from ai_org.body_codec import BodyCodecClient; "
        'BodyCodecClient(os.path.join(os.environ["RUNNER_TEMP"], '
        '"ai-org-codec-bin", "ai-org-cuecodec")).handshake()\'',
    ),
    RequiredProof(
        "Run tests",
        ("python -m pytest -q",),
        "python -m pytest -q",
    ),
)


class CIContractError(ValueError):
    """The executable CI workflow does not contain the frozen closure path."""


@dataclass(frozen=True, slots=True)
class _Step:
    job: str
    job_condition: str | None
    job_continue_on_error: str | None
    name: str
    run: str
    condition: str | None
    continue_on_error: str | None


def _workflow_steps(text: str) -> tuple[_Step, ...]:
    """Extract the small fail-closed step surface used by the CI contract."""
    lines = text.splitlines()
    steps: list[_Step] = []
    index = 0
    current_job = ""
    job_condition: str | None = None
    job_continue_on_error: str | None = None
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if (
            line.startswith("  ")
            and not line.startswith("    ")
            and stripped.endswith(":")
            and not stripped.startswith("#")
        ):
            current_job = stripped[:-1]
            job_condition = None
            job_continue_on_error = None
        elif line.startswith("    if:"):
            job_condition = line.split(":", 1)[1].strip()
        elif line.startswith("    continue-on-error:"):
            job_continue_on_error = line.split(":", 1)[1].strip()
        if line.startswith("      - name:") and not stripped.startswith("#"):
            name = line.split(":", 1)[1].strip().strip("'\"")
            index += 1
            run_parts: list[str] = []
            condition: str | None = None
            continue_on_error: str | None = None
            while index < len(lines) and not lines[index].startswith("      - "):
                candidate = lines[index]
                if candidate.startswith("  ") and not candidate.startswith("        "):
                    break
                if candidate.startswith("        run:"):
                    value = candidate.split(":", 1)[1].strip()
                    if value not in {"|", ">", "|-", ">-"}:
                        run_parts.append(value)
                    index += 1
                    while index < len(lines) and lines[index].startswith("          "):
                        content = lines[index].strip()
                        if content and not content.startswith("#"):
                            run_parts.append(content)
                        index += 1
                    continue
                if candidate.startswith("        if:"):
                    condition = candidate.split(":", 1)[1].strip()
                if candidate.startswith("        continue-on-error:"):
                    continue_on_error = candidate.split(":", 1)[1].strip()
                index += 1
            steps.append(
                _Step(
                    job=current_job,
                    job_condition=job_condition,
                    job_continue_on_error=job_continue_on_error,
                    name=name,
                    run=" ".join(run_parts),
                    condition=condition,
                    continue_on_error=continue_on_error,
                )
            )
            continue
        index += 1
    return tuple(steps)


def require_migration_closure_ci(text: str) -> None:
    """Require each closure proof exactly once, executable, and in order."""
    steps = _workflow_steps(text)
    positions: list[int] = []
    proof_jobs: set[str] = set()
    failures: list[str] = []
    for proof in REQUIRED_PROOFS:
        matches = [(index, step) for index, step in enumerate(steps) if step.name == proof.name]
        if len(matches) != 1:
            failures.append(f"{proof.name}:count={len(matches)}")
            continue
        position, step = matches[0]
        if not step.job:
            failures.append(f"{proof.name}:job-missing")
            continue
        if step.job_condition is not None:
            failures.append(f"{proof.name}:job-conditional={step.job_condition}")
            continue
        if step.job_continue_on_error not in {None, "false"}:
            failures.append(
                f"{proof.name}:job-continue-on-error={step.job_continue_on_error}"
            )
            continue
        if step.condition is not None:
            failures.append(f"{proof.name}:conditional={step.condition}")
            continue
        if step.continue_on_error not in {None, "false"}:
            failures.append(f"{proof.name}:continue-on-error={step.continue_on_error}")
            continue
        missing_commands = [command for command in proof.commands if command not in step.run]
        if missing_commands:
            failures.append(f"{proof.name}:non-executable={','.join(missing_commands)}")
            continue
        # Command fragments are useful diagnostics, but cannot prove
        # execution: ``echo 'python -m pytest -q'`` contains the fragment and
        # performs no test.  Freeze the normalized run body so wrappers,
        # success-forcing shell operators, and other textual decoys fail the
        # contract instead of bypassing the mandatory proof.
        if step.run != proof.exact_run:
            failures.append(f"{proof.name}:non-canonical-command")
            continue
        positions.append(position)
        proof_jobs.add(step.job)
    if len(positions) == len(REQUIRED_PROOFS) and positions != sorted(positions):
        failures.append("proof-order")
    if len(positions) == len(REQUIRED_PROOFS) and len(proof_jobs) != 1:
        failures.append("proof-job-boundary")
    if failures:
        raise CIContractError("migration_closure_ci:" + ";".join(failures))


def verify_workflow(path: str | Path = WORKFLOW_PATH) -> None:
    require_migration_closure_ci(Path(path).read_text(encoding="utf-8"))


def main(argv: Iterable[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    path = Path(arguments[0]) if arguments else WORKFLOW_PATH
    try:
        verify_workflow(path)
    except (OSError, CIContractError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
