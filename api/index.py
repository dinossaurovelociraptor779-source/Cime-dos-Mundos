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

bootstrap()

# Keep an explicit subclass named "handler": this is the Python entry-point
# format documented by Vercel for BaseHTTPRequestHandler functions.
class handler(Gateway):
    pass
