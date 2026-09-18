import os, sys, json, traceback
from pathlib import Path
from http.server import BaseHTTPRequestHandler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("CIME_DATA_DIR", "/tmp/CimeDados")
os.environ.setdefault("CIME_PORT", "80")
os.environ.setdefault("CIME_BIND", "0.0.0.0")
os.environ["CIME_DEPLOYMENT"] = "vercel"
# OAuth MUST use one fixed production callback. Do not let VERCEL_URL or an old
# Vercel environment variable replace it, because Google compares redirect_uri exactly.
os.environ["CIME_PUBLIC_URL"] = "https://cime-dos-mundos-5-0-miguel-3106.vercel.app"
os.environ["GOOGLE_REDIRECT_URI"] = os.environ["CIME_PUBLIC_URL"] + "/oauth/google/callback"

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


_GATEWAY = None
_IMPORT_ERROR = None
_BOOT_DONE = False


def _load_gateway():
    global _GATEWAY, _IMPORT_ERROR, _BOOT_DONE
    if _GATEWAY is not None:
        return _GATEWAY
    if _IMPORT_ERROR is not None:
        raise _IMPORT_ERROR

    try:
        import server_v5 as cime
        from server_v5 import Gateway, bootstrap

        public = _public_base()
        if public:
            cime.public_lan_base = lambda: public
            # Re-assert the same exact URI after importing server_v5 so local
            # defaults or an old client_secret.json cannot overwrite production OAuth.
            os.environ["GOOGLE_REDIRECT_URI"] = public + "/oauth/google/callback"

        if not _BOOT_DONE:
            bootstrap()
            _BOOT_DONE = True

        # Copy the gateway implementation onto the lightweight Vercel handler.
        # This keeps /api/health independent from heavyweight imports while
        # preserving the original Gateway request handling for every other route.
        for name in (
            "send_json", "send_html", "body", "public", "do_OPTIONS",
            "do_GET", "do_POST"
        ):
            setattr(handler, name, getattr(Gateway, name))
        handler.send_file = getattr(Gateway, "send_file", None)
        handler.protocol_version = getattr(Gateway, "protocol_version", "HTTP/1.1")
        handler.server_version = getattr(Gateway, "server_version", "CimeDosMundos/5.0")
        _GATEWAY = Gateway
        return _GATEWAY
    except Exception as exc:
        _IMPORT_ERROR = exc
        raise


class handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CimeDosMundos/5.0"

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
            _load_gateway()
            return getattr(self, method)( )
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

    def do_OPTIONS(self):
        return self._delegate("do_OPTIONS")
