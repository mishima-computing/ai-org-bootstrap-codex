"""Library entry points: trace() runs the full chain, calls() lists a run.

Canonical chain (Memento tattoo -- the whole point of this package):
    field -> version   (git history of the body + committed round records)
    version -> run     (supervisor span covers the commit; series mentioned)
    run -> call        (carrier stdout contains the target text)
    call -> session    (stderr-recorded session id, content cross-checked)
    session -> thinking(mechanical extraction from the rollout, verbatim)

Two anchoring modes for the first link:
  * default    -- "who last wrote the CURRENT text": target resolved at the
                  branch tip, walk back from the tip.
  * at_commit  -- origin mode: target resolved AS OF a historical commit,
                  walk back from THAT commit to the commit that INTRODUCED
                  the text. Lets dead (since-deleted) text be adjudicated.
The rest of the chain is identical: it hangs off the introducing commit.

Every function here is deterministic and read-only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from . import gitread, rollout, runlog, version
from .model import CarrierCall, ProvenanceReport, SessionLink, ThinkingLink


def trace(vessel: str, branch: str, body_path: str,
          node: Optional[str] = None, term: Optional[str] = None,
          sessions_root: Optional[str] = None,
          fallback_days: int = 3,
          at_commit: Optional[str] = None) -> ProvenanceReport:
    """Adjudicate provenance for a spot in a committed body.

    Default mode resolves the target against the branch tip ("who last
    wrote the CURRENT text"). Origin mode (at_commit=<sha>) resolves the
    body and the target AS OF that commit, then finds the commit that
    INTRODUCED that text at or before it -- so text later rewritten or
    deleted can still be traced to its producing session.

    Returns a ProvenanceReport whose links are each resolved or explicitly
    broken (ok=False + reason). Never raises for evidence gaps; raises only
    for unusable arguments (missing repo/branch).
    """
    vessel = os.path.abspath(vessel)
    report = ProvenanceReport(vessel=vessel, branch=branch,
                              body_path=body_path)
    sroot = Path(sessions_root) if sessions_root else (
        rollout.DEFAULT_SESSIONS_ROOT)

    if not os.path.isdir(os.path.join(vessel, ".git")) and not os.path.isfile(
            os.path.join(vessel, ".git")):
        report.target.reason = f"{vessel} is not a git repository"
        return report
    if not gitread.branch_exists(vessel, branch):
        report.target.reason = f"branch {branch!r} does not exist in {vessel}"
        return report

    # --- anchor (origin mode) ---------------------------------------------
    # The anchor is the ref the body/target are resolved AS OF: the branch
    # tip by default, or at_commit in origin mode. Everything downstream
    # (version walk, run selection) hangs off the anchor, not the tip.
    anchor_sha = ""
    if at_commit:
        resolved = gitread.rev_parse_commit(vessel, at_commit)
        if resolved is None:
            report.target.reason = (
                f"at_commit {at_commit!r} does not resolve to a commit "
                f"in {vessel}")
            return report
        if not gitread.is_ancestor(vessel, resolved, branch):
            report.target.reason = (
                f"at_commit {resolved[:12]} is not reachable from branch "
                f"{branch!r}; cannot walk that branch's history from it")
            return report
        anchor_sha = resolved
        report.at_commit = anchor_sha

    # --- target ---------------------------------------------------------
    anchor_ref = anchor_sha or branch
    body_text = gitread.show_blob(vessel, anchor_ref, body_path)
    if body_text is None:
        where = (f"commit {anchor_sha[:12]}" if anchor_sha
                 else f"the tip of {branch!r}")
        report.target.reason = f"{body_path!r} does not exist at {where}"
        return report
    report.target = version.resolve_target(body_text, body_path, term, node)
    if not report.target.ok:
        return report

    # --- field -> version -------------------------------------------------
    report.version = version.resolve_version(
        vessel, branch, body_path, report.target, at_commit=anchor_sha)
    if not report.version.ok:
        return report

    # --- version -> run ----------------------------------------------------
    probes = _target_probes(report)
    report.run, run = runlog.select_run(
        vessel, branch, report.version.committed_at, probes)
    if not report.run.ok or run is None:
        return report

    # --- run -> call --------------------------------------------------------
    call, matches = runlog.select_call(run, probes)
    if call is None:
        stages = [
            candidate.stage or "<empty>"
            for candidate in run.carrier_calls
            if runlog.stdout_contains(run.run_dir, candidate, probes)
        ]
        report.run.reason = (
            "no authoring carrier call in the selected run contains the "
            "target text; all matching carrier calls were nonproducer "
            "stages: " + ", ".join(stages))
        return report
    report.call = call
    if len(matches) > 1:
        report.run.reason = (
            f"{len(matches)} carrier calls contain the target text; the "
            "earliest is adjudicated as the producer, the others are listed "
            "as candidates: "
            + ", ".join(f"#{m.index}({m.step or m.output_name})"
                        for m in matches[1:]))

    # --- call -> session ------------------------------------------------------
    report.session = rollout.match_session(
        run.run_dir, call, vessel, sessions_root=sroot,
        probes=probes, fallback_days=fallback_days)
    call.session_uuid = report.session.uuid
    call.rollout_path = report.session.rollout_path
    call.confidence = report.session.confidence
    if not report.session.ok:
        return report

    # --- session -> thinking ---------------------------------------------------
    target_texts = []
    if report.target.scalar_text:
        target_texts.append(report.target.scalar_text)
    report.thinking = rollout.extract_thinking(
        report.session.rollout_path, target_texts, term=term)
    return report


def _target_probes(report: ProvenanceReport) -> list:
    """Ordered probe texts: the full scalar first, the bare term last."""
    probes = []
    if report.target.scalar_text:
        probes.append(report.target.scalar_text)
    if report.target.term:
        probes.append(report.target.term)
    return probes


def calls(vessel: str, run_dir_or_latest: str,
          sessions_root: Optional[str] = None,
          match_sessions: bool = True,
          fallback_days: int = 3) -> list:
    """List a run's carrier calls with their session matches.

    run_dir_or_latest: absolute/relative run dir, or "latest" for the
    newest run under <vessel>/.ai-org/log/runs.
    """
    vessel = os.path.abspath(vessel)
    sroot = Path(sessions_root) if sessions_root else (
        rollout.DEFAULT_SESSIONS_ROOT)
    if run_dir_or_latest == "latest":
        run_dirs = runlog.find_run_dirs(vessel)
        if not run_dirs:
            raise FileNotFoundError(
                f"no runs under {vessel}/.ai-org/log/runs")
        run_dir = run_dirs[-1]
    else:
        run_dir = os.path.abspath(run_dir_or_latest)
    if not os.path.isfile(os.path.join(run_dir, runlog.SUPERVISOR)):
        raise FileNotFoundError(f"{run_dir} has no {runlog.SUPERVISOR}")

    run = runlog.load_run(run_dir)
    if match_sessions:
        for call in run.carrier_calls:
            link = rollout.match_session(
                run.run_dir, call, vessel, sessions_root=sroot,
                fallback_days=fallback_days)
            call.session_uuid = link.uuid
            call.rollout_path = link.rollout_path
            call.confidence = link.confidence
    return run.carrier_calls
