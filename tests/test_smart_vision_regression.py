import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _scope_nodes(function):
    """Yield nodes belonging to this function, excluding nested scopes."""
    stack = list(reversed(function.body))
    while stack:
        node = stack.pop()
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        yield node
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _initialized_before_canonical_pipeline(tree):
    """Return True only when one function initializes the guard before its pipeline."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        scope = list(_scope_nodes(node))
        init_lines = []
        pipeline_lines = []

        for child in scope:
            if isinstance(child, ast.Assign):
                targets = child.targets
                assigns_guard = any(
                    isinstance(target, ast.Name) and target.id == "combined_verify"
                    for target in targets
                )
                if (
                    assigns_guard
                    and isinstance(child.value, ast.Constant)
                    and child.value.value is None
                ):
                    init_lines.append(child.lineno)
            elif isinstance(child, ast.AnnAssign):
                assigns_guard = (
                    isinstance(child.target, ast.Name)
                    and child.target.id == "combined_verify"
                )
                if (
                    assigns_guard
                    and isinstance(child.value, ast.Constant)
                    and child.value.value is None
                ):
                    init_lines.append(child.lineno)

            if (
                isinstance(child, ast.If)
                and isinstance(child.test, ast.Name)
                and child.test.id == "canonical"
            ):
                pipeline_lines.append(child.lineno)

        if any(init_line < pipeline_line for init_line in init_lines for pipeline_line in pipeline_lines):
            return True

    return False


class SmartVisionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "server_legacy.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_combined_verify_is_initialized_before_conditional_pipeline(self):
        # AST check avoids coupling the test to comments or formatting.
        self.assertTrue(_initialized_before_canonical_pipeline(self.tree))

    def test_guard_after_pipeline_does_not_pass_regression_check(self):
        tree = ast.parse(
            "def run():\n"
            "    if canonical:\n"
            "        pass\n"
            "    combined_verify = None\n"
        )
        self.assertFalse(_initialized_before_canonical_pipeline(tree))

    def test_guard_in_another_function_does_not_pass_regression_check(self):
        tree = ast.parse(
            "def initialize():\n"
            "    combined_verify = None\n"
            "def run_pipeline():\n"
            "    if canonical:\n"
            "        pass\n"
        )
        self.assertFalse(_initialized_before_canonical_pipeline(tree))

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
