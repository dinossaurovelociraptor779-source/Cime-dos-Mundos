import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SCOPE_BOUNDARIES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_current_scope(root):
    """Walk a function body without crossing into nested scopes."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPE_BOUNDARIES):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _initialized_before_canonical_branch(tree):
    """Require initialization and the canonical-title branch in one function scope."""
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        nodes = list(_walk_current_scope(function))
        init_lines = []
        branch_lines = []
        for node in nodes:
            if isinstance(node, ast.Assign):
                targets = node.targets
                if any(
                    isinstance(target, ast.Name) and target.id == "combined_verify"
                    for target in targets
                ) and isinstance(node.value, ast.Constant) and node.value.value is None:
                    init_lines.append(node.lineno)
            elif isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "combined_verify"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is None
                ):
                    init_lines.append(node.lineno)
            elif isinstance(node, ast.If):
                if isinstance(node.test, ast.Name) and node.test.id == "canonical":
                    branch_lines.append(node.lineno)

        if init_lines and branch_lines and min(init_lines) < min(branch_lines):
            return True
    return False


class SmartVisionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "server_legacy.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_combined_verify_is_initialized_before_conditional_pipeline(self):
        # Structural guard against UnboundLocalError when no title is recognized.
        self.assertTrue(
            _initialized_before_canonical_branch(self.tree),
            "combined_verify deve ser inicializado com None antes de if canonical "
            "no mesmo escopo de função",
        )

    def test_guard_does_not_accept_initialization_in_another_function(self):
        unrelated_scopes = ast.parse(
            "def initialize_only():\n"
            "    combined_verify = None\n"
            "def pipeline_only():\n"
            "    if canonical:\n"
            "        pass\n"
        )
        self.assertFalse(_initialized_before_canonical_branch(unrelated_scopes))

    def test_backend_parses_as_python(self):
        ast.parse((ROOT / "server_v5.py").read_text(encoding="utf-8"))
        ast.parse((ROOT / "api" / "cime.py").read_text(encoding="utf-8"))
        ast.parse((ROOT / "api" / "index.py").read_text(encoding="utf-8"))

    def test_image_endpoint_has_diagnostics_and_result_contract(self):
        # Contract-level guard only; this does not execute OCR or external APIs.
        self.assertIn('"success": True', self.source)
        self.assertIn('"timing":', self.source)
        self.assertIn('"diagnostics":', self.source)
        self.assertIn('"season_number":season_number', self.source)


if __name__ == "__main__":
    unittest.main()
