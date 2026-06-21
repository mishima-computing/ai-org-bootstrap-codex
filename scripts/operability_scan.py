"""Deterministic operability scan for the conservative-designer (ADR-0014 operability extension).

Detects the TARGET repo's operability surface (start/run convention, config/secrets, health signal, resource
bounds, observability, state/migration, dependency pinning), INFERS the deliverable kind from that surface
(deliverable_kind.classify_kind — advisory), and derives a KIND-AWARE missing_safety_checks gap-list (a CLI
is not faulted for lacking /health). It pre-fills the design-proposal `continuity` FACTUAL fields the LLM
should not have to guess; the LLM keeps the judgment fields. Reuses secret_scan and the RepoIndex.

NOTE (carriage): `selected_profiles` is NOT filled here — it is a plain string list validated against
AUTHORIZED profile ids (scripts/profile-evidence-check.py), so an inferred-kind candidate cannot ride in it.
The inferred kind + evidence goes into `ecosystem_facts_used`; the LLM owns `selected_profiles`.
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
            "kind_verdict": verdict,
            "surface": {k: v for k, v in surface.items() if v},
            "dependency_pinning": deps,
            "forbidden_expansions": forbidden,
            "missing_safety_checks": missing,
            "continuity_prefill": prefill,
            "summary": (f"inferred kind={verdict['kind']} ({verdict['confidence']}); "
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
        facts = [f"inferred deliverable_kind={verdict['kind']} (confidence {verdict['confidence']}, advisory): "
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
