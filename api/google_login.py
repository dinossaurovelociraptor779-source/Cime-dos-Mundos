import os,sys,json
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

os.environ.setdefault("CIME_DATA_DIR","/tmp/CimeDados")
os.environ.setdefault("CIME_PORT","80")
os.environ.setdefault("CIME_BIND","0.0.0.0")
os.environ["CIME_DEPLOYMENT"]="vercel"
os.environ["CIME_PUBLIC_URL"]="https://cime-dos-mundos.vercel.app"
os.environ["GOOGLE_REDIRECT_URI"]=os.environ["CIME_PUBLIC_URL"]+"/oauth/google/callback"

_gateway=None
_boot=False

def load_gateway():
    global _gateway,_boot
    if _gateway is not None:
        return _gateway
    import server_v5 as cime
    from server_v5 import Gateway,bootstrap
    cime.public_lan_base=lambda: os.environ["CIME_PUBLIC_URL"]
    os.environ["GOOGLE_REDIRECT_URI"]=os.environ["CIME_PUBLIC_URL"]+"/oauth/google/callback"
    if not _boot:
        bootstrap()
        _boot=True
    _gateway=Gateway
    return _gateway

class handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    server_version="CimeDosMundos/5.0"

    def _run(self,method):
        try:
            cls=load_gateway()
            for name in ("send_json","send_html","body","public","do_OPTIONS","do_GET","do_POST"):
                setattr(handler,name,getattr(cls,name))
            handler.send_file=getattr(cls,"send_file",None)
            handler.protocol_version=getattr(cls,"protocol_version","HTTP/1.1")
            handler.server_version=getattr(cls,"server_version","CimeDosMundos/5.0")
            # Always invoke the one known Google credential route inside the gateway.
            self.path="/api/auth/google/credential"
            return getattr(self,method)()
        except Exception as exc:
            raw=json.dumps({
                "ok":False,
                "error":"Backend Python não conseguiu inicializar.",
                "detail":str(exc)[:1000]
            },ensure_ascii=False,separators=(",",":")).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def do_POST(self):
        return self._run("do_POST")

    def do_OPTIONS(self):
        return self._run("do_OPTIONS")
