---
profile_id: python-testing
scope: Python test continuity for deterministic pytest and async fixture behavior.
covers: deterministic tests; pytest-asyncio strict mode; async fixture correctness; fixture and event-loop isolation; fast-runner ordering flakes; Windows path and encoding portability.
freshness: Medium horizon; authored 2026-06-13 from genius-supplied dated anchors; re-check on pytest or pytest-asyncio major behavior changes.
supersede_trigger: Supersede when repo-local test harness evidence narrows these rules or pytest-asyncio changes async fixture semantics again.
evidence_refs: 2026-06-13 https://pytest-asyncio.readthedocs.io/en/latest/reference/configuration.html#asyncio-mode; 2026-06-13 https://pytest-asyncio.readthedocs.io/en/latest/reference/configuration.html#asyncio-default-test-loop-scope; 2026-06-13 https://docs.pytest.org/en/stable/how-to/fixtures.html; 2026-06-13 https://docs.pytest.org/en/stable/how-to/tmp_path.html; 2026-06-13 https://docs.python.org/3/library/pathlib.html; 2025-05-26 https://pytest-asyncio.readthedocs.io/en/v1.0.0/reference/changelog.html
---

Decidable review: tests that claim determinism must control time, randomness, ordering, filesystem state, and network boundaries inside fixtures or explicit fakes.
Decidable review: async pytest suites must use strict `asyncio_mode` semantics (default since pytest-asyncio v0.19.0) or an equivalent explicit marker/fixture contract for async fixtures.
Decidable review: fixtures that mutate process, event-loop, cwd, env, or filesystem state must restore isolation per test; prefer fresh loop scope when async state can leak.
Claim limit: fast runners and parallel or randomized ordering can expose hidden fixture coupling; treat ordering flakes as state-isolation defects, not runner defects.
Decidable review: path handling should use `pathlib` or pytest temp paths, not platform-specific separators or repo-root writes.
Decidable review: text fixtures and golden files must declare encoding so Windows defaults cannot change assertions.
