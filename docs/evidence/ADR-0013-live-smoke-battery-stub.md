# ADR-0013 Live Smoke/Battery Evidence Stub

## Status

External/pending. This stub does not prove runtime compatibility.

## Purpose

ADR-0013 requires live runtime claims to pass a live smoke/battery regime in addition to
static Linon review. This file reserves the committed evidence path for that regime.

## Required Future Contents

- Composition or runtime path under test.
- Exact command or harness used.
- Inputs, fixtures, and environment assumptions.
- Persisted logs for prompts, tool calls, outputs, diffs, verifier findings, and controller
  decisions when carriers are involved.
- Pass/fail result and timestamp.
- Link to the static Linon evidence for the same claim, if any.

Until those contents exist, any runtime-compatibility claim that points here remains a
**Hypothesis** under ADR-0011.
