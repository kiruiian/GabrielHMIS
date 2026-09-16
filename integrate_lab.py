from pathlib import Path

APP = Path('app.py')
MARKER = "# ====================== RADIOLOGY ======================"
IMPORT = "\nfrom lab_module import register_lab_module\n"
REGISTER = "\n# ====================== LABORATORY MODULE ======================\nregister_lab_module(app, db, Patient, Visit, ServiceRequest, AuditLog)\n"

text = APP.read_text(encoding='utf-8')

if 'from lab_module import register_lab_module' not in text:
    anchor = 'from werkzeug.utils import secure_filename\n'
    if anchor not in text:
        raise SystemExit('Could not find the import anchor in app.py')
    text = text.replace(anchor, anchor + IMPORT, 1)

# Add laboratory role so admin can create lab staff accounts.
old_roles = "VALID_ROLES = {'receptionist', 'doctor', 'nurse', 'triage', 'pharmacist', 'radiographer', 'admin', 'records', 'accounts', 'hr'}"
new_roles = "VALID_ROLES = {'receptionist', 'doctor', 'nurse', 'triage', 'pharmacist', 'radiographer', 'lab', 'admin', 'records', 'accounts', 'hr'}"
if old_roles in text:
    text = text.replace(old_roles, new_roles, 1)

if 'register_lab_module(app, db, Patient, Visit, ServiceRequest, AuditLog)' not in text:
    if MARKER not in text:
        raise SystemExit('Could not find the module registration anchor in app.py')
    text = text.replace(MARKER, REGISTER + '\n' + MARKER, 1)

APP.write_text(text, encoding='utf-8')
print('Laboratory module integration applied successfully.')
