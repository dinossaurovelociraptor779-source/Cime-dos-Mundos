"""Targeted regressions for the Cime 5.0 stabilization pass.

These tests verify route persistence classification and guard against backup
cross-account leakage. They do not replace live OAuth, OCR, or Vercel tests.
"""
import json
import unittest
from pathlib import Path

from api.cime import _is_write_request

ROOT = Path(__file__).resolve().parents[1]


class PersistenceRouteTests(unittest.TestCase):
    def test_all_state_changing_get_routes_are_persisted(self):
        routes = (
            "/api/auth/status",
            "/api/bootstrap",
            "/api/library/enrich",
            "/api/library/covers",
            "/api/import/history/consolidate",
            "/oauth/google/start",
            "/oauth/google/callback",
            "/api/google_oauth_callback",
            "/api/google_oauth_callback.py",
        )
        for route in routes:
            with self.subTest(route=route):
                self.assertTrue(_is_write_request("do_GET", route))

    def test_mutation_methods_are_always_persisted(self):
        for method in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
            with self.subTest(method=method):
                self.assertTrue(_is_write_request(method, "/api/titles"))

    def test_read_only_get_routes_are_not_marked_as_writes(self):
        for route in ("/api/titles", "/api/anime/search", "/api/stats", "/api/vision/status"):
            with self.subTest(route=route):
                self.assertFalse(_is_write_request("do_GET", route))


class BackupIsolationTests(unittest.TestCase):
    def test_backup_does_not_copy_shared_legacy_database_or_media(self):
        source = (ROOT / "server_legacy.py").read_text(encoding="utf-8")
        start = source.index('if path == "/api/backup":')
        end = source.index('if path == "/api/export":', start)
        backup = source[start:end]
        self.assertNotIn('z.write(DB, "cime.db")', backup)
        self.assertIn("media_dir=os.fspath(MEDIA)", backup)
        self.assertIn('"imported_history"', backup)

    def test_manifest_declares_svg_icon_with_svg_mime_type(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest.get("icons"))
        for icon in manifest["icons"]:
            if icon.get("src", "").endswith(".svg"):
                self.assertEqual(icon.get("type"), "image/svg+xml")
                self.assertEqual(icon.get("sizes"), "any")


class DiagnosticsTests(unittest.TestCase):
    def test_auth_diagnostics_use_public_origin_in_production(self):
        source = (ROOT / "server_v5.py").read_text(encoding="utf-8")
        self.assertIn("google_origin=public_origin or f'http://localhost:{PORT}'", source)
        self.assertIn("'lan_enabled':os.getenv('CIME_DEPLOYMENT')!='vercel'", source)


if __name__ == "__main__":
    unittest.main()
