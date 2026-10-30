import os,sys,json,hmac,hashlib
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs,urlparse

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

os.environ.setdefault("CIME_DATA_DIR", "/tmp/CimeDados")
os.environ.setdefault("CIME_PORT", "80")
os.environ.setdefault("CIME_BIND", "0.0.0.0")
os.environ["CIME_DEPLOYMENT"] = "vercel"

CLIENT_ID="826707020754-58ogodcs5h007tt3nqqlhig5k9tlgsvb.apps.googleusercontent.com"
REDIRECT_URI="https://cime-dos-mundos.vercel.app/oauth/google/callback"
from cime_security import SESSION_SECRET as SECRET

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

def signed_handoff(code):
    payload=str(code or "")
    sig=hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()
    return payload+"."+sig

def verify_handoff(value):
    try:
        raw=str(value or "")
        if "." not in raw:return ""
        code,sig=raw.rsplit(".",1)
        expected=hmac.new(SECRET.encode(),code.encode(),hashlib.sha256).hexdigest()
        return code if hmac.compare_digest(sig,expected) else ""
    except Exception:
        return ""

def finish_login(handler,code,state,as_json=False):
    code=str(code or "").strip()
    state=str(state or "").strip()
    legacy_mode = str(getattr(handler, "_legacy_google_mode", "") or "") == "1"
    if not code:
        code=verify_handoff(cookie_value(handler,"cime_google_handoff"))
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
        handler.send_header("Set-Cookie",f"cime5_session={token}; Path=/; Max-Age={SESSION_DAYS*24*60*60}; HttpOnly; Secure; SameSite=Lax")
        handler.send_header("Set-Cookie","cime_google_state=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
        handler.send_header("Set-Cookie","cime_google_handoff=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
        handler.send_header("Content-Length",str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
        return

    handler.send_response(302)
    server_v5.set_session_cookie(handler,token)
    handler.send_header("Location","/app")
    handler.send_header("Set-Cookie","cime_google_state=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
    handler.send_header("Set-Cookie","cime_google_handoff=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
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

    def _run_persistent(self,action):
        from cime_persistent_store import request_context, PersistentStoreError
        try:
            with request_context(write=True) as store:
                if store is not None:
                    store.attach_response(self)
                import server_v5
                server_v5.bootstrap()
                try:
                    action()
                except Exception as exc:
                    self._reply(400,{"ok":False,"error":str(exc)[:500]})
        except PersistentStoreError as exc:
            self._reply(503,{
                "ok":False,
                "error":"O armazenamento permanente do Cime está indisponível. O login não foi concluído.",
                "code":getattr(exc,"code","CIME_PERSISTENCE_UNAVAILABLE")
            })
        except Exception:
            self._reply(500,{"ok":False,"error":"Backend Google do Cime não conseguiu inicializar."})

    def do_GET(self):
        q=parse_qs(urlparse(self.path).query,keep_blank_values=True)
        self._legacy_google_mode=str(q.get("legacy",[""])[0] or "")
        self._run_persistent(lambda: finish_login(
            self,
            q.get("code",[""])[0],
            q.get("state",[""])[0],
            as_json=self._legacy_google_mode=="1"
        ))

    def do_POST(self):
        try:
            n=int(self.headers.get("Content-Length","0") or 0)
            body=self.rfile.read(n)
            d=json.loads(body.decode("utf-8") or "{}")
            code=str(d.get("code") or "").strip()
            state=str(d.get("state") or "").strip()
            if code and state:
                self.send_response(303)
                self.send_header("Location","/oauth/google/callback?"+__import__("urllib.parse").parse.urlencode({"state":state,"legacy":"1"}))
                self.send_header("Set-Cookie","cime_google_handoff="+signed_handoff(code)+"; Path=/; Max-Age=120; HttpOnly; Secure; SameSite=Lax")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length","0")
                self.end_headers()
                return
            self._run_persistent(lambda: finish_login(self,code,state,as_json=True))
        except Exception as exc:
            self._reply(400,{"ok":False,"error":str(exc)[:500]})
