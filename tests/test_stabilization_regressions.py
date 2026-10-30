"""Targeted regression tests for the Cime 5.0 stabilization pass.

These checks cover route persistence classification, account-scoped backups,
PWA metadata and public-origin diagnostics. They do not replace live OAuth,
OCR, Neon recovery or multi-account integration tests.
"""
import io
import json
import os
import unittest
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("CIME_SESSION_SECRET", "test-only-session-secret-" + "x" * 64)
os.environ.pop("CIME_DEPLOYMENT", None)

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


class RequestBodyTests(unittest.TestCase):
    def test_legacy_reader_reuses_cached_body_without_consuming_next_request(self):
        from server_v5 import Gateway

        handler = object.__new__(Gateway)
        handler._cime_body_cache = b'{"titles":[]}'
        handler.headers = {}
        handler.rfile = io.BytesIO(b'NEXT HTTP REQUEST')
        self.assertEqual(handler.read_body(), b'{"titles":[]}')
        self.assertEqual(handler.rfile.read(), b'NEXT HTTP REQUEST')

    def test_new_keep_alive_request_clears_previous_body_cache(self):
        from server_v5 import Gateway

        handler = object.__new__(Gateway)
        handler._cime_body_cache = b'previous request'
        handler.headers = {"Content-Length": "3"}
        handler.rfile = io.BytesIO(b'new')
        handler._reset_body_cache()
        self.assertEqual(handler.read_body(), b'new')


class DiagnosticsTests(unittest.TestCase):
    def test_health_and_persistence_diagnostics_are_public(self):
        source = (ROOT / "server_v5.py").read_text(encoding="utf-8")
        start = source.index("def public(self,path):")
        end = source.index("\n    def ", start + 5)
        public_routes = source[start:end]
        for route in ("/health", "/api/health", "/api/cime_health", "/api/persistence/health"):
            with self.subTest(route=route):
                self.assertIn(repr(route), public_routes)

    def test_auth_diagnostics_use_public_origin_in_production(self):
        source = (ROOT / "server_v5.py").read_text(encoding="utf-8")
        self.assertIn("google_origin=public_origin or f'http://localhost:{PORT}'", source)
        self.assertIn("'lan_enabled':os.getenv('CIME_DEPLOYMENT')!='vercel'", source)

    def test_health_routes_identify_their_liveness_only_contract(self):
        for path in ("api/health.py", "api/cime_health.py"):
            source = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn('"health_type": "liveness"', source)
            self.assertIn('"/api/persistence/health"', source)



class BrowserOcrFallbackTests(unittest.TestCase):
    def test_server_uses_browser_ocr_hint_when_tesseract_is_unavailable(self):
        import server_legacy as legacy

        hint = "Demon Slayer Season 3 Episode 12"
        with patch.object(legacy, "_ocr_cache_get", return_value=None), patch.object(legacy, "_ocr_cache_put"), patch.object(legacy.Image, "open", side_effect=OSError("Tesseract image unavailable")):
            result = legacy.read_image_anchor_text(b"not-an-image", hint)
        self.assertIn("Demon Slayer", result)
        self.assertIn("Episode 12", result)

    def test_upload_route_accepts_client_ocr_hint(self):
        source = (ROOT / "server_legacy.py").read_text(encoding="utf-8")
        self.assertIn('self.headers.get("X-OCR-Hint"', source)
        self.assertIn("read_image_anchor_text(body,client_ocr_hint)", source)

    def test_frontend_has_lazy_browser_ocr_fallback(self):
        source = (ROOT / "app.html").read_text(encoding="utf-8")
        self.assertIn("tesseract.js@5/dist/tesseract.min.js", source)
        self.assertIn("createWorker('eng+por')", source)
        self.assertIn("uploadHeaders['X-OCR-Hint']=ocrHint", source)

if __name__ == "__main__":
    unittest.main()
