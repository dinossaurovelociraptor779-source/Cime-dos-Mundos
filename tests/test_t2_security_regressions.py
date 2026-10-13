import base64, hashlib, hmac, json, os, sqlite3, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault("CIME_SESSION_SECRET", "ci-test-secret-" + "x"*48)
import server_v5

class T2SecurityRegressions(unittest.TestCase):
    def _token(self,key,role="beta",provider="local",email="owner@example.com",verified=False):
        payload={"uid":7,"email":email,"name":"Owner","role":role,"provider":provider,"email_verified":verified,
                 "exp":int((datetime.now(timezone.utc)+timedelta(hours=1)).timestamp())}
        enc=base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":")).encode()).decode().rstrip("=")
        sig=hmac.new(key.encode(),enc.encode(),hashlib.sha256).hexdigest()
        return "c5."+enc+"."+sig
    def test_token_signed_with_wrong_key_is_rejected(self):
        token=self._token("definitely-not-the-configured-key")
        self.assertIsNone(server_v5._stateless_user(token))
    def test_creator_role_inside_signed_token_is_ignored(self):
        user=server_v5._stateless_user(self._token(server_v5.SESSION_TOKEN_SECRET,role="creator"))
        self.assertIsNotNone(user)
        self.assertEqual(user["role"],"beta")
    def test_allowlisted_local_registration_is_always_beta(self):
        with patch.dict(os.environ,{"VERCEL":"1","CIME_CREATOR_EMAILS":"owner@example.com"},clear=False):
            self.assertEqual(server_v5.new_account_role("owner@example.com",provider="local",email_verified=True),"beta")
        source=(Path(__file__).resolve().parents[1]/"server_v5.py").read_text(encoding="utf-8")
        register=source[source.index("if path in ('/api/auth/register'"):source.index("if path in ('/api/auth/login'")]
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
            patches=[patch.object(server_v5,"DATA",data),patch.object(server_v5,"USERS_DIR",users_dir),
                     patch.object(server_v5,"USERS_MEDIA",media_dir),patch.object(server_v5,"AUTH_DB",auth_path),
                     patch.object(server_v5.legacy,"init_db",lambda:None)]
            try:
                for p in patches:p.start()
                server_v5.ensure_library(7)
            finally:
                for p in reversed(patches):p.stop()
            with sqlite3.connect(target) as c: rows=c.execute("SELECT id,title FROM titles").fetchall()
            self.assertEqual(rows,[(11,"Legacy title")])
            self.assertTrue((data/".cime_legacy_migration_done").exists())
            self.assertTrue(old_db.exists())

if __name__ == "__main__": unittest.main()
