import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class SmartVisionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "server_legacy.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_combined_verify_is_initialized_before_conditional_pipeline(self):
        # Prevents UnboundLocalError when the image yields no canonical title.
        initialization = re.search(r"^\s*combined_verify\s*=\s*None\b", self.source, re.MULTILINE)
        conditional = re.search(r"^\s*if canonical\s*:", self.source, re.MULTILINE)
        self.assertIsNotNone(initialization, "combined_verify deve ser inicializado com None")
        self.assertIsNotNone(conditional, "pipeline deve conter a condição if canonical")
        self.assertLess(initialization.start(), conditional.start(),
                        "combined_verify deve ser inicializado antes do pipeline condicional")

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
