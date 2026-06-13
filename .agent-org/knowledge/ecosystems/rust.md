---
profile_id: rust
scope: Rust implementation continuity for toolchain floors, workspace shape, lockfiles, safety boundaries, and FFI claims.
covers: repo-declared rust-version and edition MSRV floor; workspace default-members; Cargo.lock pinning; Send and Sync with unsafe; panic versus Result boundaries; conditional PyO3 or maturin abi3 FFI.
freshness: Medium horizon; authored 2026-06-13 from genius-supplied dated anchors; re-check on Cargo resolver, edition, or PyO3/maturin FFI changes.
supersede_trigger: Supersede when repo-local Rust CI pins or upstream Cargo/PyO3/maturin semantics materially change.
evidence_refs: 2026-06-13 https://doc.rust-lang.org/cargo/reference/manifest.html#the-rust-version-field; 2025-02-20 https://blog.rust-lang.org/2025/02/20/Rust-1.85.0/; 2026-06-13 https://doc.rust-lang.org/cargo/reference/resolver.html; 2026-06-13 https://doc.rust-lang.org/cargo/reference/workspaces.html#the-default-members-field; 2026-06-13 https://doc.rust-lang.org/nomicon/send-and-sync.html; 2026-06-13 https://www.maturin.rs/distribution.html#python-abi3-wheels
---

Decidable review: the compatibility floor is repo-declared by `rust-version`, edition, and CI/toolchain pins; edition 2024 requires Rust >=1.85, while resolver 3 (its default) requires Rust >=1.84.
Decidable review: workspace `default-members` is a subset of `members`; CI that builds defaults may not cover every member unless it says so.
Decidable review: application and binary repos should commit `Cargo.lock`; libraries may intentionally omit it only as a declared policy.
Decidable review: `unsafe`, manual `Send`, or manual `Sync` marks a concurrency boundary requiring a stated invariant near the code.
Decidable review: public library or FFI boundaries should return `Result` for recoverable errors; panics are acceptable only as explicit invariant violations.
Claim limit: PyO3, maturin, and `abi3` rules apply only when the repo actually declares Python FFI packaging.
