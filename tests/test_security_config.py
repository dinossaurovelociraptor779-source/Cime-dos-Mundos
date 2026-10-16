import os
import unittest
from unittest.mock import patch
import security_config

class SecurityConfigTests(unittest.TestCase):
    def test_production_requires_explicit_secret(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CIME_SESSION_SECRET ausente"): security_config.load_session_secret()
    def test_production_rejects_short_secret(self):
        with patch.dict(os.environ, {"VERCEL": "1", "CIME_SESSION_SECRET": "too-short"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "pelo menos 32"): security_config.load_session_secret()
    def test_production_accepts_configured_secret(self):
        secret="x"*48
        with patch.dict(os.environ, {"VERCEL":"1","CIME_SESSION_SECRET":secret}, clear=True):
            self.assertEqual(security_config.load_session_secret(),secret)
    def test_only_verified_allowlisted_google_email_is_creator(self):
        with patch.dict(os.environ, {"VERCEL":"1","CIME_CREATOR_EMAILS":"owner@example.com"}, clear=True):
            self.assertEqual(security_config.new_account_role("owner@example.com",provider="google",email_verified=True),"creator")
            self.assertEqual(security_config.new_account_role("owner@example.com",provider="google",email_verified=False),"beta")
            self.assertEqual(security_config.new_account_role("owner@example.com",provider="local",email_verified=True),"beta")
            self.assertEqual(security_config.new_account_role("other@example.com",provider="google",email_verified=True),"beta")
    def test_first_local_account_is_not_creator(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(security_config.new_account_role("dev@example.com",provider="local",first_user=True),"beta")

if __name__ == "__main__": unittest.main()
