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

from server_v5 import Gateway, bootstrap

bootstrap()

# Keep an explicit subclass named "handler": this is the Python entry-point
# format documented by Vercel for BaseHTTPRequestHandler functions.
class handler(Gateway):
    pass
