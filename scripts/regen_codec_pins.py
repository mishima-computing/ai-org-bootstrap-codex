#!/usr/bin/env python3
"""Regenerate the codec handshake digest pins in ai_org/body_codec.py.

Run this after ANY change to the CUE schema / catalog / manifest that the Go
codec hashes (semantic_status.cue, durable-body-catalog-v1.json, etc.). It reads
the FOUR digests the current codec actually computes at handshake and rewrites
EXPECTED_{AUTHORITY,CATALOG,MANIFEST,PUBLIC_CONTRACT}_SHA256 to those true values.

NEVER hand-guess a codec digest -- run this tool. It is deterministic: it asks
the codec what it computed, it does not invent hashes.

Usage:  PYTHONPATH=<repo> python3 scripts/regen_codec_pins.py
Exit 0 and prints REGEN_OK on success (handshake passes after the rewrite).
"""
import importlib
import re
import subprocess
import sys

import ai_org.body_codec as bc
from ai_org.body_codec import strict_json_loads

_KEYS = ("authority_sha256", "catalog_sha256", "manifest_sha256", "public_contract_sha256")


def _computed_digests() -> dict[str, str]:
    """Capture the digests the codec reports, bypassing the pin comparison."""
    seen: dict[str, str] = {}
    orig = bc.BodyCodecClient._validate_handshake

    def _capture(self, result):
        md = strict_json_loads(result.artifact.data)
        for key in _KEYS:
            seen[key] = md.get(key) or md.get(key.replace("_sha256", "_digest"))
        return md  # skip the EXPECTED_* comparison; we only want the truth

    bc.BodyCodecClient._validate_handshake = _capture  # type: ignore[assignment]
    try:
        bc.BodyCodecClient().handshake(force=True)
    finally:
        bc.BodyCodecClient._validate_handshake = orig  # type: ignore[assignment]
    missing = [k for k in _KEYS if not seen.get(k)]
    if missing:
        raise SystemExit(f"codec did not report digests: {missing}")
    return seen


def main() -> int:
    path = bc.__file__
    src = original = open(path, encoding="utf-8").read()
    digests = _computed_digests()
    changed = []
    for key in _KEYS:
        name = "EXPECTED_" + key.upper()
        value = digests[key]
        pat = rf'({name}: Final = ")([0-9a-f]{{64}})(")'
        m = re.search(pat, src)
        if not m:
            raise SystemExit(f"{name} constant not found in {path}")
        if m.group(2) != value:
            src = re.sub(pat, rf"\g<1>{value}\g<3>", src, count=1)
            changed.append(f"{name}: {m.group(2)[:8]}..->{value[:8]}..")
    if src != original:
        open(path, "w", encoding="utf-8").write(src)
    # Verify in a FRESH process (module already imported with old constants).
    check = subprocess.run(
        [sys.executable, "-c",
         "from ai_org import body_codec; body_codec.BodyCodecClient().handshake(); print('HANDSHAKE_OK')"],
        capture_output=True, text=True,
    )
    if "HANDSHAKE_OK" not in check.stdout:
        raise SystemExit(f"handshake still fails after regen:\n{check.stdout}\n{check.stderr}")
    print("REGEN_OK", changed or "(already current)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
