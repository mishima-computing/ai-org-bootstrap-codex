#!/usr/bin/env python3
"""Deterministic controller workflow runner (ADR-0004 Phase 2).

The Workflow side of the Workflow/Activity split. It advances a fixed phase sequence —
prepare → validate_contract → run_carrier → enforce_scope → run_verifiers → package_evidence →
await_semantic_judgment — wiring Phase-1 modules (models, scope, evidence) and the carrier harness.
It NEVER authors the contract or judges the deliverable: it executes mechanics, records an immutable
journal, and returns a ControllerRunReport for the semantic core to judge. The carrier runner is
injectable (the Activity boundary), so the wiring is testable offline without a real Codex carrier.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling modules, any PYTHONPATH
import controller_models as models          # noqa: E402
import controller_scope as scope            # noqa: E402
import controller_verifiers as verifiers    # noqa: E402
from controller_evidence import RunJournal, sha256_text  # noqa: E402


def _default_carrier_runner(repo, prompt, sandbox, *, timeout, retries, out_dir, output_file=None,
                            resume_session=None):
    import carrier_harness
    return carrier_harness.run_carrier(repo, prompt, sandbox, timeout=timeout, retries=retries,
                                       out_dir=out_dir, prepend_discipline=True, output_file=output_file,
                                       resume_session=resume_session)


PRODUCER_OUTPUT_RETRIES = 2   # extra carrier re-runs when a producer left no deliverable


def _cross_lane_forbidden(repo, own_allowed) -> tuple:
    """Globs owned by OTHER write-roles' registry lanes (e.g. a CI-writer's `.github/workflows/**`) and
    NOT in THIS role's own allowed-set. A role must never LAND nor be BLOCKED BY another role's lane: a
    CI-writer's legitimate workflow file, left in a shared repair worktree, otherwise reads as this role's
    out-of-scope deviation and sinks an otherwise-correct stage (observed live: a `mocks/ ONLY` goal whose
    implementer repair blocked on a `.github` file the CI-writer had legitimately produced). Deterministic,
    registry-derived — do NOT depend on the aufheben contract to remember to list it (#48: a goal's
    deliverable boundary is orthogonal to infra roles' standing authority; each role stays in its own lane
    and strays are reverted, not blocked on). Returns globs to ADD to coord_forbidden; () if the registry
    is unreadable (the LLM-provided forbidden_paths still apply). A clean wave is unaffected — a role's own
    worktree never holds another lane's files, so this only bites a shared repair worktree's leftover."""
    try:
        import controller_run
        from ai_org_bootstrap.registry import load_runtime_registry
        reg = load_runtime_registry(controller_run.org_root(Path(repo)) / "registry" / "runtime-registry.yaml")
    except Exception:                                      # noqa: BLE001 — lane derivation never breaks a run
        return ()
    own = set(own_allowed or ())
    lanes = {g for e in reg for g in (e.write_scope or ()) if g not in own and "/" in g}
    return tuple(sorted(lanes))


def _ensure_producer_output(repo, contract, output_schema, output_path, carrier, carrier_ok, journal):
    """A producing carrier can exit cleanly yet leave an EMPTY/absent result.json (a transient carrier
    miss — the model emitted no final message). The process-level retry in run_carrier never fires for
    that (exit 0), so the whole producer wave dies on one flake. Re-run the carrier a few times until the
    deliverable is non-empty. Default carrier path only; an injected runner (tests) is left untouched."""
    if not (carrier_ok and output_schema and output_path):
        return carrier, carrier_ok
    op = Path(repo) / output_path

    def _empty():
        return not op.is_file() or not op.read_text(encoding="utf-8", errors="replace").strip()

    tries = 0
    while _empty() and tries < PRODUCER_OUTPUT_RETRIES:
        tries += 1
        carrier = _default_carrier_runner(repo, contract.prompt, contract.sandbox,
                                          timeout=contract.timeout, retries=contract.retries,
                                          out_dir=journal.dir, output_file=op)
        carrier_ok = bool(carrier.get("ok"))
        journal.append("producer_output_retry", {"try": tries, "carrier_ok": carrier_ok,
                                                  "still_empty": _empty()})
    return carrier, carrier_ok


def run_contract(repo, contract, run_id, *, verifier_specs=None, include_builtin_gates=True,
                 declared=None, carrier_runner=None, clock=None,
                 quality_gate_enabled=False, cache_enabled=False,
                 output_schema=None, output_path=None,
                 resume_session=None) -> models.ControllerRunReport:
    """Execute one contract deterministically and return the report (await_semantic_judgment)."""
    repo = Path(repo)
    contract = contract if isinstance(contract, models.CarrierContract) else \
        models.CarrierContract.from_dict(contract)
    contract.validate()

    journal = RunJournal(repo, run_id, clock=clock)
    journal.append("validate_contract", {"role": contract.role, "sandbox": contract.sandbox,
                                         "prompt_sha256": sha256_text(contract.prompt),
                                         "files_allowed_to_change": contract.files_allowed_to_change})

    # content-hash baseline: a pre-dirty file the carrier edits further is caught by content, not
    # just path (NN3 — path-set subtraction would hide it).
    snapshot = scope.baseline_snapshot(repo)
    journal.append("baseline", {"snapshot_paths": sorted(snapshot)})

    # content-addressed cache: same contract + same pre-run state → REPLAY the prior change bundle
    # and SKIP the carrier (the dominant token cost). Conservative: replay verifies, else cache miss.
    carrier = None
    cache_hit = False
    cache_key = cache_state = None
    if cache_enabled:
        import controller_cache
        cache_state = controller_cache.state_hash(repo, snapshot)
        cache_key = controller_cache.contract_key(contract.to_dict(), cache_state)
        bundle = controller_cache.lookup(repo, cache_key, cache_state)
        if bundle and controller_cache.replay(repo, bundle, snapshot):
            carrier = bundle["carrier_result"]
            cache_hit = True
            journal.append("cache_hit", {"key": cache_key})

    if carrier is None:
        if carrier_runner is not None:
            carrier = carrier_runner(repo, contract.prompt, contract.sandbox,
                                     timeout=contract.timeout, retries=contract.retries,
                                     out_dir=journal.dir, resume_session=resume_session)
        else:
            ofile = (repo / output_path) if output_path else None
            carrier = _default_carrier_runner(repo, contract.prompt, contract.sandbox,
                                              timeout=contract.timeout, retries=contract.retries,
                                              out_dir=journal.dir, output_file=ofile,
                                              resume_session=resume_session)
    carrier_ok = bool(carrier.get("ok"))
    attempts = carrier.get("attempts", [])
    journal.append("run_carrier", {"ok": carrier_ok, "cache_hit": cache_hit, "attempts": attempts})
    if carrier_runner is None and not cache_hit:   # absorb a transient empty producer deliverable
        carrier, carrier_ok = _ensure_producer_output(repo, contract, output_schema, output_path,
                                                      carrier, carrier_ok, journal)
    if cache_enabled and not cache_hit and carrier_ok:
        import controller_cache
        controller_cache.store(repo, cache_key, cache_state, snapshot, carrier)

    # quality gate (implementer): AFTER the carrier, BEFORE scope (fast_fix mutates the carrier's
    # files — scope must see the post-quality tree). Lint/debug only; no destructive auto-revert.
    quality = None
    if quality_gate_enabled and carrier_ok:
        import quality_gate
        carrier_changed = scope.changed_since(repo, snapshot)
        quality = quality_gate.run_quality_gate(carrier_changed, repo)
        journal.append("quality_gate", quality)

    # schema-output gate (producing / verifying carriers): validate the carrier's JSON output against
    # its role schema, in Python (zero LLM tokens) and fail-closed — a malformed proposal is rejected
    # before the controller reads it or a downstream carrier is wasted on it.
    output_gate = None
    if output_schema and output_path and carrier_ok:
        import controller_cache
        import controller_output
        if not controller_cache._safe_rel(repo, output_path):  # no traversal/escape to a foreign file
            output_gate = {"output_ok": False, "errors": [f"output_path escapes repo: {output_path}"]}
        else:
            op = repo / output_path
            text = op.read_text(encoding="utf-8", errors="replace") if op.is_file() else ""
            output_gate = controller_output.gate_output(text, output_schema)
        journal.append("output_gate", output_gate)

    # forbidden classes are controller-owned: contract paths ADD to DEFAULT_FORBIDDEN, never replace
    # it (NN2 — an interested-party contract must not be able to disable the absolute forbidden set).
    forbidden = tuple(scope.DEFAULT_FORBIDDEN) + tuple(contract.forbidden_paths or ())
    # the designated deliverable channel (output_path) is not a codebase deviation: whitelist exactly
    # that one path on a LOCAL copy so the journaled contract.files_allowed_to_change is never mutated.
    # forbidden_hits and the output traversal guard run independently of this list, so widening it here
    # cannot launder a forbidden or escaping path.
    allowed = list(contract.files_allowed_to_change or [])
    if output_path:
        allowed.append(output_path)

    # strip-and-pass for COORDINATION-forbidden paths: paths another role owns (e.g. a CI-writer's
    # .github/workflows/*, which an over-eager implementer routinely adds "to be helpful"). Such an
    # out-of-bounds extra must NOT land AND must NOT sink an otherwise-correct stage — so revert it
    # BEFORE the scope check, leaving the deliverable as exactly the in-scope work. The ABSOLUTE/security
    # set (DEFAULT_FORBIDDEN) is left to HARD-FAIL: attempting it is the signal, not a slip.
    coord_forbidden = tuple(contract.forbidden_paths or ()) \
        + _cross_lane_forbidden(repo, contract.files_allowed_to_change)   # #48: other roles' lanes, registry-derived
    # Strip over the FULL dirty set (porcelain_touched), not this stage's delta (changed_since), and
    # do it even when the carrier failed: a coordination-forbidden file created in an EARLIER iteration
    # that TIMED OUT (carrier_ok False -> strip skipped) would otherwise persist in the shared repair
    # worktree, and `enforce` flags forbidden against the full set — so every later repair stage fails
    # on a leftover its own delta no longer contains, looping forever. Cleaning the leftover unconditionally
    # lets the next iteration start in-scope. (DEFAULT_FORBIDDEN/security is still left to HARD-FAIL below.)
    if coord_forbidden:
        import fnmatch
        import subprocess
        stripped = []
        for f in scope.porcelain_touched(repo):
            if any(fnmatch.fnmatch(f, g) for g in coord_forbidden) and \
               not any(fnmatch.fnmatch(f, g) for g in scope.DEFAULT_FORBIDDEN):
                # unstage (a staged-new add survives a plain `checkout HEAD --`), then revert if the path
                # exists at HEAD, else remove the working-tree copy. Handles staged/untracked/modified/deleted.
                subprocess.run(["git", "-C", str(repo), "reset", "-q", "HEAD", "--", f], capture_output=True)
                co = subprocess.run(["git", "-C", str(repo), "checkout", "-q", "HEAD", "--", f],
                                    capture_output=True)
                p = Path(repo) / f
                if co.returncode != 0 and p.exists():        # not at HEAD -> newly added; remove it
                    p.unlink()
                # a leftover from an earlier iteration sits in THIS stage's baseline snapshot; reverting it
                # would otherwise read as a DELETE-vs-baseline and resurface as a deviation. Drop it from the
                # snapshot so the strip is a true no-op to the scope check, not a new "change".
                snapshot.pop(f, None)
                stripped.append(f)
        if stripped:
            journal.append("strip_coordination_forbidden", {"stripped": stripped})

    # mechanical scope PREVENTION (ADR-0008): TRIM the carrier's out-of-scope edits to the contract instead
    # of failing the leaf — deterministic, no LLM, no human. Forbidden touches are NOT stripped (enforce()
    # below still fails them loudly); a scope overreach simply doesn't kill the leaf any more.
    oos_stripped = scope.strip_to_scope(repo, allowed, baseline_snapshot=snapshot, forbidden=forbidden)
    if oos_stripped:
        journal.append("scope_strip_out_of_scope", {"stripped": oos_stripped})

    scope_report = scope.enforce(repo, allowed, baseline_snapshot=snapshot,
                                 forbidden=forbidden, declared=declared)
    journal.append("enforce_scope", scope_report.to_dict())

    specs = list(verifier_specs or [])
    if include_builtin_gates:
        specs = verifiers.builtin_gate_specs(repo) + specs
    verifier_runs = verifiers.run_all(specs, evidence_dir=journal.dir) if specs else []
    journal.append("run_verifiers", {"results": [v.to_dict() for v in verifier_runs]})

    # expected_verifiers must all have run AND passed (NN3 — a required guard that never ran is a fail)
    ran_pass = {v.name for v in verifier_runs if v.status == "pass"}
    missing_expected = [n for n in contract.expected_verifiers if n not in ran_pass]

    # content-addressed diff artifact (includes untracked carrier deliverables) attached to the report
    import carrier_harness
    diff_art = carrier_harness.diff_artifact(repo, journal.dir / "diff.patch") \
        if scope_report.changed else None

    unresolved = []
    if not carrier_ok:
        unresolved.append("carrier did not complete (timeout/hang/nonzero)")
    if not scope_report.scope_ok:
        unresolved.append(f"scope: deviations={scope_report.deviations} "
                          f"forbidden={scope_report.forbidden_hits} undeclared={scope_report.undeclared}")
    for v in verifier_runs:
        if v.status != "pass":
            unresolved.append(f"verifier {v.name}: {v.status} (exit {v.exit_code})")
    if missing_expected:
        unresolved.append(f"expected verifiers missing or not passed: {missing_expected}")
    quality_ok = quality is None or bool(quality.get("quality_pass"))
    if quality is not None and not quality_ok:
        unresolved.append(f"quality gate: {quality.get('lint', {}).get('new_error_count')} new lint, "
                          f"{len(quality.get('debug_tags', []))} debug tags, "
                          f"tools_failed={quality.get('tools_failed')}")
    output_ok = output_gate is None or bool(output_gate.get("output_ok"))
    if output_gate is not None and not output_ok:
        unresolved.append(f"schema-output gate: {output_gate.get('errors')}")

    ok = (carrier_ok and scope_report.scope_ok and verifiers.all_passed(verifier_runs)
          and not missing_expected and quality_ok and output_ok)
    report = models.ControllerRunReport(
        contract_role=contract.role, ok=ok, sandbox=contract.sandbox, attempts=attempts,
        changed_files=scope_report.changed, scope=scope_report.to_dict(), diff_artifact=diff_art,
        quality=quality, verifier_results=[v.to_dict() for v in verifier_runs],
        unresolved_failures=unresolved,
        session_id=carrier.get("session_id") if isinstance(carrier, dict) else None,
    )
    journal.append("package_evidence", {"ok": ok, "report": report.to_dict()})
    # await_semantic_judgment: the report is returned; the LLM/owner decides. The workflow does not.
    return report


if __name__ == "__main__":
    import argparse
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--contract", required=True, help="path to contract.json")
    p.add_argument("--run-id", required=True)
    args = p.parse_args()
    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    rep = run_contract(args.repo, contract, args.run_id)
    print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    raise SystemExit(0 if rep.ok else 1)
