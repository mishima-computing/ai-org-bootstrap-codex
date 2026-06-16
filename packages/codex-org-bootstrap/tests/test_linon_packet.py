from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
VERIFY = ROOT / "scripts" / "verify-linon-packet.py"
FIXTURE_DIR = ROOT / "fixtures" / "linon-review" / "packet"


def run_verify(packet: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), str(packet)],
        cwd=ROOT,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class LinonPacketTests(unittest.TestCase):
    def test_example_packet_is_accepted(self) -> None:
        result = run_verify(FIXTURE_DIR / "example-packet.json")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_forged_packet_is_rejected_with_hash_mismatch(self) -> None:
        result = run_verify(FIXTURE_DIR / "forged-packet.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sha256 mismatch", result.stderr)

    def test_scope_violation_packet_is_rejected_with_path(self) -> None:
        result = run_verify(FIXTURE_DIR / "scope-violation-packet.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("docs/architecture.md", result.stderr)

    def test_missing_ratification_status_is_rejected(self) -> None:
        packet = json.loads((FIXTURE_DIR / "example-packet.json").read_text(encoding="utf-8"))
        del packet["implementation_contract"]["ratification_status"]
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_packet = Path(tmp_dir) / "missing-ratification.json"
            tmp_packet.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
            result = run_verify(tmp_packet)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ratification_status", result.stderr)


if __name__ == "__main__":
    unittest.main()
