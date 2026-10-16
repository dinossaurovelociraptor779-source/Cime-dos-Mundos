import ast, base64, hashlib, hmac, json, os, shutil, sqlite3, tempfile, threading, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import security_config

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/"server_v5.py").read_text(encoding="utf-8")
TREE=ast.parse(SOURCE)

def extracted_function(name, namespace):
    node=next(n for n in TREE.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    module=ast.Module(body=[node],type_ignores=[])
    exec(compile(module,str(ROOT/"server_v5.py"),"exec"),namespace)
    return namespace[name]

class T2SecurityRegressions(unittest.TestCase):
    def setUp(self):
        self.secret="ci-test-secret-"+"x"*48
        self.token_user=extracted_function("_stateless_user",{
            "SESSION_TOKEN_SECRET":self.secret,"base64":base64,"hashlib":hashlib,
            "hmac":hmac,"json":json,"datetime":datetime,"timezone":timezone,
            "normalize_email":lambda value:str(value or "").strip().lower(),
            "valid_email":lambda value:bool(__import__("re").match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$",value))
        })
    def token(self,key,role="beta",provider="local",email="owner@example.com",verified=False):
        payload={"uid":7,"email":email,"name":"Owner","role":role,"provider":provider,
                 "email_verified":verified,"exp":int((datetime.now(timezone.utc)+timedelta(hours=1)).timestamp())}
        enc=base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":")).encode()).decode().rstrip("=")
        sig=hmac.new(key.encode(),enc.encode(),hashlib.sha256).hexdigest()
        return "c5."+enc+"."+sig
    def test_token_signed_with_wrong_key_is_rejected(self):
        self.assertIsNone(self.token_user(self.token("wrong-signing-key")))
        # This test also prevents regressions to the old hard-coded signing fallbacks.
        for path in ("api/auth_complete.py", "api/google_oauth_start.py"):
            source = (ROOT / path).read_text(encoding="utf-8").lower()
            self.assertNotIn("fallback", source)
    def test_creator_role_inside_signed_token_is_ignored(self):
        user=self.token_user(self.token(self.secret,role="creator",provider="local"))
        self.assertIsNotNone(user)
        self.assertEqual(user["role"],"beta")
    def test_stateless_token_rejects_invalid_identity_claims(self):
        self.assertIsNone(self.token_user(self.token(self.secret,email="not-an-email")))
        payload={"uid":0,"email":"owner@example.com","name":"Owner","role":"beta","provider":"local",
                 "exp":int((datetime.now(timezone.utc)+timedelta(hours=1)).timestamp())}
        enc=base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":")).encode()).decode().rstrip("=")
        sig=hmac.new(self.secret.encode(),enc.encode(),hashlib.sha256).hexdigest()
        self.assertIsNone(self.token_user("c5."+enc+"."+sig))
    def test_stateless_token_does_not_accept_unknown_provider(self):
        payload={"uid":7,"email":"owner@example.com","name":"Owner","role":"creator","provider":"admin",
                 "exp":int((datetime.now(timezone.utc)+timedelta(hours=1)).timestamp())}
        enc=base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":")).encode()).decode().rstrip("=")
        sig=hmac.new(self.secret.encode(),enc.encode(),hashlib.sha256).hexdigest()
        self.assertIsNone(self.token_user("c5."+enc+"."+sig))
    def test_allowlisted_local_registration_is_always_beta(self):
        with patch.dict(os.environ,{"VERCEL":"1","CIME_CREATOR_EMAILS":"owner@example.com"},clear=False):
            self.assertEqual(security_config.new_account_role("owner@example.com",provider="local",email_verified=True),"beta")
        register=SOURCE[SOURCE.index("if path in ('/api/auth/register'"):SOURCE.index("if path in ('/api/auth/login'")]
        self.assertIn("'beta'",register)
        self.assertNotIn("new_account_role(email",register)
    def test_legacy_migration_runs_for_creator_id_not_equal_to_one(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)/"data"; data.mkdir()
            auth_path=data/"auth.db"; old_db=data/"cime.db"
            with sqlite3.connect(auth_path) as c:
                c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT NOT NULL)")
                c.execute("INSERT INTO users(id,role) VALUES(7,'creator')")
            with sqlite3.connect(old_db) as c:
                c.execute("CREATE TABLE titles (id INTEGER PRIMARY KEY,title TEXT NOT NULL)")
                c.execute("INSERT INTO titles(id,title) VALUES(11,'Legacy title')")
            users_dir=data/"users_data"; media_dir=data/"users_media"
            target=users_dir/"7"/"library.db"; target.parent.mkdir(parents=True)
            with sqlite3.connect(target) as c: c.execute("CREATE TABLE titles (id INTEGER PRIMARY KEY,title TEXT NOT NULL)")
            def auth_db():
                c=sqlite3.connect(auth_path); c.row_factory=sqlite3.Row; return c
            def user_db_path(uid):
                p=users_dir/str(uid); p.mkdir(parents=True,exist_ok=True); return p/"library.db"
            ns={"_tls":threading.local(),"legacy":SimpleNamespace(init_db=lambda:None),
                "DATA":data,"USERS_DIR":users_dir,"USERS_MEDIA":media_dir,"auth_db":auth_db,
                "sqlite3":sqlite3,"shutil":shutil,"user_db_path":user_db_path}
            migrate=extracted_function("ensure_library",ns)
            migrate(7)
            with sqlite3.connect(target) as c: rows=c.execute("SELECT id,title FROM titles").fetchall()
            self.assertEqual(rows,[(11,"Legacy title")])
            self.assertTrue((data/".cime_legacy_migration_done").exists())
            self.assertTrue(old_db.exists())

if __name__=="__main__": unittest.main()
