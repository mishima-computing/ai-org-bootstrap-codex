"""Deterministic operability scan for the conservative-designer (ADR-0014 operability extension).

Detects the TARGET repo's operability surface (start/run convention, config/secrets, health signal, resource
bounds, observability, state/migration, dependency pinning), detects the existing repo surface kind
(deliverable_kind.classify_kind — advisory), and derives a KIND-AWARE missing_safety_checks gap-list (a CLI
is not faulted for lacking /health). It pre-fills the design-proposal `continuity` FACTUAL fields the LLM
should not have to guess; the LLM keeps the judgment fields. Reuses secret_scan and the RepoIndex.

NOTE (carriage): `selected_profiles` is NOT filled here — it is a plain string list validated against
AUTHORIZED profile ids (scripts/profile-evidence-check.py), so an inferred-kind candidate cannot ride in it.
The existing repo surface kind + evidence goes into `ecosystem_facts_used`; the LLM owns `selected_profiles`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import deliverable_kind as dk
import pre_localizer

_SERVER_RE = re.compile(r"uvicorn|gunicorn|hypercorn|waitress|flask run|http\.server|node\s|npm (run )?start|serve\b|rails server")
_PORT_RE = re.compile(r"EXPOSE\s+\d+|\.listen\(\s*\d|0\.0\.0\.0:\d|app\.run\([^)]*port|server\.listen"
                      r"|\bPORT\b\s*[=:]|process\.env\.PORT|getenv\(\s*['\"]PORT")
_FRAMEWORK_RE = re.compile(r"\b(flask|fastapi|starlette|aiohttp|express|django|sinatra|gin|axum|actix)\b")
_ROUTE_RE = re.compile(r"@app\.(get|post|put|route)|app\.(get|post|put)\(|@router\.|addRoute|app\.use\(")
_HEALTH_RE = re.compile(r"/health\b|/healthz|/readyz|/livez|/readiness|/ready\b|livenessProbe|readinessProbe")
_GRPC_RE = re.compile(r"\bimport grpc\b|grpc\.server|add_.*Servicer_to_server|google\.protobuf")
_CLILIB_RE = re.compile(r"\b(argparse|click|typer|cobra|clap|commander)\b")
_SCHED_RE = re.compile(r"kind:\s*CronJob|kind:\s*Job|crontab|schedule\.|APScheduler|@cron")
_OBS_RE = re.compile(r"\b(logging|getLogger|structlog|console\.(log|error)|pino|winston|prometheus|statsd|opentelemetry|metrics)\b")
_ENV_RE = re.compile(r"os\.environ|os\.getenv|process\.env|dotenv|BaseSettings|configparser")
_MIGRATION_DIRS = ("migrations", "alembic", "prisma")
_LOCKFILES = {"package-lock.json": "npm", "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm",
              "poetry.lock": "poetry", "Pipfile.lock": "pipenv", "Cargo.lock": "cargo",
              "go.sum": "go", "uv.lock": "uv"}
_DEPLOY_FILES = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "fly.toml", "render.yaml",
                 "nixpacks.toml", "Procfile")
_MAX_BYTES = 200_000
INTERFACE_DELTAS = ("no_surface_change", "modifies_existing_interface", "adds_new_interface",
                    "removes_interface", "unknown")
_INTERFACE_KIND_SET = set(dk.INTERFACE_KINDS)
_ROUTE_TOKEN_RE = re.compile(r"(^|[\s'\"`])/[A-Za-z0-9][A-Za-z0-9_./{}:-]*")

_NO_SURFACE_PATTERNS = (
    re.compile(r"\bno\s+(?:public\s+)?(?:surface|interface|api|behavior|behaviour)\s+change\b"),
    re.compile(r"\bwithout\s+changing\s+(?:public\s+)?(?:surface|interface|api|behavior|behaviour)\b"),
    re.compile(r"\bbehavior[- ]preserving\b|\bbehaviour[- ]preserving\b"),
)
_NO_SURFACE_TOKENS = {"refactor", "refactoring", "rename", "renaming", "cleanup", "clean", "restructure",
                      "restructuring", "organize", "internal", "private", "deduplicate", "consolidate"}
_ADD_TOKENS = {"add", "adds", "adding", "create", "creates", "creating", "introduce", "introduces",
               "introducing", "expose", "exposes", "exposing", "new", "implement", "implements",
               "support", "supports"}
_REMOVE_TOKENS = {"remove", "removes", "removing", "delete", "deletes", "deleting", "drop", "drops",
                  "dropping", "retire", "retires", "retiring", "deprecate", "deprecates", "deprecating"}
_MODIFY_TOKENS = {"modify", "modifies", "modifying", "change", "changes", "changing", "update", "updates",
                  "updating", "alter", "alters", "altering", "extend", "extends", "extending", "adjust",
                  "adjusts", "adjusting", "replace", "replaces", "replacing"}
_INTERFACE_TOKENS = {"endpoint", "endpoints", "route", "routes", "api", "apis", "http", "rpc", "grpc",
                     "command", "commands", "cli", "flag", "flags", "option", "options", "argument",
                     "arguments", "export", "exports", "symbol", "symbols", "module", "schema", "json",
                     "webhook", "event", "events", "public"}
_WEAK_TOKENS = {"fix", "improve", "handle", "support", "update", "change"}


class OperabilityScan:
    def __init__(self, repo, candidates=None, guard_map=None, index=None):
        self.repo = Path(repo).resolve()
        self.candidates = list(candidates or [])
        self.guard_map = guard_map or {}
        self.index = index or pre_localizer.RepoIndex.cached(self.repo)

    def build(self) -> dict:
        surface = self._detect_surface()
        verdict = dk.classify_kind(surface)
        deps = self._dependency_pinning()
        missing = self._missing_safety_checks(verdict, surface, deps)
        if self._critical_secret():        # a committed secret is an operability hazard regardless of kind
            missing.insert(0, "committed secret detected — externalize config/secrets (secret_scan)")
        forbidden = self._forbidden_expansions()
        eco = self._ecosystem_facts(surface, verdict, deps)
        prefill = {                                  # the design-proposal continuity FACTUAL fields
            "version_constraints": deps[:6],
            "ecosystem_facts_used": eco[:8],
            "forbidden_expansions": forbidden[:6],
            "missing_safety_checks": missing[:6],
        }
        return {
            "existing_repo_surface_kind": verdict,
            "kind_verdict": verdict,
            "surface": {k: v for k, v in surface.items() if v},
            "dependency_pinning": deps,
            "forbidden_expansions": forbidden,
            "missing_safety_checks": missing,
            "continuity_prefill": prefill,
            "summary": (f"existing repo surface kind={verdict['kind']} ({verdict['confidence']}); "
                        f"{len(missing)} missing required operability check(s); {len(deps)} dependency fact(s)."),
        }

    # ---- surface detection ----
    def _read(self, rel: str) -> str:
        p = self.repo / rel
        try:
            if p.is_file() and p.stat().st_size <= _MAX_BYTES:
                return p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
        return ""

    def _detect_surface(self) -> dict:
        s: dict = {"evidence": {}}
        names = {Path(p).name: p for p in self.index.paths}
        text_blobs = []
        for rel in self.index.paths:
            if Path(rel).suffix.lower() in (".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rb", ".rs",
                                            ".yaml", ".yml", ".toml", ".json", ".proto", "") or \
                    Path(rel).name in _DEPLOY_FILES or Path(rel).name.endswith("Dockerfile"):
                txt = self._read(rel)
                if txt:
                    text_blobs.append((rel, txt))
        allbins = "\n".join(t for _, t in text_blobs)

        # Dockerfile CMD/ENTRYPOINT
        docker = "\n".join(t for r, t in text_blobs if Path(r).name == "Dockerfile" or r.endswith("Dockerfile"))
        cmds = re.findall(r"^\s*(?:CMD|ENTRYPOINT)\s+.+$", docker, re.MULTILINE)
        if cmds:
            s["dockerfile_cmd"] = cmds[:3]
            if any(_SERVER_RE.search(c) for c in cmds):
                s["server_cmd"] = [c.strip()[:120] for c in cmds if _SERVER_RE.search(c)][:2]
        # package.json
        if "package.json" in names:
            try:
                pkg = json.loads(self._read(names["package.json"]))
                if pkg.get("bin"):
                    s["package_bin"] = True
                scripts = pkg.get("scripts") or {}
                if scripts.get("start"):
                    s["start_script"] = True
                    if _SERVER_RE.search(scripts["start"]):
                        s.setdefault("server_cmd", []).append("package.json start: " + scripts["start"][:80])
                # NB: package.json `main` is the LIBRARY entry (import target), NOT a run/start command — a
                # plain library ("main": "index.js", no bin, no start script) must stay classifiable as library.
            except (json.JSONDecodeError, OSError):
                pass
        # pyproject [project.scripts]
        if "pyproject.toml" in names:
            py = self._read(names["pyproject.toml"])
            if re.search(r"\[project\.scripts\]|console_scripts|\[tool\.poetry\.scripts\]", py):
                s["console_scripts"] = True
        # Procfile
        if "Procfile" in names:
            proc = self._read(names["Procfile"])
            if re.search(r"^\s*web:", proc, re.MULTILINE):
                s["server_cmd"] = s.get("server_cmd", []) + ["Procfile web:"]
            if re.search(r"^\s*(worker|release|cron):", proc, re.MULTILINE):
                s["scheduler"] = s.get("scheduler", []) + ["Procfile worker/release"]
        # __main__ / entrypoints
        if any(Path(p).name == "__main__.py" for p in self.index.paths):
            s["entrypoint"] = True
        # content signals
        if _PORT_RE.search(allbins):
            s["bound_port"] = True
        if _FRAMEWORK_RE.search(allbins):
            s["http_framework"] = True
        if _ROUTE_RE.search(allbins):
            s["routes"] = ["route handler(s) detected"]
        if _HEALTH_RE.search(allbins):
            s["health_routes"] = sorted(set(_HEALTH_RE.findall(allbins)))[:3] or ["health route"]
        protos = [p for p in self.index.paths if p.endswith(".proto")]
        if protos:
            s["proto"] = protos[:3]
        if _GRPC_RE.search(allbins):
            s["grpc"] = True
        if _CLILIB_RE.search(allbins):
            s["cli_lib"] = True
        if _SCHED_RE.search(allbins):
            s["scheduler"] = s.get("scheduler", []) + ["scheduler/Job marker"]
        if _OBS_RE.search(allbins):
            s["observability"] = True
        if _ENV_RE.search(allbins):
            s["env_config"] = True
        if re.search(r"resources:\s*\n\s*(limits|requests)|mem_limit|cpus:|--memory", allbins):
            s["resource_bounds"] = True
        if any(d in p.split("/") for p in self.index.paths for d in _MIGRATION_DIRS) or \
                any(p.endswith(".sql") for p in self.index.paths):
            s["state_migration"] = True
        # composite signals for the classifier
        s["entrypoint"] = bool(s.get("entrypoint") or s.get("console_scripts") or s.get("package_bin")
                               or s.get("server_cmd") or s.get("start_script"))
        s["explicit_deploy_config"] = any(n in names for n in _DEPLOY_FILES)
        has_source = any(Path(p).suffix.lower() in (".py", ".js", ".ts", ".go", ".rb", ".rs")
                         for p in self.index.paths)
        s["importable_only"] = has_source and not s["entrypoint"] and not s.get("bound_port")
        # a json deliverable: schema/data .json present, no source and no run surface (exclude manifests)
        _manifest = {"package.json", "package-lock.json", "composer.json", "tsconfig.json"}
        s["json_artifact"] = (not has_source and not s["entrypoint"]
                              and any(p.endswith(".json") and Path(p).name not in _manifest
                                      for p in self.index.paths))
        s["code_pattern_only"] = not s["explicit_deploy_config"] and not s.get("console_scripts") \
            and not s.get("package_bin")
        return s

    # ---- derived facts ----
    def _critical_secret(self) -> bool:
        """Fail-soft, gitleaks-free (regex fallback) committed-secret check — never sinks the design stage."""
        try:
            import secret_scan
            res = secret_scan.scan_dir(str(self.repo), prefer_gitleaks=False, include_archives=False)
            return any((f.get("severity") == "critical") for f in (res.get("findings") or []))
        except Exception:   # noqa: BLE001 — advisory only
            return False

    def _dependency_pinning(self) -> list:
        out = []
        seen = set()
        for p in self.index.paths:                 # iterate ALL paths (monorepos have many lockfiles)
            n = Path(p).name
            if n in _LOCKFILES and n not in seen:
                seen.add(n)
                out.append(f"{_LOCKFILES[n]} dependencies pinned via {n}")
        for req in [p for p in self.index.paths if Path(p).name.startswith("requirements") and p.endswith(".txt")]:
            txt = self._read(req)
            pinned = sum(1 for ln in txt.splitlines() if "==" in ln)
            loose = sum(1 for ln in txt.splitlines() if ln.strip() and not ln.strip().startswith("#") and "==" not in ln)
            out.append(f"{req}: {pinned} pinned, {loose} unpinned")
        return out or ["no lockfile / pinned manifest detected"]

    def _missing_safety_checks(self, verdict: dict, surface: dict, deps: list) -> list:
        present = {
            "start_run": surface.get("entrypoint"),
            "bound_port": surface.get("bound_port"),
            "health": surface.get("health_routes"),
            "config_secrets": surface.get("env_config"),
            "resource_bounds": surface.get("resource_bounds"),
            "observability": surface.get("observability"),
            "dependency_pinning": any("pinned via" in d or " pinned," in d for d in deps),
            "state_migration": surface.get("state_migration"),
            "importable": surface.get("importable_only"),   # library's required check IS satisfiable
            "schema_valid": surface.get("json_artifact"),
        }
        out = []
        for check in sorted(dk.required_operability(verdict["kind"])):
            if check in present and not present[check]:
                out.append(f"absent: {check} (required for kind '{verdict['kind']}')")
            elif check not in present:   # not directly detectable (clean_exit, stop_rollback, importable, schema_valid)
                out.append(f"undetected: {check} (required for kind '{verdict['kind']}' — confirm in design)")
        return out

    def _forbidden_expansions(self) -> list:
        out = []
        for e in (self.guard_map.get("protected_exports") or [])[:4]:
            out.append(f"do not clobber protected export in {e.get('file')}")
        for a in (self.guard_map.get("governing_adrs") or [])[:2]:
            out.append(f"honor governing doc {a.get('doc')}")
        return out

    def _ecosystem_facts(self, surface: dict, verdict: dict, deps: list) -> list:
        facts = [f"existing_repo_surface_kind={verdict['kind']} (confidence {verdict['confidence']}, advisory): "
                 + "; ".join(verdict.get("evidence", []))[:140]]
        if surface.get("server_cmd"):
            facts.append("start: " + str(surface["server_cmd"][0])[:120])
        if surface.get("health_routes"):
            facts.append("health signal: " + ", ".join(surface["health_routes"]))
        if surface.get("env_config"):
            facts.append("config externalized via env")
        if deps:
            facts.append(deps[0])
        if surface.get("observability"):
            facts.append("observability hooks present")
        return [f[:200] for f in facts]


class ChangeIntentScan:
    """Advisory leaf-objective scanner for interface-delta routing.

    This deliberately reads the leaf objective and localized candidate scope. It does not declare the contract
    kind and is not a gate input; it gives designers a deterministic hint about whether the task changes an
    interface at all.
    """

    def __init__(self, repo, objective: str, candidates=None, index=None, existing_repo_surface_kind=None):
        self.repo = Path(repo).resolve()
        self.objective = objective or ""
        self.candidates = list(candidates or [])
        self.index = index or pre_localizer.RepoIndex.cached(self.repo)
        self.existing_repo_surface_kind = existing_repo_surface_kind

    def build(self) -> dict:
        tokens = pre_localizer._split_tokens(self.objective)
        lower = self.objective.lower()
        localized_scope = self._localized_scope()
        objective_signals = self._objective_signals(tokens, lower)
        objective_signals.extend(self._scope_signals(localized_scope))
        delta = self._classify(tokens, lower, objective_signals, localized_scope)
        existing = self._existing_surface_kind()
        advice = self._deliverable_kind_advice(delta, existing)
        return {
            "interface_delta": delta,
            "advisory_only": True,
            "objective_signals": objective_signals[:12],
            "localized_scope": localized_scope[:12],
            "existing_repo_surface_kind": existing,
            "deliverable_kind_advice": advice,
            "contract_design_advice": self._contract_design_advice(delta, advice, existing),
        }

    def _existing_surface_kind(self) -> dict:
        if isinstance(self.existing_repo_surface_kind, dict):
            return self.existing_repo_surface_kind
        scan = OperabilityScan(self.repo, [self._candidate_path(c) for c in self.candidates], index=self.index)
        surface = scan._detect_surface()
        return dk.classify_kind(surface)

    def _localized_scope(self) -> list[dict]:
        out = []
        for c in self.candidates:
            path = self._candidate_path(c)
            if not path:
                continue
            reasons = self._candidate_reasons(c)
            entry = {"path": path, "score": self._candidate_score(c), "reasons": reasons[:6]}
            surface = self._surface_clues(path)
            if surface:
                entry["surface_signals"] = surface[:5]
            out.append(entry)
        return out

    def _candidate_path(self, candidate) -> str:
        if isinstance(candidate, str):
            return candidate
        if isinstance(candidate, dict):
            return str(candidate.get("path") or "")
        return str(getattr(candidate, "path", "") or "")

    def _candidate_score(self, candidate):
        if isinstance(candidate, dict):
            return candidate.get("score", 0)
        return getattr(candidate, "score", 0)

    def _candidate_reasons(self, candidate) -> list[str]:
        if isinstance(candidate, dict):
            reasons = candidate.get("reasons") or []
        else:
            reasons = getattr(candidate, "reasons", []) or []
        return [str(r)[:200] for r in reasons]

    def _surface_clues(self, rel: str) -> list[str]:
        text = self._read(rel)
        clues = []
        if _ROUTE_RE.search(text) or _HEALTH_RE.search(text):
            clues.append("http route surface")
        if _GRPC_RE.search(text) or rel.endswith(".proto"):
            clues.append("rpc surface")
        if _CLILIB_RE.search(text):
            clues.append("cli surface")
        if any(rx.search(text) for rx in (re.compile(r"\bmodule\.exports\b"), re.compile(r"^\s*export\s+", re.MULTILINE),
                                          re.compile(r"^\s*__all__\s*=", re.MULTILINE))):
            clues.append("library export surface")
        if rel.endswith(".json"):
            clues.append("json artifact surface")
        return clues

    def _read(self, rel: str) -> str:
        p = self.repo / rel
        try:
            if p.is_file() and p.stat().st_size <= _MAX_BYTES:
                return p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
        return ""

    def _objective_signals(self, tokens: set[str], lower: str) -> list[dict]:
        signals = []
        for name, words in (("no_surface_change", _NO_SURFACE_TOKENS), ("adds_new_interface", _ADD_TOKENS),
                            ("removes_interface", _REMOVE_TOKENS), ("modifies_existing_interface", _MODIFY_TOKENS),
                            ("interface_clue", _INTERFACE_TOKENS)):
            hits = sorted(tokens & words)
            if hits:
                signals.append({"kind": name, "tokens": hits[:8]})
        phrases = [rx.pattern for rx in _NO_SURFACE_PATTERNS if rx.search(lower)]
        if phrases:
            signals.append({"kind": "no_surface_change", "phrases": phrases[:3]})
        routes = sorted(set(m.group(0).strip(" '\"`") for m in _ROUTE_TOKEN_RE.finditer(self.objective)))
        if routes:
            signals.append({"kind": "interface_clue", "routes": routes[:6]})
        if "existing" in tokens:
            signals.append({"kind": "existing_interface_clue", "tokens": ["existing"]})
        return signals

    def _scope_signals(self, localized_scope: list[dict]) -> list[dict]:
        surface = sorted({s for item in localized_scope for s in item.get("surface_signals", [])})
        if not surface:
            return []
        return [{"kind": "localized_scope_surface", "surface_signals": surface[:8]}]

    def _classify(self, tokens: set[str], lower: str, signals: list[dict], localized_scope: list[dict]) -> str:
        if not self.objective.strip():
            return "unknown"
        no_surface = bool(tokens & _NO_SURFACE_TOKENS) or any(rx.search(lower) for rx in _NO_SURFACE_PATTERNS)
        add = bool(tokens & _ADD_TOKENS)
        remove = bool(tokens & _REMOVE_TOKENS)
        modify = bool(tokens & _MODIFY_TOKENS)
        interface = bool(tokens & _INTERFACE_TOKENS) or bool(_ROUTE_TOKEN_RE.search(self.objective))
        existing = "existing" in tokens or "current" in tokens
        localized_interface = any(item.get("surface_signals") for item in localized_scope)

        active = sum(bool(x) for x in (add and interface, remove and interface, modify and interface, no_surface))
        if active > 1:
            return "unknown"
        if no_surface:
            return "no_surface_change"
        if remove and interface:
            return "removes_interface"
        if add and interface:
            return "adds_new_interface"
        if modify and (interface or existing or localized_interface):
            return "modifies_existing_interface"
        if interface and existing:
            return "modifies_existing_interface"
        if not localized_scope and (tokens & _WEAK_TOKENS):
            return "unknown"
        if not signals:
            return "unknown"
        return "unknown"

    def _deliverable_kind_advice(self, delta: str, existing: dict):
        if delta == "no_surface_change":
            return "none"
        if delta == "unknown":
            return None
        kind = (existing or {}).get("kind")
        if kind in _INTERFACE_KIND_SET:
            return kind
        return "undetermined"

    def _contract_design_advice(self, delta: str, advice, existing: dict) -> list[str]:
        if delta == "no_surface_change":
            return [
                "treat this as behavior-preserving restructuring, rename, or refactor",
                "prefer deliverable_kind: none unless aufheben finds a real new or changed interface",
                "use regression_suite, static_checks, and forbidden_patterns rather than interface conformance",
            ]
        if delta == "adds_new_interface":
            return [
                "treat this as adding an externally checkable surface",
                f"prefer deliverable_kind: {advice} when the existing surface evidence supports it",
                "encode a conformance profile for the new endpoint, command, export, schema, or service boundary",
            ]
        if delta == "modifies_existing_interface":
            return [
                "treat this as changing an existing externally checkable surface",
                f"prefer deliverable_kind: {advice} when the existing surface evidence supports it",
                "pin both preserved behavior and the changed interface behavior in conformance/regression gates",
            ]
        if delta == "removes_interface":
            return [
                "treat this as removing or retiring an externally checkable surface",
                f"prefer deliverable_kind: {advice} when the existing surface evidence supports it",
                "encode checks that prove the retired surface is absent or safely rejected and compatibility is addressed",
            ]
        return [
            "change intent is unknown; do not treat existing_repo_surface_kind as authoritative deliverable_kind",
            f"existing_repo_surface_kind is {(existing or {}).get('kind', 'undetermined')}",
            "aufheben must choose deliverable_kind from the actual contract objective and declared acceptance criteria",
        ]
