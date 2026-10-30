from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VercelRoutingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.gateway = (ROOT / "api" / "cime.py").read_text(encoding="utf-8")

    def test_frontend_uses_canonical_api_paths(self):
        start = self.index.index("function cimeBackendPath(path){")
        end = self.index.index("\n}", start) + 2
        helper = self.index[start:end]
        self.assertIn("return String(path||'');", helper)
        self.assertNotIn("return '/api/cime?__cime_path='", helper)

    def test_gateway_ignores_gateway_sentinel_when_restoring_route(self):
        self.assertIn("candidates=[str(value or \"\").strip() for value in q.get(\"__cime_path\",[])]", self.gateway)
        self.assertIn("(value.rstrip(\"/\") or \"/\")!=\"/api/cime\"", self.gateway)

    def test_qr_route_has_gateway_fallback(self):
        self.assertIn("def _qr_fallback_get(self):", self.gateway)
        self.assertIn('!= "/api/qr":', self.gateway)
        self.assertIn("if self._qr_fallback_get():", self.gateway)

    def test_all_mutating_get_routes_are_persisted(self):
        self.assertIn("def _is_write_request(method, path):", self.gateway)
        self.assertIn('"/api/library/enrich"', self.gateway)
        self.assertIn('"/api/library/covers"', self.gateway)
        self.assertIn('"/api/auth/status"', self.gateway)
        self.assertIn('"/api/bootstrap"', self.gateway)
        self.assertIn('write_request = _is_write_request(method, path)', self.gateway)

    def test_oauth_fallback_does_not_bypass_persistence_wrapper(self):
        self.assertIn("Do not handle OAuth here: direct responses bypass the persistent", self.gateway)
        self.assertIn("with request_context(write=True) as store:", self.gateway)

    def test_backend_routing_module_parses(self):
        import ast
        ast.parse(self.gateway)


if __name__ == "__main__":
    unittest.main()
