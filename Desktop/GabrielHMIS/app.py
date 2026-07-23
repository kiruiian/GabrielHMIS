import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, inspect, text
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, instance_relative_config=True)
os.makedirs(app.instance_path, exist_ok=True)

app.config['SECRET_KEY'] = 'super_secret_key_change_me_in_production'
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(app.instance_path, 'hmis.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

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
    started_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('visits', lazy=True))


class QueueEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    visit_id = db.Column(db.Integer, db.ForeignKey('visit.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    doctor = db.Column(db.String(100))
    status = db.Column(db.String(30), default='queued')
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


class ServiceRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    service_type = db.Column(db.String(50))      # Lab, Pharmacy, Imaging, Referral
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='Pending')
    requested_by = db.Column(db.String(100))
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('service_requests', lazy=True, order_by=requested_at.desc()))

# ====================== AUTH ======================
def login_required(role=None):
    def decorator(f):
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in first.', 'warning')
                return redirect(url_for('login'))
            if role and session.get('role') not in [role, 'admin']:
                flash('Access denied.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        decorated_function.__name__ = f.__name__
        return decorated_function
    return decorator

# ====================== DB SETUP ======================
def ensure_schema():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', full_name='Administrator', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("Default admin created: admin / admin123")

with app.app_context():
    ensure_schema()

# ====================== LOGIN ======================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            session['full_name'] = user.full_name
            flash(f'Welcome, {user.full_name}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/admin/register-staff', methods=['GET', 'POST'])
@login_required(role='admin')
def register_staff():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        full_name = request.form.get('full_name', '').strip()
        role = request.form.get('role')
        password = request.form.get('password', '').strip()

        if not username or not full_name or not password:
            flash('All fields are required.', 'danger')
            return redirect(url_for('register_staff'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register_staff'))

        new_user = User(username=username, full_name=full_name, role=role)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        flash(f'Staff "{full_name}" registered successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('register_staff.html')

# ====================== MAIN ROUTES ======================
@app.route('/')
@login_required()
def dashboard():
    q = request.args.get('q', '').strip()
    if q:
        search = f"%{q}%"
        patients = Patient.query.filter(
            or_(
                Patient.name.ilike(search),
                Patient.national_id.ilike(search),
                Patient.phone.ilike(search)
            )
        ).order_by(Patient.registration_date.desc()).all()
        patients_count = len(patients)
    else:
        patients = Patient.query.order_by(Patient.registration_date.desc()).limit(20).all()
        patients_count = Patient.query.count()

    return render_template('dashboard.html', patients_count=patients_count, patients=patients, q=q)

@app.route('/patient/<national_id>')
@login_required()
def patient_details(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    visit = Visit.query.filter_by(patient_id=patient.id).order_by(Visit.started_at.desc()).first()
    return render_template('patient.html', patient=patient, last_visit=visit)

@app.route('/patient/<national_id>/records')
@login_required()
def patient_records(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    visits = Visit.query.filter_by(patient_id=patient.id).order_by(Visit.started_at.desc()).all()
    return render_template('patient_records.html', patient=patient, visits=visits)
@app.route('/patient/<national_id>/card', methods=['GET', 'POST'])
@login_required()
def patient_card(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    
    vitals = patient.vitals
    notes = patient.clinical_notes
    requests = patient.service_requests

    if request.method == 'POST':
        action = request.form.get('action')
        user_role = session.get('role')

        if action == 'vitals':
            if user_role not in ['nurse', 'triage', 'doctor', 'records', 'admin']:
                flash('Only authorized medical staff can record vitals.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            
            new_vital = VitalSigns(
                patient_id=patient.id,
                bp=request.form.get('bp'),
                temperature=request.form.get('temperature') or None,
                pulse=request.form.get('pulse') or None,
                respiration=request.form.get('respiration') or None,
                weight=request.form.get('weight') or None,
                height=request.form.get('height') or None,
                oxygen_sat=request.form.get('oxygen_sat') or None,
                notes=request.form.get('vital_notes'),
                recorded_by=session.get('full_name', 'Staff')
            )
            db.session.add(new_vital)
            flash('Vitals recorded successfully!', 'success')

        elif action == 'note':
            if user_role not in ['nurse', 'doctor', 'records', 'admin']:
                flash('Only medical staff can write clinical notes.', 'danger')
                return redirect(url_for('patient_card', national_id=national_id))
            
            new_note = ClinicalNote(
                patient_id=patient.id,
                note=request.form.get('note'),
                doctor=session.get('full_name', 'Staff')
            )
            db.session.add(new_note)
            flash('Clinical note saved!', 'success')

        # You can add referral logic later
        db.session.commit()
        return redirect(url_for('patient_card', national_id=national_id))

    # Render template with user_role
    return render_template('patient_card.html', 
                         patient=patient, 
                         vitals=vitals, 
                         notes=notes, 
                         requests=requests,
                         user_role=session.get('role'))   #role based access control in template
@app.route('/outpatient-queue')
@login_required()
def outpatient_queue():
    queue_entries = QueueEntry.query.filter_by(status='queued').order_by(QueueEntry.queued_at.desc()).all()
    return render_template('outpatient_queue.html', queue_entries=queue_entries)

@app.route('/patients')
@login_required()
def patients():
    national_id = request.args.get('national_id', '').strip()
    name = request.args.get('name', '').strip()
    phone = request.args.get('phone', '').strip()
    page = int(request.args.get('page', 1))
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

    last_visits = {}
    for p in patients_list:
        v = Visit.query.filter_by(patient_id=p.id).order_by(Visit.started_at.desc()).first()
        last_visits[p.id] = v.started_at if v else None

    return render_template('patients.html', patients=patients_list, last_visits=last_visits, national_id=national_id, name=name, phone=phone, page=page, per_page=per_page, total=total)

@app.route('/records')
@login_required()
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
@login_required()
def start_visit(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    if request.method == 'POST':
        payment_method = request.form.get('payment_method')
        invoice_number = request.form.get('invoice_number')
        visit_type = 'Revisit' if patient.visit_type == 'Revisit' else 'New Visit'
        visit = Visit(patient_id=patient.id, visit_type=visit_type, payment_method=payment_method, invoice_number=invoice_number)
        patient.visit_type = 'Revisit'
        db.session.add(visit)
        db.session.flush()
        queue = QueueEntry(visit_id=visit.id, patient_id=patient.id, doctor=None, status='queued')
        db.session.add(queue)
        db.session.commit()
        flash(f"Started visit for {patient.name} ({payment_method}) and queued", 'success')
        return redirect(url_for('dashboard'))
    return render_template('visit_start.html', patient=patient)

@app.route('/queue/assign/<int:queue_id>', methods=['GET', 'POST'])
@login_required()
def queue_assign(queue_id):
    queue = QueueEntry.query.get_or_404(queue_id)
    if request.method == 'POST':
        doctor = request.form.get('doctor')
        invoice_number = request.form.get('invoice_number')
        queue.doctor = doctor
        if queue.visit:
            queue.visit.invoice_number = invoice_number
        db.session.commit()
        flash('Queue updated', 'success')
        return redirect(url_for('dashboard'))
    return render_template('queue_assign.html', queue=queue)

@app.route('/queue/claim/<int:queue_id>', methods=['POST'])
@login_required()
def queue_claim(queue_id):
    data = request.get_json() or {}
    doctor = data.get('doctor') or request.form.get('doctor')
    queue = QueueEntry.query.get_or_404(queue_id)
    if doctor:
        queue.doctor = doctor
    queue.status = 'in_progress'
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
    revisit_patients = Patient.query.filter(Patient.visit_type == 'Revisit').count()
    new_visit_patients = Patient.query.filter(
        or_(Patient.visit_type == 'New Visit', Patient.visit_type.is_(None))
    ).count()
    return {
        'total_patients': total_patients,
        'today_patients': today_patients,
        'revisit_patients': revisit_patients,
        'new_visit_patients': new_visit_patients
    }

@app.route('/register', methods=['GET', 'POST'])
@login_required()
def register_patient():
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        other_names = request.form.get('other_names', '').strip()
        try:
            age = int(request.form['age'])
            if age < 0 or age > 120:
                raise ValueError
        except ValueError:
            flash("Please enter a valid age.", "danger")
            return redirect(url_for("register_patient"))

        gender = request.form['gender']
        phone = request.form['phone']
        address = request.form.get('address', '')
        visit_type = request.form.get('visit_type', 'New Visit')
        national_id = request.form.get('national_id', '').strip()

        if not national_id:
            flash("Please enter a national ID.", "danger")
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

        db.session.add(new_patient)
        db.session.commit()

        flash(f'Patient {new_patient.full_name} registered successfully! National ID: {new_patient.national_id}', 'success')
        return redirect(url_for('dashboard'))
    
    pre_national_id = request.args.get('national_id', '').strip()
    return render_template('register.html', national_id=pre_national_id)

@app.route('/patient/lookup', methods=['POST'])
@login_required()
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
@login_required()
def edit_patient(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        other_names = request.form.get('other_names', '').strip()
        age_raw = request.form.get('age', '').strip()
        try:
            age_val = int(age_raw) if age_raw != '' else None
            if age_val is not None and (age_val < 0 or age_val > 120):
                raise ValueError
        except ValueError:
            flash('Please enter a valid age.', 'danger')
            return redirect(url_for('edit_patient', national_id=national_id))

        patient.first_name = first_name or patient.display_first_name
        patient.other_names = other_names
        patient.name = f"{patient.first_name} {patient.other_names}".strip()
        patient.age = age_val
        patient.gender = request.form.get('gender')
        patient.phone = request.form.get('phone')
        patient.address = request.form.get('address', '')
        db.session.commit()
        flash('Patient updated successfully.', 'success')
        return redirect(url_for('patient_details', national_id=patient.national_id))

    return render_template('patient_edit.html', patient=patient)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)