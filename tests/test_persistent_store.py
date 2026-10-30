import io
import sqlite3
import tempfile
import unittest
import zipfile
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
    def test_snapshot_round_trip_preserves_sqlite_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(store_module, "DATA_DIR", root):
                db_path = root / "users" / "1" / "library.db"
                db_path.parent.mkdir(parents=True)
                with sqlite3.connect(db_path) as db:
                    db.execute("CREATE TABLE titles (name TEXT NOT NULL)")
                    db.execute("INSERT INTO titles(name) VALUES (?)", ("Arcane",))
                payload = store_module._pack_data_dir()

                (root / "users" / "1" / "library.db").unlink()
                store_module._restore_data_dir(payload)

                with sqlite3.connect(db_path) as db:
                    names = db.execute("SELECT name FROM titles").fetchall()
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

    def test_write_response_saves_before_headers_are_sent(self):
        events = []
        request_store = store_module.RequestStore(None, None, True, 0, False)
        handler = _FakeHandler()
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
