import os,sys,json
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs,urlparse,urlencode

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
    cime.public_lan_base=lambda:"https://cime-dos-mundos.vercel.app"
    os.environ["GOOGLE_REDIRECT_URI"]="https://cime-dos-mundos.vercel.app/oauth/google/callback"
    if not _boot:
        bootstrap()
        _boot=True
    _gateway=Gateway
    return _gateway

class handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    server_version="CimeDosMundos/5.0"

    def _restore_route(self):
        try:
            q=parse_qs(urlparse(self.path).query,keep_blank_values=True)
            original=str((q.get("__cime_path") or [""])[0] or "").strip()
            if original:
                clean=[]
                for key,vals in q.items():
                    if key=="__cime_path":
                        continue
                    for value in vals:
                        clean.append((key,value))
                self.path=original.rstrip("/") or "/"
                if clean:
                    self.path+="?"+urlencode(clean,doseq=True)
            # Native Vercel routing sends /api/cime/... directly to this file.
            # Gateway expects the public API path /api/....
            parsed=urlparse(self.path)
            # Google login has a dedicated public alias. If Vercel ever sends
            # that request through the generic gateway, normalize it before
            # delegating to server_v5 so it can never end in "Rota não encontrada".
            if parsed.path in ("/api/google_login","/api/google-login"):
                self.path="/api/auth/google/credential"
                if parsed.query:
                    self.path+="?"+parsed.query
                return
            if parsed.path=="/api/cime" or parsed.path.startswith("/api/cime/"):
                suffix=parsed.path[len("/api/cime"):] or "/"
                self.path="/api"+suffix
                if parsed.query:
                    self.path+="?"+parsed.query
        except Exception:
            pass

    def _run(self,method):
        self._restore_route()
        try:
            cls=load_gateway()
            for name in ("send_json","send_html","body","public","do_OPTIONS","do_GET","do_POST"):
                setattr(handler,name,getattr(cls,name))
            handler.send_file=getattr(cls,"send_file",None)
            handler.protocol_version=getattr(cls,"protocol_version","HTTP/1.1")
            handler.server_version=getattr(cls,"server_version","CimeDosMundos/5.0")
            return getattr(self,method)()
        except Exception as exc:
            raw=json.dumps({
                "ok":False,
                "error":"Backend Python não conseguiu inicializar.",
                "detail":str(exc)[:800]
            },ensure_ascii=False,separators=(",",":")).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def do_GET(self):
        return self._run("do_GET")

    def do_POST(self):
        return self._run("do_POST")

    def do_OPTIONS(self):
        return self._run("do_OPTIONS")
