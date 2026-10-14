import os
import unittest
from unittest.mock import patch

import storage_backends


class DurableStorageConfigTests(unittest.TestCase):
    def test_status_never_exposes_secrets(self):
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://user:secret@host/db",
            "BLOB_READ_WRITE_TOKEN": "vercel_blob_secret",
        }, clear=True):
            result = storage_backends.durable_storage_config()
        self.assertEqual(result, {
            "database_configured": True,
            "blob_configured": True,
        })
        self.assertNotIn("secret", repr(result))

    def test_missing_configuration_fails_closed_on_vercel(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            with self.assertRaises(storage_backends.StorageConfigurationError) as ctx:
                storage_backends.require_durable_storage()
        self.assertIn("DATABASE_URL", str(ctx.exception))
        self.assertIn("BLOB_READ_WRITE_TOKEN", str(ctx.exception))

    def test_local_development_does_not_require_cloud_secrets(self):
        with patch.dict(os.environ, {}, clear=True):
            storage_backends.require_durable_storage()

    def test_neon_connection_requires_database_url(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                storage_backends.StorageConfigurationError,
                "DATABASE_URL",
            ):
                storage_backends.connect_neon()


if __name__ == "__main__":
    unittest.main()
