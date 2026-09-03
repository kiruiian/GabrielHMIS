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

VALID_ROLES = {'receptionist', 'doctor', 'nurse', 'triage', 'admin', 'records', 'accounts', 'hr'}
PATIENT_ACCESS_ROLES = {'receptionist', 'doctor', 'nurse', 'triage', 'records'}
REGISTRATION_ROLES = {'receptionist', 'records'}
VISIT_MANAGEMENT_ROLES = {'receptionist', 'records'}
QUEUE_ASSIGNMENT_ROLES = {'receptionist', 'records'}
CLINICAL_READ_ROLES = {'doctor', 'nurse', 'triage', 'records'}
VITALS_ENTRY_ROLES = {'nurse', 'triage'}
NOTE_ENTRY_ROLES = {'doctor', 'nurse'}
SERVICE_REQUEST_ROLES = {'doctor'}
PAYMENT_METHODS = {'SHA', 'SHA FFS', 'CASH PAYER', 'BROWNS PLANTATIONS', 'BRITAM', 'JUBILEE'}
ACTIVE_QUEUE_STATUSES = {'queued', 'in_progress'}

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
    status = db.Column(db.String(30), default='registered')  # registered → with_nurse → with_doctor → completed
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    patient = db.relationship('Patient', backref=db.backref('visits', lazy=True))


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
    return render_template('patient.html', patient=patient, last_visit=visit)

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
            note = request.form.get('note', '').strip()
            if not note:
                flash('Clinical note cannot be empty.', 'danger')
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
            if service_type not in {'Lab', 'Pharmacy', 'Radiology'}:
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

    return render_template('patient_card.html', 
                         patient=patient, 
                         vitals=vitals, 
                         notes=notes, 
                         requests=requests,
                         user_role=user_role)
@app.route('/outpatient-queue')
@login_required(roles=CLINICAL_READ_ROLES | QUEUE_ASSIGNMENT_ROLES)
def outpatient_queue():
    visible_statuses = {'queued', 'with_doctor'} if session.get('role') in {'doctor', 'admin'} else {'queued'}
    queue_entries = QueueEntry.query.filter(QueueEntry.status.in_(visible_statuses)).order_by(QueueEntry.queued_at.desc()).all()
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

@app.route('/visit/start/<national_id>', methods=['GET', 'POST'])
@login_required(roles=VISIT_MANAGEMENT_ROLES)
def start_visit(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    if request.method == 'POST':
        payment_method = request.form.get('payment_method', '').strip()
        invoice_number = request.form.get('invoice_number', '').strip()
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

        visit_type = 'Revisit' if patient.visit_type == 'Revisit' else 'New Visit'
        visit = Visit(patient_id=patient.id, visit_type=visit_type, payment_method=payment_method, invoice_number=invoice_number)
        patient.visit_type = 'Revisit'
        db.session.add(visit)
        db.session.flush()
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
        invoice_number = request.form.get('invoice_number', '').strip()
        valid_doctors = {staff_member.full_name for staff_member in doctors}
        if doctor and doctor not in valid_doctors:
            flash('Please assign a registered doctor.', 'danger')
            return redirect(url_for('queue_assign', queue_id=queue.id))
        queue.doctor = doctor
        if queue.visit:
            queue.visit.invoice_number = invoice_number
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
        visit_type = request.form.get('visit_type', 'New Visit')
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
        if visit_type not in {'New Visit', 'Revisit'}:
            flash("Please choose a valid visit type.", "danger")
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
            visit_type=visit_type
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
        return redirect(url_for('dashboard'))
    
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
