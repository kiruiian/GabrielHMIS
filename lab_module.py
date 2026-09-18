from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, session


LAB_ROLES = {"lab", "admin"}
LAB_VIEW_ROLES = {"lab", "doctor", "nurse", "triage", "records", "admin"}


# Placeholder laboratory catalogue.
# These prices are temporary and should later be replaced with
# the official St Gabriel Hospital laboratory tariff.
DEFAULT_TESTS = [
    {
        "test_code": "LAB-CBC",
        "test_name": "Complete Blood Count (CBC)",
        "specimen_type": "EDTA Blood",
        "department": "Hematology",
        "price": 800,
    },
    {
        "test_code": "LAB-MAL",
        "test_name": "Malaria Test (mRDT)",
        "specimen_type": "Whole Blood",
        "department": "Parasitology",
        "price": 200,
    },
    {
        "test_code": "LAB-GLU",
        "test_name": "Blood Glucose",
        "specimen_type": "Blood",
        "department": "Clinical Chemistry",
        "price": 200,
    },
    {
        "test_code": "LAB-UA",
        "test_name": "Urinalysis",
        "specimen_type": "Urine",
        "department": "Clinical Chemistry",
        "price": 200,
    },
    {
        "test_code": "LAB-PREG",
        "test_name": "Urine Pregnancy Test (PDT)",
        "specimen_type": "Urine",
        "department": "Clinical Chemistry",
        "price": 200,
    },
    {
        "test_code": "LAB-HIV",
        "test_name": "HIV Test",
        "specimen_type": "Blood",
        "department": "Serology",
        "price": 200,
    },
    {
        "test_code": "LAB-STOOL",
        "test_name": "Stool Ova & Cysts",
        "specimen_type": "Stool",
        "department": "Parasitology",
        "price": 200,
    },
    {
        "test_code": "LAB-HBS",
        "test_name": "Hepatitis B Surface Antigen (HBsAg)",
        "specimen_type": "Blood",
        "department": "Serology",
        "price": 500,
    },
    {
        "test_code": "LAB-CREA",
        "test_name": "Creatinine",
        "specimen_type": "Serum",
        "department": "Clinical Chemistry",
        "price": 400,
    },
    {
        "test_code": "LAB-UREA",
        "test_name": "Urea",
        "specimen_type": "Serum",
        "department": "Clinical Chemistry",
        "price": 400,
    },
    {
        "test_code": "LAB-LFT",
        "test_name": "Liver Function Tests (LFTs)",
        "specimen_type": "Serum",
        "department": "Clinical Chemistry",
        "price": 1000,
    },
    {
        "test_code": "LAB-LIPID",
        "test_name": "Lipid Profile",
        "specimen_type": "Serum",
        "department": "Clinical Chemistry",
        "price": 1000,
    },
    {
        "test_code": "LAB-HBA1C",
        "test_name": "HbA1c",
        "specimen_type": "Blood",
        "department": "Clinical Chemistry",
        "price": 800,
    },
    {
        "test_code": "LAB-ESR",
        "test_name": "ESR",
        "specimen_type": "EDTA Blood",
        "department": "Hematology",
        "price": 300,
    },
]


def register_lab_module(app, db, Patient, Visit, ServiceRequest, AuditLog):
    """Register the laboratory workflow."""

    class LabTest(db.Model):
        __tablename__ = "lab_test"

        id = db.Column(db.Integer, primary_key=True)
        test_code = db.Column(db.String(30), unique=True, nullable=False)
        test_name = db.Column(db.String(120), nullable=False)
        specimen_type = db.Column(db.String(80), nullable=False)
        department = db.Column(db.String(80), default="Laboratory")
        price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
        active = db.Column(db.Boolean, default=True, nullable=False)

        def __repr__(self):
            return f"<LabTest {self.test_code} - {self.test_name}>"

    class LabOrder(db.Model):
        __tablename__ = "lab_order"

        id = db.Column(db.Integer, primary_key=True)
        patient_id = db.Column(
            db.Integer,
            db.ForeignKey("patient.id"),
            nullable=False,
        )
        visit_id = db.Column(
            db.Integer,
            db.ForeignKey("visit.id"),
        )
        service_request_id = db.Column(
            db.Integer,
            db.ForeignKey("service_request.id"),
        )

        # Kept for compatibility with the existing laboratory workflow.
        # For new multi-test orders, individual tests are stored in LabOrderItem.
        test_name = db.Column(db.String(160), nullable=False)
        specimen_type = db.Column(db.String(80))

        priority = db.Column(
            db.String(20),
            default="Routine",
            nullable=False,
        )

        status = db.Column(
            db.String(30),
            default="Requested",
            nullable=False,
        )

        requested_by = db.Column(db.String(100))
        requested_at = db.Column(
            db.DateTime,
            default=datetime.utcnow,
            nullable=False,
        )

        collected_by = db.Column(db.String(100))
        collected_at = db.Column(db.DateTime)

        received_by = db.Column(db.String(100))
        received_at = db.Column(db.DateTime)

        rejection_reason = db.Column(db.Text)

        result = db.Column(db.Text)
        units = db.Column(db.String(50))
        reference_range = db.Column(db.String(120))
        interpretation = db.Column(db.Text)

        verified_by = db.Column(db.String(100))
        verified_at = db.Column(db.DateTime)

        completed_at = db.Column(db.DateTime)
        reviewed_by = db.Column(db.String(100))
        reviewed_at = db.Column(db.DateTime)

        patient = db.relationship(
            Patient,
            backref=db.backref(
                "lab_orders",
                lazy=True,
                order_by="LabOrder.requested_at.desc()",
            ),
        )

        visit = db.relationship(
            Visit,
            backref=db.backref(
                "lab_orders",
                lazy=True,
            ),
        )

        service_request = db.relationship(
            ServiceRequest,
            backref=db.backref(
                "lab_orders",
                lazy=True,
            ),
        )

    class LabOrderItem(db.Model):
        __tablename__ = "lab_order_item"

        id = db.Column(db.Integer, primary_key=True)

        lab_order_id = db.Column(
            db.Integer,
            db.ForeignKey("lab_order.id"),
            nullable=False,
        )

        lab_test_id = db.Column(
            db.Integer,
            db.ForeignKey("lab_test.id"),
            nullable=False,
        )

        # Price is copied from the catalogue at the time of ordering.
        # This protects historical invoices if the catalogue price changes later.
        price = db.Column(
            db.Numeric(10, 2),
            nullable=False,
            default=0,
        )

        status = db.Column(
            db.String(30),
            default="Requested",
            nullable=False,
        )

        created_at = db.Column(
            db.DateTime,
            default=datetime.utcnow,
            nullable=False,
        )

        lab_order = db.relationship(
            LabOrder,
            backref=db.backref(
                "items",
                lazy=True,
                cascade="all, delete-orphan",
            ),
        )

        lab_test = db.relationship(
            LabTest,
            backref=db.backref(
                "order_items",
                lazy=True,
            ),
        )

        @property
        def subtotal(self):
            return float(self.price or 0)

    def seed_lab_tests():
        """Create the placeholder laboratory catalogue if tests do not exist."""

        for test_data in DEFAULT_TESTS:
            existing_test = LabTest.query.filter_by(
                test_code=test_data["test_code"]
            ).first()

            if existing_test is None:
                db.session.add(LabTest(**test_data))

        db.session.commit()

    def current_user_allowed(roles):
        return session.get("role") in roles

    def audit(action, target_id, details=None):
        db.session.add(
            AuditLog(
                actor_user_id=session.get("user_id"),
                action=action,
                target_type="lab_order",
                target_id=str(target_id),
                details=details,
            )
        )

    def sync_orders():
        """
        Create structured LabOrder rows for older generic Lab service requests.

        This keeps existing Lab service requests working while the new
        catalogue-based ordering system is introduced.
        """

        requests = (
            ServiceRequest.query.filter_by(service_type="Lab")
            .order_by(ServiceRequest.requested_at.asc())
            .all()
        )

        for service in requests:
            existing = LabOrder.query.filter_by(
                service_request_id=service.id
            ).first()

            if existing:
                continue

            patient = service.patient

            latest_visit = (
                Visit.query.filter_by(patient_id=patient.id)
                .order_by(Visit.started_at.desc())
                .first()
            )

            order = LabOrder(
                patient_id=patient.id,
                visit_id=latest_visit.id if latest_visit else None,
                service_request_id=service.id,
                test_name=(
                    service.description
                    or "Laboratory investigation"
                ).strip()[:160],
                requested_by=service.requested_by,
                requested_at=(
                    service.requested_at
                    or datetime.utcnow()
                ),
                status=(
                    "Requested"
                    if (service.status or "").lower()
                    not in {"completed", "cancelled"}
                    else service.status.title()
                ),
            )

            db.session.add(order)

        db.session.commit()

    @app.route("/lab")
    def lab_queue():
        if "user_id" not in session:
            return redirect(url_for("login"))

        if not current_user_allowed(LAB_VIEW_ROLES):
            flash("Access denied for your role.", "danger")
            return redirect(url_for("dashboard"))

        sync_orders()

        status = request.args.get(
            "status",
            "active",
        ).strip().lower()

        if status == "completed":
            orders = (
                LabOrder.query.filter(
                    LabOrder.status.in_(
                        {"Verified", "Completed"}
                    )
                )
                .order_by(
                    LabOrder.completed_at.desc(),
                    LabOrder.requested_at.desc(),
                )
                .limit(50)
                .all()
            )

        elif status == "all":
            orders = (
                LabOrder.query.order_by(
                    LabOrder.requested_at.desc()
                )
                .limit(100)
                .all()
            )

        else:
            orders = (
                LabOrder.query.filter(
                    ~LabOrder.status.in_(
                        {
                            "Verified",
                            "Completed",
                            "Cancelled",
                        }
                    )
                )
                .order_by(
                    LabOrder.priority.desc(),
                    LabOrder.requested_at.asc(),
                )
                .all()
            )

        return render_template(
            "lab.html",
            orders=orders,
            status_filter=status,
        )

    @app.route("/lab/<int:order_id>", methods=["GET", "POST"])
    def lab_order(order_id):
        if "user_id" not in session:
            return redirect(url_for("login"))

        if not current_user_allowed(LAB_VIEW_ROLES):
            flash("Access denied for your role.", "danger")
            return redirect(url_for("dashboard"))

        order = LabOrder.query.get_or_404(order_id)

        if request.method == "POST":

            if not current_user_allowed(LAB_ROLES):
                flash(
                    "Only laboratory staff can process or verify laboratory results.",
                    "danger",
                )
                return redirect(
                    url_for(
                        "lab_order",
                        order_id=order.id,
                    )
                )

            action = request.form.get(
                "action",
                "",
            ).strip()

            actor = session.get(
                "full_name",
                session.get(
                    "username",
                    "Laboratory",
                ),
            )

            if action == "collect":

                if order.status in {
                    "Requested",
                    "Rejected",
                }:
                    order.specimen_type = (
                        request.form.get(
                            "specimen_type",
                            "",
                        ).strip()[:80]
                        or order.specimen_type
                    )

                    order.priority = (
                        request.form.get(
                            "priority",
                            "Routine",
                        ).strip()
                        or "Routine"
                    )

                    order.collected_by = actor
                    order.collected_at = datetime.utcnow()
                    order.status = "Collected"
                    order.rejection_reason = None

                    audit(
                        "lab_specimen_collected",
                        order.id,
                        f"test={order.test_name}",
                    )

                    db.session.commit()

                    flash(
                        "Specimen collection recorded.",
                        "success",
                    )

            elif action == "receive":

                if order.status == "Collected":
                    order.received_by = actor
                    order.received_at = datetime.utcnow()
                    order.status = "Received"

                    audit(
                        "lab_specimen_received",
                        order.id,
                        f"test={order.test_name}",
                    )

                    db.session.commit()

                    flash(
                        "Specimen received in laboratory.",
                        "success",
                    )

            elif action == "reject":

                reason = request.form.get(
                    "rejection_reason",
                    "",
                ).strip()

                if not reason:
                    flash(
                        "Enter the reason for rejecting the specimen.",
                        "danger",
                    )

                    return redirect(
                        url_for(
                            "lab_order",
                            order_id=order.id,
                        )
                    )

                order.rejection_reason = reason[:1000]
                order.status = "Rejected"
                order.received_by = actor
                order.received_at = datetime.utcnow()

                audit(
                    "lab_specimen_rejected",
                    order.id,
                    f"reason={reason[:180]}",
                )

                db.session.commit()

                flash(
                    "Specimen rejected and reason recorded.",
                    "warning",
                )

            elif action == "start":

                if order.status == "Received":
                    order.status = "Processing"

                    audit(
                        "lab_processing_started",
                        order.id,
                        f"test={order.test_name}",
                    )

                    db.session.commit()

                    flash(
                        "Laboratory processing started.",
                        "success",
                    )

            elif action == "save_result":

                result = request.form.get(
                    "result",
                    "",
                ).strip()

                units = request.form.get(
                    "units",
                    "",
                ).strip()

                reference_range = request.form.get(
                    "reference_range",
                    "",
                ).strip()

                interpretation = request.form.get(
                    "interpretation",
                    "",
                ).strip()

                if not result:
                    flash(
                        "Enter the laboratory result before saving.",
                        "danger",
                    )

                    return redirect(
                        url_for(
                            "lab_order",
                            order_id=order.id,
                        )
                    )

                order.result = result
                order.units = units[:50] or None
                order.reference_range = (
                    reference_range[:120]
                    or None
                )
                order.interpretation = (
                    interpretation
                    or None
                )

                order.status = "Result Entered"

                audit(
                    "lab_result_entered",
                    order.id,
                    f"test={order.test_name}",
                )

                db.session.commit()

                flash(
                    "Result saved. It now requires verification.",
                    "success",
                )

            elif action == "verify":

                if not order.result:
                    flash(
                        "A result must be entered before verification.",
                        "danger",
                    )
                    return redirect(
                        url_for(
                            "lab_order",
                            order_id=order.id,
                        )
                    )

                order.verified_by = actor
                order.verified_at = datetime.utcnow()
                order.completed_at = datetime.utcnow()
                order.status = "Verified"
                order.reviewed_by = None
                order.reviewed_at = None

                if order.service_request:
                    order.service_request.status = "Completed"

                # Send patient back to doctor queue for result review
                from flask import current_app
                # QueueEntry and Visit are on the main app models – import via app context objects if needed
                visit_id = order.visit_id
                if visit_id:
                    # Use raw SQL-safe approach through existing session
                    queue = (
                        db.session.execute(
                            db.text(
                                "SELECT id FROM queue_entry WHERE visit_id = :vid LIMIT 1"
                            ),
                            {"vid": visit_id},
                        ).first()
                    )
                    if queue:
                        db.session.execute(
                            db.text(
                                "UPDATE queue_entry SET status = 'results_ready' WHERE id = :qid"
                            ),
                            {"qid": queue[0]},
                        )
                    else:
                        db.session.execute(
                            db.text(
                                "INSERT INTO queue_entry (visit_id, patient_id, status, queued_at) "
                                "VALUES (:vid, :pid, 'results_ready', CURRENT_TIMESTAMP)"
                            ),
                            {"vid": visit_id, "pid": order.patient_id},
                        )

                audit(
                    "lab_result_verified",
                    order.id,
                    f"test={order.test_name}",
                )

                db.session.commit()

                flash(
                    "Laboratory result verified and sent to doctor queue.",
                    "success",
                )
        return render_template(
            "lab_order.html",
            order=order,
        )

    @app.route("/lab/<int:order_id>/print")
    def lab_result_print(order_id):
        if "user_id" not in session:
            return redirect(url_for("login"))

        if not current_user_allowed(LAB_VIEW_ROLES):
            flash(
                "Access denied for your role.",
                "danger",
            )
            return redirect(url_for("dashboard"))

        order = LabOrder.query.get_or_404(order_id)

        return render_template(
            "lab_result_print.html",
            order=order,
        )

    # Create any new laboratory tables.
    with app.app_context():
        db.create_all()
        seed_lab_tests()

    # Expose the models so app.py and future billing/order routes
    # can use them without importing this module's local classes.
    app.config["LAB_TEST_MODEL"] = LabTest
    app.config["LAB_ORDER_MODEL"] = LabOrder
    app.config["LAB_ORDER_ITEM_MODEL"] = LabOrderItem
    app.config["LAB_DEFAULT_TESTS"] = DEFAULT_TESTS