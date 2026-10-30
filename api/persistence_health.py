import json
import os
from http.server import BaseHTTPRequestHandler

os.environ.setdefault("CIME_DATA_DIR", "/tmp/CimeDados")
os.environ["CIME_DEPLOYMENT"] = "vercel"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            from cime_persistent_store import request_context, PersistentStoreError

            with request_context(write=False) as store:
                payload = {
                    "ok": bool(store is not None),
                    "version": "5.0",
                    "storage": "Neon PostgreSQL" if store is not None else "local",
                    "persistent": bool(store is not None),
                    "database_connected": bool(store is not None),
                    "snapshot_exists": bool(store.snapshot_exists) if store is not None else False,
                    "snapshot_version": int(store.version) if store is not None else 0,
                    "message": (
                        "Conexão com o banco permanente confirmada."
                        if store is not None
                        else "O armazenamento permanente não está ativo."
                    ),
                }
                status = 200 if store is not None else 503
        except PersistentStoreError as exc:
            payload = {
                "ok": False,
                "version": "5.0",
                "storage": "Neon PostgreSQL",
                "persistent": False,
                "database_connected": False,
                "snapshot_exists": False,
                "error": str(exc),
                "code": getattr(exc, "code", "CIME_PERSISTENCE_UNAVAILABLE"),
            }
            status = 503
        except Exception:
            payload = {
                "ok": False,
                "version": "5.0",
                "persistent": False,
                "database_connected": False,
                "error": "Não foi possível verificar o armazenamento permanente.",
            }
            status = 500

        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
