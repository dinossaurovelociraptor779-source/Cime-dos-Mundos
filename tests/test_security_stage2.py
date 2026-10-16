"""Regression tests for Etapa 2 account/session security.

Run locally with: python -m unittest discover -s tests -v
"""
import os
import tempfile
import unittest

_TEST_DATA = tempfile.mkdtemp(prefix="cime-security-tests-")
os.environ["CIME_DATA_DIR"] = _TEST_DATA
os.environ["CIME_SESSION_SECRET"] = "test-only-secret-do-not-use-in-production"
os.environ.pop("CIME_DEPLOYMENT", None)

import server_v5


class SessionSecurityTests(unittest.TestCase):
    def test_password_hash_is_verified_and_not_plaintext(self):
        password = "Teste-forte-2026!"
        encoded = server_v5.pw_hash(password)
        self.assertNotEqual(encoded, password)
        self.assertTrue(server_v5.pw_ok(password, encoded))
        self.assertFalse(server_v5.pw_ok("senha-errada", encoded))

    def test_signed_session_token_round_trip(self):
        token = server_v5._stateless_token(
            123, "teste@example.com", "Conta de teste", "beta", "local"
        )
        user = server_v5._stateless_user(token)
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], 123)
        self.assertEqual(user["email"], "teste@example.com")
        self.assertEqual(user["role"], "beta")

    def test_tampered_session_token_is_rejected(self):
        token = server_v5._stateless_token(
            123, "teste@example.com", "Conta de teste", "beta", "local"
        )
        parts = token.split(".")
        parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
        self.assertIsNone(server_v5._stateless_user(".".join(parts)))

    def test_each_user_has_a_different_library_database_path(self):
        self.assertNotEqual(
            server_v5.user_db_path(101),
            server_v5.user_db_path(202),
        )

    def test_account_creation_uses_serialized_role_assignment(self):
        source_path = os.path.join(os.path.dirname(server_v5.__file__), "server_v5.py")
        with open(source_path, encoding="utf-8") as source_file:
            source = source_file.read()
        self.assertGreaterEqual(source.count("BEGIN IMMEDIATE"), 4)


if __name__ == "__main__":
    unittest.main()
