"""Single source of truth for Cime's session and OAuth-state signing key."""
from __future__ import annotations

import os
import secrets


def load_session_secret() -> str:
    value = str(os.getenv("CIME_SESSION_SECRET", "")).strip()
    is_vercel = (
        str(os.getenv("VERCEL", "")).strip() == "1"
        or str(os.getenv("CIME_DEPLOYMENT", "")).strip().lower() == "vercel"
    )

    if not value:
        if is_vercel:
            raise RuntimeError(
                "CIME_SESSION_SECRET não está configurada. "
                "Defina uma chave aleatória e secreta nos ambientes da Vercel."
            )
        # Local development only: create a process-local key instead of using
        # a public, hardcoded fallback. Restarting the local server invalidates
        # local sessions unless the developer defines CIME_SESSION_SECRET in .env.
        value = secrets.token_hex(48)
        os.environ["CIME_SESSION_SECRET"] = value

    if len(value) < 64:
        raise RuntimeError(
            "CIME_SESSION_SECRET precisa ter pelo menos 64 caracteres aleatórios."
        )
    return value


SESSION_SECRET = load_session_secret()
