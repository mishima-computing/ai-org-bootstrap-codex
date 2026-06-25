"""Deterministic existing-repo-surface-kind classifier (shared, ADR-0014 operability extension).

The conservative-designer runs BEFORE the aufheben-designer declares `deliverable_kind`, so at design time
there is no contract to read. This module infers the existing repo surface kind from the operability surface
the scan collected — advisory only — using the same first-match-under-precedence + evidence +
refuse-don't-guess shape that production source-to-deploy systems use (Cloud Native Buildpacks detect,
Heroku bin/detect, Nixpacks / Railpack start-command precedence). It never fabricates certainty:
ambiguous → `unknown_service_like`, unclassifiable-but-executable → `undetermined` (the same vocabulary
conformance.py already uses).

Two uses of ONE vocabulary (ADR-0009 boundary — the designer chooses+encodes, deterministic systems verify):
  - design-time:   classify_kind(surface) → advisory existing_repo_surface_kind candidate, gates which
                   operability checks are relevant to the current repo surface.
  - post-aufheben: contract.deliverable_kind stays AUTHORITATIVE (contract_preflight / conformance); the
                   classifier can re-run on the surface as a declared-vs-existing-surface consistency check.

`required_operability(kind)` is the kind→required-check map: health/readiness is mandatory only for the
service kinds; a CLI's "readiness" is a clean exit, a Job's is completion (Kubernetes draws this line).
"""
from __future__ import annotations

# the fixed conformance vocabulary (mirrors contract_preflight._INTERFACE_KINDS / conformance dispatch)
INTERFACE_KINDS = ("cli", "library", "http_service", "rpc_service", "batch_job", "json")
# advisory-only inference outcomes that are NOT contract kinds
UNKNOWN_SERVICE = "unknown_service_like"   # a listener that is not demonstrably http or rpc
UNDETERMINED = "undetermined"              # an executable interface we cannot classify (conformance.py:218)

# kind -> the operability checks that are REQUIRED for it. A check ABSENT from a kind's set is NOT a gap
# for that kind (this is what kills "no /health on a CLI" false positives). "health" is service-only.
_REQUIRED = {
    "http_service": {"start_run", "bound_port", "health", "config_secrets", "resource_bounds",
                     "stop_rollback", "observability", "dependency_pinning"},
    "rpc_service":  {"start_run", "bound_port", "health", "config_secrets", "resource_bounds",
                     "stop_rollback", "observability", "dependency_pinning"},
    "batch_job":    {"start_run", "clean_exit", "config_secrets", "resource_bounds", "stop_rollback",
                     "observability", "dependency_pinning"},
    "cli":          {"start_run", "clean_exit", "dependency_pinning"},
    "library":      {"importable", "dependency_pinning"},
    "json":         {"schema_valid"},
    UNKNOWN_SERVICE: {"start_run", "config_secrets", "observability", "dependency_pinning"},
    UNDETERMINED:   {"dependency_pinning"},
}


def required_operability(kind: str) -> set:
    """The mandatory operability checks for a kind (advisory union for unknown/undetermined)."""
    return set(_REQUIRED.get(kind, _REQUIRED[UNDETERMINED]))


def classify_kind(surface: dict) -> dict:
    """Infer the existing repo surface kind from the operability surface. Returns:
        {kind, confidence: high|medium|low|unknown, evidence: [...], candidates: [{kind,evidence}],
         advisory_only: True}
    Precedence is first-match over strongest-evidence-first (CNB/Heroku/Nixpacks); a runner-up that also
    fires is kept as a candidate (the margin/ambiguity signal) rather than silently dropped."""
    s = surface or {}
    candidates: list[dict] = []

    def fire(kind, evidence):
        candidates.append({"kind": kind, "evidence": list(evidence)})

    # 1. rpc_service — strongest, least ambiguous: a service descriptor
    proto = s.get("proto") or []
    if proto and s.get("grpc"):
        fire("rpc_service", [f"proto {proto[0]}", "grpc server"])
    # 2. http_service — a server entrypoint AND a bound port, or a web framework with routes
    server_cmd = s.get("server_cmd") or []
    if (server_cmd and s.get("bound_port")) or (s.get("http_framework") and (s.get("routes") or s.get("health_routes"))):
        ev = []
        if server_cmd:
            ev.append(f"server start: {server_cmd[0]}")
        if s.get("bound_port"):
            ev.append("binds a port")
        if s.get("http_framework"):
            ev.append("http framework")
        fire("http_service", ev)
    # 3. batch_job — scheduled / one-shot (cron, k8s Job/CronJob, Procfile worker/release)
    sched = s.get("scheduler") or []
    if sched:
        fire("batch_job", [f"scheduler: {sched[0]}"])
    # 4. cli — installed command entrypoints
    if s.get("console_scripts") or s.get("package_bin"):
        fire("cli", ["console_scripts/[project.scripts]" if s.get("console_scripts") else "package.json bin"])
    elif s.get("cli_lib") and (s.get("entrypoint") or s.get("start_script")):
        fire("cli", ["argparse/click/cobra entrypoint"])
    # 5. json — a data artifact with no run surface
    if s.get("json_artifact") and not (server_cmd or s.get("entrypoint")):
        fire("json", ["json artifact, no entrypoint"])
    # 6. listener that is not demonstrably http/rpc -> unknown_service_like (do not force into batch_job)
    if s.get("bound_port") and not any(c["kind"] in ("http_service", "rpc_service") for c in candidates):
        fire(UNKNOWN_SERVICE, ["binds a port but no http/rpc evidence"])
    # 7. library — the residual: importable code, no entrypoint of any kind
    if not candidates and s.get("importable_only"):
        fire("library", ["importable modules, no run entrypoint"])

    if not candidates:
        # an executable interface clearly exists but we cannot classify it -> undetermined; else nothing.
        if server_cmd or s.get("entrypoint") or s.get("dockerfile_cmd"):
            return {"kind": UNDETERMINED, "confidence": "unknown",
                    "evidence": ["executable interface present, unclassifiable"],
                    "candidates": [], "advisory_only": True}
        return {"kind": UNDETERMINED, "confidence": "unknown",
                "evidence": ["no run/deploy convention detected"], "candidates": [], "advisory_only": True}

    top = candidates[0]
    runner = next((c for c in candidates[1:] if c["kind"] != top["kind"]), None)
    ambiguous = runner is not None
    return {"kind": top["kind"], "confidence": _confidence(top, runner, s), "evidence": top["evidence"],
            "candidates": candidates, "ambiguous": ambiguous, "advisory_only": True}


def _confidence(top: dict, runner: dict | None, s: dict) -> str:
    """Confidence is NOT a fabricated 0-1 score (CNB/Heroku emit none) — it is the precedence margin:
    unknown = a different-kind runner-up is at least as strongly evidenced (a genuine tie, regardless of how
    many signals the winner has); high = explicit deploy config or two independent signals and no such tie;
    low = code-pattern only; medium otherwise."""
    if runner is not None and len(runner.get("evidence", [])) >= len(top.get("evidence", [])):
        return "unknown"
    if s.get("explicit_deploy_config") or len(top.get("evidence", [])) >= 2:
        return "high"
    if s.get("code_pattern_only"):
        return "low"
    return "medium"
