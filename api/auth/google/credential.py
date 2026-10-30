import os, sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# credential.py is nested under api/auth/google; the repository root is
# therefore three parents above this file.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("CIME_DATA_DIR", "/tmp/CimeDados")
os.environ.setdefault("CIME_PORT", "80")
os.environ.setdefault("CIME_BIND", "0.0.0.0")
os.environ["CIME_DEPLOYMENT"] = "vercel"
os.environ["CIME_PUBLIC_URL"] = "https://cime-dos-mundos.vercel.app"
os.environ["GOOGLE_REDIRECT_URI"] = os.environ["CIME_PUBLIC_URL"] + "/oauth/google/callback"

_gateway = None
_boot = False

def load_gateway():
    global _gateway, _boot
    if _gateway is not None:
        return _gateway
    import server_v5 as cime
    from server_v5 import Gateway, bootstrap
    cime.public_lan_base = lambda: os.environ["CIME_PUBLIC_URL"]
    os.environ["GOOGLE_REDIRECT_URI"] = os.environ["CIME_PUBLIC_URL"] + "/oauth/google/callback"
    if not _boot:
        bootstrap()
        _boot = True
    _gateway = Gateway
    return _gateway

class handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CimeDosMundos/5.0"

    def _run(self, method):
        self.path = "/api/auth/google/credential"
        try:
            from cime_persistent_store import request_context, PersistentStoreError
            with request_context(write=method in ("do_POST", "do_PUT", "do_DELETE")) as store:
                if store is not None:
                    store.attach_response(self)
                cls = load_gateway()
                # Copy helpers only; keep this wrapper installed for every invocation.
                for name in ("send_json", "send_html", "body", "read_body", "public"):
                    setattr(handler, name, getattr(cls, name))
                handler.send_file = getattr(cls, "send_file", None)
                handler.protocol_version = getattr(cls, "protocol_version", "HTTP/1.1")
                handler.server_version = getattr(cls, "server_version", "CimeDosMundos/5.0")
                return getattr(cls, method)(self)
        except PersistentStoreError as exc:
            import json
            raw = json.dumps({
                "ok": False,
                "error": "O armazenamento permanente do Cime está indisponível. O login não foi concluído.",
                "code": getattr(exc, "code", "CIME_PERSISTENCE_UNAVAILABLE")
            }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception as exc:
            import json
            raw = json.dumps({
                "ok": False,
                "error": "Backend Python não conseguiu inicializar.",
                "detail": str(exc)[:1000]
            }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def do_POST(self):
        return self._run("do_POST")

    def do_OPTIONS(self):
        return self._run("do_OPTIONS")
