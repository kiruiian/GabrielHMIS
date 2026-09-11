import os
import dotenv
import secrets
import math
from datetime import datetime, timedelta
from functools import wraps

import click
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


app = Flask(__name__, instance_relative_config=True)
os.makedirs(app.instance_path, exist_ok=True)

is_production = os.getenv('APP_ENV', 'development').lower() == 'production'
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    if is_production:
        raise RuntimeError('SECRET_KEY must be set when APP_ENV=production.')
    secret_key = secrets.token_urlsafe(32)

app.config['SECRET_KEY'] = secret_key
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL', f"sqlite:///{os.path.join(app.instance_path, 'hmis.db')}"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = env_flag('SESSION_COOKIE_SECURE', is_production)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

db = SQLAlchemy(app)

VALID_ROLES = {'receptionist', 'doctor', 'nurse', 'triage', 'pharmacist', 'admin', 'records', 'accounts', 'hr'}
PATIENT_ACCESS_ROLES = {'receptionist', 'doctor', 'nurse', 'triage', 'records'}
REGISTRATION_ROLES = {'receptionist', 'records'}
VISIT_MANAGEMENT_ROLES = {'receptionist', 'records'}
QUEUE_ASSIGNMENT_ROLES = {'receptionist', 'records'}
CLINICAL_READ_ROLES = {'doctor', 'nurse', 'triage', 'records', 'pharmacist'}
INVOICE_VIEW_ROLES = {'accounts', 'admin', 'receptionist', 'records'}
VITALS_ENTRY_ROLES = {'nurse', 'triage'}
NOTE_ENTRY_ROLES = {'doctor', 'nurse'}
SERVICE_REQUEST_ROLES = {'doctor'}
PHARMACY_ROLES = {'pharmacist'}
PAYMENT_METHODS = {'SHA', 'SHA FFS', 'CASH PAYER', 'BROWNS PLANTATIONS', 'BRITAM', 'JUBILEE'}
CONSULTATION_FEES = {'New Visit': 1000, 'Revisit': 500}
ACTIVE_QUEUE_STATUSES = {'queued', 'in_progress'}

def get_queue_identifier(patient_id):
    latest_request = ServiceRequest.query.filter_by(patient_id=patient_id).order_by(ServiceRequest.requested_at.desc()).first()
    if latest_request and (latest_request.status or 'Pending').lower() not in {'completed', 'cancelled', 'closed'}:
        return {
            'label': latest_request.service_type or 'Service',
            'key': (latest_request.service_type or 'service').lower(),
            'detail': 'Service requested'
        }

    latest_queue = QueueEntry.query.filter_by(patient_id=patient_id).order_by(QueueEntry.queued_at.desc()).first()
    labels = {'queued': 'Waiting', 'with_nurse': 'Nurse', 'with_doctor': 'Doctor', 'in_progress': 'Doctor'}
    status = latest_queue.status if latest_queue else 'queued'
    return {'label': labels.get(status, 'Waiting'), 'key': status, 'detail': 'Current queue stage'}


def determine_visit_type(patient):
    """Classify the visit based on whether this patient has a previous visit record."""
    previous_visits = Visit.query.filter_by(patient_id=patient.id).count()
    return 'Revisit' if patient.visit_type == 'Revisit' or previous_visits > 0 else 'New Visit'

# ====================== USER MODEL ======================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(100))
    role = db.Column(db.String(20), default='receptionist')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# ====================== PATIENT ======================
class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    national_id = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    other_names = db.Column(db.String(100))
    age = db.Column(db.Integer)
    gender = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    patient_image = db.Column(db.String(255))
    visit_type = db.Column(db.String(20), default='New Visit')
    registration_date = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def full_name(self):
        if self.first_name:
            return f"{self.first_name} {self.other_names or ''}".strip()
        return self.name or ''

    @property
    def display_first_name(self):
        if self.first_name:
            return self.first_name
        if self.name:
            return self.name.split(' ')[0]
        return ''

    @property
    def display_other_names(self):
        if self.other_names:
            return self.other_names
        if self.name:
            parts = self.name.split(' ')
            return ' '.join(parts[1:]) if len(parts) > 1 else ''
        return ''

class Visit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    visit_type = db.Column(db.String(20))
    payment_method = db.Column(db.String(80))
    invoice_number = db.Column(db.String(80))
    consultation_amount = db.Column(db.Numeric(10, 2))
    status = db.Column(db.String(30), default='registered')  # registered → with_nurse → with_doctor → completed
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    patient = db.relationship('Patient', backref=db.backref('visits', lazy=True))


class InvoicePayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    visit_id = db.Column(db.Integer, db.ForeignKey('visit.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_method = db.Column(db.String(80), nullable=False)
    received_by = db.Column(db.String(100), nullable=False)
    received_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    notes = db.Column(db.String(255))

    visit = db.relationship('Visit', backref=db.backref('payments', lazy=True, order_by=received_at.desc()))


class QueueEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    visit_id = db.Column(db.Integer, db.ForeignKey('visit.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    doctor = db.Column(db.String(100))
    status = db.Column(db.String(30), default='queued')  # queued → with_nurse → with_doctor → completed
    queued_at = db.Column(db.DateTime, default=datetime.utcnow)

    visit = db.relationship('Visit', backref=db.backref('queue_entry', uselist=False))
    patient = db.relationship('Patient', backref=db.backref('queue_entries', lazy=True))

# ====================== NEW MODELS FOR PATIENT CARD ======================

class VitalSigns(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    bp = db.Column(db.String(20))                    # e.g. "120/80"
    temperature = db.Column(db.Float)
    pulse = db.Column(db.Integer)
    respiration = db.Column(db.Integer)
    pain_score = db.Column(db.Integer)
    weight = db.Column(db.Float)
    height = db.Column(db.Float)
    oxygen_sat = db.Column(db.Float)                 # SpO2
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('vitals', lazy=True, order_by=timestamp.desc()))


class ClinicalNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    note = db.Column(db.Text, nullable=False)
    doctor = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('clinical_notes', lazy=True, order_by=timestamp.desc()))
class Consultation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    visit_id = db.Column(db.Integer, db.ForeignKey('visit.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    
    history = db.Column(db.Text)               # History of Presenting Illness
    examination = db.Column(db.Text)           # Physical examination findings
    diagnosis = db.Column(db.Text)             # Diagnosis
    plan = db.Column(db.Text)                  # Management plan
    
    doctor_name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    visit = db.relationship('Visit', backref=db.backref('consultation', uselist=False))
    patient = db.relationship('Patient', backref=db.backref('consultations', lazy=True))


class ServiceRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    service_type = db.Column(db.String(50))      # Lab, Pharmacy, Imaging, Referral
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='Pending')
    requested_by = db.Column(db.String(100))
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('service_requests', lazy=True, order_by=requested_at.desc()))


class Prescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey('visit.id'))
    item_type = db.Column(db.String(30), default='Medicine', nullable=False)
    medication = db.Column(db.String(120), nullable=False)
    identifier = db.Column(db.String(80))
    needs_refill = db.Column(db.Boolean, default=False)
    rx_take = db.Column(db.Integer, default=1, nullable=False)
    strength = db.Column(db.String(60))
    dosage = db.Column(db.String(80), nullable=False)
    form = db.Column(db.String(50))
    interval = db.Column(db.String(30))
    frequency = db.Column(db.String(80), nullable=False)
    frequency_unit = db.Column(db.String(20), default='Hours')
    route = db.Column(db.String(50), default='Oral')
    indication = db.Column(db.String(160))
    duration = db.Column(db.String(80), nullable=False)
    quantity = db.Column(db.String(40))
    instructions = db.Column(db.Text)
    start_date = db.Column(db.Date, default=datetime.utcnow)
    end_date = db.Column(db.Date)
    as_needed = db.Column(db.Boolean, default=False)
    take_as_prescribed = db.Column(db.Boolean, default=True)
    prescribed_by = db.Column(db.String(100), nullable=False)
    prescribed_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='prescribed')
    dispensed_by = db.Column(db.String(100))
    dispensed_at = db.Column(db.DateTime)
    dispensed_units = db.Column(db.Integer)
    unit_price = db.Column(db.Numeric(10, 2))
    pharmacy_amount = db.Column(db.Numeric(10, 2))

    patient = db.relationship('Patient', backref=db.backref('prescriptions', lazy=True, order_by=prescribed_at.desc()))
    visit = db.relationship('Visit', backref=db.backref('prescriptions', lazy=True))


class MedicineStock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    medication = db.Column(db.String(120), unique=True, nullable=False)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    reorder_level = db.Column(db.Integer, default=10, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(50), nullable=False)
    target_id = db.Column(db.String(80), nullable=False)
    details = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    actor = db.relationship('User')

# ====================== AUTH ======================
def login_required(roles=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in first.', 'warning')
                return redirect(url_for('login'))
            if roles:
                allowed_roles = {roles} if isinstance(roles, str) else set(roles)
                allowed_roles.add('admin')
                if session.get('role') not in allowed_roles:
                    flash('Access denied for your role.', 'danger')
                    return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': csrf_token}


@app.before_request
def validate_csrf_token():
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return None

    if request.endpoint == 'login' and request.method == 'POST':
        return None

    supplied_token = request.headers.get('X-CSRF-Token') if request.is_json else request.form.get('csrf_token')
    expected_token = session.get('_csrf_token')
    if expected_token and supplied_token and secrets.compare_digest(expected_token, supplied_token):
        return None

    if request.is_json:
        return jsonify({'error': 'Invalid or missing CSRF token.'}), 400
    flash('Your form expired or could not be verified. Please try again.', 'danger')
    return redirect(request.referrer or url_for('login'))


def audit(action, target_type, target_id, details=None):
    db.session.add(AuditLog(
        actor_user_id=session.get('user_id'),
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=details,
    ))


def valid_password(password):
    return len(password) >= 12


def parse_optional_number(raw_value, label, minimum=None, maximum=None, integer=False):
    raw_value = (raw_value or '').strip()
    if not raw_value:
        return None
    try:
        value = int(raw_value) if integer else float(raw_value)
    except ValueError as error:
        raise ValueError(f'{label} must be a number.') from error
    if not math.isfinite(value) or (minimum is not None and value < minimum) or (
        maximum is not None and value > maximum
    ):
        raise ValueError(f'{label} must be between {minimum} and {maximum}.')
    return value


def parse_optional_date(raw_value):
    raw_value = (raw_value or '').strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, '%Y-%m-%d').date()
    except ValueError as error:
        raise ValueError('Please enter a valid prescription date.') from error

# ====================== DB SETUP ======================
def ensure_schema():
    with app.app_context():
        db.create_all()
        visit_columns = {
            row[1] for row in db.session.execute(text('PRAGMA table_info(visit)'))
        }
        if 'status' not in visit_columns:
            db.session.execute(text(
                "ALTER TABLE visit ADD COLUMN status VARCHAR(30) DEFAULT 'registered'"
            ))
        if 'completed_at' not in visit_columns:
            db.session.execute(text('ALTER TABLE visit ADD COLUMN completed_at DATETIME'))
        if 'consultation_amount' not in visit_columns:
            db.session.execute(text('ALTER TABLE visit ADD COLUMN consultation_amount NUMERIC(10, 2)'))
        visits_with_old_numbers = db.session.execute(text(
            "SELECT id FROM visit WHERE invoice_number IS NULL "
            "OR invoice_number = '' OR invoice_number NOT LIKE 'INV-%'"
        )).scalars().all()
        for visit_id in visits_with_old_numbers:
            db.session.execute(
                text('UPDATE visit SET invoice_number = :invoice_number WHERE id = :visit_id'),
                {'invoice_number': f'INV-{visit_id:06d}', 'visit_id': visit_id}
            )
        vital_columns = {
            row[1] for row in db.session.execute(text('PRAGMA table_info(vital_signs)'))
        }
        if 'pain_score' not in vital_columns:
            db.session.execute(text('ALTER TABLE vital_signs ADD COLUMN pain_score INTEGER'))
        prescription_columns = {
            row[1] for row in db.session.execute(text('PRAGMA table_info(prescription)'))
        }
        prescription_migrations = {
            'item_type': "ALTER TABLE prescription ADD COLUMN item_type VARCHAR(30) DEFAULT 'Medicine'",
            'identifier': 'ALTER TABLE prescription ADD COLUMN identifier VARCHAR(80)',
            'needs_refill': 'ALTER TABLE prescription ADD COLUMN needs_refill BOOLEAN DEFAULT 0',
            'rx_take': 'ALTER TABLE prescription ADD COLUMN rx_take INTEGER DEFAULT 1',
            'form': 'ALTER TABLE prescription ADD COLUMN form VARCHAR(50)',
            'interval': 'ALTER TABLE prescription ADD COLUMN interval VARCHAR(30)',
            'frequency_unit': "ALTER TABLE prescription ADD COLUMN frequency_unit VARCHAR(20) DEFAULT 'Hours'",
            'route': "ALTER TABLE prescription ADD COLUMN route VARCHAR(50) DEFAULT 'Oral'",
            'indication': 'ALTER TABLE prescription ADD COLUMN indication VARCHAR(160)',
            'start_date': 'ALTER TABLE prescription ADD COLUMN start_date DATE',
            'end_date': 'ALTER TABLE prescription ADD COLUMN end_date DATE',
            'as_needed': 'ALTER TABLE prescription ADD COLUMN as_needed BOOLEAN DEFAULT 0',
            'take_as_prescribed': 'ALTER TABLE prescription ADD COLUMN take_as_prescribed BOOLEAN DEFAULT 1',
            'dispensed_by': 'ALTER TABLE prescription ADD COLUMN dispensed_by VARCHAR(100)',
            'dispensed_at': 'ALTER TABLE prescription ADD COLUMN dispensed_at DATETIME',
            'visit_id': 'ALTER TABLE prescription ADD COLUMN visit_id INTEGER',
            'dispensed_units': 'ALTER TABLE prescription ADD COLUMN dispensed_units INTEGER',
            'unit_price': 'ALTER TABLE prescription ADD COLUMN unit_price NUMERIC(10, 2)',
            'pharmacy_amount': 'ALTER TABLE prescription ADD COLUMN pharmacy_amount NUMERIC(10, 2)',
        }
        for column, migration in prescription_migrations.items():
            if column not in prescription_columns:
                db.session.execute(text(migration))
        db.session.execute(text(
            "UPDATE prescription SET visit_id = ("
            "SELECT visit.id FROM visit "
            "WHERE visit.patient_id = prescription.patient_id "
            "AND visit.started_at <= prescription.prescribed_at "
            "ORDER BY visit.started_at DESC LIMIT 1) "
            "WHERE prescription.visit_id IS NULL"
        ))
        db.session.commit()

with app.app_context():
    ensure_schema()


@app.cli.command('create-admin')
@click.option('--username', prompt=True, help='Unique username for the administrator.')
@click.option('--full-name', prompt=True, help='Administrator display name.')
@click.password_option(confirmation_prompt=True)
def create_admin(username, full_name, password):
    """Create the first administrator without embedding credentials in source code."""
    username = username.strip().lower()
    full_name = full_name.strip()
    if not username or not full_name:
        raise click.UsageError('Username and full name are required.')
    if not valid_password(password):
        raise click.UsageError('Password must be at least 12 characters long.')
    if User.query.filter_by(username=username).first():
        raise click.UsageError('That username already exists.')

    admin = User(username=username, full_name=full_name, role='admin')
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    click.echo(f'Administrator {username} created.')

# ====================== LOGIN ======================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session.clear()
            session.permanent = True
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            session['full_name'] = user.full_name
            flash(f'Welcome, {user.full_name}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'danger')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
@login_required()
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/admin/register-staff', methods=['GET', 'POST'])
@login_required(roles='admin')
def register_staff():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        full_name = request.form.get('full_name', '').strip()
        role = request.form.get('role')
        password = request.form.get('password', '').strip()

        if not username or not full_name or not password:
            flash('All fields are required.', 'danger')
            return redirect(url_for('register_staff'))
        if role not in VALID_ROLES:
            flash('Please choose a valid staff role.', 'danger')
            return redirect(url_for('register_staff'))
        if not valid_password(password):
            flash('Password must be at least 12 characters long.', 'danger')
            return redirect(url_for('register_staff'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register_staff'))

        new_user = User(username=username, full_name=full_name, role=role)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.flush()
        audit('staff_created', 'user', new_user.id, f'role={role}')
        db.session.commit()

        flash(f'Staff "{full_name}" registered successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('register_staff.html')

# ====================== MAIN ROUTES ======================
@app.route('/')
@login_required()
def dashboard():
    return render_template('dashboard.html')

@app.route('/patient/<national_id>')
@login_required(roles=PATIENT_ACCESS_ROLES)
def patient_details(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    visit = Visit.query.filter_by(patient_id=patient.id).order_by(Visit.started_at.desc()).first()
    queue_identifier = get_queue_identifier(patient.id)
    return render_template('patient.html', patient=patient, last_visit=visit, queue_identifier=queue_identifier)

@app.route('/patient/<national_id>/records')
@login_required(roles=PATIENT_ACCESS_ROLES)
def patient_records(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    visits = Visit.query.filter_by(patient_id=patient.id).order_by(Visit.started_at.desc()).all()
    return render_template('patient_records.html', patient=patient, visits=visits)
@app.route('/patient/<national_id>/card', methods=['GET', 'POST'])
@login_required(roles=CLINICAL_READ_ROLES)
def patient_card(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()

    if request.method == 'POST':
        action = request.form.get('action')
        user_role = session.get('role')

        if action == 'vitals':
            if user_role not in VITALS_ENTRY_ROLES and user_role != 'admin':
                flash('Only authorized medical staff can record vitals.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            try:
                temperature = parse_optional_number(request.form.get('temperature'), 'Temperature', 30, 45)
                pulse = parse_optional_number(request.form.get('pulse'), 'Pulse', 20, 250, integer=True)
                respiration = parse_optional_number(request.form.get('respiration'), 'Respiration', 4, 80, integer=True)
                pain_score = parse_optional_number(request.form.get('pain_score'), 'Pain score', 1, 10, integer=True)
                weight = parse_optional_number(request.form.get('weight'), 'Weight', 0.5, 500)
                height = parse_optional_number(request.form.get('height'), 'Height', 20, 300)
                oxygen_sat = parse_optional_number(request.form.get('oxygen_sat'), 'Oxygen saturation', 0, 100)
            except ValueError as error:
                flash(str(error), 'danger')
                return redirect(url_for('patient_card', national_id=national_id))

            new_vital = VitalSigns(
                patient_id=patient.id,
                bp=request.form.get('bp', '').strip()[:20] or None,
                temperature=temperature,
                pulse=pulse,
                respiration=respiration,
                pain_score=pain_score,
                weight=weight,
                height=height,
                oxygen_sat=oxygen_sat,
                notes=request.form.get('vital_notes', '').strip() or None,
                recorded_by=session.get('full_name', 'Staff')
            )
            db.session.add(new_vital)
            db.session.flush()
            audit('vitals_recorded', 'vital_signs', new_vital.id, f'patient_id={patient.id}')
            flash('Vitals recorded successfully!', 'success')

        elif action == 'note':
            if user_role not in NOTE_ENTRY_ROLES and user_role != 'admin':
                flash('Only medical staff can write clinical notes.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            soap_sections = [
                ('Subjective', request.form.get('history', '').strip()),
                ('Objective', request.form.get('examination', '').strip()),
                ('Assessment', request.form.get('diagnosis', '').strip()),
                ('Plan', request.form.get('plan', '').strip()),
            ]
            note = '\n\n'.join(f'{label}:\n{content}' for label, content in soap_sections if content)
            if not note:
                flash('Enter at least one section before saving the clinical note.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))

            new_note = ClinicalNote(
                patient_id=patient.id,
                note=note,
                doctor=session.get('full_name', 'Staff')
            )
            db.session.add(new_note)
            db.session.flush()
            audit('clinical_note_recorded', 'clinical_note', new_note.id, f'patient_id={patient.id}')
            flash('Clinical note saved!', 'success')
        elif action == 'service_request':
            if user_role not in SERVICE_REQUEST_ROLES and user_role != 'admin':
                flash('Only doctors can request clinical services.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            service_type = request.form.get('service_type', '').strip()
            if service_type not in {'Lab', 'Pharmacy', 'Radiology', 'Referral'}:
                flash('Please choose a valid service.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            description = request.form.get('service_description', '').strip()
            if not description:
                flash('Please describe the requested service.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            db.session.add(ServiceRequest(
                patient_id=patient.id,
                service_type=service_type,
                description=description,
                requested_by=session.get('full_name', 'Doctor')
            ))
            flash(f'{service_type} request sent successfully.', 'success')
        elif action == 'prescription':
            if user_role not in SERVICE_REQUEST_ROLES and user_role != 'admin':
                flash('Only doctors can prescribe medication.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            medication = request.form.get('medication', '').strip()
            dosage = request.form.get('dosage', '').strip()
            frequency = request.form.get('frequency', '').strip()
            frequency_unit = request.form.get('frequency_unit', 'Hours').strip()
            try:
                rx_take = int(request.form.get('rx_take', '1'))
            except ValueError:
                rx_take = 0
            duration_value = request.form.get('duration', '').strip()
            duration_unit = request.form.get('duration_unit', 'Days').strip()
            duration = f'{duration_value} {duration_unit}'.strip()
            try:
                start_date = parse_optional_date(request.form.get('start_date'))
                end_date = parse_optional_date(request.form.get('end_date'))
            except ValueError as error:
                flash(str(error), 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            if not all([medication, dosage, frequency, duration]) or rx_take not in {1, 2, 3}:
                flash('Medication, dosage, frequency, and duration are required.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            active_visit = Visit.query.filter(
                Visit.patient_id == patient.id,
                db.or_(Visit.status != 'completed', Visit.status.is_(None))
            ).order_by(Visit.started_at.desc()).first()
            prescription = Prescription(
                patient_id=patient.id,
                visit_id=active_visit.id if active_visit else None,
                item_type=request.form.get('item_type', 'Medicine').strip() or 'Medicine',
                medication=medication,
                identifier=request.form.get('identifier', '').strip() or None,
                needs_refill=request.form.get('needs_refill') == 'on',
                rx_take=rx_take,
                strength=request.form.get('strength', '').strip() or None,
                dosage=dosage,
                form=request.form.get('form', '').strip() or None,
                interval=request.form.get('interval', '').strip() or None,
                frequency=frequency,
                frequency_unit=frequency_unit if frequency_unit in {'Minutes', 'Hours'} else 'Hours',
                route=request.form.get('route', '').strip() or 'Oral',
                indication=request.form.get('indication', '').strip() or None,
                duration=duration,
                quantity=request.form.get('quantity', '').strip() or None,
                instructions=request.form.get('instructions', '').strip() or None,
                start_date=start_date,
                end_date=end_date,
                as_needed=request.form.get('administration') == 'as_needed',
                take_as_prescribed=request.form.get('administration', 'prescribed') == 'prescribed',
                prescribed_by=session.get('full_name', 'Doctor')
            )
            db.session.add(prescription)
            db.session.flush()
            audit('prescription_created', 'prescription', prescription.id, f'patient_id={patient.id}')
            flash('Prescription sent to pharmacy.', 'success')
        else:
            flash('Unknown patient-card action.', 'danger')
            return redirect(url_for('patient_card', national_id=national_id))

        db.session.commit()
        return redirect(url_for('patient_card', national_id=national_id))

    user_role = session.get('role')
    vitals_query = VitalSigns.query.filter_by(patient_id=patient.id)
    if user_role == 'doctor':
        today = datetime.utcnow().date()
        today_start = datetime.combine(today, datetime.min.time())
        tomorrow_start = today_start + timedelta(days=1)
        vitals_query = vitals_query.filter(
            VitalSigns.timestamp >= today_start,
            VitalSigns.timestamp < tomorrow_start
        )
    vitals = vitals_query.order_by(VitalSigns.timestamp.desc()).limit(10).all()
    notes = ClinicalNote.query.filter_by(patient_id=patient.id).order_by(ClinicalNote.timestamp.desc()).limit(50).all()
    requests = ServiceRequest.query.filter_by(patient_id=patient.id).order_by(ServiceRequest.requested_at.desc()).limit(20).all()
    active_visit = Visit.query.filter(
        Visit.patient_id == patient.id,
        db.or_(Visit.status != 'completed', Visit.status.is_(None))
    ).order_by(Visit.started_at.desc()).first()

    if active_visit and user_role in {'doctor', 'admin'} and active_visit.consultation_amount is None:
        active_visit.consultation_amount = CONSULTATION_FEES.get(active_visit.visit_type or 'New Visit', 1000)
        if not active_visit.invoice_number:
            active_visit.invoice_number = f'INV-{active_visit.id:06d}'
        audit(
            'consultation_billed',
            'visit',
            active_visit.id,
            f'amount={active_visit.consultation_amount}; visit_type={active_visit.visit_type or "New Visit"}'
        )
        db.session.commit()
        flash('Consultation billing has been updated on the invoice.', 'success')

    return render_template('patient_card.html', 
                         patient=patient, 
                         vitals=vitals, 
                         notes=notes, 
                         requests=requests,
                         active_visit=active_visit,
                         user_role=user_role,
                         queue_identifier=get_queue_identifier(patient.id))


@app.route('/patient/<national_id>/complete-visit', methods=['POST'])
@login_required(roles='doctor')
def complete_visit(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    visit = Visit.query.filter(
        Visit.patient_id == patient.id,
        Visit.status != 'completed'
    ).order_by(Visit.started_at.desc()).first()

    if not visit:
        flash('No active visit found for this patient.', 'warning')
        return redirect(url_for('patient_card', national_id=national_id))

    visit.status = 'completed'
    visit.completed_at = datetime.utcnow()
    queue = QueueEntry.query.filter_by(visit_id=visit.id).first()
    if queue:
        queue.status = 'completed'
    audit('visit_completed', 'visit', visit.id, f'patient_id={patient.id}')
    db.session.commit()
    flash(f'Visit completed for {patient.full_name}.', 'success')
    return redirect(url_for('dashboard'))
@app.route('/outpatient-queue')
@login_required(roles=CLINICAL_READ_ROLES | QUEUE_ASSIGNMENT_ROLES)
def outpatient_queue():
    visible_statuses = {'queued', 'with_doctor', 'in_progress'}
    queue_entries = QueueEntry.query.filter(QueueEntry.status.in_(visible_statuses)).order_by(QueueEntry.queued_at.desc()).all()
    for entry in queue_entries:
        entry.queue_identifier = get_queue_identifier(entry.patient_id)
    return render_template('outpatient_queue.html', queue_entries=queue_entries)

@app.route('/patient/<national_id>/send-to-doctor', methods=['POST'])
@login_required(roles={'nurse', 'triage'})
def send_to_doctor(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    
    # Get the latest visit
    visit = Visit.query.filter_by(patient_id=patient.id).order_by(Visit.started_at.desc()).first()
    
    if not visit:
        flash('No active visit found for this patient.', 'warning')
        return redirect(url_for('patient_card', national_id=national_id))
    
    # Update visit status
    visit.status = 'with_doctor'
    
    # Update or create queue entry
    queue = QueueEntry.query.filter_by(visit_id=visit.id).first()
    if queue:
        queue.status = 'with_doctor'
    else:
        queue = QueueEntry(
            visit_id=visit.id,
            patient_id=patient.id,
            status='with_doctor'
        )
        db.session.add(queue)
    
    db.session.commit()
    
    flash(f'Patient {patient.full_name} has been sent to the doctor.', 'success')
    return redirect(url_for('patient_card', national_id=national_id))

@app.route('/patients')
@login_required(roles=PATIENT_ACCESS_ROLES)
def patients():
    national_id = request.args.get('national_id', '').strip()
    name = request.args.get('name', '').strip()
    phone = request.args.get('phone', '').strip()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    per_page = 20

    query = Patient.query
    if national_id:
        query = query.filter(Patient.national_id.ilike(f"%{national_id}%"))
    if name:
        name_search = f"%{name}%"
        query = query.filter(
            or_(
                Patient.name.ilike(name_search),
                Patient.first_name.ilike(name_search),
                Patient.other_names.ilike(name_search)
            )
        )
    if phone:
        query = query.filter(Patient.phone.ilike(f"%{phone}%"))

    total = query.count()
    patients_list = query.order_by(Patient.registration_date.desc()).offset((page-1)*per_page).limit(per_page).all()

    patient_ids = [patient.id for patient in patients_list]
    last_visits = {
        patient_id: last_visit for patient_id, last_visit in db.session.query(
            Visit.patient_id, db.func.max(Visit.started_at)
        ).filter(Visit.patient_id.in_(patient_ids)).group_by(Visit.patient_id).all()
    } if patient_ids else {}

    return render_template('patients.html', patients=patients_list, last_visits=last_visits, national_id=national_id, name=name, phone=phone, page=page, per_page=per_page, total=total)

@app.route('/records')
@login_required(roles=PATIENT_ACCESS_ROLES)
def records():
    national_id = request.args.get('national_id', '').strip()
    phone = request.args.get('phone', '').strip()
    query = Patient.query

    if national_id:
        query = query.filter(Patient.national_id.ilike(f"%{national_id}%"))
    if phone:
        query = query.filter(Patient.phone.ilike(f"%{phone}%"))

    patients_list = query.order_by(Patient.registration_date.desc()).limit(50).all()
    return render_template('records.html', patients=patients_list, national_id=national_id, phone=phone)

@app.route('/invoices')
@login_required(roles=INVOICE_VIEW_ROLES)
def invoices():
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    status_filter = request.args.get('status', '').strip()

    query = Visit.query.filter(Visit.invoice_number.isnot(None), Visit.invoice_number != '')

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(Visit.started_at >= start_dt)
        except ValueError:
            flash('Please enter a valid start date.', 'danger')
            start_date = ''

    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            query = query.filter(Visit.started_at <= end_dt + timedelta(days=1))
        except ValueError:
            flash('Please enter a valid end date.', 'danger')
            end_date = ''

    invoice_rows = []
    for visit in query.order_by(Visit.started_at.desc()).all():
        pharmacy_total = sum(
            float(item.pharmacy_amount or 0)
            for item in Prescription.query.filter_by(visit_id=visit.id).filter(
                Prescription.status == 'dispensed',
                Prescription.pharmacy_amount.isnot(None)
            ).all()
        )
        has_pending_pharmacy_charge = Prescription.query.filter(
            Prescription.visit_id == visit.id,
            Prescription.status == 'dispensed',
            Prescription.pharmacy_amount.is_(None)
        ).first() is not None
        consultation_total = float(visit.consultation_amount) if visit.consultation_amount is not None else None
        invoice_total = (
            consultation_total + pharmacy_total
            if consultation_total is not None and not has_pending_pharmacy_charge
            else None
        )
        paid_total = sum(float(payment.amount) for payment in InvoicePayment.query.filter_by(visit_id=visit.id).all())
        payment_status = (
            'Pending billing' if invoice_total is None else
            'Paid' if paid_total >= invoice_total else
            'Partially paid' if paid_total > 0 else
            'Unpaid'
        )
        balance = max(invoice_total - paid_total, 0) if invoice_total is not None else None
        if status_filter and payment_status != status_filter:
            continue
        invoice_rows.append({
            'visit': visit,
            'patient': visit.patient,
            'payment_status': payment_status,
            'balance': balance,
        })

    return render_template('invoices.html', invoices=invoice_rows, start_date=start_date, end_date=end_date, status_filter=status_filter)

@app.route('/invoices/<int:visit_id>')
@login_required(roles=INVOICE_VIEW_ROLES)
def invoice_detail(visit_id):
    visit = Visit.query.filter(
        Visit.id == visit_id,
        Visit.invoice_number.isnot(None),
        Visit.invoice_number != ''
    ).first_or_404()
    pharmacy_prescriptions = Prescription.query.filter_by(visit_id=visit.id).filter(
        Prescription.status == 'dispensed',
        Prescription.pharmacy_amount.isnot(None)
    ).order_by(Prescription.dispensed_at.asc()).all()
    pharmacy_total = sum(float(item.pharmacy_amount or 0) for item in pharmacy_prescriptions)
    has_pending_pharmacy_charge = Prescription.query.filter(
        Prescription.visit_id == visit.id,
        Prescription.status == 'dispensed',
        Prescription.pharmacy_amount.is_(None)
    ).first() is not None
    consultation_total = float(visit.consultation_amount) if visit.consultation_amount is not None else None
    invoice_total = (
        consultation_total + pharmacy_total
        if consultation_total is not None and not has_pending_pharmacy_charge
        else None
    )
    payments = InvoicePayment.query.filter_by(visit_id=visit.id).order_by(InvoicePayment.received_at.desc()).all()
    paid_total = sum(float(payment.amount) for payment in payments)
    balance = max(invoice_total - paid_total, 0) if invoice_total is not None else None
    payment_status = (
        'Pending billing' if invoice_total is None else
        'Paid' if paid_total >= invoice_total else
        'Partially paid' if paid_total > 0 else
        'Unpaid'
    )
    return render_template(
        'invoice_detail.html',
        visit=visit,
        patient=visit.patient,
        pharmacy_prescriptions=pharmacy_prescriptions,
        pharmacy_total=pharmacy_total,
        has_pending_pharmacy_charge=has_pending_pharmacy_charge,
        invoice_total=invoice_total,
        payments=payments,
        paid_total=paid_total,
        balance=balance,
        payment_status=payment_status,
    )


@app.route('/invoices/<int:visit_id>/payments', methods=['POST'])
@login_required(roles={'accounts'})
def record_invoice_payment(visit_id):
    visit = Visit.query.filter(
        Visit.id == visit_id,
        Visit.invoice_number.isnot(None),
        Visit.invoice_number != ''
    ).first_or_404()
    try:
        amount = float(request.form.get('amount', '').strip())
    except (TypeError, ValueError):
        flash('Enter a valid payment amount.', 'danger')
        return redirect(url_for('invoice_detail', visit_id=visit.id))
    payment_method = request.form.get('payment_method', '').strip()
    if amount <= 0 or payment_method not in PAYMENT_METHODS:
        flash('Choose a valid payment method and enter an amount greater than zero.', 'danger')
        return redirect(url_for('invoice_detail', visit_id=visit.id))

    pharmacy_total = sum(
        float(item.pharmacy_amount or 0)
        for item in Prescription.query.filter_by(visit_id=visit.id).filter(
            Prescription.status == 'dispensed',
            Prescription.pharmacy_amount.isnot(None)
        ).all()
    )
    has_pending_pharmacy_charge = Prescription.query.filter(
        Prescription.visit_id == visit.id,
        Prescription.status == 'dispensed',
        Prescription.pharmacy_amount.is_(None)
    ).first() is not None
    consultation_total = float(visit.consultation_amount) if visit.consultation_amount is not None else None
    invoice_total = (
        consultation_total + pharmacy_total
        if consultation_total is not None and not has_pending_pharmacy_charge
        else None
    )
    paid_total = sum(float(payment.amount) for payment in InvoicePayment.query.filter_by(visit_id=visit.id).all())
    if invoice_total is None:
        flash('Complete billing before recording a payment.', 'warning')
        return redirect(url_for('invoice_detail', visit_id=visit.id))
    if amount > invoice_total - paid_total:
        flash(f'Payment exceeds the outstanding balance of {invoice_total - paid_total:.2f}.', 'danger')
        return redirect(url_for('invoice_detail', visit_id=visit.id))

    db.session.add(InvoicePayment(
        visit_id=visit.id,
        amount=amount,
        payment_method=payment_method,
        received_by=session.get('full_name', session.get('username', 'Accounts')),
        notes=request.form.get('notes', '').strip()[:255] or None,
    ))
    audit('invoice_payment_recorded', 'visit', visit.id, f'amount={amount}; payment_method={payment_method}')
    db.session.commit()
    flash('Payment recorded successfully.', 'success')
    return redirect(url_for('invoice_detail', visit_id=visit.id))


@app.route('/payments/<int:payment_id>/receipt')
@login_required(roles=INVOICE_VIEW_ROLES)
def payment_receipt(payment_id):
    payment = InvoicePayment.query.get_or_404(payment_id)
    return render_template('payment_receipt.html', payment=payment, visit=payment.visit, patient=payment.visit.patient)

@app.route('/visit/start/<national_id>', methods=['GET', 'POST'])
@login_required(roles=VISIT_MANAGEMENT_ROLES)
def start_visit(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    if request.method == 'POST':
        payment_method = request.form.get('payment_method', '').strip()
        if payment_method not in PAYMENT_METHODS:
            flash('Please choose a valid payment method.', 'danger')
            return redirect(url_for('start_visit', national_id=national_id))
        existing_queue = QueueEntry.query.filter(
            QueueEntry.patient_id == patient.id,
            QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES)
        ).first()
        if existing_queue:
            flash('This patient already has an active queue entry.', 'warning')
            return redirect(url_for('outpatient_queue'))

        visit_type = determine_visit_type(patient)
        visit = Visit(patient_id=patient.id, visit_type=visit_type, payment_method=payment_method)
        patient.visit_type = 'Revisit' if visit_type == 'Revisit' else 'New Visit'
        db.session.add(visit)
        db.session.flush()
        visit.invoice_number = f'INV-{visit.id:06d}'
        queue = QueueEntry(visit_id=visit.id, patient_id=patient.id, doctor=None, status='queued')
        db.session.add(queue)
        db.session.flush()
        audit('visit_started', 'visit', visit.id, f'patient_id={patient.id}; queue_id={queue.id}')
        db.session.commit()
        flash(f"Started visit for {patient.name} ({payment_method}) and queued", 'success')
        return redirect(url_for('dashboard'))
    return render_template('visit_start.html', patient=patient)

@app.route('/queue/assign/<int:queue_id>', methods=['GET', 'POST'])
@login_required(roles=QUEUE_ASSIGNMENT_ROLES)
def queue_assign(queue_id):
    queue = QueueEntry.query.get_or_404(queue_id)
    doctors = User.query.filter_by(role='doctor').order_by(User.full_name).all()
    if request.method == 'POST':
        doctor = request.form.get('doctor', '').strip()
        valid_doctors = {staff_member.full_name for staff_member in doctors}
        if doctor and doctor not in valid_doctors:
            flash('Please assign a registered doctor.', 'danger')
            return redirect(url_for('queue_assign', queue_id=queue.id))
        queue.doctor = doctor
        audit('queue_updated', 'queue_entry', queue.id, f'doctor={doctor or "unassigned"}')
        db.session.commit()
        flash('Queue updated', 'success')
        return redirect(url_for('dashboard'))
    return render_template('queue_assign.html', queue=queue, doctors=doctors)

@app.route('/queue/claim/<int:queue_id>', methods=['POST'])
@login_required(roles='doctor')
def queue_claim(queue_id):
    queue = QueueEntry.query.get_or_404(queue_id)
    if queue.status not in {'queued', 'with_doctor'}:
        return jsonify({'error': 'This queue entry is no longer available.'}), 409
    queue.doctor = session.get('full_name', session.get('username'))
    queue.status = 'in_progress'
    audit('queue_claimed', 'queue_entry', queue.id, f'doctor={queue.doctor}')
    db.session.commit()
    return jsonify({'ok': True, 'patient_url': url_for('patient_details', national_id=queue.patient.national_id)})

@app.route('/api/dashboard-stats')
@login_required()
def dashboard_stats():
    today = datetime.utcnow().date()
    total_patients = Patient.query.count()
    today_patients = Patient.query.filter(
        db.func.date(Patient.registration_date) == today
    ).count()
    today_visits = Visit.query.filter(db.func.date(Visit.started_at) == today).count()
    revisit_visits = Visit.query.filter(Visit.visit_type == 'Revisit').count()
    new_visits = Visit.query.filter(Visit.visit_type == 'New Visit').count()
    return {
        'total_patients': total_patients,
        'today_patients': today_patients,
        'today_visits': today_visits,
        'revisit_visits': revisit_visits,
        'new_visits': new_visits
    }


@app.route('/pharmacy')
@login_required(roles=PHARMACY_ROLES)
def pharmacy_queue():
    prescriptions = Prescription.query.filter(
        db.or_(
            Prescription.status.in_({'prescribed', 'processing'}),
            db.and_(
                Prescription.status == 'dispensed',
                Prescription.pharmacy_amount.is_(None)
            )
        )
    ).order_by(Prescription.prescribed_at.asc()).all()
    stock_by_name = {
        item.medication.casefold(): item
        for item in MedicineStock.query.all()
    }
    response = app.make_response(render_template(
        'pharmacy.html',
        prescriptions=prescriptions,
        stock_by_name=stock_by_name,
    ))
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response


@app.route('/pharmacy/inventory', methods=['GET', 'POST'])
@login_required(roles=PHARMACY_ROLES)
def pharmacy_inventory():
    if request.method == 'POST':
        medication = request.form.get('medication', '').strip()
        try:
            quantity = int(request.form.get('quantity', '').strip())
            unit_price = float(request.form.get('unit_price', '').strip())
            reorder_level = int(request.form.get('reorder_level', '10').strip())
        except (TypeError, ValueError):
            flash('Enter valid stock quantity, price, and reorder level.', 'danger')
            return redirect(url_for('pharmacy_inventory'))
        if not medication or quantity < 0 or unit_price < 0 or reorder_level < 0:
            flash('Medicine name is required and stock values cannot be negative.', 'danger')
            return redirect(url_for('pharmacy_inventory'))

        stock = MedicineStock.query.filter(db.func.lower(MedicineStock.medication) == medication.casefold()).first()
        if stock:
            stock.quantity += quantity
            stock.unit_price = unit_price
            stock.reorder_level = reorder_level
            action = 'updated'
        else:
            stock = MedicineStock(
                medication=medication,
                quantity=quantity,
                unit_price=unit_price,
                reorder_level=reorder_level,
            )
            db.session.add(stock)
            action = 'added'
        audit('medicine_stock_updated', 'medicine_stock', stock.id or 'new', f'medication={medication}; quantity_added={quantity}')
        db.session.commit()
        flash(f'{medication} stock {action}.', 'success')
        return redirect(url_for('pharmacy_inventory'))

    stock_items = MedicineStock.query.order_by(MedicineStock.medication.asc()).all()
    return render_template('pharmacy_inventory.html', stock_items=stock_items)


@app.route('/pharmacy/<int:prescription_id>/dispense', methods=['POST'])
@login_required(roles=PHARMACY_ROLES)
def dispense_prescription(prescription_id):
    prescription = Prescription.query.get_or_404(prescription_id)
    is_billing_completion = prescription.status == 'dispensed' and prescription.pharmacy_amount is None
    if prescription.status == 'dispensed' and not is_billing_completion:
        flash(f'{prescription.medication} has already been dispensed.', 'warning')
        return redirect(url_for('pharmacy_queue'))

    try:
        dispensed_units = int(request.form.get('dispensed_units', '').strip())
        unit_price = float(request.form.get('unit_price', '').strip())
    except (TypeError, ValueError):
        flash('Enter valid units dispensed and price per unit.', 'danger')
        return redirect(url_for('pharmacy_queue'))
    if dispensed_units <= 0 or unit_price < 0:
        flash('Units must be greater than zero and price cannot be negative.', 'danger')
        return redirect(url_for('pharmacy_queue'))

    stock = MedicineStock.query.filter(
        db.func.lower(MedicineStock.medication) == prescription.medication.casefold()
    ).first()
    if not stock or stock.quantity < dispensed_units:
        available = stock.quantity if stock else 0
        flash(f'Insufficient stock for {prescription.medication}. Available: {available}.', 'danger')
        return redirect(url_for('pharmacy_queue'))
    if unit_price == 0 and stock.unit_price is not None:
        unit_price = float(stock.unit_price)

    prescription.status = 'dispensed'
    prescription.dispensed_by = session.get('full_name', session.get('username', 'Pharmacist'))
    prescription.dispensed_at = datetime.utcnow()
    prescription.dispensed_units = dispensed_units
    prescription.unit_price = unit_price
    prescription.pharmacy_amount = round(dispensed_units * unit_price, 2)
    stock.quantity -= dispensed_units
    pharmacy_request = ServiceRequest.query.filter(
        ServiceRequest.patient_id == prescription.patient_id,
        ServiceRequest.service_type == 'Pharmacy',
        ServiceRequest.status.in_({'Pending', 'processing', 'in_progress'})
    ).order_by(ServiceRequest.requested_at.desc()).first()
    if pharmacy_request:
        pharmacy_request.status = 'Completed'
    audit(
        'pharmacy_charge_recorded' if is_billing_completion else 'prescription_dispensed',
        'prescription',
        prescription.id,
        f'patient_id={prescription.patient_id}; units={dispensed_units}; amount={prescription.pharmacy_amount}; dispensed_by={prescription.dispensed_by}'
    )
    db.session.commit()
    flash(f'{prescription.medication} marked as dispensed.', 'success')
    return redirect(url_for('pharmacy_queue'))

@app.route('/register', methods=['GET', 'POST'])
@login_required(roles=REGISTRATION_ROLES)
def register_patient():
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        other_names = request.form.get('other_names', '').strip()
        try:
            age = parse_optional_number(request.form.get('age'), 'Age', 0, 120, integer=True)
        except ValueError:
            flash("Please enter a valid age.", "danger")
            return redirect(url_for("register_patient"))

        gender = request.form.get('gender', '').strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '')
        national_id = request.form.get('national_id', '').strip()

        if not first_name:
            flash("Please enter the patient's first name.", "danger")
            return redirect(url_for("register_patient"))
        if not national_id or len(national_id) > 20:
            flash("Please enter a valid national ID.", "danger")
            return redirect(url_for("register_patient"))
        if gender not in {'', 'Male', 'Female', 'Other'}:
            flash("Please choose a valid gender.", "danger")
            return redirect(url_for("register_patient"))

        combined_name = f"{first_name} {other_names}".strip()
        new_patient = Patient(
            national_id=national_id,
            name=combined_name,
            first_name=first_name or combined_name,
            other_names=other_names,
            age=age,
            gender=gender,
            phone=phone,
            address=address,
            visit_type='New Visit'
        )

        try:
            db.session.add(new_patient)
            db.session.flush()
            audit('patient_registered', 'patient', new_patient.id)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('A patient with that national ID already exists.', 'danger')
            return redirect(url_for('register_patient', national_id=national_id))

        flash(f'Patient {new_patient.full_name} registered successfully! National ID: {new_patient.national_id}', 'success')
        return redirect(url_for('patient_details', national_id=new_patient.national_id))
    
    pre_national_id = request.args.get('national_id', '').strip()
    return render_template('register.html', national_id=pre_national_id)

@app.route('/patient/lookup', methods=['POST'])
@login_required(roles=REGISTRATION_ROLES)
def patient_lookup():
    national_id = request.form.get('national_id', '').strip()
    if not national_id:
        flash('Please enter a national ID to search.', 'warning')
        return redirect(url_for('dashboard'))

    patient = Patient.query.filter_by(national_id=national_id).first()
    if patient:
        return redirect(url_for('patient_details', national_id=patient.national_id))
    return redirect(url_for('register_patient', national_id=national_id))

@app.route('/patient/<national_id>/edit', methods=['GET', 'POST'])
@login_required(roles=REGISTRATION_ROLES)
def edit_patient(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        other_names = request.form.get('other_names', '').strip()
        age_raw = request.form.get('age', '').strip()
        try:
            age_val = parse_optional_number(age_raw, 'Age', 0, 120, integer=True)
        except ValueError:
            flash('Please enter a valid age.', 'danger')
            return redirect(url_for('edit_patient', national_id=national_id))

        if not first_name:
            flash("Please enter the patient's first name.", 'danger')
            return redirect(url_for('edit_patient', national_id=national_id))

        patient.first_name = first_name
        patient.other_names = other_names
        patient.name = f"{patient.first_name} {patient.other_names}".strip()
        patient.age = age_val
        patient.gender = request.form.get('gender', '').strip()
        patient.phone = request.form.get('phone', '').strip()
        patient.address = request.form.get('address', '')
        audit('patient_updated', 'patient', patient.id)
        db.session.commit()
        flash('Patient updated successfully.', 'success')
        return redirect(url_for('patient_details', national_id=patient.national_id))

    return render_template('patient_edit.html', patient=patient)

if __name__ == '__main__':
    app.run(
        debug=env_flag('FLASK_DEBUG'),
        host=os.getenv('APP_HOST', '127.0.0.1'),
        port=int(os.getenv('PORT', '5000')),
    )
