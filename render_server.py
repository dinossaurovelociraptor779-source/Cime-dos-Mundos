"""HTTP entry point for the Render deployment of Cime dos Mundos 5.0."""
from __future__ import annotations

import os
from http.server import ThreadingHTTPServer

os.environ.setdefault("CIME_DEPLOYMENT", "render")
os.environ.setdefault("CIME_PERSISTENT_STORE", "1")
os.environ.setdefault("CIME_DATA_DIR", "/tmp/CimeDados")
os.environ.setdefault("CIME_BIND", "0.0.0.0")
os.environ["CIME_PORT"] = str(os.getenv("PORT") or os.getenv("CIME_PORT") or "10000")

public_url = str(os.getenv("RENDER_EXTERNAL_URL") or os.getenv("CIME_PUBLIC_URL") or "").strip().rstrip("/")
if public_url:
    os.environ["CIME_PUBLIC_URL"] = public_url
    os.environ["GOOGLE_REDIRECT_URI"] = public_url + "/oauth/google/callback"

from api.cime import handler  # noqa: E402


if __name__ == "__main__":
    address = os.getenv("CIME_BIND", "0.0.0.0")
    port = int(os.environ["CIME_PORT"])
    server = ThreadingHTTPServer((address, port), handler)
    server.daemon_threads = True
    print(f"Cime dos Mundos 5.0 ativo na porta {port}", flush=True)
    server.serve_forever()
