from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from datetime import datetime
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hmis.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Patient Model
class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer)
    gender = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    registration_date = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Patient {self.name}>"

# Create database tables
with app.app_context():
    db.create_all()

@app.route('/')
def dashboard():
    q = request.args.get('q', '').strip()
    if q:
        search = f"%{q}%"
        patients = Patient.query.filter(
            or_(
                Patient.name.ilike(search),
                Patient.patient_id.ilike(search),
                Patient.phone.ilike(search)
            )
        ).order_by(Patient.registration_date.desc()).all()
        patients_count = len(patients)
    else:
        patients = Patient.query.order_by(Patient.registration_date.desc()).limit(20).all()
        patients_count = Patient.query.count()

    return render_template('dashboard.html', patients_count=patients_count, patients=patients, q=q)

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
        
        # Create with a temporary token, flush to get DB id, then set a permanent patient_id
        temp_id = f"TEMP-{uuid.uuid4().hex}"

        new_patient = Patient(
            patient_id=temp_id,
            name=name,
            age=age,
            gender=gender,
            phone=phone,
            address=address
        )

        db.session.add(new_patient)
        db.session.flush()  # assigns new_patient.id from DB

        # Permanent patient ID using the numeric PK ensures global uniqueness
        new_patient.patient_id = f"SGH{new_patient.id:08d}"
        db.session.commit()

        flash(f'Patient {name} registered successfully! ID: {new_patient.patient_id}', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('register.html')

if __name__ == '__main__':
    app.run(debug=True)