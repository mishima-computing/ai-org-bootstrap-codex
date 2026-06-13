---
profile_id: python
scope: Python implementation continuity for async, native-extension, packaging, and declared failure semantics.
covers: asyncio blocking offload; native-extension thread affinity; uv and pyproject runtime pins; declared fail-open or fail-closed contracts.
freshness: Medium horizon; authored 2026-06-13 from genius-supplied dated anchors; re-check on Python, uv, PyO3, or tree-sitter concurrency changes.
supersede_trigger: Supersede when selector gains repo-local Python lint or when upstream async/native-extension semantics materially change.
evidence_refs: 2026-06-13 https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread; 2026-06-13 https://pyo3.rs/main/class/thread-safety; 2026-06-13 https://tree-sitter.github.io/py-tree-sitter/classes/tree_sitter.Parser.html; 2026-06-13 https://docs.astral.sh/uv/concepts/projects/layout/#the-pyprojecttoml; 2026-06-13 https://docs.astral.sh/uv/concepts/projects/sync/#syncing-the-environment; 2026-06-13 https://packaging.python.org/en/latest/specifications/pyproject-toml/#requires-python
---

Decidable review: async code must offload blocking subprocess, filesystem, or CPU work from the event loop with `asyncio.to_thread()` or an equivalent executor boundary.
Decidable review: Python runtime compatibility is repo-declared by `pyproject.toml` `requires-python` and lock state such as `uv.lock`; do not infer a wider floor.
Claim limit: native-extension objects may carry thread-affinity hazards; PyO3 `#[pyclass(unsendable)]` can panic on cross-thread access including GC traversal from another thread, and tree-sitter `Parser` is not `Send`.
Claim limit: free-threaded/no-GIL safety is not implied by ordinary Python compatibility metadata; require explicit upstream and repo evidence.
Declared contract: fail-open vs fail-closed behavior must be named at the boundary that consumes uncertain analysis, never left implicit.
Boundary: keep these as implementation-craft continuity rules, not lint bodies or product-specific worldview.
