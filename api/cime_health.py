import os,sys,json,traceback
from pathlib import Path
from http.server import BaseHTTPRequestHandler

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

os.environ.setdefault("CIME_DATA_DIR","/tmp/CimeDados")
os.environ.setdefault("CIME_PORT","80")
os.environ.setdefault("CIME_BIND","0.0.0.0")
os.environ["CIME_DEPLOYMENT"]="vercel"
os.environ["CIME_PUBLIC_URL"]="https://cime-dos-mundos.vercel.app"
os.environ["GOOGLE_REDIRECT_URI"]=os.environ["CIME_PUBLIC_URL"]+"/oauth/google/callback"

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status=200
        payload={
            "ok":True,
            "version":"5.0",
            "service":"Cime dos Mundos",
            "deployment":"vercel",
            "online":True,
            "public_url":"https://cime-dos-mundos.vercel.app",
            "backend":"unknown"
        }
        try:
            import server_v5
            payload["backend"]="loaded"
            payload["google_configured"]=bool(server_v5.google_ok())
            payload["backend_port"]=int(server_v5.PORT)
        except Exception as exc:
            status=500
            payload["ok"]=False
            payload["online"]=False
            payload["backend"]="import_error"
            payload["error"]="Backend Python falhou ao inicializar."
            payload["detail"]=str(exc)[:1200]
            payload["trace"]=traceback.format_exc()[-2500:]
        raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
