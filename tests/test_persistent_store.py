import io
import sqlite3
import tempfile
import unittest
import zipfile
import os
from pathlib import Path
from unittest.mock import patch

import cime_persistent_store as store_module


class _FakeHandler:
    def __init__(self):
        self.events = []
        self.headers = []
        self.status = None
        self.body_stream = io.BytesIO()
        self.wfile = self.body_stream
        self._headers_buffer = []

    def end_headers(self):
        self.events.append("headers")

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers.append((name, value))


class PersistentSnapshotTests(unittest.TestCase):
    def test_explicit_postgres_urls_take_precedence(self):
        values = {
            "CIME_DATABASE_URL": "postgresql://cime@example.invalid/cime",
            "SUPABASE_DB_URL": "postgresql://supabase@example.invalid/cime",
            "POSTGRES_URL_NON_POOLING": "postgresql://neon@example.invalid/cime",
        }
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(
                store_module._database_url(),
                "postgresql://cime@example.invalid/cime",
            )
        with patch.dict(os.environ, {k: v for k, v in values.items() if k != "CIME_DATABASE_URL"}, clear=True):
            self.assertEqual(
                store_module._database_url(),
                "postgresql://supabase@example.invalid/cime",
            )

    def test_render_can_opt_into_postgres_persistence(self):
        with patch.dict(os.environ, {"CIME_DEPLOYMENT": "render", "CIME_PERSISTENT_STORE": "1"}, clear=True):
            self.assertTrue(store_module._persistent_store_enabled())
        with patch.dict(os.environ, {"CIME_DEPLOYMENT": "render", "CIME_PERSISTENT_STORE": "0"}, clear=True):
            self.assertFalse(store_module._persistent_store_enabled())
        with patch.dict(os.environ, {"CIME_DEPLOYMENT": "vercel", "CIME_PERSISTENT_STORE": "0"}, clear=True):
            self.assertTrue(store_module._persistent_store_enabled())

    def test_snapshot_table_is_private_to_database_roles(self):
        self.assertIn("ENABLE ROW LEVEL SECURITY", " ".join(store_module.TABLE_SECURITY_SQL))
        self.assertIn("REVOKE ALL", " ".join(store_module.TABLE_SECURITY_SQL))
        self.assertNotIn("FORCE ROW LEVEL SECURITY", " ".join(store_module.TABLE_SECURITY_SQL))

    def test_render_request_context_hydrates_from_postgres(self):
        class Cursor:
            def __init__(self):
                self.statements = []

            def execute(self, statement, params=None):
                self.statements.append(statement)

            def fetchone(self):
                return None

            def close(self):
                pass

        class Connection:
            def __init__(self):
                self.db_cursor = Cursor()
                self.closed = False

            def cursor(self):
                return self.db_cursor

            def close(self):
                self.closed = True

        conn = Connection()
        with (
            patch.dict(os.environ, {"CIME_DEPLOYMENT": "render", "CIME_PERSISTENT_STORE": "1"}, clear=True),
            patch.object(store_module, "_connect", return_value=conn),
            patch.object(store_module, "_TABLE_READY", False),
        ):
            with store_module.request_context(write=False) as request_store:
                self.assertIsNotNone(request_store)
                self.assertFalse(request_store.snapshot_exists)
            self.assertIn("ENABLE ROW LEVEL SECURITY", " ".join(conn.db_cursor.statements))
            self.assertTrue(conn.closed)

    def test_snapshot_round_trip_preserves_sqlite_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(store_module, "DATA_DIR", root):
                db_path = root / "users" / "1" / "library.db"
                db_path.parent.mkdir(parents=True)
                db = sqlite3.connect(db_path)
                try:
                    db.execute("CREATE TABLE titles (name TEXT NOT NULL)")
                    db.execute("INSERT INTO titles(name) VALUES (?)", ("Arcane",))
                    db.commit()
                finally:
                    db.close()
                payload = store_module._pack_data_dir()

                (root / "users" / "1" / "library.db").unlink()
                store_module._restore_data_dir(payload)

                db = sqlite3.connect(db_path)
                try:
                    names = db.execute("SELECT name FROM titles").fetchall()
                finally:
                    db.close()
                self.assertEqual(names, [("Arcane",)])

    def test_snapshot_rejects_path_traversal_without_clearing_current_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "keep.txt"
            marker.write_text("preserve", encoding="utf-8")
            payload_buffer = io.BytesIO()
            with zipfile.ZipFile(payload_buffer, "w") as archive:
                archive.writestr("../outside.txt", "unsafe")
            with patch.object(store_module, "DATA_DIR", root):
                with self.assertRaises(store_module.PersistentStoreError):
                    store_module._restore_data_dir(payload_buffer.getvalue())
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_failed_snapshot_extraction_preserves_current_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "keep.txt"
            marker.write_text("preserve", encoding="utf-8")
            payload_buffer = io.BytesIO()
            with zipfile.ZipFile(payload_buffer, "w") as archive:
                archive.writestr("new.txt", "new data")
            with patch.object(store_module, "DATA_DIR", root):
                with patch.object(store_module.zipfile.ZipFile, "extractall", side_effect=RuntimeError("bad archive")):
                    with self.assertRaises(store_module.PersistentStoreError):
                        store_module._restore_data_dir(payload_buffer.getvalue())
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_write_response_saves_before_headers_are_sent(self):
        events = []
        request_store = store_module.RequestStore(None, None, True, 0, False)
        handler = _FakeHandler()
        handler.events = events
        with patch.object(request_store, "save", side_effect=lambda: events.append("save")):
            request_store.attach_response(handler)
            handler.end_headers()
        self.assertEqual(events, ["save", "headers"])

    def test_read_only_response_does_not_write_snapshot(self):
        request_store = store_module.RequestStore(None, None, False, 1, True)
        handler = _FakeHandler()
        with patch.object(request_store, "save") as save:
            request_store.attach_response(handler)
            handler.end_headers()
        save.assert_not_called()
        self.assertEqual(handler.events, ["headers"])

    def test_failed_snapshot_write_returns_service_unavailable(self):
        request_store = store_module.RequestStore(None, None, True, 0, False)
        handler = _FakeHandler()
        with patch.object(request_store, "save", side_effect=RuntimeError("database unavailable")):
            request_store.attach_response(handler)
            handler.end_headers()
        self.assertEqual(handler.status, 503)
        self.assertIn(b"nao foi possivel gravar", handler.body_stream.getvalue().lower())
        self.assertIn(("Cache-Control", "no-store"), handler.headers)


if __name__ == "__main__":
    unittest.main()
