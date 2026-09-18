import os, re, json, base64, hashlib, hmac, secrets, sqlite3, shutil, sys
from datetime import datetime, timedelta, timezone
import time
import socket
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import Request, urlopen
from pathlib import Path

BASE=Path(__file__).resolve().parent

def load_dotenv_file():
    env_file = BASE/'.env'
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding='utf-8', errors='ignore').splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k,v=line.split('=',1)
        k=k.strip(); v=v.strip().strip('\"').strip("'")
        if k and k not in os.environ:
            os.environ[k]=v

load_dotenv_file()
DATA=Path(os.getenv('CIME_DATA_DIR', str(BASE/'CimeDados')))
DATA.mkdir(parents=True, exist_ok=True)
PORT=int(os.getenv('CIME_PORT','8790'))
BIND=os.getenv('CIME_BIND','0.0.0.0')
AUTH_DB=DATA/'cime_auth.db'
USERS_DIR=DATA/'users_data'; USERS_MEDIA=DATA/'users_media'
SESSION_DAYS=30
MAX_BODY=16*1024*1024
MAX_PHOTO_BYTES=12*1024*1024
GROUP_INVITE_DAYS=7
GROUPS_MEDIA=DATA/'groups_media'
_tls=__import__('threading').local()
_DB_WAL_READY=set()
_DB_PRAGMA_LOCK=__import__('threading').RLock()
_BOOTSTRAP_CACHE={}
_BOOTSTRAP_CACHE_LOCK=__import__('threading').RLock()
_BOOTSTRAP_TTL=4.0

def lan_ips():
    """Return private LAN IPv4 addresses, preferring the address used by the default route."""
    found=[]
    route_ip=None
    try:
        infos=socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET)
        found.extend(x[4][0] for x in infos if x and x[4] and x[4][0])
    except Exception:
        pass
    try:
        sock=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.settimeout(0.5)
        try:
            sock.connect(('8.8.8.8',80)); route_ip=sock.getsockname()[0]
            if route_ip: found.insert(0,route_ip)
        finally: sock.close()
    except Exception:
        pass
    out=[]
    for ip in found:
        if not ip or ip.startswith('127.') or ip.startswith('169.254.') or ip in out:
            continue
        try:
            a,b,*_=map(int,ip.split('.'))
            private=(a==10) or (a==172 and 16<=b<=31) or (a==192 and b==168)
            if private: out.append(ip)
        except Exception:
            continue
    # The UDP route probe identifies the interface Windows is actually using to reach
    # the network/Internet. Keep that address first instead of alphabetically preferring
    # virtual 192.168.x.x adapters such as Mobile Hotspot (often 192.168.137.1).
    if route_ip in out:
        out=[route_ip]+[ip for ip in out if ip!=route_ip]
    return out or ['127.0.0.1']

def lan_ip():
    return lan_ips()[0]


def public_lan_base():
    """Return a browser-reachable LAN base URL even when the page was opened via localhost."""
    ip = lan_ip()
    return f"http://{ip}:{PORT}"

sys.path.insert(0,str(BASE))
import server_legacy as legacy

class DynamicPath:
    def __fspath__(self):
        uid=getattr(_tls,'user_id',None)
        p=(USERS_MEDIA/str(uid)) if uid else (DATA/'anonymous_media')
        p.mkdir(parents=True,exist_ok=True)
        return str(p)
    def __str__(self): return os.fspath(self)
legacy.MEDIA=DynamicPath()
legacy.PORT=PORT


def _fast_db_setup(c, path):
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA synchronous=NORMAL')
    c.execute('PRAGMA temp_store=MEMORY')
    c.execute('PRAGMA cache_size=-20000')
    c.execute('PRAGMA mmap_size=134217728')
    key=str(path)
    if key not in _DB_WAL_READY:
        with _DB_PRAGMA_LOCK:
            if key not in _DB_WAL_READY:
                c.execute('PRAGMA journal_mode=WAL')
                _DB_WAL_READY.add(key)

def auth_db():
    c=sqlite3.connect(AUTH_DB,timeout=15); _fast_db_setup(c,AUTH_DB); return c

def user_db_path(uid):
    p=USERS_DIR/str(uid); p.mkdir(parents=True,exist_ok=True); return p/'library.db'

def current_uid(): return getattr(_tls,'user_id',None)

def user_db():
    uid=current_uid()
    if not uid:
        c=sqlite3.connect(legacy.DB,timeout=15); _fast_db_setup(c,legacy.DB); return c
    path=user_db_path(uid); c=sqlite3.connect(path,timeout=15); _fast_db_setup(c,path); return c
legacy.db=user_db

def pw_hash(password):
    salt=secrets.token_bytes(16); dk=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,320000); return base64.b64encode(salt+dk).decode()
def pw_ok(password,encoded):
    try:
        raw=base64.b64decode(encoded); salt,exp=raw[:16],raw[16:]; got=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,320000); return hmac.compare_digest(got,exp)
    except Exception: return False

def init_auth():
    c=auth_db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT UNIQUE NOT NULL,password_hash TEXT,display_name TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'beta',provider TEXT NOT NULL DEFAULT 'local',provider_subject TEXT DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_login_at TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,user_agent TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,kind TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,page TEXT DEFAULT '',created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS oauth_states(state TEXT PRIMARY KEY,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,description TEXT DEFAULT '',owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,group_type TEXT NOT NULL DEFAULT 'both');
    CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,role TEXT NOT NULL DEFAULT 'member',status TEXT NOT NULL DEFAULT 'active',joined_at TEXT NOT NULL,PRIMARY KEY(group_id,user_id));
    CREATE TABLE IF NOT EXISTS group_invites(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,inviter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,email TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,expires_at TEXT NOT NULL,accepted_by INTEGER REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS group_recommendations(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,title_name TEXT NOT NULL,recommendation TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS group_photos(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,filename TEXT NOT NULL,mime TEXT NOT NULL,path TEXT NOT NULL,caption TEXT DEFAULT '',taken_at TEXT DEFAULT '',location TEXT DEFAULT '',created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS group_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,kind TEXT NOT NULL DEFAULT 'text',body TEXT DEFAULT '',photo_id INTEGER REFERENCES group_photos(id) ON DELETE SET NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS group_share_links(id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,inviter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,token_hash TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active');
    CREATE INDEX IF NOT EXISTS idx_group_invites_email_status ON group_invites(email,status);
    ''')
    try: c.execute("ALTER TABLE groups ADD COLUMN group_type TEXT NOT NULL DEFAULT 'both'")
    except sqlite3.OperationalError: pass
    c.execute('CREATE INDEX IF NOT EXISTS idx_group_share_links_token ON group_share_links(token_hash,status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_groups_owner_updated ON groups(owner_id,updated_at DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_group_members_user_status ON group_members(user_id,status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_group_recommendations_group_created ON group_recommendations(group_id,created_at DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_group_photos_group_created ON group_photos(group_id,created_at DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_group_messages_group_id ON group_messages(group_id,id DESC)')
    c.commit(); c.close(); USERS_DIR.mkdir(exist_ok=True); USERS_MEDIA.mkdir(exist_ok=True); GROUPS_MEDIA.mkdir(exist_ok=True)

def normalize_email(v): return str(v or '').strip().lower()
def valid_email(v): return bool(re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', v))
def group_member(uid,gid):
    c=auth_db(); r=c.execute('SELECT gm.*,g.name,g.owner_id FROM group_members gm JOIN groups g ON g.id=gm.group_id WHERE gm.group_id=? AND gm.user_id=? AND gm.status="active"',(gid,uid)).fetchone(); c.close(); return dict(r) if r else None

def group_message_rows(gid, after_id=0, limit=80):
    c=auth_db()
    rows=c.execute("""SELECT m.id,m.group_id,m.user_id,m.kind,m.body,m.photo_id,m.created_at,u.display_name,u.email,
                             p.filename,p.mime,p.caption AS photo_caption,p.taken_at,p.location
                      FROM group_messages m
                      JOIN users u ON u.id=m.user_id
                      LEFT JOIN group_photos p ON p.id=m.photo_id
                      WHERE m.group_id=? AND m.id>?
                      ORDER BY m.id ASC LIMIT ?""",(gid,int(after_id),int(limit))).fetchall()
    c.close()
    return [dict(r) for r in rows]

def safe_photo_bytes(data):
    raw=base64.b64decode(data,validate=True)
    if len(raw)>MAX_PHOTO_BYTES: raise ValueError('A foto excede 12 MB.')
    if raw.startswith(bytes.fromhex('ffd8ff')): return raw,'image/jpeg'
    if raw.startswith(bytes.fromhex('89504e470d0a1a0a')): return raw,'image/png'
    if raw[:6] in (b'GIF87a',b'GIF89a'): return raw,'image/gif'
    if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP': return raw,'image/webp'
    raise ValueError('Formato nao suportado. Use JPG, PNG, GIF ou WEBP.')

def maybe_send_invite_email(email,group_name,inviter_name,link):
    host=os.getenv('SMTP_HOST','').strip()
    if not host: return False,'smtp_not_configured'
    import smtplib
    from email.message import EmailMessage
    msg=EmailMessage()
    msg['Subject']=f'Convite para o grupo {group_name} - Cime dos Mundos'
    msg['From']=os.getenv('SMTP_FROM',os.getenv('SMTP_USER',''))
    msg['To']=email
    msg.set_content(f'{inviter_name} convidou voce para o grupo "{group_name}".\n\nAceite: {link}\n\nExpira em {GROUP_INVITE_DAYS} dias.')
    with smtplib.SMTP(host,int(os.getenv('SMTP_PORT','587')),timeout=15) as smtp:
        if os.getenv('SMTP_TLS','1')!='0': smtp.starttls()
        if os.getenv('SMTP_USER'): smtp.login(os.getenv('SMTP_USER'),os.getenv('SMTP_PASSWORD',''))
        smtp.send_message(msg)
    return True,'sent'

def ensure_library(uid):
    _tls.user_id=uid
    try:
        legacy.init_db()
        # Migrate legacy cime.db once into first creator library, preserving data.
        target=user_db_path(uid); old=DATA/'cime.db'
        c=sqlite3.connect(target); n=c.execute('SELECT COUNT(*) FROM titles').fetchone()[0] if target.exists() else 0; c.close()
        if uid==1 and (DATA/'media').exists():
            dest=USERS_MEDIA/'1'; dest.mkdir(parents=True,exist_ok=True)
            for f in (DATA/'media').iterdir():
                if f.is_file() and not (dest/f.name).exists(): shutil.copy2(f,dest/f.name)
        if n==0 and old.exists() and uid==1:
            # legacy already initialized target; attach and copy matching columns by full row.
            legacy_conn=sqlite3.connect(old); legacy_conn.row_factory=sqlite3.Row
            rows=legacy_conn.execute('SELECT * FROM titles').fetchall()
            if rows:
                tgt=legacy.db(); cols=[r[1] for r in tgt.execute('PRAGMA table_info(titles)').fetchall()];
                for r in rows:
                    keys=[k for k in r.keys() if k in cols and k!='id'];
                    if not keys: continue
                    qs=','.join('?' for _ in keys); tgt.execute(f"INSERT INTO titles ({','.join(keys)}) VALUES ({qs})",[r[k] for k in keys])
                tgt.commit(); tgt.close()
            legacy_conn.close()
    finally: delattr(_tls,'user_id') if hasattr(_tls,'user_id') else None

def token_make(uid, ua=''):
    raw=secrets.token_urlsafe(48); h=hashlib.sha256(raw.encode()).hexdigest(); now=datetime.now(timezone.utc).replace(tzinfo=None); exp=now+timedelta(days=SESSION_DAYS); c=auth_db(); c.execute('INSERT INTO sessions(token_hash,user_id,created_at,expires_at,user_agent) VALUES(?,?,?,?,?)',(h,uid,now.isoformat(timespec='seconds'),exp.isoformat(timespec='seconds'),ua[:500])); c.commit(); c.close(); return raw

def _cookie_token(handler):
    raw_cookie=handler.headers.get('Cookie','')
    for part in raw_cookie.split(';'):
        if part.strip().startswith('cime5_session='):
            return part.split('=',1)[1].strip()
    return ''

def set_session_cookie(handler, token, max_age=SESSION_DAYS*24*60*60):
    handler.send_header('Set-Cookie', f'cime5_session={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax')

def clear_session_cookie(handler):
    handler.send_header('Set-Cookie', 'cime5_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')

def get_user(handler):
    h=handler.headers.get('Authorization',''); raw=h[7:].strip() if h.startswith('Bearer ') else ''
    if not raw:
        raw=_cookie_token(handler)
    if not raw: return None
    h=hashlib.sha256(raw.encode()).hexdigest(); c=auth_db(); row=c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',(h,datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'))).fetchone();
    if row:
        now=datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            exp=datetime.fromisoformat(str(row['expires_at']))
        except Exception:
            exp=now
        if (exp-now).total_seconds() < 3*86400:
            c.execute('UPDATE sessions SET expires_at=? WHERE token_hash=?',((now+timedelta(days=SESSION_DAYS)).isoformat(timespec='seconds'),h)); c.commit()
    c.close(); return dict(row) if row else None

def require(handler):
    u=get_user(handler)
    if not u: handler.send_json({'error':'Faça login para continuar.','code':'AUTH_REQUIRED'},401); return None
    _tls.user_id=u['id']; return u

# Google Identity Services uses a public OAuth Client ID in the browser.
# Keep the legacy code-flow support optional, but do not require a client secret
# for the normal Sign in with Google credential flow used by the 5.0 beta.
GOOGLE_DEFAULT_CLIENT_ID='826707020754-58ogodcs5h007tt3nqqlhig5k9tlgsvb.apps.googleusercontent.com'

def _load_google_client_file():
    candidates=[BASE/'client_secret.json', BASE/'google_client_secret.json']
    candidates += sorted(BASE.glob('client_secret_*.json'))
    candidates += [BASE/'google_config.json']
    for fp in candidates:
        if not fp.exists(): continue
        try:
            data=json.loads(fp.read_text(encoding='utf-8')); cfg=data.get('web') or data.get('installed') or data
            cid=cfg.get('client_id') or cfg.get('clientId'); sec=cfg.get('client_secret') or cfg.get('clientSecret'); reds=cfg.get('redirect_uris') or cfg.get('redirectUris') or []
            if cid: os.environ.setdefault('GOOGLE_CLIENT_ID',str(cid))
            if sec: os.environ.setdefault('GOOGLE_CLIENT_SECRET',str(sec))
            if reds and not os.getenv('GOOGLE_REDIRECT_URI'): os.environ['GOOGLE_REDIRECT_URI']=str(reds[0])
            break
        except Exception: pass

if not os.getenv('GOOGLE_CLIENT_ID'):
    os.environ['GOOGLE_CLIENT_ID']=GOOGLE_DEFAULT_CLIENT_ID
if not os.getenv('GOOGLE_REDIRECT_URI'):
    os.environ['GOOGLE_REDIRECT_URI']=f'http://localhost:{PORT}/oauth/google/callback'
_load_google_client_file()
def google_ok():
    cid=str(os.getenv('GOOGLE_CLIENT_ID','')).strip()
    return bool(cid and cid.endswith('.apps.googleusercontent.com'))

_GOOGLE_CERT_CACHE={'exp':0.0,'keys':{}}
def _google_public_keys():
    now=time.time()
    if _GOOGLE_CERT_CACHE['keys'] and _GOOGLE_CERT_CACHE['exp']>now: return _GOOGLE_CERT_CACHE['keys']
    req=Request('https://www.googleapis.com/oauth2/v3/certs',headers={'User-Agent':'CimeDosMundos/5.0'})
    with urlopen(req,timeout=8) as r: data=json.loads(r.read().decode('utf-8'))
    keys={}
    for k in data.get('keys',[]):
        if k.get('kid') and k.get('kty')=='RSA' and k.get('n') and k.get('e'): keys[k['kid']]=k
    _GOOGLE_CERT_CACHE['keys']=keys; _GOOGLE_CERT_CACHE['exp']=now+3600
    return keys

def verify_google_credential(credential):
    """Validate a Google Identity Services ID token robustly.

    Prefer local JWT verification for speed, with Google's tokeninfo endpoint as
    a fallback when keys rotate or a local JWT library/claim edge case occurs.
    Both paths enforce the configured audience and a verified e-mail.
    """
    if not credential: raise ValueError('Credencial Google ausente.')
    local_error = None
    try:
        import jwt
        header=jwt.get_unverified_header(credential); kid=header.get('kid')
        jwk=_google_public_keys().get(kid)
        if not jwk: raise ValueError('Chave de assinatura do Google não encontrada.')
        from jwt.algorithms import RSAAlgorithm
        public_key=RSAAlgorithm.from_jwk(json.dumps(jwk))
        claims=jwt.decode(
            credential, public_key, algorithms=['RS256'],
            audience=os.getenv('GOOGLE_CLIENT_ID'),
            issuer='https://accounts.google.com'
        )
        if not claims.get('email'): raise ValueError('A conta Google não forneceu e-mail.')
        if str(claims.get('email_verified')).lower() != 'true':
            raise ValueError('O Google não confirmou o e-mail desta conta.')
        return claims
    except Exception as e:
        local_error = e

    # Reliable fallback for local development / key-rotation edge cases.
    try:
        req=Request(
            'https://oauth2.googleapis.com/tokeninfo?id_token='+urlencode({'id_token':credential})[9:],
            headers={'User-Agent':'CimeDosMundos/5.0'}
        )
        with urlopen(req,timeout=10) as r:
            claims=json.loads(r.read().decode('utf-8'))
        aud=str(claims.get('aud',''))
        if aud != str(os.getenv('GOOGLE_CLIENT_ID','')):
            raise ValueError('O Client ID do token Google não corresponde ao Cime dos Mundos.')
        if not claims.get('email'): raise ValueError('A conta Google não forneceu e-mail.')
        if str(claims.get('email_verified')).lower() != 'true':
            raise ValueError('O Google não confirmou o e-mail desta conta.')
        return claims
    except Exception as e:
        raise ValueError('Google recusou a credencial. Confira no Google Cloud se a origem autorizada inclui exatamente a URL do Cime e se o Client ID configurado pertence ao mesmo projeto.') from e

def portal_redirect(handler, path='/app'):
    handler.send_response(302); handler.send_header('Location',path); handler.send_header('Content-Length','0'); handler.send_header('Connection','keep-alive'); handler.end_headers()

class Gateway(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    server_version='CimeDosMundos/5.0'
    def log_message(self,fmt,*args): print('[%s] %s'%(datetime.now().strftime('%H:%M:%S'),fmt%args))
    def send_json(self,data,status=200):
        raw=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()
        use_gzip='gzip' in self.headers.get('Accept-Encoding','').lower() and len(raw)>=1024
        if use_gzip:
            import gzip
            raw=gzip.compress(raw,compresslevel=5)
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Vary','Accept-Encoding')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','SAMEORIGIN')
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
        if use_gzip:self.send_header('Content-Encoding','gzip')
        self.send_header('Connection','keep-alive')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def send_html(self,name):
        fp=BASE/name; raw=fp.read_bytes();
        import hashlib, gzip
        etag='"'+hashlib.sha1((str(fp.stat().st_mtime_ns)+str(fp.stat().st_size)).encode()).hexdigest()+'"'
        if self.headers.get('If-None-Match')==etag:
            self.send_response(304); self.send_header('ETag',etag); self.send_header('Cache-Control','private, max-age=30, must-revalidate'); self.send_header('Vary','Accept-Encoding'); self.send_header('Connection','keep-alive'); self.send_header('Content-Length','0'); self.end_headers(); return
        use_gzip='gzip' in self.headers.get('Accept-Encoding','').lower() and len(raw)>=1024
        if use_gzip: raw=gzip.compress(raw,compresslevel=5)
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Cache-Control','private, max-age=30, must-revalidate'); self.send_header('Vary','Accept-Encoding'); self.send_header('ETag',etag); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Connection','keep-alive');
        if use_gzip:self.send_header('Content-Encoding','gzip')
        self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def body(self):
        n=int(self.headers.get('Content-Length','0') or 0)
        if n>MAX_BODY: raise ValueError('Requisição muito grande.')
        return self.rfile.read(n)
    def public(self,path): return path in ('/','/app','/oauth/google/start','/oauth/google/callback','/api/auth/status','/api/auth/register','/api/auth/login','/api/auth/logout','/api/vision/status','/api/qr','/api/auth/google/credential')

    def do_OPTIONS(self):
        self.send_response(204); self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Access-Control-Allow-Methods','GET,POST,PUT,PATCH,DELETE,OPTIONS'); self.send_header('Access-Control-Allow-Headers','Content-Type, Authorization, X-Filename'); self.send_header('Content-Length','0'); self.end_headers()

    def do_GET(self):
        _tls.__dict__.pop('user_id',None)
        p=urlparse(self.path); path=p.path.rstrip('/') or '/'
        if path in ('/health','/api/health'):
            return self.send_json({'ok':True,'version':'5.0','service':'Cime dos Mundos','port':PORT,'bind':BIND,'lan_ip':lan_ip(),'lan_ips':lan_ips(),'lan_url':f'http://{lan_ip()}:{PORT}'})
        if path=='/api/library/enrich':
            u=require(self)
            if not u:return
            qs=parse_qs(p.query)
            limit=max(1,min(12,int(qs.get('limit',['6'])[0] or 6)))
            after_id=max(0,int(qs.get('after_id',['0'])[0] or 0))
            titles=legacy.all_titles()
            # Analisa uma vez cada registro incompleto neste passe, em ordem estável.
            # Assim a interface não fica presa repetindo os mesmos títulos que não foram encontrados.
            def incomplete(x):
                return any([
                    not x.get('cover'),
                    not x.get('title_english'),
                    not x.get('title_japanese'),
                    not x.get('synopsis'),
                    not x.get('synopsis_pt'),
                    not x.get('year'),
                    not x.get('duration'),
                    not x.get('genres'),
                    not x.get('season'),
                    not x.get('episodes') and not x.get('total_episodes'),
                    not x.get('source_id'),
                ])
            titles=[x for x in titles if int(x.get('id') or 0)>after_id and incomplete(x)]
            titles=sorted(titles,key=lambda x:int(x.get('id') or 0))[:limit]
            out=[]
            for row in titles:
                try: out.append(_safe_enrich_title(row))
                except Exception as e: out.append({'id':row.get('id'),'name':row.get('name'),'changed':False,'reason':'erro','detail':str(e)[:240]})
            next_after=int(titles[-1].get('id') or after_id) if titles else after_id
            return self.send_json({'success':True,'processed':len(out),'results':out,'after_id':after_id,'next_after_id':next_after,'finished':not bool(titles) or len(titles)<limit})
        if path=='/api/library/covers':
            u=require(self)
            if not u:return
            qs=parse_qs(p.query)
            limit=max(1,min(8,int(qs.get('limit',['4'])[0] or 4)))
            after_id=max(0,int(qs.get('after_id',['0'])[0] or 0))
            force=str(qs.get('force',['0'])[0] or '0')=='1'
            titles=legacy.all_titles()
            def needs_cover(x):
                if force: return True
                try: ss=json.loads(x.get('seasons_json') or '[]')
                except Exception: ss=[]
                missing_season=any(isinstance(e,dict) and not e.get('cover') for e in ss) if ss else False
                return not x.get('cover') or missing_season
            titles=[x for x in titles if int(x.get('id') or 0)>after_id and needs_cover(x)]
            titles=sorted(titles,key=lambda x:int(x.get('id') or 0))[:limit]
            out=[]
            for row in titles:
                try: out.append(_safe_enrich_title(row))
                except Exception as e: out.append({'id':row.get('id'),'name':row.get('name'),'changed':False,'reason':'erro','detail':str(e)[:240]})
            next_after=int(titles[-1].get('id') or after_id) if titles else after_id
            return self.send_json({'success':True,'processed':len(out),'results':out,'after_id':after_id,'next_after_id':next_after,'finished':not bool(titles) or len(titles)<limit})

        if path=='/api/qr':
            try:
                from urllib.parse import unquote
                import qrcode
                text=parse_qs(p.query).get('text',[''])[0].strip()
                if not text or len(text)>2048 or not (text.startswith('http://') or text.startswith('https://')):
                    return self.send_json({'error':'Texto/URL inválido para QR.'},400)
                img=qrcode.make(text)
                import io
                buf=io.BytesIO(); img.save(buf,format='PNG'); raw=buf.getvalue()
                self.send_response(200); self.send_header('Content-Type','image/png'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            except Exception as e:
                return self.send_json({'error':f'Não foi possível gerar o QR Code: {e}'},500)

        if path=='/api/connection-info':
            return self.send_json({'version':'5.0','port':PORT,'bind':BIND,'local_url':f'http://localhost:{PORT}','lan_ip':lan_ip(),'lan_ips':lan_ips(),'lan_url':f'http://{lan_ip()}:{PORT}','google_js_origin':f'http://localhost:{PORT}','mobile_hint':'PC e celular precisam estar na mesma rede Wi-Fi. Se o endereço não abrir, confirme a regra privada TCP 8790 no Windows e teste a URL LAN exibida pelo modo celular.'})
        if path in ('/manifest.json','/service-worker.js','/icon-192.png','/icon-512.png'):
            types={
                '/manifest.json':'application/manifest+json; charset=utf-8',
                '/service-worker.js':'application/javascript; charset=utf-8',
                '/icon-192.png':'image/png',
                '/icon-512.png':'image/png'
            }
            return self.send_file(str(BASE/path.lstrip('/')),types[path])
        if path=='/index.html':
            return self.send_file(str(BASE/'index.html'),'text/html; charset=utf-8')
        if path in ('/','/login','/cadastro','/register'):
            return self.send_html('index.html')
        if path in ('/app','/app/'):
            if not require(self): return
            return self.send_html('app.html')
        if path=='/oauth/google/start':
            if not google_ok(): return self.send_json({'error':'Google ainda não está configurado. Coloque client_secret.json ao lado do servidor 5.0 ou configure GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET e GOOGLE_REDIRECT_URI.','code':'GOOGLE_NOT_CONFIGURED'},503)
            state=secrets.token_urlsafe(24); c=auth_db(); c.execute('DELETE FROM oauth_states WHERE created_at<?',((datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(minutes=10)).isoformat(timespec='seconds'),)); c.execute('INSERT INTO oauth_states(state,created_at) VALUES(?,?)',(state,datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'))); c.commit(); c.close(); q=urlencode({'client_id':os.getenv('GOOGLE_CLIENT_ID'),'redirect_uri':os.getenv('GOOGLE_REDIRECT_URI'),'response_type':'code','scope':'openid email profile','state':state,'prompt':'select_account'}); self.send_response(302); self.send_header('Location','https://accounts.google.com/o/oauth2/v2/auth?'+q); self.end_headers(); return
        if path=='/oauth/google/callback':
            q=parse_qs(p.query); code=q.get('code',[''])[0]; state=q.get('state',[''])[0]; c=auth_db(); ok=c.execute('SELECT 1 FROM oauth_states WHERE state=?',(state,)).fetchone(); c.execute('DELETE FROM oauth_states WHERE state=?',(state,)); c.commit(); c.close()
            if not google_ok() or not code or not ok: return self.send_json({'error':'Falha no login do Google.'},400)
            data=urlencode({'code':code,'client_id':os.getenv('GOOGLE_CLIENT_ID'),'client_secret':os.getenv('GOOGLE_CLIENT_SECRET'),'redirect_uri':os.getenv('GOOGLE_REDIRECT_URI'),'grant_type':'authorization_code'}).encode(); req=Request('https://oauth2.googleapis.com/token',data=data,headers={'Content-Type':'application/x-www-form-urlencoded'});
            try:
                with urlopen(req,timeout=15) as r: tok=json.loads(r.read().decode()); req2=Request('https://openidconnect.googleapis.com/v1/userinfo',headers={'Authorization':'Bearer '+tok['access_token']});
                with urlopen(req2,timeout=15) as r: info=json.loads(r.read().decode())
            except Exception as e: return self.send_json({'error':'Não foi possível validar a conta Google.','detail':str(e)},502)
            email=str(info.get('email','')).lower().strip(); sub=str(info.get('sub','')); name=str(info.get('name') or email.split('@')[0]); now=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'); c=auth_db(); u=c.execute('SELECT * FROM users WHERE provider="google" AND provider_subject=?',(sub,)).fetchone()
            if not u: u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
            if not u:
                role='creator' if c.execute('SELECT 1 FROM users').fetchone() is None else 'beta'; cur=c.execute('INSERT INTO users(email,password_hash,display_name,role,provider,provider_subject,created_at,updated_at,last_login_at) VALUES(?,?,?,?,?,?,?,?,?)',(email,None,name,role,'google',sub,now,now,now)); uid=cur.lastrowid
            else:
                uid=u['id']; c.execute('UPDATE users SET provider="google",provider_subject=?,display_name=?,last_login_at=?,updated_at=? WHERE id=?',(sub,name,now,now,uid))
            c.commit(); c.close(); ensure_library(uid); tok=token_make(uid,self.headers.get('User-Agent','')); self.send_response(302); set_session_cookie(self,tok); self.send_header('Location','/app'); self.end_headers(); return
        if path=='/api/auth/status':
            u=get_user(self); return self.send_json({'authenticated':bool(u),'google_configured':google_ok(),'google_client_id':os.getenv('GOOGLE_CLIENT_ID',''),'google_js_origin':f'http://localhost:{PORT}','google_redirect_uri':os.getenv('GOOGLE_REDIRECT_URI',''),'lan_enabled':BIND in ('0.0.0.0','::'),'user':({'id':u['id'],'email':u['email'],'display_name':u['display_name'],'role':u['role'],'provider':u['provider']} if u else None)})
        if path=='/api/bootstrap':
            u=require(self)
            if not u: return
            uid=int(u['id'])
            now_m=time.monotonic()
            with _BOOTSTRAP_CACHE_LOCK:
                hit=_BOOTSTRAP_CACHE.get(uid)
                if hit and (now_m-hit[0]) < _BOOTSTRAP_TTL:
                    return self.send_json(hit[1])
            # One round-trip for the first library paint. This replaces three/four
            # independent startup requests and keeps the UI responsive on mobile LAN.
            titles=legacy.all_titles()
            stats=legacy.library_stats()
            rate=legacy.automatic_watch_rate()
            vision={'configured':bool(legacy.vision_configured()),'provider':'OpenAI Vision + Web Search' if legacy.vision_configured() else 'OCR local'}
            payload={'version':'5.0','user':{'id':u['id'],'email':u['email'],'display_name':u['display_name'],'role':u['role'],'provider':u['provider']},'titles':titles,'stats':stats,'watch_rate':rate,'vision':vision}
            with _BOOTSTRAP_CACHE_LOCK:
                _BOOTSTRAP_CACHE[uid]=(time.monotonic(),payload)
                if len(_BOOTSTRAP_CACHE)>24:
                    oldest=min(_BOOTSTRAP_CACHE.items(), key=lambda kv:kv[1][0])[0]
                    _BOOTSTRAP_CACHE.pop(oldest,None)
            return self.send_json(payload)
        if path=='/api/creator/feedback':
            u=require(self)
            if not u: return
            if u.get('role')!='creator': return self.send_json({'error':'Acesso restrito ao criador.'},403)
            c=auth_db(); rows=c.execute('SELECT f.*,u.email,u.display_name FROM feedback f JOIN users u ON u.id=f.user_id ORDER BY f.created_at DESC').fetchall(); c.close(); return self.send_json([dict(r) for r in rows])
        if path=='/api/groups':
            u=require(self)
            if not u:return
            c=auth_db(); rows=c.execute('SELECT g.*,gm.role,(SELECT COUNT(*) FROM group_members x WHERE x.group_id=g.id AND x.status="active") member_count FROM groups g JOIN group_members gm ON gm.group_id=g.id AND gm.user_id=? AND gm.status="active" ORDER BY g.updated_at DESC',(u['id'],)).fetchall(); c.close(); return self.send_json([dict(r) for r in rows])
        m=re.fullmatch(r'/api/groups/(\d+)/pulse',path)
        if m:
            u=require(self)
            if not u:return
            gid=int(m.group(1))
            if not group_member(u['id'],gid): return self.send_json({'error':'Grupo não encontrado ou sem acesso.'},404)
            qs=parse_qs(p.query)
            since=str(qs.get('since',[''])[0] or '')
            try: client_count=int(qs.get('count',['-1'])[0])
            except Exception: client_count=-1
            try: client_last_message=int(qs.get('message',['0'])[0])
            except Exception: client_last_message=0
            c=auth_db()
            g=c.execute('SELECT id,updated_at FROM groups WHERE id=?',(gid,)).fetchone()
            count_row=c.execute('SELECT COUNT(*) AS n FROM group_members WHERE group_id=? AND status=\"active\"',(gid,)).fetchone()
            msg_row=c.execute('SELECT COALESCE(MAX(id),0) AS n FROM group_messages WHERE group_id=?',(gid,)).fetchone()
            current_count=int(count_row['n'] or 0) if count_row else 0
            last_message_id=int(msg_row['n'] or 0) if msg_row else 0
            changed=(str(g['updated_at'] if g else '')!=since) or (client_count>=0 and client_count!=current_count) or (client_last_message!=last_message_id)
            out={'changed':changed,'updated_at':str(g['updated_at'] if g else ''),'member_count':current_count,'last_message_id':last_message_id}
            if changed:
                members=c.execute('SELECT u.id,u.email,u.display_name,gm.role,gm.joined_at FROM group_members gm JOIN users u ON u.id=gm.user_id WHERE gm.group_id=? AND gm.status=\"active\" ORDER BY gm.joined_at',(gid,)).fetchall()
                out['members']=[dict(x) for x in members]
            c.close(); return self.send_json(out)
        m=re.fullmatch(r'/api/groups/(\d+)/chat',path)
        if m:
            u=require(self)
            if not u:return
            gid=int(m.group(1))
            if not group_member(u['id'],gid): return self.send_json({'error':'Sem acesso ao grupo.'},403)
            qs=parse_qs(p.query)
            try: after_id=max(0,int(qs.get('after_id',['0'])[0] or 0))
            except Exception: after_id=0
            c=auth_db(); row=c.execute('SELECT COALESCE(MAX(id),0) AS n FROM group_messages WHERE group_id=?',(gid,)).fetchone(); c.close()
            return self.send_json({'messages':group_message_rows(gid,after_id,80),'last_message_id':int(row['n'] or 0) if row else 0})
        m=re.fullmatch(r'/api/groups/(\d+)',path)
        if m:
            u=require(self)
            if not u:return
            gid=int(m.group(1));
            if not group_member(u['id'],gid): return self.send_json({'error':'Grupo não encontrado ou sem acesso.'},404)
            c=auth_db(); g=c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone(); members=c.execute('SELECT u.id,u.email,u.display_name,gm.role,gm.joined_at FROM group_members gm JOIN users u ON u.id=gm.user_id WHERE gm.group_id=? AND gm.status="active" ORDER BY gm.joined_at',(gid,)).fetchall(); recs=c.execute('SELECT r.*,u.email,u.display_name FROM group_recommendations r JOIN users u ON u.id=r.user_id WHERE r.group_id=? ORDER BY r.created_at DESC',(gid,)).fetchall(); photos=c.execute('SELECT p.id,p.user_id,p.filename,p.mime,p.caption,p.taken_at,p.location,p.created_at,u.email,u.display_name FROM group_photos p JOIN users u ON u.id=p.user_id WHERE p.group_id=? ORDER BY p.created_at DESC',(gid,)).fetchall(); c.close(); return self.send_json({'group':dict(g),'members':[dict(x) for x in members],'recommendations':[dict(x) for x in recs],'photos':[dict(x) for x in photos],'messages':group_message_rows(gid,0,80)})
        m=re.fullmatch(r'/api/groups/(\d+)/photos/(\d+)',path)
        if m:
            u=require(self)
            if not u:return
            gid,pid=map(int,m.groups());
            if not group_member(u['id'],gid): return self.send_json({'error':'Sem acesso.'},403)
            c=auth_db(); row=c.execute('SELECT * FROM group_photos WHERE id=? AND group_id=?',(pid,gid)).fetchone(); c.close()
            if not row:return self.send_json({'error':'Foto não encontrada.'},404)
            stored_path=str(row['path'] or '')
            fp=Path(stored_path) if stored_path and Path(stored_path).is_absolute() else (DATA/stored_path if stored_path else Path(''))
            if not fp.exists() and stored_path:
                # Compatibilidade com registros antigos que guardavam caminho relativo à instalação.
                legacy_fp=BASE/stored_path
                if legacy_fp.exists(): fp=legacy_fp
            if not fp.exists():return self.send_json({'error':'Arquivo da foto não encontrado.'},404)
            wants_thumb=parse_qs(p.query).get('thumb',['0'])[0]=='1'
            if wants_thumb:
                try:
                    from PIL import Image, ImageOps
                    thumb_path=fp.with_name(fp.name+'.thumb.jpg')
                    src_mtime=fp.stat().st_mtime_ns
                    if (not thumb_path.exists()) or thumb_path.stat().st_mtime_ns < src_mtime:
                        im=Image.open(fp).convert('RGB')
                        im.thumbnail((720,720),Image.Resampling.LANCZOS)
                        tmp=thumb_path.with_suffix('.tmp')
                        im.save(tmp,format='JPEG',quality=78,optimize=True)
                        tmp.replace(thumb_path)
                    raw=thumb_path.read_bytes(); ctype='image/jpeg'; max_age='86400'
                except Exception:
                    raw=fp.read_bytes(); ctype=row['mime']; max_age='3600'
            else:
                raw=fp.read_bytes(); ctype=row['mime']; max_age='3600'
            self.send_response(200); self.send_header('Content-Type',ctype); self.send_header('Cache-Control',f'private,max-age={max_age}'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if path=='/api/import/history/list':
            u=require(self)
            if not u:return
            _tls.user_id=u['id']
            try:
                return self.send_json(legacy.imported_history_rows() if hasattr(legacy,'imported_history_rows') else [])
            finally:
                _tls.__dict__.pop('user_id',None)
        if path=='/api/import/history':
            u=require(self)
            if not u:return
            _tls.user_id=u['id']
            try:
                return legacy.Handler.do_GET(self)
            finally:
                _tls.__dict__.pop('user_id',None)
        if path=='/api/import/history/consolidate':
            u=require(self)
            if not u:return
            _tls.user_id=u['id']
            try:
                return self.send_json(legacy.consolidate_imported_seasons())
            finally:
                _tls.__dict__.pop('user_id',None)
        if path=='/api/vision/status':
            return legacy.Handler.do_GET(self) if False else self.send_json({'configured':legacy.vision_configured(),'provider':'OpenAI Vision + Web Search' if legacy.vision_configured() else 'OCR local'})
        if path.startswith('/api/') or path.startswith('/media/'):
            if not require(self): return
            return legacy.Handler.do_GET(self)
        self.send_json({'error':'Rota não encontrada.'},404)
    def do_POST(self):
        _tls.__dict__.pop('user_id',None); p=urlparse(self.path); path=p.path.rstrip('/') or '/'
        # Não invalide a cache para login, QR, grupos, feedback ou outras rotas que
        # não alteram a biblioteca. Isso evitava aproveitar a cache e deixava cada
        # carregamento voltar ao SQLite sem necessidade.
        try:
            if path=='/api/import/history/consolidate':
                u=require(self)
                if not u:return
                _tls.user_id=u['id']
                try:
                    return self.send_json(legacy.consolidate_imported_seasons())
                finally:
                    _tls.__dict__.pop('user_id',None)

            if path=='/api/auth/google/credential':
                try:
                    payload=json.loads(self.body().decode('utf-8'))
                    claims=verify_google_credential(str(payload.get('credential','')))
                except Exception as e:
                    return self.send_json({'error':'Não foi possível validar o login do Google.','detail':str(e),'code':'GOOGLE_INVALID_CREDENTIAL'},401)
                email=normalize_email(claims.get('email')); sub=str(claims.get('sub','')); name=str(claims.get('name') or email.split('@')[0]); now=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')
                c=auth_db(); u=c.execute('SELECT * FROM users WHERE provider="google" AND provider_subject=?',(sub,)).fetchone()
                if not u: u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                if not u:
                    role='creator' if c.execute('SELECT 1 FROM users').fetchone() is None else 'beta'
                    cur=c.execute('INSERT INTO users(email,password_hash,display_name,role,provider,provider_subject,created_at,updated_at,last_login_at) VALUES(?,?,?,?,?,?,?,?,?)',(email,None,name,role,'google',sub,now,now,now)); uid=cur.lastrowid
                else:
                    uid=u['id']; role=u['role']; c.execute('UPDATE users SET provider="google",provider_subject=?,display_name=?,last_login_at=?,updated_at=? WHERE id=?',(sub,name,now,now,uid))
                c.commit(); c.close(); ensure_library(uid); tok=token_make(uid,self.headers.get('User-Agent','')); self.send_response(200); set_session_cookie(self,tok); raw=json.dumps({'ok':True,'token':tok,'user':{'id':uid,'email':email,'display_name':name,'role':role,'provider':'google'}},ensure_ascii=False).encode(); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            if path in ('/api/auth/register','/api/register','/register'):
                d=json.loads(self.body().decode() or '{}'); email=str(d.get('email','')).strip().lower(); pw=str(d.get('password','')); name=str(d.get('display_name','')).strip() or email.split('@')[0]
                if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$',email): return self.send_json({'error':'Informe um e-mail válido.'},400)
                if len(pw)<8: return self.send_json({'error':'A senha precisa ter pelo menos 8 caracteres.'},400)
                c=auth_db(); first=c.execute('SELECT 1 FROM users LIMIT 1').fetchone() is None; now=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')
                try: cur=c.execute('INSERT INTO users(email,password_hash,display_name,role,provider,created_at,updated_at,last_login_at) VALUES(?,?,?,?,?,?,?,?)',(email,pw_hash(pw),name,'creator' if first else 'beta','local',now,now,now)); uid=cur.lastrowid; c.commit()
                except sqlite3.IntegrityError: c.close(); return self.send_json({'error':'Este e-mail já está cadastrado.'},409)
                c.close(); ensure_library(uid); tok=token_make(uid,self.headers.get('User-Agent','')); self.send_response(201); set_session_cookie(self,tok); raw=json.dumps({'token':tok,'user':{'id':uid,'email':email,'display_name':name,'role':'creator' if first else 'beta'}},ensure_ascii=False).encode(); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            if path in ('/api/auth/login','/api/login','/login'):
                d=json.loads(self.body().decode() or '{}'); email=str(d.get('email','')).strip().lower(); pw=str(d.get('password','')); c=auth_db(); u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone();
                if not u or not u['password_hash'] or not pw_ok(pw,u['password_hash']): c.close(); return self.send_json({'error':'E-mail ou senha inválidos.'},401)
                c.execute('UPDATE users SET last_login_at=?,updated_at=? WHERE id=?',(datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'),datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'),u['id'])); c.commit(); c.close(); ensure_library(u['id']); tok=token_make(u['id'],self.headers.get('User-Agent','')); self.send_response(200); set_session_cookie(self,tok); raw=json.dumps({'token':tok,'user':{'id':u['id'],'email':u['email'],'display_name':u['display_name'],'role':u['role']}},ensure_ascii=False).encode(); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            if path=='/api/auth/logout':
                h=self.headers.get('Authorization',''); raw=h[7:].strip() if h.startswith('Bearer ') else ''; raw=raw or _cookie_token(self); th=hashlib.sha256(raw.encode()).hexdigest() if raw else ''; c=auth_db(); c.execute('DELETE FROM sessions WHERE token_hash=?',(th,)); c.commit(); c.close(); self.send_response(200); clear_session_cookie(self); raw=b'{"success":true}'; self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            # QR also accepts POST so malformed/direct clients cannot poison a keep-alive connection.
            if path=='/api/qr':
                try:
                    raw=self.body()
                    d=json.loads(raw.decode('utf-8') or '{}')
                    text=str(d.get('text') or '').strip()
                    if not text or len(text)>2048 or not (text.startswith('http://') or text.startswith('https://')):
                        return self.send_json({'error':'Texto/URL inválido para QR.'},400)
                    import qrcode
                    import io
                    img=qrcode.make(text); bio=io.BytesIO(); img.save(bio,format='PNG'); data=bio.getvalue()
                    self.send_response(200); self.send_header('Content-Type','image/png'); self.send_header('Cache-Control','no-store'); self.send_header('Connection','keep-alive'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
                except Exception as e:
                    return self.send_json({'error':f'Não foi possível gerar o QR Code: {e}'},500)
            if path=='/api/groups':
                u=require(self)
                if not u:return
                d=json.loads(self.body().decode() or '{}'); name=str(d.get('name','')).strip(); desc=str(d.get('description','')).strip(); gtype=str(d.get('group_type','both')).strip().lower()
                if not name:return self.send_json({'error':'Dê um nome ao grupo.'},400)
                if gtype not in ('recommendations','memories','both'): gtype='both'
                now=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'); c=auth_db(); cur=c.execute('INSERT INTO groups(name,description,owner_id,created_at,updated_at,group_type) VALUES(?,?,?,?,?,?)',(name,desc,u['id'],now,now,gtype)); gid=cur.lastrowid; c.execute('INSERT INTO group_members(group_id,user_id,role,status,joined_at) VALUES(?,?,?,?,?)',(gid,u['id'],'owner','active',now)); c.commit(); c.close(); return self.send_json({'success':True,'group_id':gid,'group_type':gtype},201)
            m=re.fullmatch(r'/api/groups/(\d+)/share',path)
            if m:
                u=require(self)
                if not u:return
                gid=int(m.group(1)); mem=group_member(u['id'],gid)
                if not mem or mem['role'] not in ('owner','admin'):return self.send_json({'error':'Somente o dono ou administrador pode compartilhar o convite.'},403)
                d=json.loads(self.body().decode() or '{}'); action=str(d.get('action','create')).strip().lower()
                if action=='revoke':
                    c=auth_db(); c.execute("UPDATE group_share_links SET status='revoked' WHERE group_id=? AND inviter_id=? AND status='active'",(gid,u['id'])); c.commit(); c.close(); return self.send_json({'success':True,'revoked':True})
                raw=secrets.token_urlsafe(32); th=hashlib.sha256(raw.encode()).hexdigest(); now=datetime.now(timezone.utc).replace(tzinfo=None); exp=now+timedelta(days=GROUP_INVITE_DAYS)
                c=auth_db(); c.execute("UPDATE group_share_links SET status='revoked' WHERE group_id=? AND inviter_id=? AND status='active'",(gid,u['id']))
                c.execute('INSERT INTO group_share_links(group_id,inviter_id,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)',(gid,u['id'],th,now.isoformat(timespec='seconds'),exp.isoformat(timespec='seconds'))); g=c.execute('SELECT name FROM groups WHERE id=?',(gid,)).fetchone(); c.commit(); c.close()
                link=f"{public_lan_base()}/?group_share={raw}"; text=f"👥 Convite para o grupo \"{g['name']}\" no Cime dos Mundos 5.0\n\n{link}"; from urllib.parse import quote; wa='https://wa.me/?text='+quote(text)
                return self.send_json({'success':True,'group_id':gid,'invite_link':link,'whatsapp_url':wa,'expires_at':exp.isoformat(timespec='seconds')},201)
            if path=='/api/groups/share/accept':
                u=require(self)
                if not u:return
                d=json.loads(self.body().decode() or '{}'); token=str(d.get('token','')).strip(); th=hashlib.sha256(token.encode()).hexdigest(); c=auth_db(); row=c.execute("SELECT * FROM group_share_links WHERE token_hash=? AND status='active' AND expires_at>?",(th,datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'))).fetchone()
                if not row:c.close();return self.send_json({'error':'Link de convite expirado ou inválido.'},400)
                gm=c.execute('SELECT 1 FROM group_members WHERE group_id=? AND user_id=? AND status="active"',(row['group_id'],u['id'])).fetchone()
                if not gm:
                    now=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'); c.execute('INSERT OR REPLACE INTO group_members(group_id,user_id,role,status,joined_at) VALUES(?,?,?,?,?)',(row['group_id'],u['id'],'member','active',now)); c.execute('UPDATE groups SET updated_at=? WHERE id=?',(now,row['group_id'])); c.commit()
                c.close(); return self.send_json({'success':True,'group_id':row['group_id']},200)
            m=re.fullmatch(r'/api/groups/(\d+)/invites',path)
            if m:
                u=require(self)
                if not u:return
                gid=int(m.group(1)); mem=group_member(u['id'],gid)
                if not mem or mem['role'] not in ('owner','admin'):return self.send_json({'error':'Somente o dono ou administrador pode convidar.'},403)
                d=json.loads(self.body().decode() or '{}'); email=normalize_email(d.get('email'))
                if not valid_email(email):return self.send_json({'error':'Informe um e-mail válido.'},400)
                c=auth_db(); already=c.execute('SELECT 1 FROM group_members gm JOIN users u ON u.id=gm.user_id WHERE gm.group_id=? AND u.email=? AND gm.status="active"',(gid,email)).fetchone(); g=c.execute('SELECT name FROM groups WHERE id=?',(gid,)).fetchone()
                if already:c.close();return self.send_json({'error':'Essa pessoa já está no grupo.'},409)
                raw=secrets.token_urlsafe(36); th=hashlib.sha256(raw.encode()).hexdigest(); now=datetime.now(timezone.utc).replace(tzinfo=None); exp=now+timedelta(days=GROUP_INVITE_DAYS); c.execute('INSERT INTO group_invites(group_id,inviter_id,email,token_hash,created_at,expires_at) VALUES(?,?,?,?,?,?)',(gid,u['id'],email,th,now.isoformat(timespec='seconds'),exp.isoformat(timespec='seconds'))); c.commit(); c.close(); link=f"{public_lan_base()}/?invite={raw}"; sent=False; status='not_sent';
                try: sent,status=maybe_send_invite_email(email,g['name'],u['display_name'],link)
                except Exception as e: status='send_error'
                return self.send_json({'success':True,'email':email,'sent':sent,'delivery_status':status,'invite_token':raw,'invite_link':link},201)
            if path=='/api/invites/accept':
                u=require(self)
                if not u:return
                d=json.loads(self.body().decode() or '{}'); token=str(d.get('token','')).strip(); th=hashlib.sha256(token.encode()).hexdigest(); c=auth_db(); row=c.execute('SELECT * FROM group_invites WHERE token_hash=? AND status="pending" AND expires_at>?',(th,datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'))).fetchone()
                if not row:c.close();return self.send_json({'error':'Convite expirado, já usado ou inválido.'},400)
                if normalize_email(row['email'])!=normalize_email(u['email']):c.close();return self.send_json({'error':'Este convite foi enviado para outro e-mail.'},403)
                now=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'); c.execute('INSERT OR REPLACE INTO group_members(group_id,user_id,role,status,joined_at) VALUES(?,?,?,?,?)',(row['group_id'],u['id'],'member','active',now)); c.execute('UPDATE group_invites SET status="accepted",accepted_by=? WHERE id=?',(u['id'],row['id'])); c.commit(); c.close(); return self.send_json({'success':True,'group_id':row['group_id']})
            m=re.fullmatch(r'/api/groups/(\d+)/messages',path)
            if m:
                u=require(self)
                if not u:return
                gid=int(m.group(1))
                if not group_member(u['id'],gid): return self.send_json({'error':'Sem acesso ao grupo.'},403)
                d=json.loads(self.body().decode() or '{}'); text=str(d.get('body','')).strip()
                if not text:return self.send_json({'error':'Escreva uma mensagem antes de enviar.'},400)
                if len(text)>4000:return self.send_json({'error':'A mensagem ficou muito longa.'},400)
                now=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')
                c=auth_db(); cur=c.execute('INSERT INTO group_messages(group_id,user_id,kind,body,created_at) VALUES(?,?,?,?,?)',(gid,u['id'],'text',text,now)); c.execute('UPDATE groups SET updated_at=? WHERE id=?',(now,gid)); c.commit(); mid=cur.lastrowid; c.close()
                return self.send_json({'success':True,'message_id':mid,'messages':group_message_rows(gid,mid-1,1)},201)
            m=re.fullmatch(r'/api/groups/(\d+)/recommendations',path)
            if m:
                u=require(self)
                if not u:return
                gid=int(m.group(1));
                if not group_member(u['id'],gid):return self.send_json({'error':'Sem acesso ao grupo.'},403)
                d=json.loads(self.body().decode() or '{}'); title=str(d.get('title_name','')).strip(); text=str(d.get('recommendation','')).strip()
                if not title or not text:return self.send_json({'error':'Informe o título e a recomendação.'},400)
                if len(text)>2000:return self.send_json({'error':'A recomendação ficou muito longa.'},400)
                c=auth_db(); c.execute('INSERT INTO group_recommendations(group_id,user_id,title_name,recommendation,created_at) VALUES(?,?,?,?,?)',(gid,u['id'],title,text,datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'))); c.execute('UPDATE groups SET updated_at=? WHERE id=?',(datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'),gid)); c.commit(); c.close(); return self.send_json({'success':True},201)
            m=re.fullmatch(r'/api/groups/(\d+)/photos',path)
            if m:
                u=require(self)
                if not u:return
                gid=int(m.group(1));
                if not group_member(u['id'],gid):return self.send_json({'error':'Sem acesso ao grupo.'},403)
                d=json.loads(self.body().decode() or '{}'); raw,mime=safe_photo_bytes(d.get('data_base64','')); filename=re.sub(r'[^A-Za-z0-9._-]','_',str(d.get('filename','foto.jpg')))[:120] or 'foto.jpg'; caption=str(d.get('caption','')).strip()[:1000]; taken_at=str(d.get('taken_at','')).strip()[:64]; location=str(d.get('location','')).strip()[:200]
                gid_dir=GROUPS_MEDIA/str(gid); gid_dir.mkdir(parents=True,exist_ok=True); fname=secrets.token_hex(16)+'_'+filename; fp=gid_dir/fname; fp.write_bytes(raw); rel=str(fp.relative_to(DATA)).replace('\\','/'); now=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'); c=auth_db(); cur=c.execute('INSERT INTO group_photos(group_id,user_id,filename,mime,path,caption,taken_at,location,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(gid,u['id'],filename,mime,rel,caption,taken_at,location,now)); pid=cur.lastrowid; mcur=c.execute('INSERT INTO group_messages(group_id,user_id,kind,body,photo_id,created_at) VALUES(?,?,?,?,?,?)',(gid,u['id'],'photo',caption,pid,now)); c.execute('UPDATE groups SET updated_at=? WHERE id=?',(now,gid)); c.commit(); c.close(); return self.send_json({'success':True,'photo_id':pid,'message_id':mcur.lastrowid},201)
            if path=='/api/titles/bulk-delete':
                u=require(self)
                if not u:return
                d=json.loads(self.body().decode() or '{}')
                ids=[]
                for raw in (d.get('ids') or []):
                    try:
                        n=int(raw)
                        if n>0 and n not in ids: ids.append(n)
                    except Exception:
                        pass
                if not ids:return self.send_json({'error':'Nenhum título selecionado.'},400)
                if len(ids)>5000:return self.send_json({'error':'Seleção muito grande. Faça em lotes menores.'},400)
                conn=legacy.db(); conn.execute('PRAGMA foreign_keys=ON')
                try:
                    marks=','.join('?' for _ in ids)
                    found=[int(r[0]) for r in conn.execute(f'SELECT id FROM titles WHERE id IN ({marks})',ids).fetchall()]
                    if not found:
                        return self.send_json({'success':True,'deleted':0,'requested':len(ids),'missing':ids})
                    # Remove dependent history first for databases created before all FK constraints existed.
                    conn.execute(f'DELETE FROM watch_history WHERE title_id IN ({marks})',found)
                    conn.execute(f'DELETE FROM imported_history WHERE title_id IN ({marks})',found)
                    cur=conn.execute(f'DELETE FROM titles WHERE id IN ({marks})',found)
                    conn.commit()
                    legacy.invalidate_library_cache()
                    deleted=int(cur.rowcount or 0)
                    missing=[x for x in ids if x not in found]
                    return self.send_json({'success':True,'deleted':deleted,'requested':len(ids),'missing':missing})
                except Exception as e:
                    conn.rollback()
                    return self.send_json({'error':f'Não foi possível apagar a seleção: {e}'},500)
                finally:
                    conn.close()

            if path=='/api/titles/delete-all':
                u=require(self)
                if not u:return
                d=json.loads(self.body().decode() or '{}')
                if str(d.get('confirm',''))!='CIME_DELETE_ALL_5_0':
                    return self.send_json({'error':'Confirmação inválida para apagar toda a biblioteca.'},400)
                conn=legacy.db(); conn.execute('PRAGMA foreign_keys=ON')
                try:
                    total=int(conn.execute('SELECT COUNT(*) FROM titles').fetchone()[0])
                    conn.execute('DELETE FROM watch_history')
                    conn.execute('DELETE FROM imported_history')
                    conn.execute('DELETE FROM titles')
                    conn.commit()
                    legacy.invalidate_library_cache()
                    return self.send_json({'success':True,'deleted':total})
                except Exception as e:
                    conn.rollback()
                    return self.send_json({'error':f'Não foi possível apagar toda a biblioteca: {e}'},500)
                finally:
                    conn.close()
            if path=='/api/feedback':
                u=require(self)
                if not u:return
                d=json.loads(self.body().decode() or '{}'); title=str(d.get('title','')).strip(); desc=str(d.get('description','')).strip(); kind=str(d.get('kind','bug')).strip(); page=str(d.get('page',''))[:200]
                if not title or not desc:return self.send_json({'error':'Título e descrição são obrigatórios.'},400)
                c=auth_db(); c.execute('INSERT INTO feedback(user_id,kind,title,description,page,created_at) VALUES(?,?,?,?,?,?)',(u['id'],kind,title,desc,page,datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'))); c.commit(); c.close(); return self.send_json({'success':True},201)
            if path.startswith('/api/'):
                if not require(self): return
                result=legacy.Handler.do_POST(self)
                if path in ('/api/titles','/api/import','/api/import/history','/api/titles/cover-photo'):
                    legacy.invalidate_library_cache()
                return result
            self.send_json({'error':'Rota não encontrada.'},404)
        except Exception as e: self.send_json({'error':str(e)},500)
    def do_PUT(self):
        _tls.__dict__.pop('user_id',None)
        legacy.invalidate_library_cache()
        if not require(self): return
        return legacy.Handler.do_PUT(self)
    def do_DELETE(self):
        _tls.__dict__.pop('user_id',None)
        legacy.invalidate_library_cache()
        if not require(self): return
        return legacy.Handler.do_DELETE(self)



def _clean_text(v):
    return str(v or '').strip()

def _first_nonempty(*vals):
    for v in vals:
        if isinstance(v,(list,tuple)):
            if v: return v
        elif _clean_text(v):
            return v
        elif isinstance(v,(int,float)) and v is not None:
            return v
    return ''

def _genre_string(v):
    if isinstance(v,(list,tuple,set)):
        vals=[_clean_text(x) for x in v if _clean_text(x)]
        return ', '.join(dict.fromkeys(vals))
    return _clean_text(v)

def _season_label(v):
    v=_clean_text(v)
    if not v: return ''
    m=re.search(r'(?i)\b(?:season|temporada|t)\s*[-.#:]?\s*(\d+)\b',v)
    if m: return f"Season {int(m.group(1))}"
    return v.replace('_',' ').title() if v.upper()==v and len(v)<=16 else v

def _source_ref(item):
    src=_clean_text(item.get('source')).lower()
    sid=item.get('id')
    if sid in (None,''): return ''
    if src=='jikan': return str(sid)
    if src=='anilist': return f"anilist:{sid}"
    if src=='kitsu': return f"kitsu:{sid}"
    if src=='tmdb': return str(sid)
    if src=='tvmaze': return f"tvmaze:{sid}"
    if src=='wikidata': return str(sid)
    return str(sid)



def _tmdb_details_for_cover(item):
    """Detalhes de TMDB focados em poster e posters por temporada."""
    if not getattr(legacy, 'TMDB_TOKEN', ''): return None
    sid=str(item.get('id') or '')
    if sid.startswith('tmdb:'): sid=sid.split(':',1)[1]
    if not sid.isdigit(): return None
    cat=str(item.get('category') or '').strip().lower()
    media='tv' if cat in ('série','serie','desenho') or str(item.get('type') or '').upper() in ('TV','TVD') else 'movie'
    try:
        r=legacy.requests.get(
            f'https://api.themoviedb.org/3/{media}/{sid}',
            params={'language':'pt-BR'},
            headers={'Authorization':'Bearer '+legacy.TMDB_TOKEN,'accept':'application/json','User-Agent':legacy.USER_AGENT},
            timeout=8
        )
        if not r.ok: return None
        d=r.json() or {}
        poster=('https://image.tmdb.org/t/p/w780'+str(d.get('poster_path'))) if d.get('poster_path') else ''
        seasons=[]
        for season in d.get('seasons') or []:
            n=season.get('season_number')
            if n is None or int(n)==0: continue
            p=season.get('poster_path')
            seasons.append({'season':int(n),'episodes':season.get('episode_count'),'cover':('https://image.tmdb.org/t/p/w780'+str(p)) if p else ''})
        return {'cover':poster,'seasons':seasons,'id':'tmdb:'+sid,'source':'TMDB'}
    except Exception as e:
        return None


def _season_items_from_row(row):
    try:
        raw=json.loads(row.get('seasons_json') or '[]')
        if isinstance(raw,list) and raw:
            return [dict(x) for x in raw if isinstance(x,dict)]
    except Exception:
        pass
    return []


def _cover_candidates(item):
    vals=[]
    for v in (
        item.get('cover'),
        ((item.get('images') or {}).get('jpg') or {}).get('large_image_url') if isinstance(item.get('images'),dict) else '',
        ((item.get('images') or {}).get('webp') or {}).get('large_image_url') if isinstance(item.get('images'),dict) else '',
        ((item.get('images') or {}).get('jpg') or {}).get('image_url') if isinstance(item.get('images'),dict) else '',
        ((item.get('images') or {}).get('webp') or {}).get('image_url') if isinstance(item.get('images'),dict) else '',
    ):
        if str(v or '').strip() and str(v).strip() not in vals: vals.append(str(v).strip())
    return vals


def _refresh_season_covers(row, best, diagnostics):
    """Preenche capas por temporada sem apagar capas existentes."""
    seasons=_season_items_from_row(row)
    if not seasons:
        return [], False
    by={int(e.get('season') or 1):dict(e) for e in seasons}
    changed=False
    # 1) TMDB pode fornecer posters específicos de cada temporada.
    tmdb=_tmdb_details_for_cover(best) if (best.get('source')=='TMDB' or str(best.get('id','')).startswith('tmdb:')) else None
    if tmdb:
        for item in tmdb.get('seasons') or []:
            n=int(item.get('season') or 0)
            if n in by and not by[n].get('cover') and item.get('cover'):
                by[n]['cover']=item['cover']; changed=True
                if item.get('episodes') and not by[n].get('episodes'): by[n]['episodes']=item['episodes']
                diagnostics.append({'source':'TMDB temporadas','query':str(n),'status':'cover_ok','count':1})
    # 2) Para anime, faz uma busca específica para a temporada ainda sem capa.
    if len(by)<=8:
        base=str(best.get('title') or row.get('name') or '').strip()
        for n,e in sorted(by.items()):
            if e.get('cover') or not base: continue
            variants=[f'{base} Season {n}', f'{base} S{n}', f'{base} temporada {n}']
            local_best=None
            local_diag=[]
            for q in variants:
                try:
                    _, cand=legacy.smart_search_title(q, aliases=[base], category_hint=row.get('category') or '', diagnostics=local_diag, fast_mode=False)
                except Exception:
                    cand=None
                if cand and (local_best is None or float(cand.get('_match_score') or 0)>float(local_best.get('_match_score') or 0)):
                    local_best=cand
            for d in local_diag[-3:]: diagnostics.append(d)
            covers=_cover_candidates(local_best or {}) if local_best else []
            if covers:
                e['cover']=covers[0]; changed=True
                if not e.get('episodes') and local_best.get('episodes'): e['episodes']=local_best.get('episodes')
                diagnostics.append({'source':local_best.get('source') or 'multi','query':f'{base} S{n}','status':'cover_ok','count':1})
    out=[by[k] for k in sorted(by)]
    return out, changed

def _deep_verify_candidate(best, diagnostics):
    """Busca detalhes adicionais da fonte mais forte sem substituir dados do usuário."""
    item=dict(best or {})
    source=_clean_text(item.get('source'))
    sid=item.get('id')
    # Jikan/MAL: detalhes individuais trazem o conjunto mais completo para anime.
    if source=='Jikan' and sid not in (None,'') and str(sid).isdigit():
        try:
            detail=legacy.anime_info(int(sid))
            if detail and not detail.get('error'):
                for k in ('title','title_english','title_japanese','episodes','status','score','year','season','duration','synopsis','synopsis_pt','cover','genres','url','images'):
                    if detail.get(k) not in (None,'','[]',[]): item[k]=detail[k]
                item['id']=detail.get('id',sid)
                diagnostics.append({'source':'Jikan detalhe','query':str(sid),'status':'detail_ok','count':1})
        except Exception as e:
            diagnostics.append({'source':'Jikan detalhe','query':str(sid),'status':'error','detail':str(e)[:160]})
    elif source=='TVMaze' and sid not in (None,''):
        try:
            r=legacy.requests.get(f"https://api.tvmaze.com/shows/{sid}/episodes",params={'specials':0},timeout=7,headers={'User-Agent':legacy.USER_AGENT})
            if r.ok:
                eps=r.json() or []
                if eps:
                    item['episodes']=len(eps)
                    diagnostics.append({'source':'TVMaze episódios','query':str(sid),'status':'detail_ok','count':len(eps)})
        except Exception as e:
            diagnostics.append({'source':'TVMaze episódios','query':str(sid),'status':'error','detail':str(e)[:160]})
    elif source=='TMDB' or str(sid).startswith('tmdb:'):
        d=_tmdb_details_for_cover(item)
        if d:
            if d.get('cover') and not item.get('cover'): item['cover']=d['cover']
            item['_tmdb_seasons']=d.get('seasons') or []
            diagnostics.append({'source':'TMDB detalhes','query':str(sid),'status':'detail_ok','count':1})
    return item

def _safe_enrich_title(row):
    """Análise profunda multi-fonte; nunca apaga um valor já salvo pelo usuário."""
    name=_clean_text(row.get('name'))
    if not name:
        return {'id':row.get('id'),'name':name,'changed':False,'reason':'sem_nome'}
    cat=_clean_text(row.get('category'))
    canonical=legacy.canonical_title_from_text(name) or name
    diagnostics=[]

    # O botão agora faz a pesquisa completa; fast_mode era rápido demais e podia pular Jikan/Kitsu.
    results,best=legacy.smart_search_title(canonical, category_hint=cat, diagnostics=diagnostics, fast_mode=False)
    # Segunda rodada com variantes alternativas quando o primeiro candidato é fraco.
    if best and float(best.get('_match_score') or 0) < 82:
        alt=legacy.canonical_title_from_text(_clean_text(row.get('title_english'))) or _clean_text(row.get('title_english'))
        if alt and legacy.normalizar_texto(alt)!=legacy.normalizar_texto(canonical):
            r2,b2=legacy.smart_search_title(alt, category_hint=cat, diagnostics=diagnostics, fast_mode=False)
            if b2 and float(b2.get('_match_score') or 0) > float(best.get('_match_score') or 0):
                results,best=r2,b2
    if not best:
        # IA de pesquisa vira último fallback quando configurada.
        try:
            research=legacy.openai_web_research(canonical,[name],cat) if getattr(legacy,'OPENAI_API_KEY','') else None
            if research and research.get('found'):
                best={
                    'id':None,'title':research.get('canonical_title') or canonical,
                    'title_english':research.get('title_english') or '',
                    'title_japanese':research.get('title_japanese') or '',
                    'episodes':research.get('episodes'),'year':research.get('year'),
                    'season':research.get('season') or '','duration':'',
                    'synopsis':research.get('synopsis_pt') or '', 'synopsis_pt':research.get('synopsis_pt') or '',
                    'genres':research.get('genres') or [], 'cover':research.get('cover_url') or '',
                    'category':research.get('category') or cat, 'source':'OpenAI Web Search',
                    '_match_score':float(research.get('confidence') or 0)*100,
                    'sources':research.get('sources') or []
                }
                diagnostics.append({'source':'OpenAI Web Search','query':canonical,'status':'result','count':1})
        except Exception as e:
            diagnostics.append({'source':'OpenAI Web Search','query':canonical,'status':'error','detail':str(e)[:160]})
    if not best:
        return {'id':row.get('id'),'name':name,'changed':False,'reason':'nao_encontrado','diagnostics':diagnostics[-14:]}

    best=_deep_verify_candidate(best,diagnostics)
    # Capas: além da capa principal, tenta preencher cada temporada individualmente.
    season_updates, season_changed = _refresh_season_covers(row, best, diagnostics)
    conn=legacy.db(); updates={}
    category=cat or best.get('category') or 'Anime'
    # Só preenche lacunas — não sobrescreve capa, nota, status, datas etc.
    scalar_map={
        'cover':_first_nonempty(best.get('cover'), ((best.get('images') or {}).get('jpg') or {}).get('large_image_url') if isinstance(best.get('images'),dict) else ''),
        'title_english':best.get('title_english'),
        'title_japanese':best.get('title_japanese'),
        'synopsis':best.get('synopsis'),
        'synopsis_pt':_first_nonempty(best.get('synopsis_pt'),best.get('synopsis')),
        'year':best.get('year'),
        'duration':best.get('duration'),
        'genres':_genre_string(best.get('genres')),
        'season':_season_label(best.get('season')),
        'episodes':best.get('episodes'),
    }
    for field,val in scalar_map.items():
        if not row.get(field) and val not in (None,'',[]):
            if field in ('year','episodes'):
                try: val=int(float(val))
                except Exception: continue
            updates[field]=val
    if not row.get('source_id'):
        ref=_source_ref(best)
        if ref: updates['source_id']=ref
    if not row.get('total_episodes') and best.get('episodes') not in (None,''):
        try: updates['total_episodes']=int(float(best.get('episodes')))
        except Exception: pass
    if season_updates:
        updates['seasons_json']=json.dumps(season_updates,ensure_ascii=False)
        updates['season_covers']=json.dumps([e.get('cover') for e in season_updates if e.get('cover')],ensure_ascii=False)
        if not updates.get('cover') and season_updates:
            first=next((e.get('cover') for e in season_updates if e.get('cover')), '')
            if first and not row.get('cover'): updates['cover']=first
        if not row.get('total_seasons'):
            updates['total_seasons']=len(season_updates)
        if not row.get('episodes'):
            first_ep=next((e.get('episodes') for e in season_updates if e.get('season',1)==1 and e.get('episodes') not in (None,'')),None)
            if first_ep is not None:
                try: updates['episodes']=int(first_ep)
                except Exception: pass
    # Temporadas de anime: mantém a informação textual original e tenta completar a posição atual.
    if category in ('Anime','Desenho') and not row.get('current_season') and best.get('season_number'):
        try: updates['current_season']=int(best.get('season_number'))
        except Exception: pass
    if category in ('Anime','Desenho') and not row.get('total_seasons') and best.get('season_number'):
        try: updates['total_seasons']=int(best.get('season_number'))
        except Exception: pass
    # Guarda a procedência sem criar coluna nova.
    provenance=[]
    try: provenance=json.loads(row.get('history_sources') or '[]') if isinstance(row.get('history_sources'),str) else (row.get('history_sources') or [])
    except Exception: provenance=[]
    if not isinstance(provenance,list): provenance=[]
    prov={'source':best.get('source') or '','id':best.get('id'),'title':best.get('title') or best.get('name') or canonical,'score':best.get('_match_score'),'checked_at':datetime.now().isoformat(timespec='seconds')}
    provenance=[x for x in provenance if not (isinstance(x,dict) and x.get('source')==prov.get('source') and str(x.get('id'))==str(prov.get('id')))]
    provenance.insert(0,prov)
    provenance=provenance[:8]
    updates['history_sources']=json.dumps(provenance,ensure_ascii=False)

    if updates:
        sets=', '.join(f'{k}=?' for k in updates)
        vals=list(updates.values())+[int(row['id'])]
        conn.execute(f'UPDATE titles SET {sets} WHERE id=?',vals); conn.commit()
    conn.close()
    return {
        'id':row.get('id'),'name':name,'changed':bool([k for k in updates if k!='history_sources']),
        'cover':updates.get('cover') or row.get('cover') or '', 'fields':[k for k in updates if k!='history_sources'],
        'matched_title':best.get('title') or best.get('name') or '', 'source':best.get('source') or '',
        'source_id':updates.get('source_id') or row.get('source_id') or '',
        'score':best.get('_match_score'),'diagnostics':diagnostics[-14:]
    }



Gateway.read_body = Gateway.body
Gateway.send_file = legacy.Handler.send_file

def bootstrap():
    init_auth()

if __name__=='__main__':
    bootstrap(); print(f'Cime dos Mundos 5.0 Multiusuário: http://{BIND}:{PORT}'); ThreadingHTTPServer((BIND,PORT),Gateway).serve_forever()
