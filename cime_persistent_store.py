"""Durable snapshot storage for Cime's existing SQLite databases.

The app still uses SQLite internally. On Vercel and configured Render
deployments, the full Cime data directory (auth DB, per-user libraries, and
media) is loaded/saved as one compressed PostgreSQL snapshot. A PostgreSQL
advisory lock serializes requests so concurrent saves do not silently overwrite
each other.
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sqlite3
import threading
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterator

DATA_DIR = Path(os.getenv("CIME_DATA_DIR", "/tmp/CimeDados")).resolve()
LOCK_KEY = 507_432_051_050
TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.cime_runtime_snapshot (
    singleton SMALLINT PRIMARY KEY CHECK (singleton = 1),
    version BIGINT NOT NULL,
    payload BYTEA NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
TABLE_SECURITY_SQL = (
    "ALTER TABLE public.cime_runtime_snapshot ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON TABLE public.cime_runtime_snapshot FROM PUBLIC",
)
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
_LOCAL_LOCK = threading.RLock()
_LOADED_VERSION: int | None = None
_TABLE_READY = False


class PersistentStoreError(RuntimeError):
    http_status = 503
    code = "CIME_PERSISTENCE_UNAVAILABLE"


class _DiscardWriter:
    def write(self, value):
        return len(value)

    def flush(self):
        return None

    def close(self):
        return None


class RequestStore:
    def __init__(self, conn, cursor, write: bool, version: int, snapshot_exists: bool):
        self.conn = conn
        self.cursor = cursor
        self.write = bool(write)
        self.version = int(version)
        self.snapshot_exists = bool(snapshot_exists)
        self.saved = False
        self.aborted = False

    def save(self) -> int:
        global _LOADED_VERSION
        if self.saved or not self.write:
            return int(_LOADED_VERSION or 0)
        payload = _pack_data_dir()
        if len(payload) > MAX_SNAPSHOT_BYTES:
            raise PersistentStoreError(
                "O conjunto de dados do Cime ultrapassou o limite seguro do snapshot."
            )
        self.cursor.execute(
            """
            INSERT INTO public.cime_runtime_snapshot
                (singleton, version, payload, updated_at)
            VALUES (1, 1, %s, now())
            ON CONFLICT (singleton) DO UPDATE SET
                version = public.cime_runtime_snapshot.version + 1,
                payload = EXCLUDED.payload,
                updated_at = now()
            RETURNING version
            """,
            (payload,),
        )
        row = self.cursor.fetchone()
        version = int(row[0]) if row else int(_LOADED_VERSION or 0) + 1
        _LOADED_VERSION = version
        self.version = version
        self.snapshot_exists = True
        self.saved = True
        return version

    def attach_response(self, handler) -> None:
        """Save before HTTP headers reach the client, so success means persisted."""
        original_end_headers = handler.end_headers

        def guarded_end_headers():
            if self.write and not self.saved and not self.aborted:
                try:
                    self.save()
                except Exception:
                    self.aborted = True
                    try:
                        handler._headers_buffer = []
                        body = (
                            b'{"ok":false,"error":"Nao foi possivel gravar os dados '
                            b'de forma permanente. Tente novamente.",'
                            b'"code":"CIME_PERSISTENCE_SAVE_FAILED"}'
                        )
                        handler.send_response(503)
                        handler.send_header("Content-Type", "application/json; charset=utf-8")
                        handler.send_header("Cache-Control", "no-store")
                        handler.send_header("Content-Length", str(len(body)))
                        original_end_headers()
                        handler.wfile.write(body)
                        handler.wfile = _DiscardWriter()
                    except Exception:
                        pass
                    return
            return original_end_headers()

        handler.end_headers = guarded_end_headers


def _database_url() -> str:
    for name in (
        "CIME_DATABASE_URL",
        "SUPABASE_DB_URL",
        "SUPABASE_DATABASE_URL",
        "POSTGRES_URL_NON_POOLING",
        "DATABASE_URL_UNPOOLED",
        "POSTGRES_URL",
        "DATABASE_URL",
    ):
        value = str(os.getenv(name, "")).strip()
        if value:
            return value
    raise PersistentStoreError("A conexão PostgreSQL do Cime não está configurada.")


def _persistent_store_enabled() -> bool:
    """Vercel always requires durable storage; other hosts opt in explicitly."""
    if str(os.getenv("CIME_DEPLOYMENT", "")).strip().lower() == "vercel":
        return True
    configured = str(os.getenv("CIME_PERSISTENT_STORE", "")).strip().lower()
    return configured in {"1", "true", "yes", "on"}


def _ensure_schema(cursor) -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    cursor.execute(TABLE_SQL)
    for statement in TABLE_SECURITY_SQL:
        cursor.execute(statement)
    _TABLE_READY = True


def _connect():
    try:
        import psycopg
    except Exception as exc:
        raise PersistentStoreError("O driver PostgreSQL não foi instalado no backend.") from exc
    try:
        conn = psycopg.connect(
            _database_url(),
            connect_timeout=8,
            sslmode="require",
            application_name="cime-dos-mundos-5.0",
        )
        conn.autocommit = True
        return conn
    except Exception as exc:
        raise PersistentStoreError("Não foi possível conectar ao banco permanente do Cime.") from exc


def _restore_data_dir(payload: bytes) -> None:
    root = DATA_DIR.resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{root.name}.restore-", dir=root.parent))
    backup = root.with_name(f".{root.name}.backup-{uuid.uuid4().hex}")
    moved_old_data = False
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            infos = archive.infolist()
            if len(infos) > 100000:
                raise PersistentStoreError("O backup contém uma quantidade inválida de arquivos.")
            total = sum(max(0, int(item.file_size)) for item in infos)
            if total > MAX_UNPACKED_BYTES:
                raise PersistentStoreError("O backup excede o tamanho seguro de restauração.")
            for item in infos:
                posix_name = PurePosixPath(item.filename.replace("\\", "/"))
                windows_name = PureWindowsPath(item.filename)
                if (posix_name.is_absolute() or windows_name.is_absolute() or windows_name.drive
                        or not posix_name.parts
                        or any(part in ("", ".", "..") for part in posix_name.parts)):
                    raise PersistentStoreError("O backup contém um caminho inválido.")
                target = (stage.joinpath(*posix_name.parts)).resolve()
                if target != stage and stage not in target.parents:
                    raise PersistentStoreError("O backup contém um caminho inválido.")
            archive.extractall(stage)
        if root.exists():
            os.replace(root, backup)
            moved_old_data = True
        try:
            os.replace(stage, root)
        except Exception:
            if moved_old_data and backup.exists():
                os.replace(backup, root)
                moved_old_data = False
            raise
        if moved_old_data:
            shutil.rmtree(backup, ignore_errors=True)
            moved_old_data = False
    except PersistentStoreError:
        raise
    except Exception as exc:
        raise PersistentStoreError("Não foi possível restaurar os dados permanentes do Cime.") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if moved_old_data and backup.exists() and not root.exists():
            os.replace(backup, root)


def _checkpoint_sqlite_files() -> None:
    if not DATA_DIR.exists():
        return
    for path in DATA_DIR.rglob("*.db"):
        if not path.is_file():
            continue
        db = None
        try:
            db = sqlite3.connect(str(path), timeout=5)
            row = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row and int(row[0] or 0) != 0:
                raise PersistentStoreError("Um banco SQLite está ocupado durante a gravação.")
        except PersistentStoreError:
            raise
        except Exception as exc:
            raise PersistentStoreError("Não foi possível preparar os arquivos do Cime para gravação.") from exc
        finally:
            if db is not None:
                db.close()

def _pack_data_dir() -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _checkpoint_sqlite_files()
    buffer = io.BytesIO()
    try:
        files = [
            path for path in sorted(DATA_DIR.rglob("*"))
            if path.is_file() and not path.is_symlink()
            and not path.name.endswith((".db-wal", ".db-shm", ".db-journal"))
        ]
        unpacked_size = sum(path.stat().st_size for path in files)
        if unpacked_size > MAX_UNPACKED_BYTES:
            raise PersistentStoreError("Os dados locais excedem o limite seguro de restauração.")
        with zipfile.ZipFile(
            buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=5
        ) as archive:
            for path in files:
                archive.write(path, path.relative_to(DATA_DIR).as_posix())
        return buffer.getvalue()
    except PersistentStoreError:
        raise
    except Exception as exc:
        raise PersistentStoreError("Não foi possível compactar os dados do Cime.") from exc


@contextlib.contextmanager
def request_context(write: bool = False) -> Iterator[RequestStore | None]:
    """Acquire the cross-instance lock and hydrate the local SQLite directory."""
    global _LOADED_VERSION
    if not _persistent_store_enabled():
        yield None
        return

    with _LOCAL_LOCK:
        conn = _connect()
        cursor = conn.cursor()
        locked = False
        try:
            try:
                cursor.execute("SET statement_timeout = '45000ms'")
                cursor.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
                locked = True
                _ensure_schema(cursor)
                cursor.execute(
                    "SELECT version, payload FROM public.cime_runtime_snapshot WHERE singleton = 1"
                )
                row = cursor.fetchone()
                if row:
                    remote_version = int(row[0])
                    if _LOADED_VERSION != remote_version:
                        _restore_data_dir(bytes(row[1]))
                    _LOADED_VERSION = remote_version
                    exists = True
                else:
                    _LOADED_VERSION = 0
                    remote_version = 0
                    exists = False
            except PersistentStoreError:
                raise
            except Exception as exc:
                raise PersistentStoreError(
                    "O banco permanente do Cime não respondeu corretamente."
                ) from exc

            store = RequestStore(conn, cursor, write, remote_version, exists)
            yield store
        finally:
            if locked:
                try:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
                except Exception:
                    pass
            try:
                cursor.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
