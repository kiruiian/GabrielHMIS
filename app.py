import csv
import io
import os
import dotenv
import secrets
import math
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
import openpyxl

import click
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
    Response,
    send_from_directory,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, or_, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from lab_module import register_lab_module
from cryptography.fernet import Fernet

def get_fernet():
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is not set in .env")
    return Fernet(key.encode())

def encrypt_text(value):
    if not value:
        return None
    return get_fernet().encrypt(value.encode()).decode()

def decrypt_text(value):
    if not value:
        return None
    try:
        return get_fernet().decrypt(value.encode()).decode()
    except Exception:
        return value

load_dotenv()


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


app = Flask(__name__, instance_relative_config=True)
os.makedirs(app.instance_path, exist_ok=True)

is_production = os.getenv("APP_ENV", "development").lower() == "production"
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    if is_production:
        raise RuntimeError("SECRET_KEY must be set when APP_ENV=production.")
    secret_key = secrets.token_urlsafe(32)

app.config["SECRET_KEY"] = secret_key
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(app.instance_path, 'hmis.db')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = env_flag("SESSION_COOKIE_SECURE", is_production)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=10)

db = SQLAlchemy(app)

VALID_ROLES = {
    "receptionist",
    "doctor",
    "nurse",
    "triage",
    "pharmacist",
    "radiographer",
    "lab",
    "admin",
    "records",
    "accounts",
    "hr",
}
PATIENT_ACCESS_ROLES = {"receptionist", "doctor", "nurse", "triage", "records"}
REGISTRATION_ROLES = {"receptionist", "records"}
VISIT_MANAGEMENT_ROLES = {"receptionist", "records", "accounts"}
QUEUE_ASSIGNMENT_ROLES = {"receptionist", "records"}
CLINICAL_READ_ROLES = {"doctor", "nurse", "triage", "records", "pharmacist"}
APPOINTMENT_VIEW_ROLES = {"receptionist", "doctor", "nurse", "triage", "records"}
APPOINTMENT_MANAGE_ROLES = {"receptionist", "records", "admin"}
THEATRE_VIEW_ROLES = {"doctor", "nurse", "receptionist", "records", "admin"}
THEATRE_MANAGE_ROLES = {"receptionist", "admin"}
THEATRE_CLINICAL_ROLES = {"doctor", "nurse", "admin"}
INVOICE_VIEW_ROLES = {"accounts", "admin", "receptionist", "records"}
VITALS_ENTRY_ROLES = {"nurse", "triage"}
NOTE_ENTRY_ROLES = {"doctor", "nurse"}
SERVICE_REQUEST_ROLES = {"doctor", "receptionist", "records", "accounts", "admin"}
RENAL_VIEW_ROLES = {"nurse", "doctor", "records", "accounts", "receptionist", "admin"}
RENAL_CHART_ROLES = {"nurse", "doctor", "admin"}
RENAL_PREAUTH_REQUEST_ROLES = {"nurse", "doctor", "admin", "receptionist", "records", "accounts"}
RENAL_AUTHORIZATION_ROLES = {"accounts", "admin", "receptionist", "records"}
PHARMACY_ROLES = {"pharmacist"}
RADIOLOGY_ROLES = {"radiographer"}
IMAGING_VIEW_ROLES = {
    "doctor",
    "nurse",
    "records",
    "admin",
    "receptionist",
    "radiographer",
}
PAYMENT_METHODS = {
    "SHA",
    "SHA FFS",
    "CASH PAYER",
    "BROWNS PLANTATIONS",
    "BRITAM",
    "JUBILEE",
}
CONSULTATION_FEES = {"New Visit": 1000, "Revisit": 500}


IMAGING_PRICES = {
    # X-Ray
    "Chest X-Ray (PA)": 800,
    "Chest X-Ray (Lateral)": 1000,
    "Abdominal X-Ray": 1000,
    "Skull X-Ray": 1200,
    "Cervical Spine": 1200,
    "Lumbar Spine": 1200,
    "Pelvis": 1000,
    "KUB": 1000,
    "Extremity X-Ray": 800,
    # Ultrasound
    "Abdominal Ultrasound": 1500,
    "Pelvic / Gynaecological Ultrasound": 1500,
    "Obstetric Ultrasound": 1800,
    "Thyroid Ultrasound": 1200,
    "Breast Ultrasound": 1500,
    "Scrotal Ultrasound": 1200,
    "Soft Tissue Ultrasound": 1200,
    "Doppler Studies": 2500,
    # CT
    "CT Head": 6000,
    "CT Chest": 8000,
    "CT Abdomen / Pelvis": 10000,
    "CT Spine": 8000,
    "CT Angiogram": 12000,
    # MRI
    "MRI Brain": 15000,
    "MRI Cervical Spine": 15000,
    "MRI Lumbar Spine": 15000,
    "MRI Knee": 12000,
    "MRI Shoulder": 12000,
    "MRI Abdomen": 15000,
}

ACTIVE_QUEUE_STATUSES = {"queued", "in_progress"}

INPATIENT_ROLES = {"doctor", "nurse", "admin", "records", "receptionist","accounts"}


WARDS = [
    ('Male General', 'General', 'MG', 13),
    ('Female General', 'General', 'FG', 6),
    ('Maternity', 'Maternity', 'MAT', 6),
    ('Private Ward', 'Private', 'PV', 9),
    ('Pediatrics', 'Pediatrics', 'PED', 3),
    ('Executive', 'Private', 'EX', 4),
    ('General Private', 'Private', 'GP', 8),
    ('Newborn Unit', 'Maternity', 'NB', 6),
    ('Post Anaesthesia Unit', 'Theatre', 'PACU', 2),
]

BED_DAILY_RATES = {
    "Male General": 3360,
    "Female General": 3360,
    "Private Ward": 3500,
    "Pediatrics": 1500,
    "Executive": 6000,
    "General Private": 4000,
    "Newborn Unit": 2500,
    "Post Anaesthesia Unit": 2000,
}

ADMISSION_FEE = 1000  
DEFAULT_DOCTOR_FEE = 2000
DEFAULT_NURSING_FEE = 1000
DEFAULT_CONSUMABLES = 500


IMAGING_MODALITIES = {
    "X-Ray": [
        "Chest X-Ray (PA)",
        "Chest X-Ray (Lateral)",
        "Abdominal X-Ray",
        "Skull X-Ray",
        "Cervical Spine",
        "Lumbar Spine",
        "Pelvis",
        "KUB",
        "Extremity X-Ray",
    ],
    "Ultrasound": [
        "Abdominal Ultrasound",
        "Pelvic / Gynaecological Ultrasound",
        "Obstetric Ultrasound",
        "Thyroid Ultrasound",
        "Breast Ultrasound",
        "Scrotal Ultrasound",
        "Soft Tissue Ultrasound",
        "Doppler Studies",
    ],
    "CT": [
        "CT Head",
        "CT Chest",
        "CT Abdomen / Pelvis",
        "CT Spine",
        "CT Angiogram",
    ],
    "MRI": [
        "MRI Brain",
        "MRI Cervical Spine",
        "MRI Lumbar Spine",
        "MRI Knee",
        "MRI Shoulder",
        "MRI Abdomen",
    ],
}
COMMON_ICD10 = [
    {"code": "A09", "text": "Infectious gastroenteritis and colitis"},
    {"code": "A90", "text": "Dengue fever"},
    {"code": "B24", "text": "HIV disease"},
    {"code": "B50.9", "text": "Plasmodium falciparum malaria, unspecified"},
    {"code": "B54", "text": "Unspecified malaria"},
    {"code": "E11.9", "text": "Type 2 diabetes mellitus without complications"},
    {"code": "E14.9", "text": "Unspecified diabetes mellitus"},
    {"code": "I10", "text": "Essential (primary) hypertension"},
    {"code": "J06.9", "text": "Acute upper respiratory infection, unspecified"},
    {"code": "J18.9", "text": "Pneumonia, unspecified"},
    {"code": "J45.9", "text": "Asthma, unspecified"},
    {"code": "K29.7", "text": "Gastritis, unspecified"},
    {"code": "K59.1", "text": "Functional diarrhea"},
    {"code": "L30.9", "text": "Dermatitis, unspecified"},
    {"code": "M25.5", "text": "Pain in joint"},
    {"code": "M54.5", "text": "Low back pain"},
    {"code": "M79.1", "text": "Myalgia"},
    {"code": "N39.0", "text": "Urinary tract infection, site not specified"},
    {"code": "O80", "text": "Single spontaneous delivery"},
    {"code": "R50.9", "text": "Fever, unspecified"},
    {"code": "R51", "text": "Headache"},
    {"code": "S93.4", "text": "Sprain of ankle"},
    {"code": "T14.1", "text": "Injury of unspecified body region"},
    {"code": "Z00.0", "text": "General adult medical examination"},
    {"code": "Z23", "text": "Encounter for immunization"},
]

ICD11_PATH = Path(__file__).with_name("data") / "SimpleTabulation-ICD-11-MMS-en.xlsx"
_icd11_rows = None

def load_icd11():
    global _icd11_rows
    if _icd11_rows is not None:
        return _icd11_rows
    rows = []
    if ICD11_PATH.exists():
        book = openpyxl.load_workbook(ICD11_PATH, read_only=True, data_only=True)
        sheet = book.active
        for row in sheet.iter_rows(min_row=2, values_only=True):
            code = str(row[2] or "").strip()
            title = str(row[4] or "").strip().lstrip("-").strip()
            kind = str(row[5] or "").strip().lower()
            chapter = str(row[8] or "").strip()
            if not code or kind != "category" or chapter == "X":
                continue
            rows.append({"code": code, "text": title})
        book.close()
    _icd11_rows = rows or COMMON_ICD10
    return _icd11_rows

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "pdf"}
UPLOAD_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "instance", "uploads", "imaging"
)
THEATRE_UPLOAD_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "instance", "uploads", "theatre"
)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THEATRE_UPLOAD_FOLDER, exist_ok=True)


def get_queue_identifier(patient_id):
    latest_request = (
        ServiceRequest.query.filter_by(patient_id=patient_id)
        .order_by(ServiceRequest.requested_at.desc())
        .first()
    )
    if latest_request and (latest_request.status or "Pending").lower() not in {
        "completed",
        "cancelled",
        "closed",
    }:
        return {
            "label": latest_request.service_type or "Service",
            "key": (latest_request.service_type or "service").lower(),
            "detail": "Service requested",
        }

    latest_queue = (
        QueueEntry.query.filter_by(patient_id=patient_id)
        .order_by(QueueEntry.queued_at.desc())
        .first()
    )
    labels = {
        "queued": "Waiting",
        "with_nurse": "Nurse",
        "with_doctor": "Doctor",
        "in_progress": "Doctor",
    }
    status = latest_queue.status if latest_queue else "queued"
    return {
        "label": labels.get(status, "Waiting"),
        "key": status,
        "detail": "Current queue stage",
    }


def determine_visit_type(patient):
    """Classify the visit based on whether this patient has a previous visit record."""
    previous_visits = Visit.query.filter_by(patient_id=patient.id).count()
    return (
        "Revisit"
        if patient.visit_type == "Revisit" or previous_visits > 0
        else "New Visit"
    )
#databases
#user model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(100))
    role = db.Column(db.String(20), default="receptionist")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

#patient model
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
    allergies = db.Column(db.Text)
    patient_image = db.Column(db.String(255))
    visit_type = db.Column(db.String(20), default="New Visit")
    registration_date = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def full_name(self):
        if self.first_name:
            return f"{self.first_name} {self.other_names or ''}".strip()
        return self.name or ""

    @property
    def display_first_name(self):
        if self.first_name:
            return self.first_name
        if self.name:
            return self.name.split(" ")[0]
        return ""

    @property
    def display_other_names(self):
        if self.other_names:
            return self.other_names
        if self.name:
            parts = self.name.split(" ")
            return " ".join(parts[1:]) if len(parts) > 1 else ""
        return ""

    @property
    def allergies_plain(self):
        return decrypt_text(self.allergies)


class Visit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    visit_type = db.Column(db.String(20))
    payment_method = db.Column(db.String(80))
    invoice_number = db.Column(db.String(80))
    consultation_amount = db.Column(db.Numeric(10, 2))
    status = db.Column(
        db.String(30), default="registered"
    )  # registered → with_nurse → with_doctor → completed
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    patient = db.relationship("Patient", backref=db.backref("visits", lazy=True))


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    doctor = db.Column(db.String(100))
    reason = db.Column(db.String(255))
    status = db.Column(db.String(20), nullable=False, default="scheduled")
    created_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    checked_in_visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"))

    patient = db.relationship("Patient", backref=db.backref("appointments", lazy=True))
    checked_in_visit = db.relationship("Visit")


class PatientFollowUp(db.Model):
    __tablename__ = "patient_follow_up"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"))
    admission_id = db.Column(db.Integer, db.ForeignKey("admission.id"))
    follow_up_type = db.Column(db.String(60), nullable=False)
    scheduled_for = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="scheduled")
    reason = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    patient = db.relationship("Patient", backref=db.backref("follow_ups", lazy=True))
    visit = db.relationship("Visit", backref=db.backref("follow_ups", lazy=True))
    admission = db.relationship("Admission", backref=db.backref("follow_ups", lazy=True))


class TheatreProcedure(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    admission_id = db.Column(db.Integer, db.ForeignKey("admission.id"))
    scheduled_at = db.Column(db.DateTime, nullable=False)
    procedure_name = db.Column(db.String(255), nullable=False)
    surgeon = db.Column(db.String(100))
    anesthetist = db.Column(db.String(100))
    status = db.Column(db.String(20), nullable=False, default="scheduled")

    # Consent (detailed – matches Patients Consent Form.docx)
    consent_obtained = db.Column(db.Boolean, default=False)
    consent_procedure_description = db.Column(db.Text)
    consent_specific_risks = db.Column(db.Text)
    consent_given_by = db.Column(db.String(120))
    consent_relationship = db.Column(db.String(40))  # self / child / spouse / relative
    consent_explained_by = db.Column(db.String(100))
    consent_witness_name = db.Column(db.String(100))
    consent_notes = db.Column(db.Text)
    consented_at = db.Column(db.DateTime)
    consented_by_staff = db.Column(db.String(100))

    # Pre-anaesthetic review
    pre_anaesth_presenting_complaints = db.Column(db.Text)
    pre_anaesth_pmsh = db.Column(db.Text)
    pre_anaesth_diagnosis = db.Column(db.Text)
    pre_anaesth_operation = db.Column(db.Text)
    pre_anaesth_oe = db.Column(db.Text)
    pre_anaesth_systemic_exam = db.Column(db.Text)
    pre_anaesth_impression = db.Column(db.Text)
    pre_anaesth_plan = db.Column(db.Text)
    pre_anaesthesia_reviewed_by = db.Column(db.String(100))
    pre_anaesthesia_at = db.Column(db.DateTime)

    # Pre-operative theatre checklist
    checklist_id_card = db.Column(db.Boolean, default=False)
    checklist_starved = db.Column(db.Boolean, default=False)
    checklist_shaved = db.Column(db.Boolean, default=False)
    checklist_consent = db.Column(db.Boolean, default=False)
    checklist_catheterized = db.Column(db.Boolean, default=False)
    checklist_group_match = db.Column(db.Boolean, default=False)
    checklist_iv_line = db.Column(db.Boolean, default=False)
    checklist_dentures = db.Column(db.Boolean, default=False)
    checklist_ng_tube = db.Column(db.Boolean, default=False)
    checklist_hb = db.Column(db.Boolean, default=False)
    checklist_preop_obs = db.Column(db.Boolean, default=False)
    preop_temperature = db.Column(db.String(20))
    preop_pulse = db.Column(db.String(20))
    preop_bp = db.Column(db.String(20))
    preop_respiration = db.Column(db.String(20))
    premed_notes = db.Column(db.Text)
    preop_theatre_completed_by = db.Column(db.String(100))

    # Operation notes
    preoperative_diagnosis = db.Column(db.Text)
    incision = db.Column(db.String(255))
    scrub_nurse = db.Column(db.String(100))
    operation_time_from = db.Column(db.String(20))
    operation_time_to = db.Column(db.String(20))
    anesthesia_type = db.Column(db.String(100))
    procedure_performed = db.Column(db.Text)
    findings = db.Column(db.Text)
    anesthesia_notes = db.Column(db.Text)
    complications = db.Column(db.Text)
    estimated_blood_loss = db.Column(db.String(80))
    recovery_notes = db.Column(db.Text)
    postoperative_plan = db.Column(db.Text)
    post_op_diagnosis = db.Column(db.Text)
    outcome = db.Column(db.String(120))

    # Anaesthetic record key fields
    anaesth_induction = db.Column(db.String(255))
    anaesth_airway = db.Column(db.String(255))
    anaesth_premedication = db.Column(db.String(255))
    anaesth_result = db.Column(db.Text)

    # Consumables
    consumables_notes = db.Column(db.Text)
    created_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    patient = db.relationship("Patient", backref=db.backref("theatre_procedures", lazy=True))
    admission = db.relationship("Admission", backref=db.backref("theatre_procedures", lazy=True))

    attachments = db.relationship(
        "TheatreAttachment", backref="procedure", lazy=True, cascade="all, delete-orphan"
    )


class TheatreAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    procedure_id = db.Column(db.Integer, db.ForeignKey("theatre_procedure.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255))
    attachment_type = db.Column(db.String(40), default="other")
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    uploaded_by = db.Column(db.String(100))

class DialysisSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"))
    preauthorization_id = db.Column(
        db.Integer, db.ForeignKey("dialysis_authorization_request.id")
    )
    session_date = db.Column(db.Date, nullable=False)
    physician = db.Column(db.String(100))
    diagnosis = db.Column(db.Text)
    access_type = db.Column(db.String(40))
    dialysate_k = db.Column(db.String(30))
    dialysate_na = db.Column(db.String(30))
    dialysate_ca = db.Column(db.String(30))
    dialysate_bicarbonate = db.Column(db.String(30))
    pre_temperature = db.Column(db.String(30))
    pre_bp = db.Column(db.String(30))
    pre_hr = db.Column(db.String(30))
    pre_rr = db.Column(db.String(30))
    pre_current_weight = db.Column(db.String(30))
    pre_dry_weight = db.Column(db.String(30))
    uf_goal = db.Column(db.String(30))
    pre_symptoms = db.Column(db.Text)
    duration = db.Column(db.String(30))
    machine_serial = db.Column(db.String(80))
    heparin = db.Column(db.String(80))
    post_temperature = db.Column(db.String(30))
    post_bp = db.Column(db.String(30))
    post_hr = db.Column(db.String(30))
    post_rr = db.Column(db.String(30))
    post_current_weight = db.Column(db.String(30))
    post_dry_weight = db.Column(db.String(30))
    uf_achieved = db.Column(db.String(30))
    post_symptoms = db.Column(db.Text)
    medication = db.Column(db.Text)
    brief_comments = db.Column(db.Text)
    served_by = db.Column(db.String(100))
    signed_by = db.Column(db.String(100))
    signed_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="in_progress")
    completed_at = db.Column(db.DateTime)
    completed_by = db.Column(db.String(100))
    sha_authorization_number = db.Column(db.String(100))
    sha_authorization_status = db.Column(db.String(20), nullable=False, default="pending")
    created_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    patient = db.relationship("Patient", backref=db.backref("dialysis_sessions", lazy=True))
    visit = db.relationship("Visit")
    observations = db.relationship(
        "DialysisObservation",
        backref="dialysis_session",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="DialysisObservation.id",
    )


class DialysisObservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dialysis_session_id = db.Column(
        db.Integer, db.ForeignKey("dialysis_session.id"), nullable=False
    )
    reading_time = db.Column(db.String(20))
    blood_pressure = db.Column(db.String(30))
    pulse = db.Column(db.String(30))
    arterial_pressure = db.Column(db.String(30))
    venous_pressure = db.Column(db.String(30))
    blood_flow_rate = db.Column(db.String(30))
    dialysate_flow_rate = db.Column(db.String(30))
    tmp = db.Column(db.String(30))
    ufr = db.Column(db.String(30))
    fluids_removed = db.Column(db.String(30))
    comments = db.Column(db.Text)


class DialysisAuthorizationRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    sha_member_number = db.Column(db.String(100))
    diagnosis = db.Column(db.Text, nullable=False)
    clinical_justification = db.Column(db.Text, nullable=False)
    requested_sessions = db.Column(db.Integer, nullable=False)
    submission_reference = db.Column(db.String(100))
    submitted_at = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(20), nullable=False, default="pending")
    authorization_number = db.Column(db.String(100))
    approved_sessions = db.Column(db.Integer)
    valid_from = db.Column(db.Date)
    valid_until = db.Column(db.Date)
    response_notes = db.Column(db.Text)
    requested_by = db.Column(db.String(100))
    reviewed_by = db.Column(db.String(100))
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    patient = db.relationship(
        "Patient", backref=db.backref("dialysis_authorization_requests", lazy=True)
    )


class InvoicePayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_method = db.Column(db.String(80), nullable=False)
    received_by = db.Column(db.String(100), nullable=False)
    received_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    notes = db.Column(db.String(255))

    visit = db.relationship(
        "Visit", backref=db.backref("payments", lazy=True, order_by=received_at.desc())
    )


class QueueEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    doctor = db.Column(db.String(100))
    status = db.Column(
        db.String(30), default="queued"
    )  # queued → with_nurse → with_doctor → completed
    queued_at = db.Column(db.DateTime, default=datetime.utcnow)

    visit = db.relationship("Visit", backref=db.backref("queue_entry", uselist=False))
    patient = db.relationship("Patient", backref=db.backref("queue_entries", lazy=True))

class Diagnosis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"), nullable=True)
    icd_code = db.Column(db.String(40), nullable=False)
    diagnosis_text = db.Column(db.String(255), nullable=False)
    icd_version = db.Column(db.String(10), default="ICD-11")
    diagnosed_by = db.Column(db.String(100))
    diagnosed_at = db.Column(db.DateTime, default=datetime.utcnow)

class VitalSigns(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    bp = db.Column(db.String(20))  # e.g. "120/80"
    temperature = db.Column(db.Float)
    pulse = db.Column(db.Integer)
    respiration = db.Column(db.Integer)
    pain_score = db.Column(db.Integer)
    weight = db.Column(db.Float)
    height = db.Column(db.Float)
    oxygen_sat = db.Column(db.Float)  # SpO2
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship(
        "Patient", backref=db.backref("vitals", lazy=True, order_by=timestamp.desc())
    )


class ClinicalNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"))
    note = db.Column(db.Text, nullable=False)
    doctor = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship(
        "Patient",
        backref=db.backref("clinical_notes", lazy=True, order_by=timestamp.desc()),
    )
    visit = db.relationship(
        "Visit", backref=db.backref("clinical_notes", lazy=True)
    )
    @property
    def note_plain(self):
        return decrypt_text(self.note)


class Consultation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)

    history = db.Column(db.Text)  # History of Presenting Illness
    examination = db.Column(db.Text)  # Physical examination findings
    diagnosis = db.Column(db.Text)  # Diagnosis
    plan = db.Column(db.Text)  # Management plan

    doctor_name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    visit = db.relationship("Visit", backref=db.backref("consultation", uselist=False))
    patient = db.relationship("Patient", backref=db.backref("consultations", lazy=True))


class ServiceRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    service_type = db.Column(db.String(50))  # Lab, Pharmacy, Imaging, Referral
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default="Pending")
    requested_by = db.Column(db.String(100))
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship(
        "Patient",
        backref=db.backref("service_requests", lazy=True, order_by=requested_at.desc()),
    )


class Prescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"))
    item_type = db.Column(db.String(30), default="Medicine", nullable=False)
    medication = db.Column(db.String(120), nullable=False)
    identifier = db.Column(db.String(80))
    needs_refill = db.Column(db.Boolean, default=False)
    rx_take = db.Column(db.Integer, default=1, nullable=False)
    strength = db.Column(db.String(60))
    dosage = db.Column(db.String(80), nullable=False)
    form = db.Column(db.String(50))
    interval = db.Column(db.String(30))
    frequency = db.Column(db.String(80), nullable=False)
    frequency_unit = db.Column(db.String(20), default="Hours")
    route = db.Column(db.String(50), default="Oral")
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
    status = db.Column(db.String(20), default="prescribed")
    dispensed_by = db.Column(db.String(100))
    dispensed_at = db.Column(db.DateTime)
    dispensed_units = db.Column(db.Integer)
    unit_price = db.Column(db.Numeric(10, 2))
    pharmacy_amount = db.Column(db.Numeric(10, 2))

    patient = db.relationship(
        "Patient",
        backref=db.backref("prescriptions", lazy=True, order_by=prescribed_at.desc()),
    )
    visit = db.relationship("Visit", backref=db.backref("prescriptions", lazy=True))


class MedicineStock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    medication = db.Column(db.String(120), unique=True, nullable=False)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    reorder_level = db.Column(db.Integer, default=10, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(50), nullable=False)
    target_id = db.Column(db.String(80), nullable=False)
    details = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    actor = db.relationship("User")


class ImagingStudy(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"))
    service_request_id = db.Column(db.Integer, db.ForeignKey("service_request.id"))

    modality = db.Column(db.String(30), nullable=False)  # X-Ray, Ultrasound, CT, MRI
    exam_name = db.Column(db.String(120), nullable=False)
    clinical_indication = db.Column(db.Text)

    status = db.Column(
        db.String(20), default="pending"
    )  # pending, in_progress, completed, cancelled

    requested_by = db.Column(db.String(100))
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)

    performed_by = db.Column(db.String(100))
    performed_at = db.Column(db.DateTime)

    findings = db.Column(db.Text)
    impression = db.Column(db.Text)
    charge_amount = db.Column(db.Numeric(10, 2))
    reviewed_by = db.Column(db.String(100))
    reviewed_at = db.Column(db.DateTime)

    patient = db.relationship(
        "Patient", backref=db.backref("imaging_studies", lazy=True)
    )
    visit = db.relationship("Visit", backref=db.backref("imaging_studies", lazy=True))
    service_request = db.relationship(
        "ServiceRequest", backref=db.backref("imaging_study", uselist=False)
    )
    images = db.relationship(
        "ImagingImage", backref="study", lazy=True, cascade="all, delete-orphan"
    )


class ImagingImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    study_id = db.Column(db.Integer, db.ForeignKey("imaging_study.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    uploaded_by = db.Column(db.String(100))

class Ward(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    department = db.Column(db.String(80))
    active = db.Column(db.Boolean, default=True)

    beds = db.relationship("Bed", backref="ward", lazy=True)


class Bed(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ward_id = db.Column(db.Integer, db.ForeignKey("ward.id"), nullable=False)
    label = db.Column(db.String(30), nullable=False)  # e.g. A1, B2
    status = db.Column(db.String(20), default="available")  # available | occupied | blocked
    active = db.Column(db.Boolean, default=True)

    __table_args__ = (db.UniqueConstraint("ward_id", "label", name="uq_ward_bed"),)


class Admission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    bed_id = db.Column(db.Integer, db.ForeignKey("bed.id"), nullable=False)
    admitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    admitted_by = db.Column(db.String(100))
    admitting_doctor = db.Column(db.String(100))
    reason = db.Column(db.String(255))
    status = db.Column(db.String(20), default="admitted")  # admitted | discharged
    discharged_at = db.Column(db.DateTime)
    discharged_by = db.Column(db.String(100))
    discharge_notes = db.Column(db.String(255))
    discharge_diagnosis = db.Column(db.String(255))
    discharge_diagnosis_code = db.Column(db.String(40))
    hospital_course = db.Column(db.Text)
    discharge_condition = db.Column(db.String(80))
    discharge_medications = db.Column(db.Text)
    follow_up_instructions = db.Column(db.Text)
    discharge_summary_saved_at = db.Column(db.DateTime)
    discharge_summary_saved_by = db.Column(db.String(100))
    discharge_summary_discharge_at = db.Column(db.DateTime)
    visit_id = db.Column(db.Integer, db.ForeignKey("visit.id"), nullable=True)
    admission_fee = db.Column(db.Numeric(10, 2))
    bed_daily_rate = db.Column(db.Numeric(10, 2))
    bed_days = db.Column(db.Integer)
    bed_charges = db.Column(db.Numeric(10, 2))
    doctor_fee = db.Column(db.Numeric(10, 2))
    nursing_fee = db.Column(db.Numeric(10, 2))
    consumables_fee = db.Column(db.Numeric(10, 2))

    patient = db.relationship("Patient", backref=db.backref("admissions", lazy=True))
    bed = db.relationship("Bed", backref=db.backref("admissions", lazy=True))

class WardNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admission.id"), nullable=False) 
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"), nullable=False)
    note = db.Column(db.Text, nullable=False)
    note_type = db.Column(db.String(40), default="nursing")  # nursing | doctor | progress
    recorded_by = db.Column(db.String(100))
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    admission = db.relationship("Admission", backref=db.backref("ward_notes", lazy=True))
    patient = db.relationship("Patient", backref=db.backref("ward_notes", lazy=True))

#routes
# AUTHentication
def login_required(roles=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            if roles:
                allowed_roles = {roles} if isinstance(roles, str) else set(roles)
                allowed_roles.add("admin")
                if session.get("role") not in allowed_roles:
                    flash("Access denied for your role.", "danger")
                    return redirect(url_for("dashboard"))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}


@app.before_request
def validate_csrf_token():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    if request.endpoint == "login" and request.method == "POST":
        return None

    supplied_token = (
        request.headers.get("X-CSRF-Token")
        if request.is_json
        else request.form.get("csrf_token")
    )
    expected_token = session.get("_csrf_token")
    if (
        expected_token
        and supplied_token
        and secrets.compare_digest(expected_token, supplied_token)
    ):
        return None

    if request.is_json:
        return jsonify({"error": "Invalid or missing CSRF token."}), 400
    flash("Your form expired or could not be verified. Please try again.", "danger")
    return redirect(request.referrer or url_for("login"))

@app.before_request
def refresh_session():
    session.permanent = True
    session.modified = True

def audit(action, target_type, target_id, details=None):
    db.session.add(
        AuditLog(
            actor_user_id=session.get("user_id"),
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        )
    )


def valid_password(password):
    return len(password) >= 12


def parse_optional_number(raw_value, label, minimum=None, maximum=None, integer=False):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        value = int(raw_value) if integer else float(raw_value)
    except ValueError as error:
        raise ValueError(f"{label} must be a number.") from error
    if (
        not math.isfinite(value)
        or (minimum is not None and value < minimum)
        or (maximum is not None and value > maximum)
    ):
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return value


def parse_optional_date(raw_value):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError("Please enter a valid prescription date.") from error


def ensure_schema():
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        with app.app_context():
            inspector = inspect(db.engine)
            if inspector.has_table("clinical_note"):
                clinical_note_columns = {
                    column["name"]
                    for column in inspector.get_columns("clinical_note")
                }
                if "visit_id" not in clinical_note_columns:
                    db.session.execute(
                        text(
                            "ALTER TABLE clinical_note ADD COLUMN IF NOT EXISTS "
                            "visit_id INTEGER REFERENCES visit(id)"
                        )
                    )
                    db.session.commit()
        return
    with app.app_context():
        db.create_all()
        visit_columns = {
            row[1] for row in db.session.execute(text("PRAGMA table_info(visit)"))
        }
        if "status" not in visit_columns:
            db.session.execute(
                text(
                    "ALTER TABLE visit ADD COLUMN status VARCHAR(30) DEFAULT 'registered'"
                )
            )
        if "completed_at" not in visit_columns:
            db.session.execute(
                text("ALTER TABLE visit ADD COLUMN completed_at DATETIME")
            )
        if "consultation_amount" not in visit_columns:
            db.session.execute(
                text("ALTER TABLE visit ADD COLUMN consultation_amount NUMERIC(10, 2)")
            )
        clinical_note_columns = {
            row[1]
            for row in db.session.execute(text("PRAGMA table_info(clinical_note)"))
        }
        if "visit_id" not in clinical_note_columns:
            db.session.execute(
                text(
                    "ALTER TABLE clinical_note ADD COLUMN visit_id "
                    "INTEGER REFERENCES visit(id)"
                )
            )

        patient_follow_up_columns = {
            row[1]
            for row in db.session.execute(text("PRAGMA table_info(patient_follow_up)"))
        }
        follow_up_migrations = {
            "visit_id": "ALTER TABLE patient_follow_up ADD COLUMN visit_id INTEGER",
            "admission_id": "ALTER TABLE patient_follow_up ADD COLUMN admission_id INTEGER",
            "scheduled_for": "ALTER TABLE patient_follow_up ADD COLUMN scheduled_for DATETIME",
            "status": "ALTER TABLE patient_follow_up ADD COLUMN status VARCHAR(20) DEFAULT 'scheduled'",
            "reason": "ALTER TABLE patient_follow_up ADD COLUMN reason VARCHAR(255)",
            "notes": "ALTER TABLE patient_follow_up ADD COLUMN notes TEXT",
            "created_by": "ALTER TABLE patient_follow_up ADD COLUMN created_by VARCHAR(100)",
            "created_at": "ALTER TABLE patient_follow_up ADD COLUMN created_at DATETIME",
            "completed_at": "ALTER TABLE patient_follow_up ADD COLUMN completed_at DATETIME",
            #"allergies": "ALTER TABLE patient ADD COLUMN allergies TEXT",
        }
        for column, migration in follow_up_migrations.items():
            if column not in patient_follow_up_columns:
                db.session.execute(text(migration))

        visits_with_old_numbers = (
            db.session.execute(
                text(
                    "SELECT id FROM visit WHERE invoice_number IS NULL "
                    "OR invoice_number = '' OR invoice_number NOT LIKE 'INV-%'"
                )
            )
            .scalars()
            .all()
        )
        for visit_id in visits_with_old_numbers:
            db.session.execute(
                text(
                    "UPDATE visit SET invoice_number = :invoice_number WHERE id = :visit_id"
                ),
                {"invoice_number": f"INV-{visit_id:06d}", "visit_id": visit_id},
            )
        vital_columns = {
            row[1] for row in db.session.execute(text("PRAGMA table_info(vital_signs)"))
        }
        if "pain_score" not in vital_columns:
            db.session.execute(
                text("ALTER TABLE vital_signs ADD COLUMN pain_score INTEGER")
            )
        dialysis_columns = {
            row[1]
            for row in db.session.execute(
                text("PRAGMA table_info(dialysis_session)")
            )
        }
        dialysis_migrations = {
            "status": "ALTER TABLE dialysis_session ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'in_progress'",
            "completed_at": "ALTER TABLE dialysis_session ADD COLUMN completed_at DATETIME",
            "completed_by": "ALTER TABLE dialysis_session ADD COLUMN completed_by VARCHAR(100)",
            "preauthorization_id": "ALTER TABLE dialysis_session ADD COLUMN preauthorization_id INTEGER",
        }
        for column, migration in dialysis_migrations.items():
            if column not in dialysis_columns:
                db.session.execute(text(migration))
        prescription_columns = {
            row[1]
            for row in db.session.execute(text("PRAGMA table_info(prescription)"))
        }
        prescription_migrations = {
            "item_type": "ALTER TABLE prescription ADD COLUMN item_type VARCHAR(30) DEFAULT 'Medicine'",
            "identifier": "ALTER TABLE prescription ADD COLUMN identifier VARCHAR(80)",
            "needs_refill": "ALTER TABLE prescription ADD COLUMN needs_refill BOOLEAN DEFAULT 0",
            "rx_take": "ALTER TABLE prescription ADD COLUMN rx_take INTEGER DEFAULT 1",
            "form": "ALTER TABLE prescription ADD COLUMN form VARCHAR(50)",
            "interval": "ALTER TABLE prescription ADD COLUMN interval VARCHAR(30)",
            "frequency_unit": "ALTER TABLE prescription ADD COLUMN frequency_unit VARCHAR(20) DEFAULT 'Hours'",
            "route": "ALTER TABLE prescription ADD COLUMN route VARCHAR(50) DEFAULT 'Oral'",
            "indication": "ALTER TABLE prescription ADD COLUMN indication VARCHAR(160)",
            "start_date": "ALTER TABLE prescription ADD COLUMN start_date DATE",
            "end_date": "ALTER TABLE prescription ADD COLUMN end_date DATE",
            "as_needed": "ALTER TABLE prescription ADD COLUMN as_needed BOOLEAN DEFAULT 0",
            "take_as_prescribed": "ALTER TABLE prescription ADD COLUMN take_as_prescribed BOOLEAN DEFAULT 1",
            "dispensed_by": "ALTER TABLE prescription ADD COLUMN dispensed_by VARCHAR(100)",
            "dispensed_at": "ALTER TABLE prescription ADD COLUMN dispensed_at DATETIME",
            "visit_id": "ALTER TABLE prescription ADD COLUMN visit_id INTEGER",
            "dispensed_units": "ALTER TABLE prescription ADD COLUMN dispensed_units INTEGER",
            "unit_price": "ALTER TABLE prescription ADD COLUMN unit_price NUMERIC(10, 2)",
            "pharmacy_amount": "ALTER TABLE prescription ADD COLUMN pharmacy_amount NUMERIC(10, 2)",
        }
        for column, migration in prescription_migrations.items():
            if column not in prescription_columns:
                db.session.execute(text(migration))
        db.session.execute(
            text(
                "UPDATE prescription SET visit_id = ("
                "SELECT visit.id FROM visit "
                "WHERE visit.patient_id = prescription.patient_id "
                "AND visit.started_at <= prescription.prescribed_at "
                "ORDER BY visit.started_at DESC LIMIT 1) "
                "WHERE prescription.visit_id IS NULL"
            )
        )
        imaging_columns = {
            row[1]
            for row in db.session.execute(text("PRAGMA table_info(imaging_study)"))
        }
        if "charge_amount" not in imaging_columns:
            db.session.execute(
                text("ALTER TABLE imaging_study ADD COLUMN charge_amount NUMERIC(10, 2)")
            )
        if "reviewed_by" not in imaging_columns:
            db.session.execute(
                text("ALTER TABLE imaging_study ADD COLUMN reviewed_by VARCHAR(100)")
            )
        if "reviewed_at" not in imaging_columns:
            db.session.execute(
                text("ALTER TABLE imaging_study ADD COLUMN reviewed_at DATETIME")
            )
        admission_columns = {
            row[1] for row in db.session.execute(text("PRAGMA table_info(admission)"))
        }
        admission_migrations = {
            "discharge_diagnosis": "ALTER TABLE admission ADD COLUMN discharge_diagnosis VARCHAR(255)",
            "discharge_diagnosis_code": "ALTER TABLE admission ADD COLUMN discharge_diagnosis_code VARCHAR(40)",
            "hospital_course": "ALTER TABLE admission ADD COLUMN hospital_course TEXT",
            "discharge_condition": "ALTER TABLE admission ADD COLUMN discharge_condition VARCHAR(80)",
            "discharge_medications": "ALTER TABLE admission ADD COLUMN discharge_medications TEXT",
            "follow_up_instructions": "ALTER TABLE admission ADD COLUMN follow_up_instructions TEXT",
            "discharge_summary_saved_at": "ALTER TABLE admission ADD COLUMN discharge_summary_saved_at DATETIME",
            "discharge_summary_saved_by": "ALTER TABLE admission ADD COLUMN discharge_summary_saved_by VARCHAR(100)",
            "discharge_summary_discharge_at": "ALTER TABLE admission ADD COLUMN discharge_summary_discharge_at DATETIME",
        }
        for column, migration in admission_migrations.items():
            if column not in admission_columns:
                db.session.execute(text(migration))
        diagnosis_columns = {
            row[1] for row in db.session.execute(text("PRAGMA table_info(diagnosis)"))
        }
        if "icd_version" not in diagnosis_columns:
            db.session.execute(
                text(
                    "ALTER TABLE diagnosis ADD COLUMN icd_version VARCHAR(10) DEFAULT 'ICD-10'"
                )
            )

        theatre_columns = {
            row[1] for row in db.session.execute(text("PRAGMA table_info(theatre_procedure)"))
        }
        theatre_migrations = {
            "consent_obtained": "ALTER TABLE theatre_procedure ADD COLUMN consent_obtained BOOLEAN DEFAULT 0",
            "consent_procedure_description": "ALTER TABLE theatre_procedure ADD COLUMN consent_procedure_description TEXT",
            "consent_specific_risks": "ALTER TABLE theatre_procedure ADD COLUMN consent_specific_risks TEXT",
            "consent_given_by": "ALTER TABLE theatre_procedure ADD COLUMN consent_given_by VARCHAR(120)",
            "consent_relationship": "ALTER TABLE theatre_procedure ADD COLUMN consent_relationship VARCHAR(40)",
            "consent_explained_by": "ALTER TABLE theatre_procedure ADD COLUMN consent_explained_by VARCHAR(100)",
            "consent_witness_name": "ALTER TABLE theatre_procedure ADD COLUMN consent_witness_name VARCHAR(100)",
            "consent_notes": "ALTER TABLE theatre_procedure ADD COLUMN consent_notes TEXT",
            "consented_at": "ALTER TABLE theatre_procedure ADD COLUMN consented_at DATETIME",
            "consented_by_staff": "ALTER TABLE theatre_procedure ADD COLUMN consented_by_staff VARCHAR(100)",
            "pre_anaesth_presenting_complaints": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesth_presenting_complaints TEXT",
            "pre_anaesth_pmsh": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesth_pmsh TEXT",
            "pre_anaesth_diagnosis": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesth_diagnosis TEXT",
            "pre_anaesth_operation": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesth_operation TEXT",
            "pre_anaesth_oe": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesth_oe TEXT",
            "pre_anaesth_systemic_exam": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesth_systemic_exam TEXT",
            "pre_anaesth_impression": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesth_impression TEXT",
            "pre_anaesth_plan": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesth_plan TEXT",
            "pre_anaesthesia_reviewed_by": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesthesia_reviewed_by VARCHAR(100)",
            "pre_anaesthesia_at": "ALTER TABLE theatre_procedure ADD COLUMN pre_anaesthesia_at DATETIME",
            "checklist_id_card": "ALTER TABLE theatre_procedure ADD COLUMN checklist_id_card BOOLEAN DEFAULT 0",
            "checklist_starved": "ALTER TABLE theatre_procedure ADD COLUMN checklist_starved BOOLEAN DEFAULT 0",
            "checklist_shaved": "ALTER TABLE theatre_procedure ADD COLUMN checklist_shaved BOOLEAN DEFAULT 0",
            "checklist_consent": "ALTER TABLE theatre_procedure ADD COLUMN checklist_consent BOOLEAN DEFAULT 0",
            "checklist_catheterized": "ALTER TABLE theatre_procedure ADD COLUMN checklist_catheterized BOOLEAN DEFAULT 0",
            "checklist_group_match": "ALTER TABLE theatre_procedure ADD COLUMN checklist_group_match BOOLEAN DEFAULT 0",
            "checklist_iv_line": "ALTER TABLE theatre_procedure ADD COLUMN checklist_iv_line BOOLEAN DEFAULT 0",
            "checklist_dentures": "ALTER TABLE theatre_procedure ADD COLUMN checklist_dentures BOOLEAN DEFAULT 0",
            "checklist_ng_tube": "ALTER TABLE theatre_procedure ADD COLUMN checklist_ng_tube BOOLEAN DEFAULT 0",
            "checklist_hb": "ALTER TABLE theatre_procedure ADD COLUMN checklist_hb BOOLEAN DEFAULT 0",
            "checklist_preop_obs": "ALTER TABLE theatre_procedure ADD COLUMN checklist_preop_obs BOOLEAN DEFAULT 0",
            "preop_temperature": "ALTER TABLE theatre_procedure ADD COLUMN preop_temperature VARCHAR(20)",
            "preop_pulse": "ALTER TABLE theatre_procedure ADD COLUMN preop_pulse VARCHAR(20)",
            "preop_bp": "ALTER TABLE theatre_procedure ADD COLUMN preop_bp VARCHAR(20)",
            "preop_respiration": "ALTER TABLE theatre_procedure ADD COLUMN preop_respiration VARCHAR(20)",
            "premed_notes": "ALTER TABLE theatre_procedure ADD COLUMN premed_notes TEXT",
            "preop_theatre_completed_by": "ALTER TABLE theatre_procedure ADD COLUMN preop_theatre_completed_by VARCHAR(100)",
            "incision": "ALTER TABLE theatre_procedure ADD COLUMN incision VARCHAR(255)",
            "scrub_nurse": "ALTER TABLE theatre_procedure ADD COLUMN scrub_nurse VARCHAR(100)",
            "operation_time_from": "ALTER TABLE theatre_procedure ADD COLUMN operation_time_from VARCHAR(20)",
            "operation_time_to": "ALTER TABLE theatre_procedure ADD COLUMN operation_time_to VARCHAR(20)",
            "post_op_diagnosis": "ALTER TABLE theatre_procedure ADD COLUMN post_op_diagnosis TEXT",
            "outcome": "ALTER TABLE theatre_procedure ADD COLUMN outcome VARCHAR(120)",
            "anaesth_induction": "ALTER TABLE theatre_procedure ADD COLUMN anaesth_induction VARCHAR(255)",
            "anaesth_airway": "ALTER TABLE theatre_procedure ADD COLUMN anaesth_airway VARCHAR(255)",
            "anaesth_premedication": "ALTER TABLE theatre_procedure ADD COLUMN anaesth_premedication VARCHAR(255)",
            "anaesth_result": "ALTER TABLE theatre_procedure ADD COLUMN anaesth_result TEXT",
            "consumables_notes": "ALTER TABLE theatre_procedure ADD COLUMN consumables_notes TEXT",
            "post_op_diagnosis": "ALTER TABLE theatre_procedure ADD COLUMN post_op_diagnosis TEXT",
            "outcome": "ALTER TABLE theatre_procedure ADD COLUMN outcome VARCHAR(120)",
            "anaesth_induction": "ALTER TABLE theatre_procedure ADD COLUMN anaesth_induction VARCHAR(255)",
            "anaesth_airway": "ALTER TABLE theatre_procedure ADD COLUMN anaesth_airway VARCHAR(255)",
            "anaesth_premedication": "ALTER TABLE theatre_procedure ADD COLUMN anaesth_premedication VARCHAR(255)",
            "anaesth_result": "ALTER TABLE theatre_procedure ADD COLUMN anaesth_result TEXT",
            "consumables_notes": "ALTER TABLE theatre_procedure ADD COLUMN consumables_notes TEXT",
                    }
        for column, migration in theatre_migrations.items():
            if column not in theatre_columns:
                db.session.execute(text(migration))

        db.session.commit()

with app.app_context():
    ensure_schema()


@app.cli.command("create-admin")
@click.option("--username", prompt=True, help="Unique username for the administrator.")
@click.option("--full-name", prompt=True, help="Administrator display name.")
@click.password_option(confirmation_prompt=True)
def create_admin(username, full_name, password):
    """Create the first administrator without embedding credentials in source code."""
    username = username.strip().lower()
    full_name = full_name.strip()
    if not username or not full_name:
        raise click.UsageError("Username and full name are required.")
    if not valid_password(password):
        raise click.UsageError("Password must be at least 12 characters long.")
    if User.query.filter_by(username=username).first():
        raise click.UsageError("That username already exists.")

    admin = User(username=username, full_name=full_name, role="admin")
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    click.echo(f"Administrator {username} created.")


#login route
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session.clear()
            session.permanent = True
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role
            session["full_name"] = user.full_name
            audit("login_success", "user", user.id, f"role={user.role}")
            db.session.commit()
            flash(f"Welcome, {user.full_name}!", "success")
            return redirect(url_for("dashboard"))
        db.session.add(
            AuditLog(
                actor_user_id=None,
                action="login_failed",
                target_type="username",
                target_id=username[:80] or "(blank)",
                details="Invalid credentials",
            )
        )
        db.session.commit()
        flash("Invalid username or password", "danger")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required()
def logout():
    audit("logout", "user", session["user_id"])
    db.session.commit()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/admin/register-staff", methods=["GET", "POST"])
@login_required(roles="admin")
def register_staff():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role")
        password = request.form.get("password", "").strip()

        if not username or not full_name or not password:
            flash("All fields are required.", "danger")
            return redirect(url_for("register_staff"))
        if role not in VALID_ROLES:
            flash("Please choose a valid staff role.", "danger")
            return redirect(url_for("register_staff"))
        if not valid_password(password):
            flash("Password must be at least 12 characters long.", "danger")
            return redirect(url_for("register_staff"))

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return redirect(url_for("register_staff"))

        new_user = User(username=username, full_name=full_name, role=role)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.flush()
        audit("staff_created", "user", new_user.id, f"role={role}")
        db.session.commit()

        flash(f'Staff "{full_name}" registered successfully!', "success")
        return redirect(url_for("dashboard"))

    return render_template("register_staff.html")

@app.route("/api/icd11")
@login_required(roles=CLINICAL_READ_ROLES | INPATIENT_ROLES | {"admin"})
def icd11_search():
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return {"results": []}
    hits = [
        row for row in load_icd11()
        if q in row["code"].lower() or q in row["text"].lower()
    ][:15]
    return {"results": hits}

#main routes
@app.route("/")
@login_required()
def dashboard():
    return render_template("dashboard.html")


@app.route("/patient/<national_id>")
@login_required(roles=PATIENT_ACCESS_ROLES)
def patient_details(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    visit = (
        Visit.query.filter_by(patient_id=patient.id)
        .order_by(Visit.started_at.desc())
        .first()
    )
    queue_identifier = get_queue_identifier(patient.id)
    return render_template(
        "patient.html",
        patient=patient,
        last_visit=visit,
        queue_identifier=queue_identifier,
    )


@app.route("/patient/<national_id>/records")
@login_required(roles=PATIENT_ACCESS_ROLES)
def patient_records(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    visits = (
        Visit.query.filter_by(patient_id=patient.id)
        .order_by(Visit.started_at.desc())
        .all()
    )
    return render_template("patient_records.html", patient=patient, visits=visits)


@app.route("/patient/<national_id>/card", methods=["GET", "POST"])
@login_required(roles=CLINICAL_READ_ROLES)
def patient_card(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()

    if request.method == "POST":
        action = request.form.get("action")
        user_role = session.get("role")

        if action == "vitals":
            if user_role not in VITALS_ENTRY_ROLES and user_role != "admin":
                flash("Only authorized medical staff can record vitals.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))
            try:
                temperature = parse_optional_number(
                    request.form.get("temperature"), "Temperature", 30, 45
                )
                pulse = parse_optional_number(
                    request.form.get("pulse"), "Pulse", 20, 250, integer=True
                )
                respiration = parse_optional_number(
                    request.form.get("respiration"), "Respiration", 4, 80, integer=True
                )
                pain_score = parse_optional_number(
                    request.form.get("pain_score"), "Pain score", 1, 10, integer=True
                )
                weight = parse_optional_number(
                    request.form.get("weight"), "Weight", 0.5, 500
                )
                height = parse_optional_number(
                    request.form.get("height"), "Height", 20, 300
                )
                oxygen_sat = parse_optional_number(
                    request.form.get("oxygen_sat"), "Oxygen saturation", 0, 100
                )
            except ValueError as error:
                flash(str(error), "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            new_vital = VitalSigns(
                patient_id=patient.id,
                bp=request.form.get("bp", "").strip()[:20] or None,
                temperature=temperature,
                pulse=pulse,
                respiration=respiration,
                pain_score=pain_score,
                weight=weight,
                height=height,
                oxygen_sat=oxygen_sat,
                notes=request.form.get("vital_notes", "").strip() or None,
                recorded_by=session.get("full_name", "Staff"),
            )
            db.session.add(new_vital)
            db.session.flush()
            audit(
                "vitals_recorded",
                "vital_signs",
                new_vital.id,
                f"patient_id={patient.id}",
            )
            flash("Vitals recorded successfully!", "success")

        elif action == "note":
            if user_role not in NOTE_ENTRY_ROLES and user_role != "admin":
                flash("Only medical staff can write clinical notes.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))
            soap_sections = [
                ("Subjective", request.form.get("history", "").strip()),
                ("Objective", request.form.get("examination", "").strip()),
                ("Assessment", request.form.get("diagnosis", "").strip()),
                ("Plan", request.form.get("plan", "").strip()),
            ]
            note = "\n\n".join(
                f"{label}:\n{content}" for label, content in soap_sections if content
            )
            if not note:
                flash(
                    "Enter at least one section before saving the clinical note.",
                    "danger",
                )
                return redirect(url_for("patient_card", national_id=national_id))

            active_visit = (
                Visit.query.filter(
                    Visit.patient_id == patient.id,
                    db.or_(Visit.status != "completed", Visit.status.is_(None)),
                )
                .order_by(Visit.started_at.desc())
                .first()
            )
            new_note = ClinicalNote(
                patient_id=patient.id,
                visit_id=active_visit.id if active_visit else None,
                note=encrypt_text(note),
                doctor=session.get("full_name", "Staff"),
            )
            db.session.add(new_note)
            db.session.flush()
            audit(
                "clinical_note_recorded",
                "clinical_note",
                new_note.id,
                f"patient_id={patient.id}",
            )
            flash("Clinical note saved!", "success")

        elif action == "service_request":
            if user_role not in SERVICE_REQUEST_ROLES and user_role != "admin":
                flash("Only doctors can request clinical services.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            service_type = request.form.get("service_type", "").strip()
            if service_type not in {"Lab", "Pharmacy", "Radiology", "Referral"}:
                flash("Please choose a valid service.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            if service_type == "Radiology":
                modality = request.form.get("modality", "").strip()
                exam_name = request.form.get("exam_name", "").strip()
                clinical_indication = request.form.get(
                    "clinical_indication", ""
                ).strip()
                if (
                    modality not in IMAGING_MODALITIES
                    or exam_name not in IMAGING_MODALITIES.get(modality, [])
                ):
                    flash("Please select a valid imaging modality and exam.", "danger")
                    return redirect(url_for("patient_card", national_id=national_id))

                description = f"{modality}: {exam_name}"
                if clinical_indication:
                    description += f" — {clinical_indication}"

                svc = ServiceRequest(
                    patient_id=patient.id,
                    service_type="Radiology",
                    description=description,
                    requested_by=session.get("full_name", "Doctor"),
                )
                db.session.add(svc)
                db.session.flush()

                active_visit = (
                    Visit.query.filter(
                        Visit.patient_id == patient.id,
                        db.or_(Visit.status != "completed", Visit.status.is_(None)),
                    )
                    .order_by(Visit.started_at.desc())
                    .first()
                )
                study = ImagingStudy(
                    patient_id=patient.id,
                    visit_id=active_visit.id if active_visit else None,
                    service_request_id=svc.id,
                    modality=modality,
                    exam_name=exam_name,
                    clinical_indication=clinical_indication or None,
                    requested_by=session.get("full_name", "Doctor"),
                    status="pending",
                    charge_amount=IMAGING_PRICES.get(exam_name, 0),
                )
                db.session.add(study)
                db.session.flush()
                audit(
                    "imaging_requested",
                    "imaging_study",
                    study.id,
                    f"patient_id={patient.id}; {modality}/{exam_name}",
                )
                flash(f"{exam_name} request sent to Radiology.", "success")

            elif service_type == "Lab":
                selected_test_ids = request.form.getlist("lab_test_ids")
                if not selected_test_ids:
                    flash("Please select at least one laboratory test.", "danger")
                    return redirect(url_for("patient_card", national_id=national_id))

                LabTest = app.config.get("LAB_TEST_MODEL")
                LabOrder = app.config.get("LAB_ORDER_MODEL")
                LabOrderItem = app.config.get("LAB_ORDER_ITEM_MODEL")
                if not LabTest or not LabOrder or not LabOrderItem:
                    flash("Laboratory catalogue is not available.", "danger")
                    return redirect(url_for("patient_card", national_id=national_id))

                try:
                    test_ids = [int(test_id) for test_id in selected_test_ids]
                except ValueError:
                    flash("Invalid laboratory test selection.", "danger")
                    return redirect(url_for("patient_card", national_id=national_id))

                tests = (
                    LabTest.query.filter(
                        LabTest.id.in_(test_ids), LabTest.active.is_(True)
                    )
                    .order_by(LabTest.id)
                    .all()
                )
                if len(tests) != len(set(test_ids)):
                    flash(
                        "One or more selected laboratory tests are invalid or inactive.",
                        "danger",
                    )
                    return redirect(url_for("patient_card", national_id=national_id))

                active_visit = (
                    Visit.query.filter(
                        Visit.patient_id == patient.id,
                        db.or_(Visit.status != "completed", Visit.status.is_(None)),
                    )
                    .order_by(Visit.started_at.desc())
                    .first()
                )
                if not active_visit:
                    flash("The patient does not have an active visit.", "danger")
                    return redirect(url_for("patient_card", national_id=national_id))

                test_names = [test.test_name for test in tests]
                specimen_types = list(
                    dict.fromkeys(
                        test.specimen_type for test in tests if test.specimen_type
                    )
                )
                description = "Laboratory tests: " + ", ".join(test_names)
                svc = ServiceRequest(
                    patient_id=patient.id,
                    service_type="Lab",
                    description=description,
                    requested_by=session.get("full_name", "Doctor"),
                )
                db.session.add(svc)
                db.session.flush()

                lab_order = LabOrder(
                    patient_id=patient.id,
                    visit_id=active_visit.id if active_visit else None,
                    service_request_id=svc.id,
                    test_name=", ".join(test_names),
                    specimen_type=", ".join(specimen_types) or "See test items",
                    priority=request.form.get("lab_priority", "Routine").strip()
                    or "Routine",
                    status="Requested",
                    requested_by=session.get("full_name", "Doctor"),
                )
                db.session.add(lab_order)
                db.session.flush()

                lab_subtotal = 0
                for test in tests:
                    item_price = float(test.price or 0)
                    db.session.add(
                        LabOrderItem(
                            lab_order_id=lab_order.id,
                            lab_test_id=test.id,
                            price=item_price,
                            status="Requested",
                        )
                    )
                    lab_subtotal += item_price

                audit(
                    "laboratory_requested",
                    "lab_order",
                    lab_order.id,
                    f"patient_id={patient.id}; visit_id={active_visit.id}; tests={', '.join(test_names)}; subtotal={lab_subtotal:.2f}",
                )
                flash(
                    f"{len(tests)} laboratory test(s) sent successfully. Lab subtotal: KES {lab_subtotal:,.2f}",
                    "success",
                )

            elif service_type == "Referral":
                description = request.form.get("service_description", "").strip()
                if not description:
                    flash("Please describe the requested referral.", "danger")
                    return redirect(url_for("patient_card", national_id=national_id))
                db.session.add(
                    ServiceRequest(
                        patient_id=patient.id,
                        service_type="Referral",
                        description=description,
                        requested_by=session.get("full_name", "Doctor"),
                    )
                )
                flash("Referral request sent successfully.", "success")

        elif action == "diagnosis":
            if user_role not in NOTE_ENTRY_ROLES and user_role != "admin":
                flash("Only medical staff can record a diagnosis.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            icd_code = request.form.get("icd_code", "").strip()
            diagnosis_text = request.form.get("diagnosis_text", "").strip()
            if not icd_code or not diagnosis_text:
                flash(
                    "Select or enter an ICD code and diagnosis description.",
                    "danger",
                )
                return redirect(url_for("patient_card", national_id=national_id))

            active_visit = (
                Visit.query.filter(
                    Visit.patient_id == patient.id,
                    db.or_(Visit.status != "completed", Visit.status.is_(None)),
                )
                .order_by(Visit.started_at.desc())
                .first()
            )
            dx = Diagnosis(
                patient_id=patient.id,
                visit_id=active_visit.id if active_visit else None,
                icd_code=icd_code[:40],
                diagnosis_text=diagnosis_text[:255],
                icd_version="ICD-11",
                diagnosed_by=session.get(
                "full_name", session.get("username", "Doctor")
    ),
)
            db.session.add(dx)
            db.session.flush()
            audit(
                "diagnosis_recorded",
                "diagnosis",
                dx.id,
                f"patient_id={patient.id}; visit_id={dx.visit_id}; {icd_code} {diagnosis_text}",
            )
            flash(f"Diagnosis saved: {icd_code} — {diagnosis_text}", "success")
        elif action == "mark_lab_reviewed":
            if user_role not in NOTE_ENTRY_ROLES and user_role != "admin":
                flash("Only medical staff can mark lab results as reviewed.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))
            LabOrder = app.config.get("LAB_ORDER_MODEL")
            order_id = request.form.get("order_id", type=int)
            if not LabOrder or not order_id:
                flash("Invalid lab order.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))
            order = LabOrder.query.filter_by(id=order_id, patient_id=patient.id).first()
            if not order or order.status != "Verified":
                flash("Lab result not found or not verified yet.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))
            order.reviewed_by = session.get("full_name", session.get("username", "Doctor"))
            order.reviewed_at = datetime.utcnow()
            audit(
                "lab_result_reviewed",
                "lab_order",
                order.id,
                f"patient_id={patient.id}; by={order.reviewed_by}",
            )
            flash(f"Lab result marked as reviewed: {order.test_name}", "success")

        elif action == "mark_radiology_reviewed":
            if user_role not in NOTE_ENTRY_ROLES and user_role != "admin":
                flash("Only medical staff can mark radiology as reviewed.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))
            study_id = request.form.get("study_id", type=int)
            study = ImagingStudy.query.filter_by(
                id=study_id, patient_id=patient.id
            ).first()
            if not study or study.status != "completed":
                flash("Radiology report not found or not completed yet.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))
            study.reviewed_by = session.get(
                "full_name", session.get("username", "Doctor")
            )
            study.reviewed_at = datetime.utcnow()
            audit(
                "radiology_reviewed",
                "imaging_study",
                study.id,
                f"patient_id={patient.id}; by={study.reviewed_by}",
            )
            flash(
                f"Radiology marked as reviewed: {study.modality} — {study.exam_name}",
                "success",
            )
        elif action == "seen_by_doctor":
            if user_role not in {"doctor", "admin"}:
                flash("Only doctors can update this status.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            queue = (
                QueueEntry.query.filter(
                    QueueEntry.patient_id == patient.id,
                    QueueEntry.status.in_(
                        {
                            "queued",
                            "with_doctor",
                            "in_progress",
                            "results_ready",
                        }
                    ),
                )
                .order_by(QueueEntry.queued_at.desc())
                .first()
            )
            if queue:
                queue.status = "seen_by_doctor"
                queue.doctor = session.get(
                    "full_name", session.get("username", "Doctor")
                )
                audit(
                    "queue_seen_by_doctor",
                    "queue_entry",
                    queue.id,
                    f"patient_id={patient.id}",
                )
                flash(
                    "Patient marked seen by doctor. Removed from active doctor queue — pharmacy can dispense if prescribed.",
                    "success",
                )
            else:
                flash("No active doctor-queue entry found.", "warning")

        elif action == "prescription":
            if user_role not in SERVICE_REQUEST_ROLES and user_role != "admin":
                flash("Only doctors can prescribe medication.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))
            medication = request.form.get("medication", "").strip()
            dosage = request.form.get("dosage", "").strip()
            frequency = request.form.get("frequency", "").strip()
            frequency_unit = request.form.get("frequency_unit", "Hours").strip()
            try:
                rx_take = int(request.form.get("rx_take", "1"))
            except ValueError:
                rx_take = 0
            duration_value = request.form.get("duration", "").strip()
            duration_unit = request.form.get("duration_unit", "Days").strip()
            duration = f"{duration_value} {duration_unit}".strip()
            try:
                start_date = parse_optional_date(request.form.get("start_date"))
                end_date = parse_optional_date(request.form.get("end_date"))
            except ValueError as error:
                flash(str(error), "danger")
                return redirect(url_for("patient_card", national_id=national_id))
            if not all([medication, dosage, frequency, duration]) or rx_take not in {
                1,
                2,
                3,
            }:
                flash(
                    "Medication, dosage, frequency, and duration are required.",
                    "danger",
                )
                return redirect(url_for("patient_card", national_id=national_id))

            active_visit = (
                Visit.query.filter(
                    Visit.patient_id == patient.id,
                    db.or_(Visit.status != "completed", Visit.status.is_(None)),
                )
                .order_by(Visit.started_at.desc())
                .first()
            )
            prescription = Prescription(
                patient_id=patient.id,
                visit_id=active_visit.id if active_visit else None,
                item_type=request.form.get("item_type", "Medicine").strip()
                or "Medicine",
                medication=medication,
                identifier=request.form.get("identifier", "").strip() or None,
                needs_refill=request.form.get("needs_refill") == "on",
                rx_take=rx_take,
                strength=request.form.get("strength", "").strip() or None,
                dosage=dosage,
                form=request.form.get("form", "").strip() or None,
                interval=request.form.get("interval", "").strip() or None,
                frequency=frequency,
                frequency_unit=(
                    frequency_unit
                    if frequency_unit in {"Minutes", "Hours"}
                    else "Hours"
                ),
                route=request.form.get("route", "").strip() or "Oral",
                indication=request.form.get("indication", "").strip() or None,
                duration=duration,
                quantity=request.form.get("quantity", "").strip() or None,
                instructions=request.form.get("instructions", "").strip() or None,
                start_date=start_date,
                end_date=end_date,
                as_needed=request.form.get("administration") == "as_needed",
                take_as_prescribed=request.form.get("administration", "prescribed")
                == "prescribed",
                prescribed_by=session.get("full_name", "Doctor"),
            )
            db.session.add(prescription)
            db.session.flush()
            audit(
                "prescription_created",
                "prescription",
                prescription.id,
                f"patient_id={patient.id}",
            )
            flash("Prescription sent to pharmacy.", "success")

        elif action == "follow_up":
            if user_role not in {"doctor", "nurse", "records", "admin"}:
                flash("Only clinical staff can schedule follow-ups.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            follow_up_type = request.form.get("follow_up_type", "").strip()
            scheduled_for_raw = request.form.get("scheduled_for", "").strip()
            reason = request.form.get("reason", "").strip()
            notes = request.form.get("notes", "").strip()

            if not follow_up_type or not scheduled_for_raw:
                flash("Follow-up type and date are required.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            try:
                scheduled_for = datetime.strptime(scheduled_for_raw, "%Y-%m-%d")
            except ValueError:
                flash("Enter a valid follow-up date.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            follow_up = PatientFollowUp(
                patient_id=patient.id,
                visit_id=active_visit.id if (active_visit := (
                    Visit.query.filter(
                        Visit.patient_id == patient.id,
                        db.or_(Visit.status != "completed", Visit.status.is_(None)),
                    )
                    .order_by(Visit.started_at.desc())
                    .first()
                )) else None,
                admission_id=(
                    current_admission.id if (
                        current_admission := (
                            Admission.query.filter_by(patient_id=patient.id, status="admitted")
                            .order_by(Admission.admitted_at.desc())
                            .first()
                        )
                    ) else None
                ),
                follow_up_type=follow_up_type,
                scheduled_for=scheduled_for,
                status="scheduled",
                reason=reason or None,
                notes=notes or None,
                created_by=session.get("full_name", session.get("username", "Staff")),
            )
            db.session.add(follow_up)
            db.session.flush()
            audit(
                "follow_up_scheduled",
                "patient_follow_up",
                follow_up.id,
                f"patient_id={patient.id}; type={follow_up_type}",
            )
            db.session.commit()
            flash("Follow-up scheduled successfully.", "success")
            return redirect(url_for("patient_card", national_id=national_id))

        elif action == "update_follow_up_status":
            follow_up_id = request.form.get("follow_up_id", type=int)
            follow_up = PatientFollowUp.query.filter_by(
                id=follow_up_id, patient_id=patient.id
            ).first()
            if not follow_up:
                flash("Follow-up not found.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            new_status = request.form.get("follow_up_status", "scheduled").strip()
            if new_status not in {"scheduled", "completed", "cancelled"}:
                flash("Please choose a valid follow-up status.", "danger")
                return redirect(url_for("patient_card", national_id=national_id))

            follow_up.status = new_status
            if new_status == "completed":
                follow_up.completed_at = datetime.utcnow()
            elif new_status == "scheduled":
                follow_up.completed_at = None
            else:
                follow_up.completed_at = None

            db.session.commit()
            flash("Follow-up status updated.", "success")
            return redirect(url_for("patient_card", national_id=national_id))

        else:
            flash("Unknown patient-card action.", "danger")
            return redirect(url_for("patient_card", national_id=national_id))

        db.session.commit()
        return redirect(url_for("patient_card", national_id=national_id))

    # ---------- GET ----------
    user_role = session.get("role")
    vitals_query = VitalSigns.query.filter_by(patient_id=patient.id)
    if user_role == "doctor":
        today = datetime.utcnow().date()
        today_start = datetime.combine(today, datetime.min.time())
        tomorrow_start = today_start + timedelta(days=1)
        vitals_query = vitals_query.filter(
            VitalSigns.timestamp >= today_start,
            VitalSigns.timestamp < tomorrow_start,
        )
    vitals = vitals_query.order_by(VitalSigns.timestamp.desc()).limit(10).all()
    notes = (
        ClinicalNote.query.filter_by(patient_id=patient.id)
        .order_by(ClinicalNote.timestamp.desc())
        .limit(50)
        .all()
    )
    requests = (
        ServiceRequest.query.filter_by(patient_id=patient.id)
        .order_by(ServiceRequest.requested_at.desc())
        .limit(20)
        .all()
    )
    imaging_studies = (
        ImagingStudy.query.filter_by(patient_id=patient.id)
        .order_by(ImagingStudy.requested_at.desc())
        .limit(30)
        .all()
    )
    active_visit = (
        Visit.query.filter(
            Visit.patient_id == patient.id,
            db.or_(Visit.status != "completed", Visit.status.is_(None)),
        )
        .order_by(Visit.started_at.desc())
        .first()
    )

    if (
        active_visit
        and user_role in {"doctor", "admin"}
        and active_visit.consultation_amount is None
    ):
        active_visit.consultation_amount = CONSULTATION_FEES.get(
            active_visit.visit_type or "New Visit", 1000
        )
        if not active_visit.invoice_number:
            active_visit.invoice_number = f"INV-{active_visit.id:06d}"
        audit(
            "consultation_billed",
            "visit",
            active_visit.id,
            f'amount={active_visit.consultation_amount}; visit_type={active_visit.visit_type or "New Visit"}',
        )
        db.session.commit()
        flash("Consultation billing has been updated on the invoice.", "success")

    LabTest = app.config.get("LAB_TEST_MODEL")
    lab_tests = []
    if LabTest:
        lab_tests = (
            LabTest.query.filter_by(active=True)
            .order_by(LabTest.test_name.asc())
            .all()
        )

    LabOrder = app.config.get("LAB_ORDER_MODEL")
    ready_lab_orders = []
    if LabOrder:
        ready_lab_orders = (
            LabOrder.query.filter(
                LabOrder.patient_id == patient.id,
                LabOrder.status == "Verified",
                LabOrder.reviewed_at.is_(None),
            )
            .order_by(LabOrder.verified_at.desc())
            .limit(20)
            .all()
        )

    ready_radiology_studies = (
        ImagingStudy.query.filter(
            ImagingStudy.patient_id == patient.id,
            ImagingStudy.status == "completed",
            ImagingStudy.reviewed_at.is_(None),
        )
        .order_by(ImagingStudy.performed_at.desc())
        .limit(20)
        .all()
    )

    diagnoses = (
        Diagnosis.query.filter_by(patient_id=patient.id)
        .order_by(Diagnosis.diagnosed_at.desc())
        .limit(50)
        .all()
    )
    current_admission = (
        Admission.query.filter_by(patient_id=patient.id, status="admitted")
        .order_by(Admission.admitted_at.desc())
        .first()
    )
    dialysis_sessions = (
        DialysisSession.query.filter_by(patient_id=patient.id)
        .order_by(DialysisSession.session_date.desc(), DialysisSession.id.desc())
        .limit(20)
        .all()
    )
    has_dialysis_history = (
        DialysisSession.query.filter_by(
            patient_id=patient.id, status="completed"
        ).first()
        is not None
    )
    
    visits = (
        Visit.query.filter_by(patient_id=patient.id)
        .order_by(Visit.started_at.desc())
        .limit(30)
        .all()
    )
    visit_prescriptions = {}
    visit_diagnoses = {}
    visit_notes = {}
    for v in visits:
        visit_diagnoses[v.id] = [d for d in diagnoses if d.visit_id == v.id]
        visit_prescriptions[v.id] = (
            Prescription.query.filter_by(visit_id=v.id)
            .order_by(Prescription.prescribed_at.asc())
            .all()
        )
        visit_notes[v.id] = (
            ClinicalNote.query.filter_by(patient_id=patient.id, visit_id=v.id)
            .order_by(ClinicalNote.timestamp.asc())
            .all()
        )

    return render_template(
        "patient_card.html",
        patient=patient,
        vitals=vitals,
        notes=notes,
        requests=requests,
        imaging_studies=imaging_studies,
        imaging_modalities=IMAGING_MODALITIES,
        lab_tests=lab_tests,
        ready_lab_orders=ready_lab_orders,
        ready_radiology_studies=ready_radiology_studies,
        active_visit=active_visit,
        user_role=user_role,
        queue_identifier=get_queue_identifier(patient.id),
        diagnoses=diagnoses,
        visits=visits,
        visit_diagnoses=visit_diagnoses,
        visit_prescriptions=visit_prescriptions,
        visit_notes=visit_notes,
        current_admission=current_admission,
        dialysis_sessions=dialysis_sessions,
        has_dialysis_history=has_dialysis_history,
        common_icd10=COMMON_ICD10,
    )


@app.route("/patient/<national_id>/visits/<int:visit_id>/treatment-report")
@login_required(roles=CLINICAL_READ_ROLES)
def treatment_report(national_id, visit_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    visit = Visit.query.filter_by(id=visit_id, patient_id=patient.id).first_or_404()
    notes = (
        ClinicalNote.query.filter_by(patient_id=patient.id, visit_id=visit.id)
        .order_by(ClinicalNote.timestamp.asc())
        .all()
    )
    diagnoses = (
        Diagnosis.query.filter_by(patient_id=patient.id, visit_id=visit.id)
        .order_by(Diagnosis.diagnosed_at.asc())
        .all()
    )
    prescriptions = (
        Prescription.query.filter_by(patient_id=patient.id, visit_id=visit.id)
        .order_by(Prescription.prescribed_at.asc())
        .all()
    )
    return render_template(
        "treatment_report.html",
        patient=patient,
        visit=visit,
        notes=notes,
        diagnoses=diagnoses,
        prescriptions=prescriptions,
    )


@app.route("/renal")
@login_required(roles=RENAL_VIEW_ROLES)
def renal_dashboard():
    search = request.args.get("q", "").strip()
    patient_search = request.args.get("patient_q", "").strip()
    query = DialysisSession.query.join(Patient)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Patient.national_id.ilike(search_pattern),
                Patient.name.ilike(search_pattern),
                Patient.first_name.ilike(search_pattern),
                Patient.other_names.ilike(search_pattern),
            )
        )
    dialysis_sessions = (
        query.order_by(DialysisSession.session_date.desc(), DialysisSession.id.desc())
        .limit(100)
        .all()
    )
    authorization_requests = (
        DialysisAuthorizationRequest.query
        .order_by(
            DialysisAuthorizationRequest.submitted_at.desc(),
            DialysisAuthorizationRequest.id.desc(),
        )
        .limit(100)
        .all()
    )
    dialysis_sessions_by_preauthorization = {
        authorization.id: dialysis_session_for_preauthorization(authorization)
        for authorization in authorization_requests
    }
    patients = []
    if patient_search:
        patient_pattern = f"%{patient_search}%"
        patients = (
            Patient.query.filter(
                or_(
                    Patient.national_id.ilike(patient_pattern),
                    Patient.name.ilike(patient_pattern),
                    Patient.first_name.ilike(patient_pattern),
                    Patient.other_names.ilike(patient_pattern),
                )
            )
            .order_by(Patient.name.asc())
            .limit(25)
            .all()
        )
    return render_template(
        "renal.html",
        dialysis_sessions=dialysis_sessions,
        search=search,
        patient_search=patient_search,
        patients=patients,
        can_create=session.get("role") in RENAL_CHART_ROLES,
        can_request_preauthorization=(
            session.get("role") in RENAL_PREAUTH_REQUEST_ROLES
        ),
        authorization_requests=authorization_requests,
        dialysis_sessions_by_preauthorization=dialysis_sessions_by_preauthorization,
    )


def dialysis_session_for_preauthorization(preauthorization):
    conditions = [
        DialysisSession.preauthorization_id == preauthorization.id,
        db.and_(
            DialysisSession.patient_id == preauthorization.patient_id,
            DialysisSession.preauthorization_id.is_(None),
            DialysisSession.created_at >= preauthorization.created_at,
        ),
    ]
    if preauthorization.authorization_number:
        conditions.append(
            db.and_(
                DialysisSession.patient_id == preauthorization.patient_id,
                DialysisSession.sha_authorization_number
                == preauthorization.authorization_number,
            )
        )
    return (
        DialysisSession.query.filter(db.or_(*conditions))
        .order_by(DialysisSession.created_at.asc(), DialysisSession.id.asc())
        .first()
    )


@app.route(
    "/patient/<national_id>/renal/preauthorization/new",
    methods=["GET", "POST"],
)
@login_required(roles=RENAL_PREAUTH_REQUEST_ROLES)
def dialysis_preauthorization_create(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    if request.method == "POST":
        member_number = (
            request.form.get("sha_member_number", "").strip()[:100] or None
        )
        diagnosis = request.form.get("diagnosis", "").strip()[:5000]
        clinical_justification = request.form.get(
            "clinical_justification", ""
        ).strip()[:10000]
        submission_reference = (
            request.form.get("submission_reference", "").strip()[:100] or None
        )
        try:
            requested_sessions = int(request.form.get("requested_sessions", ""))
            submitted_at = datetime.strptime(
                request.form.get("submitted_at", "").strip(), "%Y-%m-%d"
            ).date()
            if requested_sessions < 1 or requested_sessions > 100:
                raise ValueError("Requested sessions must be between 1 and 100.")
        except ValueError as error:
            message = (
                str(error)
                if str(error) == "Requested sessions must be between 1 and 100."
                else "Enter a valid request date and a whole number of sessions."
            )
            flash(message, "danger")
            return render_template(
                "dialysis_preauthorization_form.html",
                patient=patient,
                form_data=request.form,
                today=datetime.now().astimezone().date().isoformat(),
            )
        if not diagnosis or not clinical_justification:
            flash("Diagnosis and clinical justification are required.", "danger")
            return render_template(
                "dialysis_preauthorization_form.html",
                patient=patient,
                form_data=request.form,
                today=datetime.now().astimezone().date().isoformat(),
            )

        preauthorization = DialysisAuthorizationRequest(
            patient=patient,
            sha_member_number=member_number,
            diagnosis=diagnosis,
            clinical_justification=clinical_justification,
            requested_sessions=requested_sessions,
            submission_reference=submission_reference,
            submitted_at=submitted_at,
            status="pending",
            requested_by=session.get(
                "full_name", session.get("username", "Staff")
            ),
        )
        db.session.add(preauthorization)
        db.session.flush()
        audit(
            "dialysis_preauthorization_requested",
            "dialysis_authorization_request",
            preauthorization.id,
            f"patient_id={patient.id}; sessions={requested_sessions}",
        )
        db.session.commit()
        flash(
            "Preauthorization request recorded. Submit it to SHA through the usual channel.",
            "success",
        )
        return redirect(
            url_for(
                "dialysis_preauthorization",
                preauthorization_id=preauthorization.id,
            )
        )

    return render_template(
        "dialysis_preauthorization_form.html",
        patient=patient,
        form_data={},
        today=datetime.now().astimezone().date().isoformat(),
    )


@app.route("/renal/preauthorization/<int:preauthorization_id>")
@login_required(roles=RENAL_VIEW_ROLES)
def dialysis_preauthorization(preauthorization_id):
    preauthorization = DialysisAuthorizationRequest.query.get_or_404(
        preauthorization_id
    )
    dialysis_session = dialysis_session_for_preauthorization(preauthorization)
    return render_template(
        "dialysis_preauthorization.html",
        preauthorization=preauthorization,
        dialysis_session=dialysis_session,
        can_update=session.get("role") in RENAL_AUTHORIZATION_ROLES,
        can_create=(
            session.get("role") in RENAL_CHART_ROLES
            and preauthorization.status == "approved"
            and dialysis_session is None
        ),
    )


@app.route(
    "/renal/preauthorization/<int:preauthorization_id>/update",
    methods=["POST"],
)
@login_required(roles=RENAL_AUTHORIZATION_ROLES)
def dialysis_preauthorization_update(preauthorization_id):
    preauthorization = DialysisAuthorizationRequest.query.get_or_404(
        preauthorization_id
    )
    status = request.form.get("status", "").strip().lower()
    if status not in {"pending", "approved", "denied"}:
        flash("Select a valid SHA preauthorization status.", "danger")
        return redirect(
            url_for(
                "dialysis_preauthorization",
                preauthorization_id=preauthorization.id,
            )
        )

    authorization_number = (
        request.form.get("authorization_number", "").strip()[:100] or None
    )
    try:
        approved_sessions = parse_optional_number(
            request.form.get("approved_sessions"),
            "Approved sessions",
            1,
            100,
            integer=True,
        )
        valid_from = parse_optional_date_for_authorization(
            request.form.get("valid_from")
        )
        valid_until = parse_optional_date_for_authorization(
            request.form.get("valid_until")
        )
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(
            url_for(
                "dialysis_preauthorization",
                preauthorization_id=preauthorization.id,
            )
        )
    if status == "approved" and not authorization_number:
        flash("Enter the SHA authorization number before marking it approved.", "danger")
        return redirect(
            url_for(
                "dialysis_preauthorization",
                preauthorization_id=preauthorization.id,
            )
        )
    if valid_from and valid_until and valid_from > valid_until:
        flash("Authorization end date must be on or after its start date.", "danger")
        return redirect(
            url_for(
                "dialysis_preauthorization",
                preauthorization_id=preauthorization.id,
            )
        )

    preauthorization.status = status
    preauthorization.authorization_number = authorization_number
    preauthorization.approved_sessions = approved_sessions
    preauthorization.valid_from = valid_from
    preauthorization.valid_until = valid_until
    preauthorization.response_notes = (
        request.form.get("response_notes", "").strip()[:5000] or None
    )
    preauthorization.reviewed_by = session.get(
        "full_name", session.get("username", "Staff")
    )
    preauthorization.reviewed_at = datetime.utcnow()
    preauthorization.updated_at = preauthorization.reviewed_at
    audit(
        "dialysis_preauthorization_updated",
        "dialysis_authorization_request",
        preauthorization.id,
        f"patient_id={preauthorization.patient_id}; status={status}",
    )
    db.session.commit()
    flash("SHA preauthorization response updated.", "success")
    return redirect(
        url_for(
            "dialysis_preauthorization",
            preauthorization_id=preauthorization.id,
        )
    )


@app.route("/patient/<national_id>/dialysis/new", methods=["GET", "POST"])
@login_required(roles=RENAL_CHART_ROLES)
def dialysis_session_create(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    preauthorization_id = request.args.get("preauthorization_id", type=int)
    preauthorization = (
        DialysisAuthorizationRequest.query.filter_by(
            id=preauthorization_id, patient_id=patient.id, status="approved"
        ).first()
        if preauthorization_id
        else None
    )
    if not preauthorization:
        abort(404)

    existing_session = dialysis_session_for_preauthorization(preauthorization)
    if existing_session:
        flash("This preauthorization already has a dialysis chart.", "info")
        return redirect(
            url_for(
                "dialysis_session",
                dialysis_session_id=existing_session.id,
            )
        )

    active_visit = (
        Visit.query.filter(
            Visit.patient_id == patient.id,
            db.or_(Visit.status != "completed", Visit.status.is_(None)),
            db.or_(Visit.visit_type != "Inpatient", Visit.visit_type.is_(None)),
        )
        .order_by(Visit.started_at.desc())
        .first()
    )
    dialysis_session = DialysisSession(
        patient=patient,
        visit=active_visit,
        preauthorization_id=preauthorization.id,
        session_date=datetime.now().astimezone().date(),
        sha_authorization_number=preauthorization.authorization_number,
        sha_authorization_status="approved",
        created_by=session.get("full_name", session.get("username", "Staff")),
    )

    if request.method == "POST":
        try:
            dialysis_session.session_date = datetime.strptime(
                request.form.get("session_date", "").strip(), "%Y-%m-%d"
            ).date()
            save_dialysis_chart(dialysis_session, request.form)
            save_dialysis_observations(dialysis_session, request.form)
        except ValueError as error:
            flash(str(error), "danger")
            return render_template(
                "dialysis_session.html",
                patient=patient,
                dialysis_session=dialysis_session,
                can_chart=True,
                can_manage_authorization=False,
                observation_rows=[],
                is_new=True,
            )

        now = datetime.utcnow()
        dialysis_session.signed_by = session.get(
            "full_name", session.get("username", "Staff")
        )
        dialysis_session.signed_at = now
        dialysis_session.updated_at = now
        db.session.add(dialysis_session)
        db.session.flush()
        audit(
            "dialysis_session_created",
            "dialysis_session",
            dialysis_session.id,
            f"patient_id={patient.id}; session_date={dialysis_session.session_date}",
        )
        if request.form.get("action") == "finish_session":
            finish_dialysis_session(dialysis_session)
        db.session.commit()
        flash(
            "Haemodialysis session finished and chart saved."
            if dialysis_session.status == "completed"
            else "Haemodialysis chart saved.",
            "success",
        )
        return redirect(
            url_for("dialysis_session", dialysis_session_id=dialysis_session.id)
        )

    return render_template(
        "dialysis_session.html",
        patient=patient,
        dialysis_session=dialysis_session,
        can_chart=True,
        can_manage_authorization=False,
        observation_rows=[],
        is_new=True,
    )


@app.route("/renal/session/<int:dialysis_session_id>")
@login_required(roles=RENAL_VIEW_ROLES)
def dialysis_session(dialysis_session_id):
    dialysis_session = DialysisSession.query.get_or_404(dialysis_session_id)
    return render_template(
        "dialysis_session.html",
        patient=dialysis_session.patient,
        dialysis_session=dialysis_session,
        can_chart=session.get("role") in RENAL_CHART_ROLES,
        can_manage_authorization=(
            session.get("role") in RENAL_AUTHORIZATION_ROLES
        ),
        observation_rows=dialysis_session.observations,
        is_new=False,
    )


@app.route(
    "/renal/session/<int:dialysis_session_id>/save",
    methods=["POST"],
)
@login_required(roles=RENAL_CHART_ROLES)
def dialysis_session_save(dialysis_session_id):
    dialysis_session = DialysisSession.query.get_or_404(dialysis_session_id)
    try:
        dialysis_session.session_date = datetime.strptime(
            request.form.get("session_date", "").strip(), "%Y-%m-%d"
        ).date()
        save_dialysis_chart(dialysis_session, request.form)
        save_dialysis_observations(dialysis_session, request.form)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(
            url_for("dialysis_session", dialysis_session_id=dialysis_session.id)
        )

    dialysis_session.signed_by = session.get(
        "full_name", session.get("username", "Staff")
    )
    dialysis_session.signed_at = datetime.utcnow()
    dialysis_session.updated_at = dialysis_session.signed_at
    if request.form.get("action") == "finish_session":
        finish_dialysis_session(dialysis_session)
    audit(
        "dialysis_session_updated",
        "dialysis_session",
        dialysis_session.id,
        f"patient_id={dialysis_session.patient_id}; "
        f"session_date={dialysis_session.session_date}",
    )
    db.session.commit()
    flash(
        "Haemodialysis session finished and chart updated."
        if dialysis_session.status == "completed"
        else "Haemodialysis chart updated.",
        "success",
    )
    return redirect(
        url_for("dialysis_session", dialysis_session_id=dialysis_session.id)
    )


def finish_dialysis_session(dialysis_session):
    if dialysis_session.status == "completed":
        return
    dialysis_session.status = "completed"
    dialysis_session.completed_at = datetime.utcnow()
    dialysis_session.completed_by = session.get(
        "full_name", session.get("username", "Staff")
    )
    audit(
        "dialysis_session_completed",
        "dialysis_session",
        dialysis_session.id,
        f"patient_id={dialysis_session.patient_id}; "
        f"completed_by={dialysis_session.completed_by}",
    )


@app.route(
    "/renal/session/<int:dialysis_session_id>/authorization",
    methods=["POST"],
)
@login_required(roles=RENAL_AUTHORIZATION_ROLES)
def dialysis_authorization_update(dialysis_session_id):
    dialysis_session = DialysisSession.query.get_or_404(dialysis_session_id)
    authorization_number = (
        request.form.get("sha_authorization_number", "").strip()[:100] or None
    )
    authorization_status = request.form.get(
        "sha_authorization_status", ""
    ).strip().lower()
    if authorization_status not in {"pending", "approved", "denied"}:
        flash("Select a valid SHA authorization status.", "danger")
        return redirect(
            url_for("dialysis_session", dialysis_session_id=dialysis_session.id)
        )
    if authorization_status == "approved" and not authorization_number:
        flash("Enter the SHA authorization number before marking it approved.", "danger")
        return redirect(
            url_for("dialysis_session", dialysis_session_id=dialysis_session.id)
        )

    dialysis_session.sha_authorization_number = authorization_number
    dialysis_session.sha_authorization_status = authorization_status
    dialysis_session.updated_at = datetime.utcnow()
    audit(
        "dialysis_authorization_updated",
        "dialysis_session",
        dialysis_session.id,
        f"patient_id={dialysis_session.patient_id}; "
        f"status={authorization_status}",
    )
    db.session.commit()
    flash("SHA authorization details updated.", "success")
    return redirect(
        url_for("dialysis_session", dialysis_session_id=dialysis_session.id)
    )


def save_dialysis_chart(dialysis_session, form):
    fields = {
        "physician": 100,
        "diagnosis": 5000,
        "access_type": 40,
        "dialysate_k": 30,
        "dialysate_na": 30,
        "dialysate_ca": 30,
        "dialysate_bicarbonate": 30,
        "pre_temperature": 30,
        "pre_bp": 30,
        "pre_hr": 30,
        "pre_rr": 30,
        "pre_current_weight": 30,
        "pre_dry_weight": 30,
        "uf_goal": 30,
        "pre_symptoms": 5000,
        "duration": 30,
        "machine_serial": 80,
        "heparin": 80,
        "post_temperature": 30,
        "post_bp": 30,
        "post_hr": 30,
        "post_rr": 30,
        "post_current_weight": 30,
        "post_dry_weight": 30,
        "uf_achieved": 30,
        "post_symptoms": 5000,
        "medication": 5000,
        "brief_comments": 5000,
        "served_by": 100,
    }
    for field, max_length in fields.items():
        setattr(
            dialysis_session,
            field,
            form.get(field, "").strip()[:max_length] or None,
        )
    if dialysis_session.access_type not in {
        None,
        "Perm catheter",
        "Temp catheter",
        "Graft / fistula",
        "Other",
    }:
        raise ValueError("Select a valid dialysis access type.")


def parse_optional_date_for_authorization(raw_value):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError("Enter a valid SHA authorization date.") from error


def save_dialysis_observations(dialysis_session, form):
    observation_fields = {
        "reading_time": "reading_time",
        "reading_bp": "blood_pressure",
        "reading_pulse": "pulse",
        "reading_ap": "arterial_pressure",
        "reading_vp": "venous_pressure",
        "reading_bfr": "blood_flow_rate",
        "reading_dfr": "dialysate_flow_rate",
        "reading_tmp": "tmp",
        "reading_ufr": "ufr",
        "reading_fluids_removed": "fluids_removed",
        "reading_comments": "comments",
    }
    submitted = {
        field: form.getlist(field) for field in observation_fields
    }
    row_count = max((len(values) for values in submitted.values()), default=0)
    if row_count > 20:
        raise ValueError("A dialysis chart can contain at most 20 timed readings.")

    dialysis_session.observations.clear()
    for index in range(row_count):
        values = {
            column: submitted[field][index].strip()
            for field, column in observation_fields.items()
            if index < len(submitted[field])
        }
        if not any(values.values()):
            continue
        observation = DialysisObservation(
            **{
                column: value[:5000 if column == "comments" else 30] or None
                for column, value in values.items()
            }
        )
        dialysis_session.observations.append(observation)


@app.route("/patient/<national_id>/complete-visit", methods=["POST"])
@login_required(roles="doctor")
def complete_visit(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    visit = (
        Visit.query.filter(Visit.patient_id == patient.id, Visit.status != "completed")
        .order_by(Visit.started_at.desc())
        .first()
    )

    if not visit:
        flash("No active visit found for this patient.", "warning")
        return redirect(url_for("patient_card", national_id=national_id))

    visit.status = "completed"
    visit.completed_at = datetime.utcnow()
    queue = QueueEntry.query.filter_by(visit_id=visit.id).first()
    if queue:
        queue.status = "completed"
    audit("visit_completed", "visit", visit.id, f"patient_id={patient.id}")
    db.session.commit()
    flash(f"Visit completed for {patient.full_name}.", "success")
    return redirect(url_for("dashboard"))

def close_stale_outpatient_queue():
    """Close open outpatient queue entries from previous UTC days only."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    open_statuses = {"queued", "with_doctor", "in_progress", "results_ready"}

    stale_entries = QueueEntry.query.filter(
        QueueEntry.status.in_(open_statuses),
        QueueEntry.queued_at < today_start,
    ).all()

    for entry in stale_entries:
        entry.status = "expired"

    if stale_entries:
        db.session.commit()
def maybe_complete_visit_after_pharmacy(visit_id, patient_id):
    """Close visit when no prescribed meds remain for this visit."""
    if not visit_id:
        return False

    visit = Visit.query.get(visit_id)
    if visit and (visit.visit_type or "") == "Inpatient":
        return False
    if not visit or (visit.status or "").lower() == "completed":
        return False

    pending = Prescription.query.filter(
        Prescription.visit_id == visit_id,
        Prescription.status.in_(["prescribed", "Prescribed", "pending", "Pending"]),
    ).count()
    if pending > 0:
        return False

    visit.status = "completed"
    if hasattr(visit, "completed_at"):
        visit.completed_at = datetime.utcnow()

    for queue in QueueEntry.query.filter(
        QueueEntry.patient_id == patient_id,
        QueueEntry.status.in_(
            {
                "queued",
                "with_doctor",
                "in_progress",
                "results_ready",
                "seen_by_doctor",
            }
        ),
    ).all():
        queue.status = "completed"

    audit(
        "visit_completed_pharmacy",
        "visit",
        visit.id,
        f"patient_id={patient_id}",
    )
    return True

@app.route("/outpatient-queue")
@login_required(roles=CLINICAL_READ_ROLES | QUEUE_ASSIGNMENT_ROLES)
def outpatient_queue():
    close_stale_outpatient_queue()

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    visible_statuses = {"queued", "with_doctor", "in_progress", "results_ready", "seen_by_doctor"}

    queue_entries = (
        QueueEntry.query.filter(
            QueueEntry.status.in_(visible_statuses),
            QueueEntry.queued_at >= today_start,
        )
        .order_by(QueueEntry.queued_at.asc())
        .all()
    )

    LabOrder = app.config.get("LAB_ORDER_MODEL")
    decorated = []

    for entry in queue_entries:
        entry.queue_identifier = get_queue_identifier(entry.patient_id)

        has_lab = False
        has_radiology = False

        if LabOrder:
            has_lab = (
                LabOrder.query.filter(
                    LabOrder.patient_id == entry.patient_id,
                    LabOrder.status == "Verified",
                    LabOrder.reviewed_at.is_(None),
                ).first()
                is not None
            )

        has_radiology = (
            ImagingStudy.query.filter(
                ImagingStudy.patient_id == entry.patient_id,
                ImagingStudy.status == "completed",
                ImagingStudy.reviewed_at.is_(None),
            ).first()
            is not None
        )

        if has_lab and has_radiology:
            entry.badge_key = "both"
            entry.sort_priority = 0
        elif has_lab:
            entry.badge_key = "lab"
            entry.sort_priority = 1
        elif has_radiology:
            entry.badge_key = "radiology"
            entry.sort_priority = 2
        else:
            entry.badge_key = "first"
            entry.sort_priority = 3

        decorated.append(entry)

    decorated.sort(key=lambda e: (e.sort_priority, e.queued_at or datetime.utcnow()))

    return render_template(
        "outpatient_queue.html",
        queue_entries=decorated,
    )


def parse_local_datetime(raw_value):
    try:
        local_value = datetime.strptime(
            (raw_value or "").strip(), "%Y-%m-%dT%H:%M"
        ).astimezone()
        return local_value.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError as error:
        raise ValueError("Enter a valid date and time.") from error


def local_day_utc_bounds(day):
    start_local = datetime.combine(day, datetime.min.time()).astimezone()
    end_local = datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone()
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def local_date_string(value):
    return value.replace(tzinfo=timezone.utc).astimezone().date().isoformat()


@app.template_filter("local_datetime")
def local_datetime(value, date_format="%d %b %Y %H:%M"):
    if not value:
        return ""
    return value.replace(tzinfo=timezone.utc).astimezone().strftime(date_format)


@app.route("/appointments")
@login_required(roles=APPOINTMENT_VIEW_ROLES | APPOINTMENT_MANAGE_ROLES)
def appointments():
    selected_date = request.args.get("date", datetime.now().astimezone().date().isoformat())
    try:
        day = datetime.strptime(selected_date, "%Y-%m-%d").date()
    except ValueError:
        flash("Select a valid appointment date.", "danger")
        day = datetime.now().astimezone().date()
        selected_date = day.isoformat()
    start_utc, end_utc = local_day_utc_bounds(day)

    appointments_for_day = (
        Appointment.query.filter(
            Appointment.scheduled_at >= start_utc,
            Appointment.scheduled_at < end_utc,
        )
        .order_by(Appointment.scheduled_at.asc())
        .all()
    )
    doctors = User.query.filter_by(role="doctor").order_by(User.full_name).all()
    return render_template(
        "appointments.html",
        appointments=appointments_for_day,
        selected_date=selected_date,
        doctors=doctors,
        payment_methods=sorted(PAYMENT_METHODS),
        can_manage=session.get("role") in APPOINTMENT_MANAGE_ROLES
        or session.get("role") == "admin",
    )


@app.route("/appointments/create", methods=["POST"])
@login_required(roles=APPOINTMENT_MANAGE_ROLES)
def appointment_create():
    patient = Patient.query.filter_by(
        national_id=request.form.get("national_id", "").strip()
    ).first()
    if not patient:
        flash("Patient not found. Register the patient first.", "danger")
        return redirect(url_for("appointments"))

    try:
        scheduled_at = parse_local_datetime(request.form.get("scheduled_at"))
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("appointments"))
    if scheduled_at < datetime.utcnow():
        flash("Appointments must be scheduled in the future.", "danger")
        return redirect(url_for("appointments", date=local_date_string(scheduled_at)))

    doctor = request.form.get("doctor", "").strip() or None
    valid_doctors = {member.full_name for member in User.query.filter_by(role="doctor")}
    if doctor and doctor not in valid_doctors:
        flash("Select a registered doctor.", "danger")
        return redirect(url_for("appointments", date=local_date_string(scheduled_at)))
    if doctor and Appointment.query.filter_by(
        doctor=doctor, scheduled_at=scheduled_at, status="scheduled"
    ).first():
        flash("That doctor already has an appointment at this time.", "warning")
        return redirect(url_for("appointments", date=local_date_string(scheduled_at)))

    appointment = Appointment(
        patient_id=patient.id,
        scheduled_at=scheduled_at,
        doctor=doctor,
        reason=request.form.get("reason", "").strip()[:255] or None,
        created_by=session.get("full_name", session.get("username", "Staff")),
    )
    db.session.add(appointment)
    db.session.flush()
    audit(
        "appointment_scheduled",
        "appointment",
        appointment.id,
        f"patient_id={patient.id}; doctor={doctor or 'unassigned'}; "
        f"scheduled_at={scheduled_at.isoformat()}",
    )
    db.session.commit()
    flash(f"Appointment booked for {patient.full_name}.", "success")
    return redirect(url_for("appointments", date=local_date_string(scheduled_at)))


@app.route("/appointments/<int:appointment_id>/manage", methods=["POST"])
@login_required(roles=APPOINTMENT_MANAGE_ROLES)
def appointment_manage(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    action = request.form.get("action", "")
    if appointment.status != "scheduled":
        flash("Only scheduled appointments can be changed.", "warning")
        return redirect(url_for("appointments", date=local_date_string(appointment.scheduled_at)))

    if action == "check_in":
        payment_method = request.form.get("payment_method", "").strip()
        if payment_method not in PAYMENT_METHODS:
            flash("Choose a valid payer before checking in.", "danger")
            return redirect(url_for("appointments", date=local_date_string(appointment.scheduled_at)))
        active_queue = QueueEntry.query.filter(
            QueueEntry.patient_id == appointment.patient_id,
            QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES),
        ).first()
        if active_queue:
            flash("This patient already has an active outpatient queue entry.", "warning")
            return redirect(url_for("appointments", date=local_date_string(appointment.scheduled_at)))

        visit_type = determine_visit_type(appointment.patient)
        visit = Visit(
            patient_id=appointment.patient_id,
            visit_type=visit_type,
            payment_method=payment_method,
        )
        appointment.patient.visit_type = "Revisit" if visit_type == "Revisit" else "New Visit"
        db.session.add(visit)
        db.session.flush()
        visit.invoice_number = f"INV-{visit.id:06d}"
        queue = QueueEntry(
            visit_id=visit.id,
            patient_id=appointment.patient_id,
            doctor=appointment.doctor,
            status="queued",
        )
        db.session.add(queue)
        db.session.flush()
        appointment.checked_in_visit_id = visit.id
        appointment.status = "checked_in"
        audit(
            "appointment_checked_in",
            "appointment",
            appointment.id,
            f"visit_id={visit.id}; queue_id={queue.id}; payment_method={payment_method}",
        )
        db.session.commit()
        flash("Patient checked in and added to the outpatient queue.", "success")
        return redirect(url_for("outpatient_queue"))

    if action in {"cancel", "no_show"}:
        appointment.status = "cancelled" if action == "cancel" else "no_show"
        audit(
            f"appointment_{appointment.status}",
            "appointment",
            appointment.id,
            f"patient_id={appointment.patient_id}",
        )
        db.session.commit()
        flash(f"Appointment marked {appointment.status.replace('_', ' ')}.", "success")
        return redirect(url_for("appointments", date=local_date_string(appointment.scheduled_at)))

    if action == "reschedule":
        try:
            scheduled_at = parse_local_datetime(request.form.get("scheduled_at"))
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("appointments", date=local_date_string(appointment.scheduled_at)))
        if scheduled_at < datetime.utcnow():
            flash("Appointments must be rescheduled to a future date and time.", "danger")
            return redirect(url_for("appointments", date=local_date_string(appointment.scheduled_at)))
        doctor = request.form.get("doctor", "").strip() or None
        valid_doctors = {member.full_name for member in User.query.filter_by(role="doctor")}
        if doctor and doctor not in valid_doctors:
            flash("Select a registered doctor.", "danger")
            return redirect(url_for("appointments", date=local_date_string(appointment.scheduled_at)))
        conflict = Appointment.query.filter(
            Appointment.id != appointment.id,
            Appointment.doctor == doctor,
            Appointment.scheduled_at == scheduled_at,
            Appointment.status == "scheduled",
        ).first() if doctor else None
        if conflict:
            flash("That doctor already has an appointment at this time.", "warning")
            return redirect(url_for("appointments", date=local_date_string(appointment.scheduled_at)))
        appointment.scheduled_at = scheduled_at
        appointment.doctor = doctor
        appointment.reason = request.form.get("reason", "").strip()[:255] or None
        audit(
            "appointment_rescheduled",
            "appointment",
            appointment.id,
            f"scheduled_at={scheduled_at.isoformat()}; doctor={doctor or 'unassigned'}",
        )
        db.session.commit()
        flash("Appointment rescheduled.", "success")
        return redirect(url_for("appointments", date=local_date_string(scheduled_at)))

    flash("Select a valid appointment action.", "danger")
    return redirect(url_for("appointments", date=local_date_string(appointment.scheduled_at)))


@app.route("/theatre")
@login_required(roles=THEATRE_VIEW_ROLES)
def theatre_list():
    selected_date = request.args.get("date", datetime.now().astimezone().date().isoformat())
    try:
        day = datetime.strptime(selected_date, "%Y-%m-%d").date()
    except ValueError:
        flash("Select a valid theatre date.", "danger")
        day = datetime.now().astimezone().date()
        selected_date = day.isoformat()
    start_utc, end_utc = local_day_utc_bounds(day)

    procedures = (
        TheatreProcedure.query.filter(
            TheatreProcedure.scheduled_at >= start_utc,
            TheatreProcedure.scheduled_at < end_utc,
            TheatreProcedure.status != "cancelled",
        )
        .order_by(TheatreProcedure.scheduled_at.asc())
        .all()
    )
    doctors = User.query.filter_by(role="doctor").order_by(User.full_name).all()
    return render_template(
        "theatre.html",
        procedures=procedures,
        selected_date=selected_date,
        doctors=doctors,
        can_manage=session.get("role") in THEATRE_MANAGE_ROLES
        or session.get("role") == "admin",
    )


@app.route("/theatre/create", methods=["POST"])
@login_required(roles=THEATRE_MANAGE_ROLES)
def theatre_create():
    patient = Patient.query.filter_by(
        national_id=request.form.get("national_id", "").strip()
    ).first()
    if not patient:
        flash("Patient not found. Register the patient first.", "danger")
        return redirect(url_for("theatre_list"))

    procedure_name = request.form.get("procedure_name", "").strip()
    if not procedure_name:
        flash("Enter the planned procedure.", "danger")
        return redirect(url_for("theatre_list"))
    try:
        scheduled_at = parse_local_datetime(request.form.get("scheduled_at"))
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("theatre_list"))
    if scheduled_at < datetime.utcnow():
        flash("Theatre procedures must be scheduled in the future.", "danger")
        return redirect(url_for("theatre_list", date=local_date_string(scheduled_at)))

    surgeon = request.form.get("surgeon", "").strip() or None
    valid_doctors = {member.full_name for member in User.query.filter_by(role="doctor")}
    if surgeon and surgeon not in valid_doctors:
        flash("Select a registered surgeon.", "danger")
        return redirect(url_for("theatre_list", date=local_date_string(scheduled_at)))
    conflict = TheatreProcedure.query.filter(
        TheatreProcedure.surgeon == surgeon,
        TheatreProcedure.scheduled_at == scheduled_at,
        TheatreProcedure.status.in_(["scheduled", "in_progress"]),
    ).first() if surgeon else None
    if conflict:
        flash("That surgeon already has a procedure scheduled at this time.", "warning")
        return redirect(url_for("theatre_list", date=local_date_string(scheduled_at)))

    admission_id = request.form.get("admission_id", type=int)
    if not admission_id:
        admission = (
            Admission.query.filter_by(patient_id=patient.id, status="admitted")
            .order_by(Admission.admitted_at.desc())
            .first()
        )
    else:
        admission = Admission.query.filter_by(
            id=admission_id, patient_id=patient.id, status="admitted"
        ).first()
    if admission_id is not None and not admission:
        flash("Select an active admission belonging to this patient.", "danger")
        return redirect(url_for("theatre_list", date=local_date_string(scheduled_at)))

    procedure = TheatreProcedure(
        patient_id=patient.id,
        admission_id=admission.id if admission else None,
        scheduled_at=scheduled_at,
        procedure_name=procedure_name[:255],
        surgeon=surgeon,
        anesthetist=request.form.get("anesthetist", "").strip()[:100] or None,
        created_by=session.get("full_name", session.get("username", "Staff")),
    )
    db.session.add(procedure)
    db.session.flush()
    audit(
        "theatre_procedure_scheduled",
        "theatre_procedure",
        procedure.id,
        f"patient_id={patient.id}; admission_id={procedure.admission_id}; "
        f"surgeon={surgeon or 'unassigned'}; scheduled_at={scheduled_at.isoformat()}",
    )
    db.session.commit()
    flash("Theatre procedure scheduled.", "success")
    return redirect(url_for("theatre_list", date=local_date_string(scheduled_at)))


@app.route("/theatre/<int:procedure_id>", methods=["GET", "POST"])
@login_required(roles=THEATRE_VIEW_ROLES)
def theatre_procedure(procedure_id):
    procedure = TheatreProcedure.query.get_or_404(procedure_id)
    can_record = session.get("role") in THEATRE_CLINICAL_ROLES
    if request.method == "POST":
        if not can_record:
            abort(403)
        if procedure.status in {"completed", "cancelled"}:
            flash("This procedure is closed and its record cannot be changed.", "warning")
            return redirect(url_for("theatre_procedure", procedure_id=procedure.id))

        staff_name = session.get("full_name") or session.get("username") or "Staff"
        now = datetime.utcnow()

        # Consent (detailed)
        procedure.consent_obtained = request.form.get("consent_obtained") == "1"
        procedure.consent_procedure_description = request.form.get("consent_procedure_description", "").strip()[:5000] or None
        procedure.consent_specific_risks = request.form.get("consent_specific_risks", "").strip()[:5000] or None
        procedure.consent_given_by = request.form.get("consent_given_by", "").strip()[:120] or None
        procedure.consent_relationship = request.form.get("consent_relationship", "").strip()[:40] or None
        procedure.consent_explained_by = request.form.get("consent_explained_by", "").strip()[:100] or None
        procedure.consent_witness_name = request.form.get("consent_witness_name", "").strip()[:100] or None
        procedure.consent_notes = request.form.get("consent_notes", "").strip()[:5000] or None
        if procedure.consent_obtained and not procedure.consented_at:
            procedure.consented_at = now
            procedure.consented_by_staff = staff_name

        # Pre-anaesthetic review
        procedure.pre_anaesth_presenting_complaints = request.form.get("pre_anaesth_presenting_complaints", "").strip()[:5000] or None
        procedure.pre_anaesth_pmsh = request.form.get("pre_anaesth_pmsh", "").strip()[:5000] or None
        procedure.pre_anaesth_diagnosis = request.form.get("pre_anaesth_diagnosis", "").strip()[:5000] or None
        procedure.pre_anaesth_operation = request.form.get("pre_anaesth_operation", "").strip()[:5000] or None
        procedure.pre_anaesth_oe = request.form.get("pre_anaesth_oe", "").strip()[:5000] or None
        procedure.pre_anaesth_systemic_exam = request.form.get("pre_anaesth_systemic_exam", "").strip()[:5000] or None
        procedure.pre_anaesth_impression = request.form.get("pre_anaesth_impression", "").strip()[:5000] or None
        procedure.pre_anaesth_plan = request.form.get("pre_anaesth_plan", "").strip()[:5000] or None
        if any([procedure.pre_anaesth_presenting_complaints, procedure.pre_anaesth_pmsh, procedure.pre_anaesth_plan]) and not procedure.pre_anaesthesia_reviewed_by:
            procedure.pre_anaesthesia_reviewed_by = staff_name
            procedure.pre_anaesthesia_at = now

        # Pre-operative theatre checklist
        procedure.checklist_id_card = request.form.get("checklist_id_card") == "1"
        procedure.checklist_starved = request.form.get("checklist_starved") == "1"
        procedure.checklist_shaved = request.form.get("checklist_shaved") == "1"
        procedure.checklist_consent = request.form.get("checklist_consent") == "1"
        procedure.checklist_catheterized = request.form.get("checklist_catheterized") == "1"
        procedure.checklist_group_match = request.form.get("checklist_group_match") == "1"
        procedure.checklist_iv_line = request.form.get("checklist_iv_line") == "1"
        procedure.checklist_dentures = request.form.get("checklist_dentures") == "1"
        procedure.checklist_ng_tube = request.form.get("checklist_ng_tube") == "1"
        procedure.checklist_hb = request.form.get("checklist_hb") == "1"
        procedure.checklist_preop_obs = request.form.get("checklist_preop_obs") == "1"
        procedure.preop_temperature = request.form.get("preop_temperature", "").strip()[:20] or None
        procedure.preop_pulse = request.form.get("preop_pulse", "").strip()[:20] or None
        procedure.preop_bp = request.form.get("preop_bp", "").strip()[:20] or None
        procedure.preop_respiration = request.form.get("preop_respiration", "").strip()[:20] or None
        procedure.premed_notes = request.form.get("premed_notes", "").strip()[:5000] or None
        if not procedure.preop_theatre_completed_by:
            procedure.preop_theatre_completed_by = staff_name

        # Operation notes
        procedure.preoperative_diagnosis = request.form.get("preoperative_diagnosis", "").strip()[:5000] or None
        procedure.incision = request.form.get("incision", "").strip()[:255] or None
        procedure.scrub_nurse = request.form.get("scrub_nurse", "").strip()[:100] or None
        procedure.operation_time_from = request.form.get("operation_time_from", "").strip()[:20] or None
        procedure.operation_time_to = request.form.get("operation_time_to", "").strip()[:20] or None
        procedure.anesthesia_type = request.form.get("anesthesia_type", "").strip()[:100] or None
        procedure.procedure_performed = request.form.get("procedure_performed", "").strip()[:10000] or None
        procedure.findings = request.form.get("findings", "").strip()[:10000] or None
        procedure.anesthesia_notes = request.form.get("anesthesia_notes", "").strip()[:10000] or None
        procedure.complications = request.form.get("complications", "").strip()[:5000] or None
        procedure.estimated_blood_loss = request.form.get("estimated_blood_loss", "").strip()[:80] or None
        procedure.recovery_notes = request.form.get("recovery_notes", "").strip()[:10000] or None
        procedure.postoperative_plan = request.form.get("postoperative_plan", "").strip()[:10000] or None
        procedure.post_op_diagnosis = request.form.get("post_op_diagnosis", "").strip()[:5000] or None
        procedure.outcome = request.form.get("outcome", "").strip()[:120] or None
        procedure.anaesth_induction = request.form.get("anaesth_induction", "").strip()[:255] or None
        procedure.anaesth_airway = request.form.get("anaesth_airway", "").strip()[:255] or None
        procedure.anaesth_premedication = request.form.get("anaesth_premedication", "").strip()[:255] or None
        procedure.anaesth_result = request.form.get("anaesth_result", "").strip()[:5000] or None
        procedure.consumables_notes = request.form.get("consumables_notes", "").strip()[:5000] or None
        procedure.post_op_diagnosis = request.form.get("post_op_diagnosis", "").strip()[:5000] or None
        procedure.outcome = request.form.get("outcome", "").strip()[:120] or None
        procedure.anaesth_induction = request.form.get("anaesth_induction", "").strip()[:255] or None
        procedure.anaesth_airway = request.form.get("anaesth_airway", "").strip()[:255] or None
        procedure.anaesth_premedication = request.form.get("anaesth_premedication", "").strip()[:255] or None
        procedure.anaesth_result = request.form.get("anaesth_result", "").strip()[:5000] or None
        procedure.consumables_notes = request.form.get("consumables_notes", "").strip()[:5000] or None

        action = request.form.get("action", "save")
        if action not in {"save", "start", "complete", "upload"}:
            flash("Select a valid theatre record action.", "danger")
            return redirect(url_for("theatre_procedure", procedure_id=procedure.id))

        if action == "upload":
            files = request.files.getlist("attachments")
            attachment_type = request.form.get("attachment_type", "other").strip() or "other"
            if attachment_type not in {"anaesthetic_record", "consumables", "other"}:
                attachment_type = "other"
            saved = 0
            for f in files:
                if not f or not f.filename:
                    continue
                original = secure_filename(f.filename)
                if "." not in original:
                    continue
                ext = original.rsplit(".", 1)[1].lower()
                if ext not in ALLOWED_IMAGE_EXTENSIONS:
                    flash(f"Skipped unsupported file: {f.filename}", "warning")
                    continue
                stored_name = f"{uuid.uuid4().hex}.{ext}"
                f.save(os.path.join(THEATRE_UPLOAD_FOLDER, stored_name))
                att = TheatreAttachment(
                    procedure_id=procedure.id,
                    filename=stored_name,
                    original_name=original,
                    attachment_type=attachment_type,
                    uploaded_by=staff_name,
                )
                db.session.add(att)
                saved += 1
            if saved:
                audit("theatre_attachment_uploaded", "theatre_procedure", procedure.id, f"count={saved}; type={attachment_type}")
                db.session.commit()
                flash(f"{saved} file(s) attached.", "success")
            else:
                flash("No valid files were uploaded.", "warning")
            return redirect(url_for("theatre_procedure", procedure_id=procedure.id))

        if action == "complete":
            if not procedure.preoperative_diagnosis or not procedure.procedure_performed or not procedure.findings:
                flash(
                    "Record the pre-operative diagnosis, procedure performed, and findings before completing.",
                    "danger",
                )
                return redirect(url_for("theatre_procedure", procedure_id=procedure.id))
            if session.get("role") not in {"doctor", "admin"}:
                abort(403)
            procedure.status = "completed"
            procedure.completed_at = datetime.utcnow()
        elif action == "start":
            procedure.status = "in_progress"

        audit(
            "theatre_record_updated",
            "theatre_procedure",
            procedure.id,
            f"action={action}; status={procedure.status}",
        )
        db.session.commit()
        flash(
            "Operative and recovery record completed."
            if action == "complete"
            else "Theatre record saved.",
            "success",
        )
        return redirect(url_for("theatre_procedure", procedure_id=procedure.id))

    return render_template(
        "theatre_procedure.html",
        procedure=procedure,
        can_record=can_record,
        can_complete=session.get("role") in {"doctor", "admin"},
    )


@app.route("/theatre/<int:procedure_id>/cancel", methods=["POST"])
@login_required(roles=THEATRE_MANAGE_ROLES)
def theatre_cancel(procedure_id):
    procedure = TheatreProcedure.query.get_or_404(procedure_id)
    if procedure.status != "scheduled":
        flash("Only scheduled procedures can be cancelled.", "warning")
    else:
        procedure.status = "cancelled"
        audit("theatre_procedure_cancelled", "theatre_procedure", procedure.id)
        db.session.commit()
        flash("Theatre procedure cancelled.", "success")
    return redirect(url_for("theatre_list", date=local_date_string(procedure.scheduled_at)))
@app.route("/patient/<national_id>/send-to-doctor", methods=["POST"])
@login_required(roles={"nurse", "triage"})
def send_to_doctor(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()

    # Get the latest visit
    visit = (
        Visit.query.filter_by(patient_id=patient.id)
        .order_by(Visit.started_at.desc())
        .first()
    )

    if not visit:
        flash("No active visit found for this patient.", "warning")
        return redirect(url_for("patient_card", national_id=national_id))

    # Update visit status
    visit.status = "with_doctor"

    # Update or create queue entry
    queue = QueueEntry.query.filter_by(visit_id=visit.id).first()
    if queue:
        queue.status = "with_doctor"
    else:
        queue = QueueEntry(
            visit_id=visit.id, patient_id=patient.id, status="with_doctor"
        )
        db.session.add(queue)

    db.session.commit()

    flash(f"Patient {patient.full_name} has been sent to the doctor.", "success")
    return redirect(url_for("patient_card", national_id=national_id))


@app.route("/patients")
@login_required(roles=PATIENT_ACCESS_ROLES)
def patients():
    national_id = request.args.get("national_id", "").strip()
    name = request.args.get("name", "").strip()
    phone = request.args.get("phone", "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    per_page = 10

    query = Patient.query
    if national_id:
        query = query.filter(Patient.national_id.ilike(f"%{national_id}%"))
    if name:
        name_search = f"%{name}%"
        query = query.filter(
            or_(
                Patient.name.ilike(name_search),
                Patient.first_name.ilike(name_search),
                Patient.other_names.ilike(name_search),
            )
        )
    if phone:
        query = query.filter(Patient.phone.ilike(f"%{phone}%"))

    total = query.count()
    patients_list = (
        query.order_by(Patient.registration_date.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    patient_ids = [patient.id for patient in patients_list]
    last_visits = (
        {
            patient_id: last_visit
            for patient_id, last_visit in db.session.query(
                Visit.patient_id, db.func.max(Visit.started_at)
            )
            .filter(Visit.patient_id.in_(patient_ids))
            .group_by(Visit.patient_id)
            .all()
        }
        if patient_ids
        else {}
    )

    return render_template(
        "patients.html",
        patients=patients_list,
        last_visits=last_visits,
        national_id=national_id,
        name=name,
        phone=phone,
        page=page,
        per_page=per_page,
        total=total,
    )


@app.route("/records")
@login_required(roles=PATIENT_ACCESS_ROLES)
def records():
    national_id = request.args.get("national_id", "").strip()
    phone = request.args.get("phone", "").strip()
    query = Patient.query

    if national_id:
        query = query.filter(Patient.national_id.ilike(f"%{national_id}%"))
    if phone:
        query = query.filter(Patient.phone.ilike(f"%{phone}%"))

    patients_list = query.order_by(Patient.registration_date.desc()).limit(50).all()
    return render_template(
        "records.html", patients=patients_list, national_id=national_id, phone=phone
    )


@app.route("/invoices")
@login_required(roles=INVOICE_VIEW_ROLES)
def invoices():
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    status_filter = request.args.get("status", "").strip()

    query = Visit.query.filter(
        Visit.invoice_number.isnot(None), Visit.invoice_number != ""
    )

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(Visit.started_at >= start_dt)
        except ValueError:
            flash("Please enter a valid start date.", "danger")
            start_date = ""

    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            query = query.filter(Visit.started_at <= end_dt + timedelta(days=1))
        except ValueError:
            flash("Please enter a valid end date.", "danger")
            end_date = ""

    invoice_rows = []
    for visit in query.order_by(Visit.started_at.desc()).all():
        pharmacy_total = sum(
            float(item.pharmacy_amount or 0)
            for item in Prescription.query.filter_by(visit_id=visit.id)
            .filter(
                Prescription.status == "dispensed",
                Prescription.pharmacy_amount.isnot(None),
            )
            .all()
        )

        # Lab charges
        LabOrder = app.config.get("LAB_ORDER_MODEL")
        lab_subtotal = 0
        if LabOrder:
            lab_orders = LabOrder.query.filter_by(visit_id=visit.id).all()
            lab_subtotal = sum(
                float(item.price or 0)
                for order in lab_orders
                for item in order.items
            )

        # Radiology charges
        radiology_total = sum(
            float(s.charge_amount or 0)
            for s in ImagingStudy.query.filter_by(visit_id=visit.id).all()
        )

        has_pending_pharmacy_charge = (
            Prescription.query.filter(
                Prescription.visit_id == visit.id,
                Prescription.status == "dispensed",
                Prescription.pharmacy_amount.is_(None),
            ).first()
            is not None
        )

        consultation_total = (
            float(visit.consultation_amount)
            if visit.consultation_amount is not None
            else None
        )

        invoice_total = (
            consultation_total + lab_subtotal + pharmacy_total + radiology_total
            if consultation_total is not None and not has_pending_pharmacy_charge
            else None
        )

        paid_total = sum(
            float(payment.amount)
            for payment in InvoicePayment.query.filter_by(visit_id=visit.id).all()
        )

        payment_status = (
            "Pending billing"
            if invoice_total is None
            else (
                "Paid"
                if paid_total >= invoice_total
                else "Partially paid" if paid_total > 0 else "Unpaid"
            )
        )

        balance = (
            max(invoice_total - paid_total, 0) if invoice_total is not None else None
        )

        if status_filter and payment_status != status_filter:
            continue

        invoice_rows.append(
            {
                "visit": visit,
                "patient": visit.patient,
                "payment_status": payment_status,
                "balance": balance,
            }
        )

    return render_template(
        "invoices.html",
        invoices=invoice_rows,
        start_date=start_date,
        end_date=end_date,
        status_filter=status_filter,
    )

@app.route("/invoices/<int:visit_id>")
@login_required(roles=INVOICE_VIEW_ROLES)
def invoice_detail(visit_id):
    visit = Visit.query.filter(
        Visit.id == visit_id,
        Visit.invoice_number.isnot(None),
        Visit.invoice_number != "",
    ).first_or_404()

    pharmacy_prescriptions = (
        Prescription.query.filter_by(visit_id=visit.id)
        .filter(
            Prescription.status == "dispensed",
            Prescription.pharmacy_amount.isnot(None),
        )
        .order_by(Prescription.dispensed_at.asc())
        .all()
    )
    pharmacy_total = sum(
        float(item.pharmacy_amount or 0) for item in pharmacy_prescriptions
    )

    # Lab charges
    LabOrder = app.config.get("LAB_ORDER_MODEL")
    lab_orders = []
    lab_subtotal = 0
    if LabOrder:
        lab_orders = LabOrder.query.filter_by(visit_id=visit.id).all()
        lab_subtotal = sum(
            float(item.price or 0)
            for order in lab_orders
            for item in order.items
        )
   # Radiology charges – also catch studies that were never linked to a visit
    radiology_studies = ImagingStudy.query.filter(
    db.or_(
        ImagingStudy.visit_id == visit.id,
        db.and_(
            ImagingStudy.patient_id == visit.patient_id,
            ImagingStudy.visit_id.is_(None),
        ),
    )
).all()

# Fill missing prices 
    for s in radiology_studies:
        if s.charge_amount is None or s.charge_amount == 0:
            price = IMAGING_PRICES.get(s.exam_name)
            if price:
                s.charge_amount = price
                db.session.add(s)
    db.session.commit()

    radiology_total = sum(float(s.charge_amount or 0) for s in radiology_studies)

    has_pending_pharmacy_charge = (
        Prescription.query.filter(
            Prescription.visit_id == visit.id,
            Prescription.status == "dispensed",
            Prescription.pharmacy_amount.is_(None),
        ).first()
        is not None
    )

    consultation_total = (
        float(visit.consultation_amount)
        if visit.consultation_amount is not None
        else None
    )
    inpatient_admissions = (
        Admission.query.filter(
            Admission.visit_id == visit.id,
            Admission.bed_charges.isnot(None),
        ).all()
    )
    if not inpatient_admissions:
        inpatient_admissions = (
            Admission.query.filter(
                Admission.patient_id == visit.patient_id,
                Admission.status == "discharged",
                Admission.bed_charges.isnot(None),
                Admission.visit_id == visit.id,
            ).all()
        )

    inpatient_total = sum(
        float(a.admission_fee or 0)
        + float(a.bed_charges or 0)
        + float(a.doctor_fee or 0)
        + float(a.nursing_fee or 0)
        + float(a.consumables_fee or 0)
        for a in inpatient_admissions
    )

    invoice_total = (
        consultation_total
        + lab_subtotal
        + pharmacy_total
        + radiology_total
        + inpatient_total
        if consultation_total is not None and not has_pending_pharmacy_charge
        else None
    )

    payments = (
        InvoicePayment.query.filter_by(visit_id=visit.id)
        .order_by(InvoicePayment.received_at.desc())
        .all()
    )
    paid_total = sum(float(payment.amount) for payment in payments)
    balance = max(invoice_total - paid_total, 0) if invoice_total is not None else None

    payment_status = (
        "Pending billing"
        if invoice_total is None
        else (
            "Paid"
            if paid_total >= invoice_total
            else "Partially paid" if paid_total > 0 else "Unpaid"
        )
    )
    return render_template(
        "invoice_detail.html",
        visit=visit,
        patient=visit.patient,
        pharmacy_prescriptions=pharmacy_prescriptions,
        pharmacy_total=pharmacy_total,
        lab_orders=lab_orders,
        lab_subtotal=lab_subtotal,
        radiology_studies=radiology_studies,
        radiology_total=radiology_total,
        has_pending_pharmacy_charge=has_pending_pharmacy_charge,
        invoice_total=invoice_total,
        payments=payments,
        paid_total=paid_total,
        balance=balance,
        payment_status=payment_status,
        inpatient_admissions=inpatient_admissions,
        inpatient_total=inpatient_total,
    )


@app.route("/invoices/<int:visit_id>/payments", methods=["POST"])
@login_required(roles={"accounts"})
def record_invoice_payment(visit_id):
    visit = Visit.query.filter(
        Visit.id == visit_id,
        Visit.invoice_number.isnot(None),
        Visit.invoice_number != "",
    ).first_or_404()
    try:
        amount = float(request.form.get("amount", "").strip())
    except (TypeError, ValueError):
        flash("Enter a valid payment amount.", "danger")
        return redirect(url_for("invoice_detail", visit_id=visit.id))
    payment_method = request.form.get("payment_method", "").strip()
    if amount <= 0 or payment_method not in PAYMENT_METHODS:
        flash(
            "Choose a valid payment method and enter an amount greater than zero.",
            "danger",
        )
        return redirect(url_for("invoice_detail", visit_id=visit.id))

    pharmacy_total = sum(
        float(item.pharmacy_amount or 0)
        for item in Prescription.query.filter_by(visit_id=visit.id)
        .filter(
            Prescription.status == "dispensed",
            Prescription.pharmacy_amount.isnot(None),
        )
        .all()
    )

    # Lab charges
    LabOrder = app.config.get("LAB_ORDER_MODEL")
    lab_subtotal = 0
    if LabOrder:
        lab_orders = LabOrder.query.filter_by(visit_id=visit.id).all()
        lab_subtotal = sum(
            float(item.price or 0)
            for order in lab_orders
            for item in order.items
        )

    # Radiology charges
    radiology_total = sum(
        float(s.charge_amount or 0)
        for s in ImagingStudy.query.filter_by(visit_id=visit.id).all()
    )

    has_pending_pharmacy_charge = (
        Prescription.query.filter(
            Prescription.visit_id == visit.id,
            Prescription.status == "dispensed",
            Prescription.pharmacy_amount.is_(None),
        ).first()
        is not None
    )

    consultation_total = (
        float(visit.consultation_amount)
        if visit.consultation_amount is not None
        else None
    )

    invoice_total = (
        consultation_total + lab_subtotal + pharmacy_total + radiology_total
        if consultation_total is not None and not has_pending_pharmacy_charge
        else None
    )

    paid_total = sum(
        float(payment.amount)
        for payment in InvoicePayment.query.filter_by(visit_id=visit.id).all()
    )

    if invoice_total is None:
        flash("Complete billing before recording a payment.", "warning")
        return redirect(url_for("invoice_detail", visit_id=visit.id))

    if amount > invoice_total - paid_total:
        flash(
            f"Payment exceeds the outstanding balance of {invoice_total - paid_total:.2f}.",
            "danger",
        )
        return redirect(url_for("invoice_detail", visit_id=visit.id))

    db.session.add(
        InvoicePayment(
            visit_id=visit.id,
            amount=amount,
            payment_method=payment_method,
            received_by=session.get("full_name", session.get("username", "Accounts")),
            notes=request.form.get("notes", "").strip()[:255] or None,
        )
    )
    audit(
        "invoice_payment_recorded",
        "visit",
        visit.id,
        f"amount={amount}; payment_method={payment_method}",
    )
    db.session.commit()
    flash("Payment recorded successfully.", "success")
    return redirect(url_for("invoice_detail", visit_id=visit.id))


@app.route("/payments/<int:payment_id>/receipt")
@login_required(roles=INVOICE_VIEW_ROLES)
def payment_receipt(payment_id):
    payment = InvoicePayment.query.get_or_404(payment_id)
    return render_template(
        "payment_receipt.html",
        payment=payment,
        visit=payment.visit,
        patient=payment.visit.patient,
    )


@app.route("/visit/start/<national_id>", methods=["GET", "POST"])
@login_required(roles=VISIT_MANAGEMENT_ROLES)
def start_visit(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    if request.method == "POST":
        payment_method = request.form.get("payment_method", "").strip()
        if payment_method not in PAYMENT_METHODS:
            flash("Please choose a valid payment method.", "danger")
            return redirect(url_for("start_visit", national_id=national_id))
        existing_queue = QueueEntry.query.filter(
            QueueEntry.patient_id == patient.id,
            QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES),
        ).first()
        if existing_queue:
            flash("This patient already has an active queue entry.", "warning")
            return redirect(url_for("outpatient_queue"))

        visit_type = determine_visit_type(patient)
        visit = Visit(
            patient_id=patient.id, visit_type=visit_type, payment_method=payment_method
        )
        patient.visit_type = "Revisit" if visit_type == "Revisit" else "New Visit"
        db.session.add(visit)
        db.session.flush()
        visit.invoice_number = f"INV-{visit.id:06d}"
        queue = QueueEntry(
            visit_id=visit.id, patient_id=patient.id, doctor=None, status="queued"
        )
        db.session.add(queue)
        db.session.flush()
        audit(
            "visit_started",
            "visit",
            visit.id,
            f"patient_id={patient.id}; queue_id={queue.id}",
        )
        db.session.commit()
        flash(
            f"Started visit for {patient.name} ({payment_method}) and queued", "success"
        )
        return redirect(url_for("dashboard"))
    return render_template("visit_start.html", patient=patient)


@app.route("/queue/assign/<int:queue_id>", methods=["GET", "POST"])
@login_required(roles=QUEUE_ASSIGNMENT_ROLES)
def queue_assign(queue_id):
    queue = QueueEntry.query.get_or_404(queue_id)
    doctors = User.query.filter_by(role="doctor").order_by(User.full_name).all()
    if request.method == "POST":
        doctor = request.form.get("doctor", "").strip()
        valid_doctors = {staff_member.full_name for staff_member in doctors}
        if doctor and doctor not in valid_doctors:
            flash("Please assign a registered doctor.", "danger")
            return redirect(url_for("queue_assign", queue_id=queue.id))
        queue.doctor = doctor
        audit(
            "queue_updated", "queue_entry", queue.id, f'doctor={doctor or "unassigned"}'
        )
        db.session.commit()
        flash("Queue updated", "success")
        return redirect(url_for("dashboard"))
    return render_template("queue_assign.html", queue=queue, doctors=doctors)


@app.route("/queue/claim/<int:queue_id>", methods=["POST"])
@login_required(roles="doctor")
def queue_claim(queue_id):
    queue = QueueEntry.query.get_or_404(queue_id)

    # Allow reopening today's active outpatient patients
    if queue.status not in {"queued", "with_doctor", "in_progress", "results_ready"}:
        return jsonify({"error": "This queue entry is no longer available."}), 409

    queue.doctor = session.get("full_name", session.get("username"))
    queue.status = "in_progress"
    audit("queue_claimed", "queue_entry", queue.id, f"doctor={queue.doctor}")
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "patient_url": url_for(
                "patient_details", national_id=queue.patient.national_id
            ),
        }
    )

@app.route("/api/dashboard-stats")
@login_required()
def dashboard_stats():
    today = datetime.utcnow().date()
    today_patients = Patient.query.filter(
        db.func.date(Patient.registration_date) == today
    ).count()
    today_visits = Visit.query.filter(db.func.date(Visit.started_at) == today).count()
    revisit_visits = Visit.query.filter(
        db.func.date(Visit.started_at) == today,
        Visit.visit_type == "Revisit",
    ).count()
    new_visits = Visit.query.filter(
        db.func.date(Visit.started_at) == today,
        Visit.visit_type == "New Visit",
    ).count()
    followups_due = PatientFollowUp.query.filter(
        PatientFollowUp.status == "scheduled",
        db.func.date(PatientFollowUp.scheduled_for) == today,
    ).count()
    return {
        "today_patients": today_patients,
        "today_visits": today_visits,
        "revisit_visits": revisit_visits,
        "new_visits": new_visits,
        "followups_due": followups_due,
    }


@app.route("/pharmacy")
@login_required(roles=PHARMACY_ROLES)
def pharmacy_queue():
    prescriptions = (
        Prescription.query.filter(
            db.or_(
                Prescription.status.in_({"prescribed", "processing"}),
                db.and_(
                    Prescription.status == "dispensed",
                    Prescription.pharmacy_amount.is_(None),
                ),
            )
        )
        .order_by(Prescription.prescribed_at.asc())
        .all()
    )
    stock_by_name = {
        item.medication.casefold(): item for item in MedicineStock.query.all()
    }
    response = app.make_response(
        render_template(
            "pharmacy.html",
            prescriptions=prescriptions,
            stock_by_name=stock_by_name,
        )
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.route("/pharmacy/inventory", methods=["GET", "POST"])
@login_required(roles=PHARMACY_ROLES)
def pharmacy_inventory():
    if request.method == "POST":
        medication = request.form.get("medication", "").strip()
        try:
            quantity = int(request.form.get("quantity", "").strip())
            unit_price = float(request.form.get("unit_price", "").strip())
            reorder_level = int(request.form.get("reorder_level", "10").strip())
        except (TypeError, ValueError):
            flash("Enter valid stock quantity, price, and reorder level.", "danger")
            return redirect(url_for("pharmacy_inventory"))
        if not medication or quantity < 0 or unit_price < 0 or reorder_level < 0:
            flash(
                "Medicine name is required and stock values cannot be negative.",
                "danger",
            )
            return redirect(url_for("pharmacy_inventory"))

        stock = MedicineStock.query.filter(
            db.func.lower(MedicineStock.medication) == medication.casefold()
        ).first()
        if stock:
            stock.quantity += quantity
            stock.unit_price = unit_price
            stock.reorder_level = reorder_level
            action = "updated"
        else:
            stock = MedicineStock(
                medication=medication,
                quantity=quantity,
                unit_price=unit_price,
                reorder_level=reorder_level,
            )
            db.session.add(stock)
            action = "added"
        audit(
            "medicine_stock_updated",
            "medicine_stock",
            stock.id or "new",
            f"medication={medication}; quantity_added={quantity}",
        )
        db.session.commit()
        flash(f"{medication} stock {action}.", "success")
        return redirect(url_for("pharmacy_inventory"))

    stock_items = MedicineStock.query.order_by(MedicineStock.medication.asc()).all()
    return render_template("pharmacy_inventory.html", stock_items=stock_items)


@app.route("/pharmacy/<int:prescription_id>/dispense", methods=["POST"])
@login_required(roles=PHARMACY_ROLES)
def dispense_prescription(prescription_id):
    prescription = Prescription.query.get_or_404(prescription_id)
    is_billing_completion = (
        prescription.status == "dispensed" and prescription.pharmacy_amount is None
    )
    if prescription.status == "dispensed" and not is_billing_completion:
        flash(f"{prescription.medication} has already been dispensed.", "warning")
        return redirect(url_for("pharmacy_queue"))

    try:
        dispensed_units = int(request.form.get("dispensed_units", "").strip())
        unit_price = float(request.form.get("unit_price", "").strip())
    except (TypeError, ValueError):
        flash("Enter valid units dispensed and price per unit.", "danger")
        return redirect(url_for("pharmacy_queue"))
    if dispensed_units <= 0 or unit_price < 0:
        flash("Units must be greater than zero and price cannot be negative.", "danger")
        return redirect(url_for("pharmacy_queue"))

    stock = MedicineStock.query.filter(
        db.func.lower(MedicineStock.medication) == prescription.medication.casefold()
    ).first()
    if not stock or stock.quantity < dispensed_units:
        available = stock.quantity if stock else 0
        flash(
            f"Insufficient stock for {prescription.medication}. Available: {available}.",
            "danger",
        )
        return redirect(url_for("pharmacy_queue"))
    if unit_price == 0 and stock.unit_price is not None:
        unit_price = float(stock.unit_price)

    prescription.status = "dispensed"
    prescription.dispensed_by = session.get(
        "full_name", session.get("username", "Pharmacist")
    )
    prescription.dispensed_at = datetime.utcnow()
    prescription.dispensed_units = dispensed_units
    prescription.unit_price = unit_price
    prescription.pharmacy_amount = round(dispensed_units * unit_price, 2)
    stock.quantity -= dispensed_units
    pharmacy_request = (
        ServiceRequest.query.filter(
            ServiceRequest.patient_id == prescription.patient_id,
            ServiceRequest.service_type == "Pharmacy",
            ServiceRequest.status.in_({"Pending", "processing", "in_progress"}),
        )
        .order_by(ServiceRequest.requested_at.desc())
        .first()
    )
    if pharmacy_request:
        pharmacy_request.status = "Completed"
        audit(
        (
            "pharmacy_charge_recorded"
            if is_billing_completion
            else "prescription_dispensed"
        ),
        "prescription",
        prescription.id,
        f"patient_id={prescription.patient_id}; units={dispensed_units}; amount={prescription.pharmacy_amount}; dispensed_by={prescription.dispensed_by}",
    )

    visit_closed = maybe_complete_visit_after_pharmacy(
        prescription.visit_id, prescription.patient_id
    )
    db.session.commit()

    if visit_closed:
        flash(
            f"{prescription.medication} dispensed. Visit completed — no remaining prescriptions.",
            "success",
        )
    else:
        flash(f"{prescription.medication} marked as dispensed.", "success")
    return redirect(url_for("pharmacy_queue"))


@app.route("/register", methods=["GET", "POST"])
@login_required(roles=REGISTRATION_ROLES)
def register_patient():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        other_names = request.form.get("other_names", "").strip()
        try:
            age = parse_optional_number(
                request.form.get("age"), "Age", 0, 120, integer=True
            )
        except ValueError:
            flash("Please enter a valid age.", "danger")
            return redirect(url_for("register_patient"))

        gender = request.form.get("gender", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "")
        national_id = request.form.get("national_id", "").strip()

        if not first_name:
            flash("Please enter the patient's first name.", "danger")
            return redirect(url_for("register_patient"))
        if not national_id or len(national_id) > 20:
            flash("Please enter a valid national ID.", "danger")
            return redirect(url_for("register_patient"))
        if gender not in {"", "Male", "Female", "Other"}:
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
            visit_type="New Visit",
        )

        try:
            db.session.add(new_patient)
            db.session.flush()
            audit("patient_registered", "patient", new_patient.id)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A patient with that national ID already exists.", "danger")
            return redirect(url_for("register_patient", national_id=national_id))

        flash(
            f"Patient {new_patient.full_name} registered successfully! National ID: {new_patient.national_id}",
            "success",
        )
        return redirect(url_for("patient_details", national_id=new_patient.national_id))

    pre_national_id = request.args.get("national_id", "").strip()
    return render_template("register.html", national_id=pre_national_id)


@app.route("/patient/lookup", methods=["POST"])
@login_required(roles=REGISTRATION_ROLES)
def patient_lookup():
    national_id = request.form.get("national_id", "").strip()
    if not national_id:
        flash("Please enter a national ID to search.", "warning")
        return redirect(url_for("dashboard"))

    patient = Patient.query.filter_by(national_id=national_id).first()
    if patient:
        return redirect(url_for("patient_details", national_id=patient.national_id))
    return redirect(url_for("register_patient", national_id=national_id))


@app.route("/patient/<national_id>/edit", methods=["GET", "POST"])
@login_required(roles=REGISTRATION_ROLES)
def edit_patient(national_id):
    patient = Patient.query.filter_by(national_id=national_id).first_or_404()
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        other_names = request.form.get("other_names", "").strip()
        age_raw = request.form.get("age", "").strip()
        try:
            age_val = parse_optional_number(age_raw, "Age", 0, 120, integer=True)
        except ValueError:
            flash("Please enter a valid age.", "danger")
            return redirect(url_for("edit_patient", national_id=national_id))

        if not first_name:
            flash("Please enter the patient's first name.", "danger")
            return redirect(url_for("edit_patient", national_id=national_id))

        patient.first_name = first_name
        patient.other_names = other_names
        patient.name = f"{patient.first_name} {patient.other_names}".strip()
        patient.age = age_val
        patient.gender = request.form.get("gender", "").strip()
        patient.phone = request.form.get("phone", "").strip()
        patient.address = request.form.get("address", "")
        patient.allergies = encrypt_text(request.form.get("allergies", "").strip() or None)
        audit("patient_updated", "patient", patient.id)
        db.session.commit()
        flash("Patient updated successfully.", "success")
        return redirect(url_for("patient_details", national_id=patient.national_id))

    return render_template("patient_edit.html", patient=patient)


# LABORATORY MODULE 
register_lab_module(app, db, Patient, Visit, ServiceRequest, AuditLog)


# RADIOLOGY 
def allowed_image_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS
    )


@app.route("/radiology")
@login_required(roles=RADIOLOGY_ROLES | {"admin"})
def radiology_queue():
    pending = (
        ImagingStudy.query.filter(ImagingStudy.status.in_({"pending", "in_progress"}))
        .order_by(ImagingStudy.requested_at.asc())
        .all()
    )
    completed_today = (
        ImagingStudy.query.filter(
            ImagingStudy.status == "completed",
            ImagingStudy.performed_at
            >= datetime.combine(datetime.utcnow().date(), datetime.min.time()),
        )
        .order_by(ImagingStudy.performed_at.desc())
        .limit(20)
        .all()
    )
    response = app.make_response(
        render_template(
            "radiology.html",
            pending=pending,
            completed_today=completed_today,
        )
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.route("/radiology/<int:study_id>", methods=["GET", "POST"])
@login_required(roles=RADIOLOGY_ROLES | IMAGING_VIEW_ROLES | {"admin"})
def radiology_study(study_id):
    study = ImagingStudy.query.get_or_404(study_id)
    patient = study.patient
        # Doctors can view completed reports; only radiology staff can process them
    if request.method == "POST":
        if session.get("role") not in RADIOLOGY_ROLES | {"admin"}:
            flash("Only radiology staff can update imaging studies.", "danger")
            return redirect(url_for("radiology_study", study_id=study.id))

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "start":
            if study.status == "pending":
                study.status = "in_progress"
                study.performed_by = session.get(
                    "full_name", session.get("username", "Radiographer")
                )
                audit(
                    "imaging_started",
                    "imaging_study",
                    study.id,
                    f"by={study.performed_by}",
                )
                db.session.commit()
                flash("Study marked as in progress.", "success")
            return redirect(url_for("radiology_study", study_id=study.id))

        if action == "save_report":
            findings = request.form.get("findings", "").strip()
            impression = request.form.get("impression", "").strip()
            if not findings and not impression:
                flash("Please enter findings or impression before saving.", "danger")
                return redirect(url_for("radiology_study", study_id=study.id))

            study.findings = findings or None
            study.impression = impression or None
            study.performed_by = session.get(
                "full_name", session.get("username", "Radiographer")
            )
            study.performed_at = datetime.utcnow()
            study.status = "completed"
            study.reviewed_by = None
            study.reviewed_at = None

            if study.service_request:
                study.service_request.status = "Completed"

            # Send patient back to doctor queue for result review
            visit_id = study.visit_id
            if not visit_id:
                latest_visit = (
                    Visit.query.filter_by(patient_id=study.patient_id)
                    .order_by(Visit.started_at.desc())
                    .first()
                )
                if latest_visit:
                    visit_id = latest_visit.id
                    study.visit_id = visit_id

            if visit_id:
                queue = QueueEntry.query.filter_by(visit_id=visit_id).first()
                if queue:
                    queue.status = "results_ready"
                else:
                    db.session.add(
                        QueueEntry(
                            visit_id=visit_id,
                            patient_id=study.patient_id,
                            status="results_ready",
                        )
                    )

            audit(
                "imaging_completed",
                "imaging_study",
                study.id,
                f"exam={study.exam_name}",
            )
            db.session.commit()
            flash("Report saved. Patient sent back to doctor queue for review.", "success")
            return redirect(url_for("radiology_queue"))
        if action == "upload_images":
            files = request.files.getlist("images")
            saved = 0
            for f in files:
                if not f or not f.filename:
                    continue
                if not allowed_image_file(f.filename):
                    flash(f"Skipped unsupported file: {f.filename}", "warning")
                    continue
                original = secure_filename(f.filename)
                ext = original.rsplit(".", 1)[1].lower()
                stored_name = f"{uuid.uuid4().hex}.{ext}"
                f.save(os.path.join(UPLOAD_FOLDER, stored_name))
                img = ImagingImage(
                    study_id=study.id,
                    filename=stored_name,
                    original_name=original,
                    uploaded_by=session.get(
                        "full_name", session.get("username", "Radiographer")
                    ),
                )
                db.session.add(img)
                saved += 1
            if saved:
                if study.status == "pending":
                    study.status = "in_progress"
                    study.performed_by = session.get(
                        "full_name", session.get("username", "Radiographer")
                    )
                audit(
                    "imaging_images_uploaded",
                    "imaging_study",
                    study.id,
                    f"count={saved}",
                )
                db.session.commit()
                flash(f"{saved} image(s) uploaded.", "success")
            else:
                flash("No valid images were uploaded.", "danger")
            return redirect(url_for("radiology_study", study_id=study.id))

    return render_template("radiology_study.html", study=study, patient=patient)

@app.route("/inpatient")
@login_required()
def inpatient_list():
    admissions = (
        Admission.query.filter_by(status="admitted")
        .order_by(Admission.admitted_at.desc())
        .all()
    )
    wards = Ward.query.filter_by(active=True).order_by(Ward.name.asc()).all()
    free_beds = (
        Bed.query.filter_by(status="available", active=True)
        .order_by(Bed.ward_id.asc(), Bed.label.asc())
        .all()
    )

    occupancy = []
    for ward in wards:
        beds = (
            Bed.query.filter_by(ward_id=ward.id, active=True)
            .order_by(Bed.label.asc())
            .all()
        )
        free = sum(1 for b in beds if b.status == "available")
        occupied = sum(1 for b in beds if b.status == "occupied")
        occupancy.append(
            {
                "ward": ward,
                "beds": beds,
                "total": len(beds),
                "free": free,
                "occupied": occupied,
            }
        )

    total_beds = sum(row["total"] for row in occupancy)
    total_occupied = sum(row["occupied"] for row in occupancy)
    total_free = sum(row["free"] for row in occupancy)
    overall_pct = round((total_occupied / total_beds * 100) if total_beds else 0)

    return render_template(
        "inpatient_list.html",
        admissions=admissions,
        wards=wards,
        free_beds=free_beds,
        occupancy=occupancy,
        total_beds=total_beds,
        total_occupied=total_occupied,
        total_free=total_free,
        overall_pct=overall_pct,
    )

@app.route("/inpatient/<int:admission_id>")
@login_required(roles=INPATIENT_ROLES | {"admin"})
def inpatient_detail(admission_id):
    adm = Admission.query.get_or_404(admission_id)
    patient = adm.patient

    notes = (
        WardNote.query.filter_by(admission_id=adm.id)
        .order_by(WardNote.recorded_at.desc())
        .all()
    )

    diagnoses = []
    if adm.visit_id:
        diagnoses = (
            Diagnosis.query.filter_by(visit_id=adm.visit_id)
            .order_by(Diagnosis.diagnosed_at.desc())
            .all()
        )

    past_diagnoses = (
        Diagnosis.query.filter(
            Diagnosis.patient_id == patient.id,
            Diagnosis.visit_id != adm.visit_id if adm.visit_id else True,
        )
        .order_by(Diagnosis.diagnosed_at.desc())
        .limit(10)
        .all()
    )

    previous_admissions = (
        Admission.query.filter(
            Admission.patient_id == patient.id,
            Admission.id != adm.id,
        )
        .order_by(Admission.admitted_at.desc())
        .limit(10)
        .all()
    )

    visits = (
        Visit.query.filter_by(patient_id=patient.id)
        .order_by(Visit.started_at.desc())
        .limit(10)
        .all()
    )
    ward_prescriptions = []
    if adm.visit_id:
        ward_prescriptions = (
            Prescription.query.filter_by(visit_id=adm.visit_id)
            .order_by(Prescription.prescribed_at.desc())
            .all()
        )

    return render_template(
        "inpatient_detail.html",
        admission=adm,
        notes=notes,
        diagnoses=diagnoses,
        past_diagnoses=past_diagnoses,
        ward_prescriptions=ward_prescriptions,
        previous_admissions=previous_admissions,
        visits=visits,
        default_discharge_time=adm.discharge_summary_discharge_at or datetime.utcnow(),
    )


@app.route("/inpatient/<int:admission_id>/discharge-summary")
@login_required(roles=INPATIENT_ROLES | {"admin"})
def inpatient_discharge_summary(admission_id):
    adm = Admission.query.get_or_404(admission_id)
    if adm.status != "discharged" and not adm.discharge_summary_saved_at:
        abort(404)
    return render_template("inpatient_discharge_summary.html", admission=adm)


@app.route("/inpatient/<int:admission_id>/note", methods=["POST"])
@login_required(roles=INPATIENT_ROLES | {"admin"})
def inpatient_add_note(admission_id):
    adm = Admission.query.get_or_404(admission_id)
    if adm.status != "admitted":
        flash("Cannot add notes to a discharged admission.", "warning")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))

    text = request.form.get("note", "").strip()
    note_type = request.form.get("note_type", "nursing").strip() or "nursing"
    if note_type not in {"nursing", "doctor", "progress"}:
        note_type = "nursing"
    if not text:
        flash("Enter a note before saving.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))

    wn = WardNote(
        admission_id=adm.id,
        patient_id=adm.patient_id,
        note=text,
        note_type=note_type,
        recorded_by=session.get("full_name", session.get("username", "Staff")),
    )
    db.session.add(wn)
    db.session.flush()
    ensure_inpatient_visit(adm)
    audit(
        "nursing_note_recorded",
        "ward_note",
        wn.id,
        f"admission_id={adm.id}; patient_id={adm.patient_id}; type={note_type}",
    )
    db.session.commit()
    flash("Nursing note saved.", "success")
    return redirect(url_for("inpatient_detail", admission_id=adm.id))

def ensure_inpatient_visit(adm):
    """One open Inpatient visit per admission, used for ward drugs and the invoice."""
    visit = Visit.query.get(adm.visit_id) if adm.visit_id else None
    if not visit:
        visit = Visit(
            patient_id=adm.patient_id,
            visit_type="Inpatient",
            status="admitted",
            started_at=adm.admitted_at or datetime.utcnow(),
        )
        db.session.add(visit)
        db.session.flush()
        adm.visit_id = visit.id
    if not visit.invoice_number:
        visit.invoice_number = f"INV-{visit.id:06d}"
    if not visit.consultation_amount:
        visit.consultation_amount = 0
    return visit

@app.route("/inpatient/<int:admission_id>/prescribe", methods=["POST"])
@login_required(roles={"doctor", "admin"})
def inpatient_prescribe(admission_id):
    adm = Admission.query.get_or_404(admission_id)
    if adm.status != "admitted":
        flash("This admission is closed.", "warning")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))

    medication = request.form.get("medication", "").strip()
    dosage = request.form.get("dosage", "").strip()
    frequency = request.form.get("frequency", "").strip()
    duration = request.form.get("duration", "").strip()
    if not all([medication, dosage, frequency, duration]):
        flash("Medication, dosage, frequency, and duration are required.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))

    visit = ensure_inpatient_visit(adm)
    prescription = Prescription(
        patient_id=adm.patient_id,
        visit_id=visit.id,
        medication=medication[:120],
        dosage=dosage[:80],
        frequency=frequency[:80],
        duration=duration[:80],
        quantity=request.form.get("quantity", "").strip()[:40] or None,
        route=request.form.get("route", "").strip()[:50] or "Oral",
        instructions=request.form.get("instructions", "").strip() or None,
        prescribed_by=session.get("full_name", session.get("username", "Doctor")),
        status="prescribed",
    )
    db.session.add(prescription)
    db.session.flush()
    audit(
        "ward_prescription_created",
        "prescription",
        prescription.id,
        f"admission_id={adm.id}; visit_id={visit.id}",
    )
    db.session.commit()
    flash(f"{medication} sent to pharmacy on the admission invoice.", "success")
    return redirect(url_for("inpatient_detail", admission_id=adm.id))

@app.route("/inpatient/admit", methods=["POST"])
@login_required(roles=INPATIENT_ROLES | {"admin"})
def inpatient_admit():
    national_id = request.form.get("national_id", "").strip()
    bed_id = request.form.get("bed_id", type=int)
    reason = request.form.get("reason", "").strip()[:255] or None
    admitting_doctor = request.form.get("admitting_doctor", "").strip()[:100] or None

    patient = Patient.query.filter_by(national_id=national_id).first()
    if not patient:
        flash("Patient not found. Register the patient first.", "danger")
        return redirect(url_for("inpatient_list"))

    bed = Bed.query.get(bed_id) if bed_id else None
    if not bed or bed.status != "available" or not bed.active:
        flash("Select an available bed.", "danger")
        return redirect(url_for("inpatient_list"))

    open_adm = Admission.query.filter_by(
        patient_id=patient.id, status="admitted"
    ).first()
    if open_adm:
        flash("This patient is already admitted.", "warning")
        return redirect(url_for("inpatient_list"))

    adm = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by=session.get("full_name", session.get("username", "Staff")),
        admitting_doctor=admitting_doctor
        or session.get("full_name", session.get("username")),
        reason=reason,
        status="admitted",
    )
    bed.status = "occupied"
    db.session.add(adm)
    db.session.flush()
    audit(
        "patient_admitted",
        "admission",
        adm.id,
        f"patient_id={patient.id}; bed={bed.label}; ward_id={bed.ward_id}",
    )
    db.session.commit()
    flash(
        f"{patient.full_name} admitted to {bed.ward.name} — {bed.label}.",
        "success",
    )
    return redirect(url_for("inpatient_list"))


def _money(name, default=0):
    raw = request.form.get(name, "").strip()
    if raw == "":
        return float(default)
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(default)


def finalize_admission_billing(adm):
    """Set admission fee + bed charges when discharging."""
    if adm.bed_charges is not None and adm.admission_fee is not None:
        return

    ward_name = adm.bed.ward.name if adm.bed and adm.bed.ward else ""
    rate = float(BED_DAILY_RATES.get(ward_name, 1500))
    fee = float(ADMISSION_FEE)

    end = adm.discharged_at or datetime.utcnow()
    start = adm.admitted_at or end
    nights = (end.date() - start.date()).days
    days = max(1, nights)

    adm.bed_daily_rate = rate
    adm.bed_days = days
    adm.bed_charges = round(rate * days, 2)
    adm.admission_fee = fee


@app.route("/inpatient/<int:admission_id>/discharge", methods=["POST"])
@login_required(roles=INPATIENT_ROLES | {"admin"})
def inpatient_discharge(admission_id):
    adm = Admission.query.get_or_404(admission_id)
    if adm.status != "admitted":
        flash("This admission is already closed.", "warning")
        return redirect(url_for("inpatient_list"))

    discharge_diagnosis = request.form.get("discharge_diagnosis", "").strip()
    discharge_diagnosis_code = (
        request.form.get("discharge_diagnosis_code", "").strip()
    )
    hospital_course = request.form.get("hospital_course", "").strip()
    discharge_condition = request.form.get("discharge_condition", "").strip()
    action = request.form.get("action", "finalize")
    if action not in {"save_draft", "finalize"}:
        flash("Choose whether to save a draft or finalize the discharge.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))
    if action == "finalize" and (not discharge_diagnosis or not hospital_course):
        flash("Enter the final diagnosis and hospital course before discharge.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))
    if discharge_diagnosis or discharge_diagnosis_code:
        selected_diagnosis = next(
            (
                row
                for row in load_icd11()
                if row["code"].casefold() == discharge_diagnosis_code.casefold()
                and row["text"] == discharge_diagnosis
            ),
            None,
        )
        if not selected_diagnosis:
            flash(
                "Select the final diagnosis from the ICD-11 search results.",
                "danger",
            )
            return redirect(url_for("inpatient_detail", admission_id=adm.id))
    elif action == "finalize":
        flash("Select an ICD-11 final diagnosis before discharge.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))
    if discharge_condition and discharge_condition not in {
        "Stable",
        "Improved",
        "Unchanged",
        "Other",
    }:
        flash("Select a valid condition at discharge.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))
    if action == "finalize" and not discharge_condition:
        flash("Select the patient's condition at discharge.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))

    try:
        admitted_at = datetime.strptime(
            request.form.get("admitted_at", "").strip(), "%Y-%m-%dT%H:%M"
        )
        discharge_summary_discharge_at = datetime.strptime(
            request.form.get("discharged_at", "").strip(), "%Y-%m-%dT%H:%M"
        )
    except ValueError:
        flash("Enter valid admission and discharge dates.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))
    if discharge_summary_discharge_at < admitted_at:
        flash("The discharge date cannot be before the admission date.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))

    if action == "finalize" and discharge_summary_discharge_at > datetime.utcnow():
        flash("The discharge date cannot be in the future.", "danger")
        return redirect(url_for("inpatient_detail", admission_id=adm.id))

    notes = request.form.get("discharge_notes", "").strip()[:255] or None
    adm.admitted_at = admitted_at
    adm.discharge_summary_discharge_at = discharge_summary_discharge_at
    adm.discharge_diagnosis = discharge_diagnosis[:255]
    adm.discharge_diagnosis_code = discharge_diagnosis_code[:40] or None
    adm.hospital_course = hospital_course[:10000]
    adm.discharge_condition = discharge_condition
    adm.discharge_medications = (
        request.form.get("discharge_medications", "").strip()[:5000] or None
    )
    adm.follow_up_instructions = (
        request.form.get("follow_up_instructions", "").strip()[:5000] or None
    )
    adm.discharge_notes = notes
    adm.discharge_summary_saved_at = datetime.utcnow()
    adm.discharge_summary_saved_by = session.get(
        "full_name", session.get("username", "Staff")
    )

    if action == "save_draft":
        audit(
            "discharge_summary_saved",
            "admission",
            adm.id,
            f"patient_id={adm.patient_id}; status=draft; "
            f"admitted_at={adm.admitted_at.isoformat()}; "
            f"planned_discharge_at={adm.discharge_summary_discharge_at.isoformat()}",
        )
        db.session.commit()
        flash("Discharge summary draft saved. The admission remains open.", "success")
        return redirect(url_for("inpatient_discharge_summary", admission_id=adm.id))

    adm.status = "discharged"
    adm.discharged_at = discharge_summary_discharge_at
    adm.discharged_by = session.get("full_name", session.get("username", "Staff"))
    if adm.bed:
        adm.bed.status = "available"

    finalize_admission_billing(adm)
    adm.doctor_fee = DEFAULT_DOCTOR_FEE
    adm.nursing_fee = DEFAULT_NURSING_FEE
    adm.consumables_fee = DEFAULT_CONSUMABLES

    visit = Visit.query.get(adm.visit_id) if adm.visit_id else None
    if not visit:
        visit = Visit(
            patient_id=adm.patient_id,
            visit_type="Inpatient",
            status="completed",
            started_at=adm.admitted_at,
            completed_at=adm.discharged_at,
        )
        db.session.add(visit)
        db.session.flush()
        adm.visit_id = visit.id

    if not visit.invoice_number:
        visit.invoice_number = f"INV-{visit.id:06d}"
    visit.status = "completed"
    if hasattr(visit, "completed_at"):
        visit.completed_at = adm.discharged_at

    audit(
        "admission_billed",
        "admission",
        adm.id,
        f"days={adm.bed_days}; bed={adm.bed_charges}; fee={adm.admission_fee}; "
        f"doctor={adm.doctor_fee}; nursing={adm.nursing_fee}; consumables={adm.consumables_fee}; "
        f"visit_id={visit.id}",
    )
    audit(
        "patient_discharged",
        "admission",
        adm.id,
        f"patient_id={adm.patient_id}; bed_id={adm.bed_id}; "
        f"discharged_at={adm.discharged_at.isoformat()}; "
        f"diagnosis_recorded={bool(adm.discharge_diagnosis)}",
    )
    db.session.commit()
    flash("Patient discharged and bed released.", "success")
    return redirect(url_for("inpatient_discharge_summary", admission_id=adm.id))

INPATIENT_BILLING_ROLES = {"accounts", "receptionist", "records", "admin"}


@app.route("/invoices/<int:visit_id>/inpatient-charges", methods=["POST"])
@login_required(roles=INPATIENT_BILLING_ROLES | {"admin"})
def update_inpatient_charges(visit_id):
    visit = Visit.query.filter(
        Visit.id == visit_id,
        Visit.invoice_number.isnot(None),
        Visit.invoice_number != "",
    ).first_or_404()

    adm = (
        Admission.query.filter(
            Admission.visit_id == visit.id,
        )
        .order_by(Admission.id.desc())
        .first()
    )
    if not adm:
        flash("No inpatient admission linked to this invoice.", "warning")
        return redirect(url_for("invoice_detail", visit_id=visit.id))

    def _money(name, current=0):
        raw = request.form.get(name, "").strip()
        if raw == "":
            return float(current or 0)
        try:
            return max(0.0, float(raw))
        except ValueError:
            return float(current or 0)

    def _int(name, current=1):
        raw = request.form.get(name, "").strip()
        if raw == "":
            return int(current or 1)
        try:
            return max(1, int(raw))
        except ValueError:
            return int(current or 1)

    days = _int("bed_days", adm.bed_days or 1)
    rate = _money("bed_daily_rate", adm.bed_daily_rate or 1500)

    adm.bed_days = days
    adm.bed_daily_rate = rate
    adm.bed_charges = round(days * rate, 2)
    adm.admission_fee = _money("admission_fee", adm.admission_fee or ADMISSION_FEE)
    adm.doctor_fee = _money("doctor_fee", adm.doctor_fee or DEFAULT_DOCTOR_FEE)
    adm.nursing_fee = _money("nursing_fee", adm.nursing_fee or DEFAULT_NURSING_FEE)
    adm.consumables_fee = _money(
        "consumables_fee", adm.consumables_fee or DEFAULT_CONSUMABLES
    )

    audit(
        "inpatient_charges_updated",
        "admission",
        adm.id,
        f"visit_id={visit.id}; days={days}; rate={rate}; bed={adm.bed_charges}",
    )
    db.session.commit()
    flash("Inpatient charges updated on invoice.", "success")
    return redirect(url_for("invoice_detail", visit_id=visit.id))

@app.route("/imaging/file/<path:filename>")
@login_required(roles=IMAGING_VIEW_ROLES)
def imaging_file(filename):
    # Prevent path traversal
    safe = os.path.basename(filename)
    return send_from_directory(UPLOAD_FOLDER, safe)


@app.route("/theatre/file/<path:filename>")
@login_required(roles=THEATRE_VIEW_ROLES)
def theatre_file(filename):
    safe = os.path.basename(filename)
    return send_from_directory(THEATRE_UPLOAD_FOLDER, safe)


@app.route("/imaging/<int:study_id>/print")
@login_required(roles=IMAGING_VIEW_ROLES)
def imaging_report_print(study_id):
    study = ImagingStudy.query.get_or_404(study_id)
    return render_template(
        "imaging_report_print.html", study=study, patient=study.patient
    )


def get_audit_log_query():
    today = datetime.utcnow().date()
    raw_start_date = request.args.get(
        "start_date", (today - timedelta(days=30)).isoformat()
    )
    raw_end_date = request.args.get("end_date", today.isoformat())
    try:
        start_date = datetime.strptime(raw_start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(raw_end_date, "%Y-%m-%d").date()
    except ValueError as error:
        abort(400, description="Enter valid start and end dates.")
    if start_date > end_date:
        abort(400, description="Start date must be on or before end date.")

    staff_id = request.args.get("staff_id", "").strip()
    staff_id_value = None
    if staff_id:
        if not staff_id.isdecimal() or len(staff_id) > 10:
            abort(400, description="Select a valid staff member.")
        staff_id_value = int(staff_id)
        if not User.query.filter_by(id=staff_id_value).first():
            abort(400, description="Select a valid staff member.")

    action = request.args.get("action", "").strip()
    if action and action not in {
        row[0] for row in db.session.query(AuditLog.action).distinct().all()
    }:
        abort(400, description="Select a valid audit action.")

    search = request.args.get("q", "").strip()[:100]
    start_at = datetime.combine(start_date, datetime.min.time())
    end_at = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    query = AuditLog.query.filter(
        AuditLog.created_at >= start_at,
        AuditLog.created_at < end_at,
    )
    if staff_id:
        query = query.filter(AuditLog.actor_user_id == staff_id_value)
    if action:
        query = query.filter(AuditLog.action == action)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            db.or_(
                AuditLog.action.ilike(pattern),
                AuditLog.target_type.ilike(pattern),
                AuditLog.target_id.ilike(pattern),
                AuditLog.details.ilike(pattern),
            )
        )

    filters = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "staff_id": staff_id,
        "action": action,
        "q": search,
    }
    return query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()), filters


@app.route("/admin/audit-log")
@login_required(roles="admin")
def audit_log():
    query, filters = get_audit_log_query()
    raw_page = request.args.get("page", "1")
    if not raw_page.isdecimal():
        abort(400, description="Page number must be positive.")
    page = int(raw_page)
    if page < 1:
        abort(400, description="Page number must be positive.")
    entries = query.paginate(page=page, per_page=100, error_out=False)
    staff = User.query.order_by(User.full_name, User.username).all()
    actions = [
        row[0]
        for row in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    ]
    return render_template(
        "audit_log.html",
        entries=entries,
        staff=staff,
        actions=actions,
        filters=filters,
    )


@app.route("/admin/audit-log/export.csv")
@login_required(roles="admin")
def audit_log_export():
    query, filters = get_audit_log_query()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        ["Timestamp (UTC)", "Staff username", "Staff name", "Action", "Record type", "Record ID", "Details"]
    )
    entries = query.all()
    for entry in entries:
        values = [
            entry.created_at.strftime("%Y-%m-%d %H:%M:%S") if entry.created_at else "",
            entry.actor.username if entry.actor else "Unknown",
            entry.actor.full_name if entry.actor and entry.actor.full_name else "",
            entry.action,
            entry.target_type,
            entry.target_id,
            entry.details or "",
        ]
        writer.writerow(
            [
                f"'{value}" if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
                for value in values
            ]
        )
    filename = f"audit-log-{filters['start_date']}-to-{filters['end_date']}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/reports/moh")
@login_required(roles={"admin", "records", "doctor"})
def moh_reports():
    from datetime import date
    from calendar import monthrange

    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        year, month = today.year, today.month
    if not (1 <= month <= 12):
        month = today.month

    start = datetime(year, month, 1)
    _, last_day = monthrange(year, month)
    end = datetime(year, month, last_day, 23, 59, 59)

    # 705A / 705B
    diagnoses = (
        db.session.query(Diagnosis, Patient)
        .join(Patient, Diagnosis.patient_id == Patient.id)
        .filter(Diagnosis.diagnosed_at >= start, Diagnosis.diagnosed_at <= end)
        .all()
    )
    under5 = {}
    over5 = {}
    for diag, patient in diagnoses:
        age = patient.age or 0
        key = (diag.icd_code or "", diag.diagnosis_text or "")
        bucket = under5 if age < 5 else over5
        bucket[key] = bucket.get(key, 0) + 1

    def sorted_diag(d):
        return sorted(
            [{"code": k[0], "name": k[1], "count": v} for k, v in d.items()],
            key=lambda x: -x["count"],
        )

    # 717
    opd_visits = Visit.query.filter(Visit.started_at >= start, Visit.started_at <= end).count()
    admissions = Admission.query.filter(Admission.admitted_at >= start, Admission.admitted_at <= end).count()
    discharges = Admission.query.filter(Admission.discharged_at >= start, Admission.discharged_at <= end).count()
    theatre_done = TheatreProcedure.query.filter(
        TheatreProcedure.status == "completed",
        TheatreProcedure.completed_at >= start,
        TheatreProcedure.completed_at <= end,
    ).count()
    dialysis_sessions = DialysisSession.query.filter(
        DialysisSession.session_date >= start.date(),
        DialysisSession.session_date <= end.date(),
    ).count()

    # 706
    lab_orders = []
    try:
        LabOrder = app.config.get("LAB_ORDER_MODEL")
        if LabOrder:
            lab_orders = LabOrder.query.filter(
                LabOrder.created_at >= start,
                LabOrder.created_at <= end,
            ).all()
    except Exception:
        pass

    lab_counts = {}
    for order in lab_orders:
        name = getattr(order, "test_name", None) or "Unknown"
        lab_counts[name] = lab_counts.get(name, 0) + 1

    lab_summary = sorted(
        [{"name": k, "count": v} for k, v in lab_counts.items()],
        key=lambda x: -x["count"],
    )

    return render_template(
        "moh_reports.html",
        year=year,
        month=month,
        under5=sorted_diag(under5),
        over5=sorted_diag(over5),
        under5_total=sum(under5.values()),
        over5_total=sum(over5.values()),
        opd_visits=opd_visits,
        admissions=admissions,
        discharges=discharges,
        theatre_done=theatre_done,
        dialysis_sessions=dialysis_sessions,
        lab_summary=lab_summary,
        lab_total=sum(lab_counts.values()),
    )
WARDS = [
    ('Male General', 'General', 'MG', 13),
    ('Female General', 'General', 'FG', 6),
    ('Maternity', 'Maternity', 'MAT', 6),
    ('Private Ward', 'Private', 'PV', 9),
    ('Pediatrics', 'Pediatrics', 'PED', 3),
    ('Executive', 'Private', 'EX', 4),
    ('General Private', 'Private', 'GP', 8),
    ('Newborn Unit', 'Maternity', 'NB', 6),
    ('Post Anaesthesia Unit', 'Theatre', 'PACU', 2),
]

with app.app_context():
    db.create_all()
    for name, dept, prefix, count in WARDS:
        w = Ward.query.filter_by(name=name).first()
        if not w:
            w = Ward(name=name, department=dept, active=True)
            db.session.add(w)
            db.session.flush()
        existing = {b.label for b in Bed.query.filter_by(ward_id=w.id).all()}
        for i in range(1, count + 1):
            label = f'{prefix}{i}'
            if label not in existing:
                db.session.add(Bed(ward_id=w.id, label=label, status='available', active=True))
    db.session.commit()
    print('Wards:', Ward.query.count())
    print('Beds:', Bed.query.count())
if __name__ == "__main__":
    app.run(
        debug=env_flag("FLASK_DEBUG"),
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
    )
