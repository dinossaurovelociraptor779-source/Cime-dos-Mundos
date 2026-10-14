"""Configuration and connection primitives for the Cime 5.0 durable-storage migration.

This module is intentionally not wired into request handlers yet. The current
application uses SQLite-specific SQL throughout server_v5.py/server_legacy.py;
switching connections without a complete schema/query migration would risk data
loss. Use these helpers for the migration and connection checks first.
"""
from __future__ import annotations

import os
from typing import Any


class StorageConfigurationError(RuntimeError):
    """Raised when durable storage is required but not configured."""


def durable_storage_config() -> dict[str, bool]:
    """Return readiness flags only; never return secrets or connection strings."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    blob_token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    return {
        "database_configured": bool(database_url),
        "blob_configured": bool(blob_token),
    }


def require_durable_storage() -> None:
    """Fail closed in Vercel if required storage credentials are missing."""
    if os.getenv("VERCEL") != "1" and os.getenv("CIME_DEPLOYMENT") != "vercel":
        return
    config = durable_storage_config()
    missing = [
        name
        for name, present in (
            ("DATABASE_URL", config["database_configured"]),
            ("BLOB_READ_WRITE_TOKEN", config["blob_configured"]),
        )
        if not present
    ]
    if missing:
        raise StorageConfigurationError(
            "Armazenamento persistente não configurado: " + ", ".join(missing)
        )


def connect_neon(*, connect_timeout: int = 8) -> Any:
    """Open a short-timeout Neon/PostgreSQL connection using psycopg 3."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise StorageConfigurationError("A variável DATABASE_URL não está configurada.")
    try:
        import psycopg
    except ImportError as exc:
        raise StorageConfigurationError(
            "Dependência psycopg ausente; instale requirements.txt."
        ) from exc
    return psycopg.connect(database_url, connect_timeout=connect_timeout)


def check_neon_connection() -> bool:
    """Check connectivity without reading or changing user data."""
    conn = connect_neon()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
            return bool(row and row[0] == 1)
    finally:
        conn.close()
