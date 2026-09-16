from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, session


LAB_ROLES = {'lab', 'admin'}
LAB_VIEW_ROLES = {'lab', 'doctor', 'nurse', 'triage', 'records', 'admin'}


DEFAULT_TESTS = [
    ('Full Blood Count (FBC)', 'EDTA Blood'),
    ('Malaria Test (mRDT)', 'Whole Blood'),
    ('Blood Glucose', 'Blood'),
    ('Urinalysis', 'Urine'),
    ('Urine Pregnancy Test (PDT)', 'Urine'),
    ('HIV Test', 'Blood'),
    ('Stool Ova & Cysts', 'Stool'),
    ('Hepatitis B Surface Antigen (HBsAg)', 'Blood'),
    ('Creatinine', 'Serum'),
    ('Urea', 'Serum'),
    ('Liver Function Tests (LFTs)', 'Serum'),
    ('Lipid Profile', 'Serum'),
    ('HbA1c', 'Blood'),
    ('ESR', 'EDTA Blood'),
]


def register_lab_module(app, db, Patient, Visit, ServiceRequest, AuditLog):
    """Register the laboratory workflow without changing the existing app structure."""

    class LabOrder(db.Model):
        __tablename__ = 'lab_order'
        id = db.Column(db.Integer, primary_key=True)
        patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
        visit_id = db.Column(db.Integer, db.ForeignKey('visit.id'))
        service_request_id = db.Column(db.Integer, db.ForeignKey('service_request.id'))
        test_name = db.Column(db.String(160), nullable=False)
        specimen_type = db.Column(db.String(80))
        priority = db.Column(db.String(20), default='Routine', nullable=False)
        status = db.Column(db.String(30), default='Requested', nullable=False)
        requested_by = db.Column(db.String(100))
        requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
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

        patient = db.relationship(Patient, backref=db.backref('lab_orders', lazy=True, order_by='LabOrder.requested_at.desc()'))
        visit = db.relationship(Visit, backref=db.backref('lab_orders', lazy=True))
        service_request = db.relationship(ServiceRequest, backref=db.backref('lab_orders', lazy=True))

    # The main app calls db.create_all() before this module is imported. Run it
    # again so the new laboratory table is created on existing installations.
    with app.app_context():
        db.create_all()

    def current_user_allowed(roles):
        return session.get('role') in roles

    def audit(action, target_id, details=None):
        db.session.add(AuditLog(
            actor_user_id=session.get('user_id'),
            action=action,
            target_type='lab_order',
            target_id=str(target_id),
            details=details,
        ))

    def sync_orders():
        """Create structured LabOrder rows for older generic Lab service requests."""
        requests = ServiceRequest.query.filter_by(service_type='Lab').order_by(ServiceRequest.requested_at.asc()).all()
        for service in requests:
            existing = LabOrder.query.filter_by(service_request_id=service.id).first()
            if existing:
                continue
            patient = service.patient
            latest_visit = Visit.query.filter_by(patient_id=patient.id).order_by(Visit.started_at.desc()).first()
            order = LabOrder(
                patient_id=patient.id,
                visit_id=latest_visit.id if latest_visit else None,
                service_request_id=service.id,
                test_name=(service.description or 'Laboratory investigation').strip()[:160],
                requested_by=service.requested_by,
                requested_at=service.requested_at or datetime.utcnow(),
                status='Requested' if (service.status or '').lower() not in {'completed', 'cancelled'} else service.status.title(),
            )
            db.session.add(order)
        db.session.commit()

    @app.route('/lab')
    def lab_queue():
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not current_user_allowed(LAB_VIEW_ROLES):
            flash('Access denied for your role.', 'danger')
            return redirect(url_for('dashboard'))

        sync_orders()
        status = request.args.get('status', 'active').strip().lower()
        if status == 'completed':
            orders = LabOrder.query.filter(LabOrder.status.in_({'Verified', 'Completed'})).order_by(LabOrder.completed_at.desc(), LabOrder.requested_at.desc()).limit(50).all()
        elif status == 'all':
            orders = LabOrder.query.order_by(LabOrder.requested_at.desc()).limit(100).all()
        else:
            orders = LabOrder.query.filter(~LabOrder.status.in_({'Verified', 'Completed', 'Cancelled'})).order_by(LabOrder.priority.desc(), LabOrder.requested_at.asc()).all()

        return render_template('lab.html', orders=orders, status_filter=status)

    @app.route('/lab/<int:order_id>', methods=['GET', 'POST'])
    def lab_order(order_id):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not current_user_allowed(LAB_VIEW_ROLES):
            flash('Access denied for your role.', 'danger')
            return redirect(url_for('dashboard'))

        order = LabOrder.query.get_or_404(order_id)

        if request.method == 'POST':
            if not current_user_allowed(LAB_ROLES):
                flash('Only laboratory staff can process or verify laboratory results.', 'danger')
                return redirect(url_for('lab_order', order_id=order.id))

            action = request.form.get('action', '').strip()
            actor = session.get('full_name', session.get('username', 'Laboratory'))

            if action == 'collect':
                if order.status in {'Requested', 'Rejected'}:
                    order.specimen_type = request.form.get('specimen_type', '').strip()[:80] or order.specimen_type
                    order.priority = request.form.get('priority', 'Routine').strip() or 'Routine'
                    order.collected_by = actor
                    order.collected_at = datetime.utcnow()
                    order.status = 'Collected'
                    order.rejection_reason = None
                    audit('lab_specimen_collected', order.id, f'test={order.test_name}')
                    db.session.commit()
                    flash('Specimen collection recorded.', 'success')

            elif action == 'receive':
                if order.status == 'Collected':
                    order.received_by = actor
                    order.received_at = datetime.utcnow()
                    order.status = 'Received'
                    audit('lab_specimen_received', order.id, f'test={order.test_name}')
                    db.session.commit()
                    flash('Specimen received in laboratory.', 'success')

            elif action == 'reject':
                reason = request.form.get('rejection_reason', '').strip()
                if not reason:
                    flash('Enter the reason for rejecting the specimen.', 'danger')
                    return redirect(url_for('lab_order', order_id=order.id))
                order.rejection_reason = reason[:1000]
                order.status = 'Rejected'
                order.received_by = actor
                order.received_at = datetime.utcnow()
                audit('lab_specimen_rejected', order.id, f'reason={reason[:180]}')
                db.session.commit()
                flash('Specimen rejected and reason recorded.', 'warning')

            elif action == 'start':
                if order.status == 'Received':
                    order.status = 'Processing'
                    audit('lab_processing_started', order.id, f'test={order.test_name}')
                    db.session.commit()
                    flash('Laboratory processing started.', 'success')

            elif action == 'save_result':
                result = request.form.get('result', '').strip()
                units = request.form.get('units', '').strip()
                reference_range = request.form.get('reference_range', '').strip()
                interpretation = request.form.get('interpretation', '').strip()
                if not result:
                    flash('Enter the laboratory result before saving.', 'danger')
                    return redirect(url_for('lab_order', order_id=order.id))
                order.result = result
                order.units = units[:50] or None
                order.reference_range = reference_range[:120] or None
                order.interpretation = interpretation or None
                order.status = 'Result Entered'
                audit('lab_result_entered', order.id, f'test={order.test_name}')
                db.session.commit()
                flash('Result saved. It now requires verification.', 'success')

            elif action == 'verify':
                if not order.result:
                    flash('A result must be entered before verification.', 'danger')
                    return redirect(url_for('lab_order', order_id=order.id))
                order.verified_by = actor
                order.verified_at = datetime.utcnow()
                order.completed_at = datetime.utcnow()
                order.status = 'Verified'
                if order.service_request:
                    order.service_request.status = 'Completed'
                audit('lab_result_verified', order.id, f'test={order.test_name}')
                db.session.commit()
                flash('Laboratory result verified and released.', 'success')

            return redirect(url_for('lab_order', order_id=order.id))

        return render_template('lab_order.html', order=order)

    @app.route('/lab/<int:order_id>/print')
    def lab_result_print(order_id):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not current_user_allowed(LAB_VIEW_ROLES):
            flash('Access denied for your role.', 'danger')
            return redirect(url_for('dashboard'))
        order = LabOrder.query.get_or_404(order_id)
        return render_template('lab_result_print.html', order=order)

    app.config['LAB_ORDER_MODEL'] = LabOrder
    app.config['LAB_DEFAULT_TESTS'] = DEFAULT_TESTS
