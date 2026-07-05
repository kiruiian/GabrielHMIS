import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, inspect, text
from datetime import datetime

app = Flask(__name__, instance_relative_config=True)
os.makedirs(app.instance_path, exist_ok=True)
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(app.instance_path, 'hmis.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Patient Model
class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    national_id = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer)
    gender = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    visit_type = db.Column(db.String(20), default='New Visit')
    registration_date = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Patient {self.name}>"

def ensure_schema():
    with app.app_context():
        legacy_db_path = os.path.join(os.getcwd(), 'hmis.db')
        instance_db_path = os.path.join(app.instance_path, 'hmis.db')
        if not os.path.exists(instance_db_path) and os.path.exists(legacy_db_path):
            import shutil
            shutil.copy2(legacy_db_path, instance_db_path)

        db.create_all()
        inspector = inspect(db.engine)
        if 'patient' not in inspector.get_table_names():
            return

        columns = {column['name'] for column in inspector.get_columns('patient')}
        if 'national_id' not in columns:
            db.session.execute(text("ALTER TABLE patient ADD COLUMN national_id VARCHAR(20)"))
            if 'patient_id' in columns:
                db.session.execute(text("UPDATE patient SET national_id = patient_id WHERE national_id IS NULL OR national_id = ''"))
            else:
                db.session.execute(text("UPDATE patient SET national_id = CAST(id AS TEXT) WHERE national_id IS NULL OR national_id = ''"))
            db.session.commit()

# Create database tables
with app.app_context():
    ensure_schema()

@app.route('/')
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
def patient_details(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    return render_template('patient.html', patient=patient)

@app.route('/api/dashboard-stats')
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
def register_patient():
    if request.method == 'POST':
        name = request.form['name']
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

        new_patient = Patient(
            national_id=national_id,
            name=name,
            age=age,
            gender=gender,
            phone=phone,
            address=address,
            visit_type=visit_type
        )

        db.session.add(new_patient)
        db.session.commit()

        flash(f'Patient {name} registered successfully! National ID: {new_patient.national_id}', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('register.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)