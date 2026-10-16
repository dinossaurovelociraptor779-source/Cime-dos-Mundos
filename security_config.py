"""Centralized security configuration for Cime dos Mundos 5.0.

Production must provide a stable, high-entropy CIME_SESSION_SECRET (32+ chars).
Local development may generate an ephemeral secret; sessions will reset on restart.
"""
import os
import secrets


def is_production():
    return os.getenv("VERCEL") == "1" or os.getenv("CIME_DEPLOYMENT", "").lower() == "vercel"


def load_session_secret():
    configured = os.getenv("CIME_SESSION_SECRET", "").strip()
    if configured:
        if len(configured) < 32:
            raise RuntimeError("CIME_SESSION_SECRET precisa ter pelo menos 32 caracteres.")
        return configured
    if is_production():
        raise RuntimeError("CIME_SESSION_SECRET ausente na produção; configure uma chave aleatória com pelo menos 32 caracteres.")
    return secrets.token_urlsafe(48)


def new_account_role(email, provider="", email_verified=False, first_user=False):
    """Only verified Google identities on the explicit allowlist can be creators."""
    normalized = str(email or "").strip().lower()
    creators = {
        item.strip().lower()
        for item in os.getenv("CIME_CREATOR_EMAILS", "").split(",")
        if item.strip()
    }
    if str(provider or "").lower() == "google" and email_verified and normalized and normalized in creators:
        return "creator"
    return "beta"
