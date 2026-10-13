import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class SmartVisionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "server_legacy.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_combined_verify_has_safe_default_for_pipeline(self):
        # The current pipeline initializes this variable in the no-canonical branch.
        # Keep this contract test tolerant of harmless comment/formatting changes.
        marker = "combined_verify = None"
        self.assertIn(marker, self.source)
        self.assertLess(self.source.index(marker), self.source.index("combined_verify or {}"))

    def test_backend_parses_as_python(self):
        ast.parse((ROOT / "server_v5.py").read_text(encoding="utf-8"))
        ast.parse((ROOT / "api" / "cime.py").read_text(encoding="utf-8"))
        ast.parse((ROOT / "api" / "index.py").read_text(encoding="utf-8"))

    def test_image_endpoint_has_diagnostics_and_result_contract(self):
        # Contract-level guard only; does not claim to run OCR or external APIs.
        self.assertIn('"success": True', self.source)
        self.assertIn('"timing":', self.source)
        self.assertIn('"diagnostics":', self.source)
        self.assertIn('"season_number":season_number', self.source)

if __name__ == "__main__":
    unittest.main()
