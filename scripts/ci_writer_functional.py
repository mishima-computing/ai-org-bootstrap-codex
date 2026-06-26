#!/usr/bin/env python3
"""Deterministic functional CI workflow author.

ADR-0019 makes the functional CI writer a small trusted kernel: derive the
decidable facts, emit an honest workflow, and refuse to commit it unless it has
been observed green-on-good and red-on-a negative control.
"""
from __future__ import annotations

import dataclasses
import fnmatch
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


WORKFLOW_PATH = ".github/workflows/functional-ci.yml"
PYTHON_MANIFESTS = ("requirements.txt", "pyproject.toml", "setup.cfg", "setup.py", "Pipfile")
SKIP_DIRS = {
    ".git",
    ".agent-runs",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
}

ALIAS_DISTRIBUTIONS = {
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "Crypto": "pycryptodome",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "googleapiclient": "google-api-python-client",
    "jwt": "PyJWT",
    "lxml": "lxml",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
}


@dataclasses.dataclass(frozen=True)
class CheckCommand:
    ecosystem: str
    command: str
    source: str


@dataclasses.dataclass(frozen=True)
class Escalation:
    code: str
    severity: str
    message: str
    evidence: str

    def to_finding(self) -> dict:
        return {
            "type": "ci_writer_escalation",
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclasses.dataclass(frozen=True)
class NegativeControlProof:
    passed: bool
    static_passed: bool
    good_input: dict
    negative_control: dict
    escalations: tuple[Escalation, ...] = ()
    actionlint: dict | None = None

    def to_result(self) -> dict:
        return {
            "passed": self.passed,
            "static_passed": self.static_passed,
            "good_input": self.good_input,
            "negative_control": self.negative_control,
            "actionlint": self.actionlint or {"available": False, "passed": None},
        }


@dataclasses.dataclass(frozen=True)
class AuthorResult:
    ok: bool
    result: dict
    workflow_path: str | None
    proof: NegativeControlProof
    escalations: tuple[Escalation, ...]


class ModuleResolutionEscalation(RuntimeError):
    def __init__(self, code: str, module: str, detail: str):
        super().__init__(f"{code}: {module}: {detail}")
        self.code = code
        self.module = module
        self.detail = detail


def _iter_files(repo: Path):
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(root) / name
            try:
                rel = path.relative_to(repo)
            except ValueError:
                continue
            yield rel


def detect_ecosystems(repo: Path) -> list[str]:
    repo = Path(repo)
    files = {str(p) for p in _iter_files(repo)}
    ecosystems: list[str] = []
    if any((repo / manifest).exists() for manifest in PYTHON_MANIFESTS) or any(p.endswith(".py") for p in files):
        ecosystems.append("python")
    if (repo / "go.mod").exists() or any(p.endswith(".go") for p in files):
        ecosystems.append("go")
    if (repo / "package.json").exists() or any(p.endswith((".js", ".jsx", ".ts", ".tsx")) for p in files):
        ecosystems.append("node")
    return ecosystems


def _has_pytest_signature(repo: Path) -> bool:
    for marker in ("pytest.ini", "conftest.py"):
        if (repo / marker).exists():
            return True
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="ignore")
        if "pytest" in text or "[tool.pytest" in text:
            return True
    for rel in _iter_files(repo):
        name = rel.name
        if name.startswith("test_") and name.endswith(".py"):
            text = (repo / rel).read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^\s*def\s+test_", text, re.M):
                return True
    return False


def discover_test_commands(repo: Path, ecosystems: list[str]) -> list[CheckCommand]:
    repo = Path(repo)
    commands: list[CheckCommand] = []
    if "python" in ecosystems:
        tests = [p for p in _iter_files(repo) if p.name.startswith("test") and p.suffix == ".py"]
        package_tests = repo / "packages" / "codex-org-bootstrap" / "tests"
        if package_tests.is_dir():
            commands.append(CheckCommand("python", "python3 -m unittest discover -s packages/codex-org-bootstrap/tests",
                                         "convention:package-unittest"))
        elif tests:
            command = "python3 -m pytest" if _has_pytest_signature(repo) else "python3 -m unittest discover"
            commands.append(CheckCommand("python", command, "convention:python-tests"))
    if "go" in ecosystems:
        if any(p.name.endswith("_test.go") for p in _iter_files(repo)):
            commands.append(CheckCommand("go", "go test ./...", "convention:go-test"))
    if "node" in ecosystems and (repo / "package.json").exists():
        try:
            package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            package = {}
        test_script = ((package.get("scripts") or {}).get("test") or "").strip()
        if test_script and "no test specified" not in test_script:
            commands.append(CheckCommand("node", "npm test", "manifest:npm-test"))
    return commands


def discover_first_party_modules(repo: Path) -> set[str]:
    modules: set[str] = set()
    for rel in _iter_files(Path(repo)):
        parts = rel.parts
        if not parts or parts[0] in {"tests", "test"} or parts[-1].startswith("test"):
            continue
        if len(parts) == 1 and rel.suffix == ".py":
            modules.add(rel.stem)
        if rel.name == "__init__.py" and len(parts) >= 2:
            modules.add(parts[0])
    return modules


def resolve_module_distribution(
    module_name: str,
    repo: Path,
    *,
    package_map: dict[str, list[str]] | None = None,
    alias_table: dict[str, str] | None = None,
    stdlib_names: set[str] | None = None,
    first_party_modules: set[str] | None = None,
) -> str:
    mod = (module_name or "").split(".")[0]
    if not mod:
        raise ModuleResolutionEscalation("empty_module", module_name, "ModuleNotFoundError.name was empty")
    stdlib = stdlib_names if stdlib_names is not None else set(getattr(sys, "stdlib_module_names", ()))
    if mod in stdlib:
        raise ModuleResolutionEscalation("stdlib_module", mod, "stdlib imports are not installable dependencies")
    first_party = first_party_modules if first_party_modules is not None else discover_first_party_modules(repo)
    if mod in first_party:
        raise ModuleResolutionEscalation("first_party_module", mod, "missing first-party import indicates PYTHONPATH or package layout")
    packages = package_map if package_map is not None else importlib.metadata.packages_distributions()
    dists = sorted(set(packages.get(mod) or []))
    if len(dists) == 1:
        return dists[0]
    if len(dists) > 1:
        raise ModuleResolutionEscalation("ambiguous_module_distribution", mod, f"maps to {', '.join(dists)}")
    aliases = alias_table if alias_table is not None else ALIAS_DISTRIBUTIONS
    if mod in aliases:
        return aliases[mod]
    raise ModuleResolutionEscalation("unresolved_module_distribution", mod, "no installed distribution or curated alias")


def python_dependency_step(repo: Path, command: CheckCommand) -> tuple[str, str]:
    if (Path(repo) / "requirements.txt").exists():
        return "Install Python dependencies", "python3 -m pip install -r requirements.txt"
    if (Path(repo) / "pyproject.toml").exists():
        return "Install Python package", "python3 -m pip install -e ."
    return "Resolve Python dependencies fail-closed", _fixpoint_step_script(command.command)


def _shell_block(command: str) -> str:
    body = command.strip()
    if "set -euo pipefail" not in body:
        body = "set -euo pipefail\n" + body
    return body


def _yaml_run_step(name: str, body: str, *, indent: str = "      ") -> str:
    lines = [f"{indent}- name: {name}", f"{indent}  run: |"]
    for line in _shell_block(body).splitlines():
        lines.append(f"{indent}    {line}")
    return "\n".join(lines)


def emit_workflow(repo: Path, checks: list[CheckCommand]) -> str:
    python_checks = [check for check in checks if check.ecosystem == "python"]
    go_checks = [check for check in checks if check.ecosystem == "go"]
    node_checks = [check for check in checks if check.ecosystem == "node"]
    out = [
        "name: functional-ci",
        "",
        "on: { pull_request: {}, push: { branches: [main] } }",
        "permissions: { contents: read }",
        "",
        "jobs:",
    ]
    if python_checks:
        out.extend([
            "  python:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - uses: actions/setup-python@v5",
            "        with:",
            "          python-version: \"3.12\"",
        ])
        name, install = python_dependency_step(repo, python_checks[0])
        out.append(_yaml_run_step(name, install))
        for idx, check in enumerate(python_checks, 1):
            out.append(_yaml_run_step(f"Run Python functional checks {idx}", check.command))
    if go_checks:
        out.extend([
            "  go:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - uses: actions/setup-go@v5",
            "        with:",
            "          go-version: \"stable\"",
        ])
        for idx, check in enumerate(go_checks, 1):
            out.append(_yaml_run_step(f"Run Go functional checks {idx}", check.command))
    if node_checks:
        out.extend([
            "  node:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - uses: actions/setup-node@v4",
            "        with:",
            "          node-version: \"22\"",
            "          cache: npm",
        ])
        out.append(_yaml_run_step("Install Node dependencies", "npm ci"))
        for idx, check in enumerate(node_checks, 1):
            out.append(_yaml_run_step(f"Run Node functional checks {idx}", check.command))
    return "\n".join(out).rstrip() + "\n"


def _fixpoint_step_script(test_command: str) -> str:
    script = FIXPOINT_RESOLVER_PY.replace("__TEST_COMMAND_JSON__", json.dumps(test_command))
    return "python3 - <<'PY'\n" + script.rstrip() + "\nPY"


FIXPOINT_RESOLVER_PY = r'''
import importlib.metadata
import os
import runpy
import shlex
import subprocess
import sys
from pathlib import Path

TEST_COMMAND = __TEST_COMMAND_JSON__
ALIASES = {
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "Crypto": "pycryptodome",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "googleapiclient": "google-api-python-client",
    "jwt": "PyJWT",
    "lxml": "lxml",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
}
SKIP_DIRS = {".git", ".agent-runs", ".mypy_cache", ".pytest_cache", ".tox", ".venv", "__pycache__", "node_modules"}

def escalate(code, detail):
    print(f"::error title=ci_writer_escalation::{code}: {detail}", file=sys.stderr)
    raise SystemExit(1)

def first_party_modules(root):
    mods = set()
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        base_path = Path(base)
        for name in files:
            path = base_path / name
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] in {"tests", "test"}:
                continue
            if len(rel.parts) == 1 and rel.suffix == ".py" and not rel.name.startswith("test"):
                mods.add(rel.stem)
            if rel.name == "__init__.py" and len(rel.parts) >= 2:
                mods.add(rel.parts[0])
    return mods

def resolve_distribution(module):
    mod = module.split(".")[0]
    if not mod:
        escalate("empty_module", "ModuleNotFoundError.name was empty")
    if mod in getattr(sys, "stdlib_module_names", set()):
        escalate("stdlib_module", f"{mod} is stdlib; do not install guesses")
    if mod in first_party_modules(Path.cwd()):
        escalate("first_party_module", f"{mod} is first-party; fix PYTHONPATH/package layout")
    dists = sorted(set(importlib.metadata.packages_distributions().get(mod) or []))
    if len(dists) == 1:
        return mod, dists[0]
    if len(dists) > 1:
        escalate("ambiguous_module_distribution", f"{mod} maps to {', '.join(dists)}")
    if mod in ALIASES:
        return mod, ALIASES[mod]
    escalate("unresolved_module_distribution", f"{mod} has no installed distribution or curated alias")

def probe_missing_module():
    args = shlex.split(TEST_COMMAND)
    if len(args) >= 3 and args[1] == "-m":
        try:
            __import__(args[2].split(".")[0])
        except ModuleNotFoundError as exc:
            return exc.name
    for path in sorted(Path.cwd().rglob("test*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
            ns = {"__name__": "__ci_writer_probe__", "__file__": str(path)}
            exec(code, ns, ns)
        except ModuleNotFoundError as exc:
            return exc.name
        except Exception:
            continue
    return None

def installed_version(dist):
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None

def comparable_version(value):
    if value is None:
        return ()
    return tuple(int(part) if part.isdigit() else part for part in value.replace("-", ".").split("."))

attempted = set()
for _ in range(30):
    proc = subprocess.run(shlex.split(TEST_COMMAND), text=True)
    if proc.returncode == 0:
        raise SystemExit(0)
    missing = probe_missing_module()
    if not missing:
        raise SystemExit(proc.returncode)
    mod, dist = resolve_distribution(missing)
    if mod in attempted:
        escalate("no_progress", f"{mod} was already attempted")
    attempted.add(mod)
    before = installed_version(dist)
    install = subprocess.run([sys.executable, "-m", "pip", "install", dist],
                             capture_output=True, text=True)
    if install.returncode != 0:
        text = (install.stdout or "") + "\n" + (install.stderr or "")
        if "ResolutionImpossible" in text or "resolution impossible" in text.lower():
            escalate("resolution_impossible", f"pip could not resolve {dist}")
        escalate("pip_install_failed", f"pip install failed for {dist}")
    after = installed_version(dist)
    if before and after and comparable_version(after) < comparable_version(before):
        escalate("dependency_downgrade", f"{dist} downgraded from {before} to {after}")
escalate("max_iterations", "dependency resolution did not converge")
'''


def workflow_false_green_findings(workflow_text: str, path: str = WORKFLOW_PATH) -> list[dict]:
    findings: list[dict] = []
    if re.search(r"\|\|\s*true\b", workflow_text):
        findings.append({"check": "false_green_static", "severity": "critical", "path": path,
                         "detail": "workflow contains '|| true'"})
    if re.search(r"continue-on-error\s*:\s*true\b", workflow_text, re.I):
        findings.append({"check": "false_green_static", "severity": "critical", "path": path,
                         "detail": "workflow contains continue-on-error: true"})
    lines = workflow_text.splitlines()
    for idx, line in enumerate(lines):
        match = re.match(r"^(\s*)run\s*:\s*([|>])\s*$", line)
        if not match:
            if re.match(r"^\s*run\s*:\s*(?![|>])", line) and "set -euo pipefail" not in line:
                findings.append({"check": "false_green_static", "severity": "major", "path": path,
                                 "line": idx + 1, "detail": "single-line run step lacks set -euo pipefail"})
            continue
        indent = len(match.group(1))
        body: list[str] = []
        for next_line in lines[idx + 1:]:
            stripped = next_line.strip()
            if stripped and len(next_line) - len(next_line.lstrip(" ")) <= indent:
                break
            body.append(next_line)
        text = "\n".join(body)
        if "set -euo pipefail" not in text:
            findings.append({"check": "false_green_static", "severity": "critical", "path": path,
                             "line": idx + 1, "detail": "multi-line run step lacks set -euo pipefail"})
        executable = [b.strip() for b in body if b.strip() and not b.strip().startswith("#")]
        if executable and all(cmd in {"set -e", "set -euo pipefail", "exit 0", "true"} or cmd.startswith("echo ")
                              for cmd in executable):
            findings.append({"check": "false_green_static", "severity": "major", "path": path,
                             "line": idx + 1, "detail": "run step has no falsifiable command"})
    return findings


def _run_actionlint(repo: Path, workflow_paths: list[Path]) -> dict:
    actionlint = shutil.which("actionlint")
    if not actionlint:
        return {"available": False, "passed": None}
    proc = subprocess.run([actionlint, *[str(path) for path in workflow_paths]], cwd=repo,
                          capture_output=True, text=True)
    return {
        "available": True,
        "passed": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def _copy_repo(src: Path, dst: Path) -> None:
    def ignore(_dir, names):
        return [name for name in names if name in SKIP_DIRS]
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def _run_commands(repo: Path, commands: list[CheckCommand]) -> dict:
    for check in commands:
        proc = subprocess.run(check.command, cwd=repo, shell=True, capture_output=True, text=True,
                              executable="/bin/bash")
        if proc.returncode != 0:
            return {"passed": False, "command": check.command, "returncode": proc.returncode,
                    "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-2000:]}
    return {"passed": True, "commands": [check.command for check in commands]}


def _plant_negative_control(repo: Path, commands: list[CheckCommand]) -> str:
    for check in commands:
        if check.ecosystem != "python":
            continue
        parts = check.command.split()
        tests_dir = repo / "tests"
        if "-s" in parts:
            idx = parts.index("-s")
            if idx + 1 < len(parts):
                tests_dir = repo / parts[idx + 1]
        tests_dir.mkdir(exist_ok=True)
        marker = tests_dir / "test_ci_writer_negative_control.py"
        marker.write_text("import unittest\n\nclass NegativeControl(unittest.TestCase):\n"
                          "    def test_negative_control_goes_red(self):\n"
                          "        self.fail('ci-writer negative control')\n",
                          encoding="utf-8")
        return str(marker.relative_to(repo))
    raise ValueError("no supported negative-control mutator for discovered commands")


def prove_negative_control(repo: Path, workflow_text: str, commands: list[CheckCommand],
                           *, workflow_path: str = WORKFLOW_PATH) -> NegativeControlProof:
    static_findings = workflow_false_green_findings(workflow_text, workflow_path)
    actionlint = None
    with tempfile.TemporaryDirectory(prefix="ci-writer-static-") as d:
        tmp = Path(d)
        wf = tmp / workflow_path
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_text(workflow_text, encoding="utf-8")
        actionlint = _run_actionlint(tmp, [wf])
    if actionlint.get("available") and not actionlint.get("passed"):
        static_findings.append({"check": "actionlint", "severity": "critical", "path": workflow_path,
                                "detail": (actionlint.get("stderr") or actionlint.get("stdout") or "").strip()})
    if static_findings:
        escalations = tuple(Escalation("negative_control_static_failed", "critical", f["detail"],
                                       f"{f.get('path', workflow_path)}:{f.get('line', 1)}")
                            for f in static_findings)
        return NegativeControlProof(False, False, {"passed": None}, {"red_observed": None},
                                    escalations, actionlint)
    if not commands:
        esc = Escalation("negative_control_unavailable", "critical",
                         "no deterministic functional command was discovered", workflow_path)
        return NegativeControlProof(False, True, {"passed": None}, {"red_observed": None}, (esc,), actionlint)
    with tempfile.TemporaryDirectory(prefix="ci-writer-good-") as good_dir:
        good_repo = Path(good_dir) / "repo"
        _copy_repo(Path(repo), good_repo)
        good = _run_commands(good_repo, commands)
    if not good.get("passed"):
        esc = Escalation("negative_control_good_failed", "critical",
                         f"good input failed command {good.get('command')}", workflow_path)
        return NegativeControlProof(False, True, good, {"red_observed": None}, (esc,), actionlint)
    with tempfile.TemporaryDirectory(prefix="ci-writer-red-") as red_dir:
        red_repo = Path(red_dir) / "repo"
        _copy_repo(Path(repo), red_repo)
        try:
            planted = _plant_negative_control(red_repo, commands)
        except ValueError as exc:
            esc = Escalation("negative_control_unavailable", "critical", str(exc), workflow_path)
            return NegativeControlProof(False, True, good, {"red_observed": None}, (esc,), actionlint)
        red = _run_commands(red_repo, commands)
        negative = {"description": f"planted failing test {planted}", "red_observed": not red.get("passed"),
                    "result": red}
    if not negative["red_observed"]:
        esc = Escalation("negative_control_survived", "critical",
                         "workflow commands stayed green on planted failing test", workflow_path)
        return NegativeControlProof(False, True, good, negative, (esc,), actionlint)
    return NegativeControlProof(True, True, good, negative, (), actionlint)


def author_functional_ci(repo: Path) -> AuthorResult:
    repo = Path(repo)
    ecosystems = detect_ecosystems(repo)
    workflows_read = [str(path.relative_to(repo)) for path in sorted((repo / ".github" / "workflows").glob("*.y*ml"))
                      if path.is_file()]
    checks = discover_test_commands(repo, ecosystems)
    escalations: list[Escalation] = []
    if not checks:
        escalations.append(Escalation("no_functional_checks", "major",
                                      "no deterministic functional checks were discovered", str(repo)))
        proof = NegativeControlProof(False, True, {"passed": None}, {"red_observed": None}, tuple(escalations))
        result = _result_payload(ecosystems, workflows_read, [], [], [], checks, proof, escalations)
        return AuthorResult(False, result, None, proof, tuple(escalations))
    workflow = emit_workflow(repo, checks)
    proof = prove_negative_control(repo, workflow, checks)
    escalations.extend(proof.escalations)
    workflows_changed: list[str] = []
    files_changed: list[str] = []
    if proof.passed:
        dest = repo / WORKFLOW_PATH
        dest.parent.mkdir(parents=True, exist_ok=True)
        old = dest.read_text(encoding="utf-8") if dest.exists() else None
        if old != workflow:
            dest.write_text(workflow, encoding="utf-8")
            workflows_changed.append(WORKFLOW_PATH)
            files_changed.append(WORKFLOW_PATH)
    result = _result_payload(ecosystems, workflows_read, workflows_changed, files_changed, [], checks,
                             proof, escalations)
    return AuthorResult(proof.passed, result, WORKFLOW_PATH if proof.passed else None, proof, tuple(escalations))


def _result_payload(ecosystems: list[str], workflows_read: list[str], workflows_changed: list[str],
                    files_changed: list[str], gaps: list[str], checks: list[CheckCommand],
                    proof: NegativeControlProof, escalations: list[Escalation]) -> dict:
    commands = [check.command for check in checks]
    return {
        "role_id": "functional-ci-action-writer",
        "detected_ecosystem": ecosystems,
        "workflows_read": workflows_read,
        "workflows_changed": workflows_changed,
        "commands_added": commands,
        "commands_already_present": [],
        "checks_added": commands,
        "checks_already_present": [],
        "gaps": gaps,
        "files_changed": files_changed,
        "negative_control": proof.to_result(),
        "escalations": [esc.to_finding() for esc in escalations],
    }


def main(argv=None) -> int:
    repo = Path(argv[0] if argv else ".").resolve()
    authored = author_functional_ci(repo)
    print(json.dumps(authored.result, indent=2, sort_keys=True))
    return 0 if authored.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
