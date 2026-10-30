import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CimeSecurityTests(unittest.TestCase):
    def run_isolated(self, source: str, extra_env: dict[str, str], *, remove=()):
        env = os.environ.copy()
        for key in remove:
            env.pop(key, None)
        env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )

    def test_vercel_fails_closed_without_session_secret(self):
        result = self.run_isolated(
            "import cime_security",
            {"VERCEL": "1", "CIME_DEPLOYMENT": "vercel"},
            remove=("CIME_SESSION_SECRET",),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CIME_SESSION_SECRET", result.stderr)

    def test_vercel_uses_explicit_secret(self):
        expected = "a7" * 48
        result = self.run_isolated(
            "import cime_security; print(cime_security.SESSION_SECRET)",
            {"VERCEL": "1", "CIME_SESSION_SECRET": expected},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), expected)

    def test_google_client_id_matches_frontend_and_backend(self):
        frontend = (ROOT / "index.html").read_text(encoding="utf-8")
        backend = (ROOT / "server_v5.py").read_text(encoding="utf-8")
        front_match = re.search(
            r"const GOOGLE_CLIENT_ID=['\"]([^'\"]+)['\"]", frontend
        )
        back_match = re.search(
            r"GOOGLE_DEFAULT_CLIENT_ID=['\"]([^'\"]+)['\"]", backend
        )
        self.assertIsNotNone(front_match, "Client ID não encontrado no frontend")
        self.assertIsNotNone(back_match, "Client ID não encontrado no backend")
        self.assertEqual(front_match.group(1), back_match.group(1))

    def test_sessions_and_user_libraries_are_isolated(self):
        with tempfile.TemporaryDirectory(prefix="cime-security-") as data_dir:
            secret = "b9" * 48
            source = r'''
import os
import sqlite3
from datetime import datetime, timezone
import server_v5 as app

app.init_auth()
now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
conn = app.auth_db()
for uid in (101, 202):
    conn.execute(
        """INSERT INTO users
           (id,email,password_hash,display_name,role,provider,provider_subject,
            created_at,updated_at,last_login_at)
           VALUES(?,?,NULL,?,'beta','local','',?,?,?)""",
        (uid, f"user{uid}@example.test", f"User {uid}", now, now, now),
    )
conn.commit()
conn.close()

token_101 = app.token_make(101)
token_202 = app.token_make(202)

class FakeHandler:
    def __init__(self, token):
        self.path = "/api/bootstrap"
        self.headers = {"Authorization": "Bearer " + token}

assert app.get_user(FakeHandler(token_101))["id"] == 101
assert app.get_user(FakeHandler(token_202))["id"] == 202

# Mesmo token corretamente assinado não autentica sem registro ativo em sessions.
unregistered = app._stateless_token(999, "fake@example.test", "Forged", "creator", "google")
assert app.get_user(FakeHandler(unregistered)) is None

# As bibliotecas são arquivos SQLite distintos por ID de usuário.
for uid in (101, 202):
    app._tls.user_id = uid
    db = app.user_db()
    db.execute("CREATE TABLE isolation_probe (value TEXT NOT NULL)")
    db.execute("INSERT INTO isolation_probe(value) VALUES (?)", (f"private-{uid}",))
    db.commit()
    db.close()

for uid in (101, 202):
    app._tls.user_id = uid
    db = app.user_db()
    values = [row[0] for row in db.execute("SELECT value FROM isolation_probe")]
    db.close()
    assert values == [f"private-{uid}"], (uid, values)

app._tls.__dict__.pop("user_id", None)
print("SESSION_AND_LIBRARY_ISOLATION_OK")
'''
            result = self.run_isolated(
                source,
                {
                    "CIME_DATA_DIR": data_dir,
                    "CIME_SESSION_SECRET": secret,
                    "CIME_DEPLOYMENT": "test",
                    "CIME_PORT": "8790",
                    "CIME_BIND": "127.0.0.1",
                },
                remove=("VERCEL",),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SESSION_AND_LIBRARY_ISOLATION_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
