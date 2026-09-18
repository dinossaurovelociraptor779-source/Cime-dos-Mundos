import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Vercel functions have a read-only source tree; runtime data goes to /tmp.
os.environ.setdefault("CIME_DATA_DIR", "/tmp/CimeDados")
os.environ.setdefault("CIME_PORT", "80")
os.environ.setdefault("CIME_BIND", "0.0.0.0")
os.environ.setdefault("CIME_DEPLOYMENT", "vercel")

import threading

import server_v5 as cime
from server_v5 import Gateway, bootstrap

# Vercel exposes these hostnames automatically. Keep public links online even
# though the legacy helper was originally designed for local LAN access.
def _public_base():
    explicit = str(os.getenv('CIME_PUBLIC_URL','')).strip().rstrip('/')
    if explicit:
        return explicit
    for key in ('VERCEL_PROJECT_PRODUCTION_URL','VERCEL_URL'):
        host = str(os.getenv(key,'')).strip().rstrip('/')
        if host:
            return host if host.startswith(('http://','https://')) else 'https://' + host
    return ''

_public = _public_base()
if _public:
    cime.public_lan_base = lambda: _public
    os.environ['GOOGLE_REDIRECT_URI'] = _public + '/oauth/google/callback'

_BOOT_LOCK = threading.Lock()
_BOOT_DONE = False

def _ensure_bootstrap():
    global _BOOT_DONE
    if _BOOT_DONE:
        return
    with _BOOT_LOCK:
        if not _BOOT_DONE:
            bootstrap()
            _BOOT_DONE = True

# Keep an explicit subclass named "handler": this is the Python entry-point
# format documented by Vercel for BaseHTTPRequestHandler functions.
class handler(Gateway):
    def _needs_bootstrap(self):
        path = self.path.split('?',1)[0].rstrip('/') or '/'
        return path not in ('/api/health', '/health')

    def do_GET(self):
        if self._needs_bootstrap():
            _ensure_bootstrap()
        return super().do_GET()

    def do_POST(self):
        _ensure_bootstrap()
        return super().do_POST()

    def do_PUT(self):
        _ensure_bootstrap()
        return super().do_PUT()

    def do_PATCH(self):
        _ensure_bootstrap()
        return super().do_PATCH()

    def do_DELETE(self):
        _ensure_bootstrap()
        return super().do_DELETE()
