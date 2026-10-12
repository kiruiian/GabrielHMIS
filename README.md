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

## Scheduling workflows

- Reception, records, or admin staff can book and manage appointments. Clinical staff can view the schedule; checking a patient in adds them to the existing outpatient queue.
- Reception or admin staff can schedule theatre procedures for outpatients or link them to an active inpatient admission. Doctors and nurses can save operative and recovery notes; doctors and admins can complete and lock the record. Completed records can be printed.
- The Renal module records outpatient haemodialysis charts from the hospital's paper form, including pre-, intra-, and post-dialysis data and timed readings. Nurses, doctors, and admins can chart, print, and mark dialysis sessions finished; finished charts remain editable and record who finished the session and when. Doctors can open the patient card's existing laboratory, prescription, radiology, and referral request workflows from the renal patient search. Accounts, admin, reception, and records staff manage SHA authorization number/status. Pending authorization is tracked but does not block clinical charting.
- Renal staff can create and print patient-linked SHA preauthorization request records for manual submission. Accounts, admin, reception, and records staff can record SHA's response; the app does not submit requests to SHA.
