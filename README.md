# Cime dos Mundos 5.0

Cime dos Mundos 5.0 is a Python application with a browser interface for personal libraries, account sessions, groups, memories, recommendations, imports and image recognition. Keep the product version at **5.0**.

## Run locally

Requires Python 3.12. From the repository folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python server_v5.py
```

The app listens on port 8790 by default. `server_v5.py` reads a local `.env` file without replacing variables already set in the shell. Local mode stores data under `CimeDados/`; do not delete that directory if it contains user data.

## Durable storage

The application keeps its existing SQLite databases and media layout. On cloud deployments it loads that data directory from a compressed PostgreSQL snapshot and writes changes back before returning a successful response. A PostgreSQL advisory lock serializes requests across instances. If the database is missing or unreachable, cloud write requests fail with HTTP 503 instead of claiming that data was saved.

The snapshot table is `public.cime_runtime_snapshot`. Setup creates it if needed, enables row-level security, and revokes table permissions from `PUBLIC`. The app uses its PostgreSQL connection role to access the snapshot; it does not expose the snapshot through Supabase's Data API. Account and library separation continues to be enforced by the existing per-account application databases.

Set one of these connection variables in the hosting provider's secret environment settings:

1. `CIME_DATABASE_URL` (recommended explicit override)
2. `SUPABASE_DB_URL` or `SUPABASE_DATABASE_URL`
3. `POSTGRES_URL_NON_POOLING` or `DATABASE_URL_UNPOOLED`
4. `POSTGRES_URL` or `DATABASE_URL`

For Supabase, use a **session-mode** PostgreSQL connection. The app holds a session advisory lock for the full request, so a transaction-mode pooler is not suitable. Use the existing Supabase project if you choose it; this repository does not create a project or migrate data automatically. The current Vercel deployment uses the PostgreSQL variables already configured there. Pointing the app at a different, empty database makes its old snapshot appear absent until the data is migrated.

`GET /api/health` is a process liveness check. `GET /api/persistence/health` is the storage readiness check: it returns HTTP 200 only when PostgreSQL is connected, and HTTP 503 when persistent storage is inactive or unavailable.

## Vercel

The production domain remains `https://cime-dos-mundos.vercel.app`. Keep `CIME_SESSION_SECRET` and the selected PostgreSQL URL as sensitive environment variables in Production and Preview. The Vercel adapter always requires persistent storage, even if `CIME_PERSISTENT_STORE=0` is set. Check `/api/persistence/health` after configuration before relying on writes.

Google OAuth requires `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. In Google Cloud Console, register this JavaScript origin:

```text
https://cime-dos-mundos.vercel.app
```

and this redirect URI:

```text
https://cime-dos-mundos.vercel.app/oauth/google/callback
```

The values must match exactly. Do not commit the client secret. For the Render domain, configure its own authorized origin and redirect URI if Google login is needed there.

## Render

`render.yaml` describes the existing free web-service shape and disables automatic deployments until the required database URL has been added. It does not create a database. Set `DATABASE_URL` to the same PostgreSQL database used by the intended Cime deployment, keep `CIME_PERSISTENT_STORE=1`, then deploy manually and confirm `/api/persistence/health` returns HTTP 200. Do not copy a credential into this file or into GitHub.

## Smart Vision and optional integrations

`GEMINI_API_KEY` is a server-side secret used by the Gemini Smart Vision integration currently under review in PR #25. Setting it on a deployment that does not yet include that integration does not activate Gemini. Keep it out of HTML, browser JavaScript and commits. Without an active provider, the UI must report remote image recognition as unconfigured; local OCR alone does not verify a title. Gemini calls may incur usage charges according to the provider's account settings. `OPENAI_API_KEY` and SMTP variables are optional and remain server-side.

## Configuration reference

`.env.example` lists supported local and hosting variables with blank secret values. Do not place real credentials in that file. `.gitignore` excludes `.env`, local SQLite data and Python caches.