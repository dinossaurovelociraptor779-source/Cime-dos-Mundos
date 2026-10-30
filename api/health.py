import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Liveness only: this function can answer even while the database is down.
        # Use /api/persistence/health for a real PostgreSQL connectivity check.
        payload = {
            "ok": True,
            "online": True,
            "health_type": "liveness",
            "version": "5.0",
            "service": "Cime dos Mundos",
            "deployment": "vercel",
            "database_checked": False,
            "database_probe": "/api/persistence/health",
            "public_url": "https://cime-dos-mundos.vercel.app",
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
