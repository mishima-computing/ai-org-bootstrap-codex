from __future__ import annotations

import unittest
from pathlib import Path

from ai_org_bootstrap.registry import load_runtime_registry


ROOT = Path(__file__).resolve().parents[3]


class RegistryTests(unittest.TestCase):
    def test_registry_maps_every_agent_to_codex_adapter(self) -> None:
        entries = load_runtime_registry(ROOT / "registry" / "runtime-registry.yaml")
        self.assertEqual(len(entries), 8)
        for entry in entries:
            self.assertTrue(entry.adapter.startswith(".codex/agents/"))
            self.assertTrue((ROOT / entry.role).is_file())
            self.assertTrue((ROOT / entry.adapter).is_file())
            self.assertTrue((ROOT / entry.schema).is_file())


if __name__ == "__main__":
    unittest.main()
