import os, json, base64, hashlib, hmac
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SESSION_DAYS=30
SECRET=os.getenv("CIME_SESSION_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "CimeDosMundos5_0_session_fallback_9c7f2d4a8e31b6f0"

def stateless_user(raw):
    try:
        parts=str(raw or "").split(".")
        if len(parts)!=3 or parts[0]!="c5":
            return None
        enc,sig=parts[1],parts[2]
        expected=hmac.new(SECRET.encode(),enc.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig,expected):
            return None
        body=base64.urlsafe_b64decode(enc+"="*((4-len(enc)%4)%4))
        payload=json.loads(body.decode("utf-8"))
        if int(payload.get("exp",0)) <= int(datetime.now(timezone.utc).timestamp()):
            return None
        return payload
    except Exception:
        return None

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        token=str(parse_qs(urlparse(self.path).query).get("token",[""])[0] or "").strip()
        if not stateless_user(token):
            raw=b'{"error":"Sessao Google invalida ou expirada.","code":"AUTH_HANDOFF_INVALID"}'
            self.send_response(401)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(302)
        self.send_header("Set-Cookie",f"cime5_session={token}; Path=/; Max-Age={SESSION_DAYS*24*60*60}; HttpOnly; Secure; SameSite=Lax")
        self.send_header("Location","/app?token="+token)
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length","0")
        self.end_headers()
