# Linon Calibration Reference Set C014-C016

Status: tracked pack fixture for Linon calibration. This file replaces the bootstrap-era `.agent-runs/.../code-review-findings.md` pointer for activation decisions.

## Scope

The reference set is static-read calibration material for the post-implementer, pre-PR Linon slot. It is not runtime evidence and does not waive live batteries.

## C014 Canonical Defect Classes

- NN1 self-report-trust: verbatim `tokensSaved`, no bridge-side recount, unverified provenance, and self-authored fake savings.
- NN2 forgeability: tenant-minted `compression.performed`, client-supplied `occurredAt`, pass-through hashes, and interested-party-writable evidence.
- NN3 unverified-integration: `/v1/compress` wire shape unverified against real upstream, mock-only adapter tests, lenient parser drift, and floating sidecar image.
- NN4 silent-failure: absent circuit breaker/retry/memory, health and metrics blind to headroom degradation, missing compose health/restart signals, invalid env fallback, and skipped-compression invisibility.

## Runtime-Authoritative Examples

- Real upstream `/v1/compress` compatibility.
- Compose-backed service readiness and `HEADROOM_BASE_URL` wiring.
- Latency, timeout, retry, and brown-out behavior under failure.
- Metrics visibility and health endpoint behavior in a running service.
- CCR compress-store-retrieve chain and admission behavior under live routing.

## C015-C016 Reserved Slots

C015 and C016 are reserved reference-set slots for the calibration run. Missing precedent or missing reference material is reported honestly as a calibration gap, not filled by analogy.
