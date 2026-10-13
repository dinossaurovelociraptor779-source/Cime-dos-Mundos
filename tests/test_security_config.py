import importlib
import os
import unittest
from unittest.mock import patch

import security_config


class SecurityConfigTests(unittest.TestCase):
    def test_production_requires_explicit_secret(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CIME_SESSION_SECRET ausente"):
                security_config.load_session_secret()

    def test_production_rejects_short_secret(self):
        with patch.dict(os.environ, {"VERCEL": "1", "CIME_SESSION_SECRET": "too-short"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "pelo menos 32"):
                security_config.load_session_secret()

    def test_production_accepts_configured_secret(self):
        secret = "x" * 48
        with patch.dict(os.environ, {"VERCEL": "1", "CIME_SESSION_SECRET": secret}, clear=True):
            self.assertEqual(security_config.load_session_secret(), secret)

    def test_creator_requires_allowlisted_email_in_production(self):
        env = {
            "VERCEL": "1",
            "CIME_CREATOR_EMAILS": "owner@example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(security_config.new_account_role("owner@example.com", first_user=False), "creator")
            self.assertEqual(security_config.new_account_role("other@example.com", first_user=True), "beta")

    def test_local_first_user_remains_creator_for_development(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(security_config.new_account_role("dev@example.com", first_user=True), "creator")
            self.assertEqual(security_config.new_account_role("dev@example.com", first_user=False), "beta")


if __name__ == "__main__":
    unittest.main()
