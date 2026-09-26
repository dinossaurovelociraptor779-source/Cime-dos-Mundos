import os,sys,json,hmac,hashlib
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs,urlparse

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

CLIENT_ID="826707020754-58ogodcs5h007tt3nqqlhig5k9tlgsvb.apps.googleusercontent.com"
REDIRECT_URI="https://cime-dos-mundos.vercel.app/oauth/google/callback"
SECRET=os.getenv("CIME_SESSION_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "CimeDosMundos5_0_oauth_state_fallback"

def verify_state(state,cookie):
    try:
        raw=str(cookie or "")
        if "." not in raw:return False
        saved,sig=raw.split(".",1)
        expected=hmac.new(SECRET.encode(),saved.encode(),hashlib.sha256).hexdigest()
        return hmac.compare_digest(saved,state) and hmac.compare_digest(sig,expected)
    except Exception:
        return False

def cookie_value(handler,name):
    for part in handler.headers.get("Cookie","").split(";"):
        part=part.strip()
        if part.startswith(name+"="):
            return part.split("=",1)[1]
    return ""

def finish_login(handler,code,state,as_json=False):
    code=str(code or "").strip()
    state=str(state or "").strip()
    if not code or not state:
        raise ValueError("Código Google ausente.")
    if not verify_state(state,cookie_value(handler,"cime_google_state")):
        raise ValueError("Validação de segurança do Google expirou. Tente novamente.")

    client_secret=os.getenv("GOOGLE_CLIENT_SECRET","").strip()
    if not client_secret:
        raise ValueError("GOOGLE_CLIENT_SECRET não está configurado na Vercel.")

    import urllib.parse
    data=urllib.parse.urlencode({
        "code":code,
        "client_id":CLIENT_ID,
        "client_secret":client_secret,
        "redirect_uri":REDIRECT_URI,
        "grant_type":"authorization_code"
    }).encode()
    req=Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={
            "Content-Type":"application/x-www-form-urlencoded",
            "User-Agent":"CimeDosMundos/5.0"
        }
    )
    with urlopen(req,timeout=15) as resp:
        tok=json.loads(resp.read().decode())

    id_token=str(tok.get("id_token") or "").strip()
    if not id_token:
        raise ValueError("O Google não devolveu id_token.")

    os.environ["CIME_DEPLOYMENT"]="vercel"
    os.environ["CIME_PUBLIC_URL"]="https://cime-dos-mundos.vercel.app"
    os.environ["GOOGLE_CLIENT_ID"]=CLIENT_ID
    os.environ["GOOGLE_REDIRECT_URI"]=REDIRECT_URI

    import server_v5
    claims=server_v5.verify_google_credential(id_token)
    email=server_v5.normalize_email(claims.get("email"))
    sub=str(claims.get("sub",""))
    name=str(claims.get("name") or email.split("@")[0])
    now=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

    c=server_v5.auth_db()
    u=c.execute(
        'SELECT * FROM users WHERE provider="google" AND provider_subject=?',
        (sub,)
    ).fetchone()
    if not u:
        u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()

    if not u:
        role="creator" if c.execute("SELECT 1 FROM users").fetchone() is None else "beta"
        cur=c.execute(
            'INSERT INTO users(email,password_hash,display_name,role,provider,provider_subject,created_at,updated_at,last_login_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (email,None,name,role,"google",sub,now,now,now)
        )
        uid=cur.lastrowid
    else:
        uid=u["id"]
        role=u["role"]
        c.execute(
            'UPDATE users SET provider="google",provider_subject=?,display_name=?,last_login_at=?,updated_at=? WHERE id=?',
            (sub,name,now,now,uid)
        )
    c.commit()
    c.close()

    server_v5.ensure_library(uid)
    token=server_v5.token_make(uid,handler.headers.get("User-Agent",""))

    payload={
        "ok":True,
        "token":token,
        "user":{
            "id":uid,
            "email":email,
            "display_name":name,
            "role":role,
            "provider":"google"
        }
    }

    if as_json:
        raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type","application/json; charset=utf-8")
        handler.send_header("Cache-Control","no-store")
        handler.send_header("Set-Cookie","cime_google_state=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
        handler.send_header("Content-Length",str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
        return

    handler.send_response(302)
    handler.send_header("Location","/app?token="+urllib.parse.quote(token,safe=""))
    handler.send_header("Set-Cookie","cime_google_state=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
    handler.send_header("Cache-Control","no-store")
    handler.send_header("Content-Length","0")
    handler.end_headers()

class handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"

    def _reply(self,status,payload):
        raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            q=parse_qs(urlparse(self.path).query,keep_blank_values=True)
            finish_login(
                self,
                q.get("code",[""])[0],
                q.get("state",[""])[0],
                as_json=False
            )
        except Exception as exc:
            self._reply(400,{"ok":False,"error":str(exc)[:500]})

    def do_POST(self):
        try:
            n=int(self.headers.get("Content-Length","0") or 0)
            body=self.rfile.read(n)
            d=json.loads(body.decode("utf-8") or "{}")
            finish_login(
                self,
                d.get("code"),
                d.get("state"),
                as_json=True
            )
        except Exception as exc:
            self._reply(400,{"ok":False,"error":str(exc)[:500]})
