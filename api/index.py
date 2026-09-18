import os, sys, json, traceback
from pathlib import Path
from http.server import BaseHTTPRequestHandler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("CIME_DATA_DIR", "/tmp/CimeDados")
os.environ.setdefault("CIME_PORT", "80")
os.environ.setdefault("CIME_BIND", "0.0.0.0")
os.environ.setdefault("CIME_DEPLOYMENT", "vercel")

_HANDLER_CLASS = None
_IMPORT_ERROR = None


def _public_base():
    explicit = str(os.getenv("CIME_PUBLIC_URL", "")).strip().rstrip("/")
    if explicit:
        return explicit
    for key in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
        host = str(os.getenv(key, "")).strip().rstrip("/")
        if host:
            return host if host.startswith(("http://", "https://")) else "https://" + host
    return ""


def _load_gateway():
    global _HANDLER_CLASS, _IMPORT_ERROR
    if _HANDLER_CLASS is not None:
        return _HANDLER_CLASS
    if _IMPORT_ERROR is not None:
        raise _IMPORT_ERROR

    try:
        import server_v5 as cime
        from server_v5 import Gateway, bootstrap

        public = _public_base()
        if public:
            cime.public_lan_base = lambda: public
            os.environ["GOOGLE_REDIRECT_URI"] = public + "/oauth/google/callback"

        # Initialize only when the first non-health request arrives.
        bootstrap()

        class VercelGateway(Gateway):
            pass

        _HANDLER_CLASS = VercelGateway
        return _HANDLER_CLASS
    except Exception as exc:
        _IMPORT_ERROR = exc
        raise


class handler(BaseHTTPRequestHandler):
    def _health(self):
        public = _public_base()
        raw = json.dumps({
            "ok": True,
            "version": "5.0",
            "service": "Cime dos Mundos",
            "deployment": "vercel",
            "online": bool(public),
            "public_url": public or None,
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _delegate(self, method):
        try:
            Gateway = _load_gateway()
            # Rebind this instance's class so the existing server_v5 methods
            # can process the request with the original implementation.
            self.__class__ = type(
                "_RuntimeGateway",
                (Gateway, handler),
                {}
            )
            return getattr(Gateway, method)(self)
        except Exception as exc:
            raw = json.dumps({
                "ok": False,
                "version": "5.0",
                "error": "Backend Python não conseguiu inicializar.",
                "detail": str(exc),
            }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                pass

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/api/health", "/health"):
            return self._health()
        return self._delegate("do_GET")

    def do_POST(self):
        return self._delegate("do_POST")

    def do_PUT(self):
        return self._delegate("do_PUT")

    def do_PATCH(self):
        return self._delegate("do_PATCH")

    def do_DELETE(self):
        return self._delegate("do_DELETE")

    def do_OPTIONS(self):
        return self._delegate("do_OPTIONS")
