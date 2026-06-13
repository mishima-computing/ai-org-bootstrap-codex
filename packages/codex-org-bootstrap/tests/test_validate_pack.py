from __future__ import annotations

import unittest
from pathlib import Path

from ai_org_bootstrap.scripts.validate_pack import validate


ROOT = Path(__file__).resolve().parents[3]


class ValidatePackTests(unittest.TestCase):
    def test_current_pack_validates(self) -> None:
        self.assertEqual(validate(ROOT), [])


if __name__ == "__main__":
    unittest.main()
