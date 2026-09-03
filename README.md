# St Gabriel Hospital HMIS

## Run locally

1. Create a virtual environment and install the pinned dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`. Generate a secret with:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Put that output in `SECRET_KEY`. Keep `SESSION_COOKIE_SECURE=false` only while using local HTTP.

3. Create the initial administrator. This deliberately requires an explicit action; there is no default account.

   ```powershell
   .\.venv\Scripts\flask.exe --app app create-admin
   ```

4. Start the application with `run.bat` and open `http://127.0.0.1:5000`.

## Before deployment

- Set `APP_ENV=production`, a unique `SECRET_KEY`, and `SESSION_COOKIE_SECURE=true`.
- Run behind HTTPS and a production WSGI server; do not use Flask's development server.
- Use PostgreSQL and managed, encrypted backups for multiple concurrent staff members.
- Never commit `instance/`, database files, backups, or `.env` files.

## Current safeguards

- State-changing requests use CSRF tokens.
- Staff roles are enforced server-side.
- Queue claims use the authenticated doctor's account.
- Patient and clinical changes are written to an audit log.
