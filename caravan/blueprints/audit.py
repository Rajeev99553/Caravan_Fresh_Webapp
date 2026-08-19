import os
from datetime import datetime, date, timedelta

from flask import (Blueprint, render_template, redirect, url_for, request,
                   flash, current_app, abort)
from werkzeug.utils import secure_filename

from .. import data
from ..data import log_action
from ..auth_core import current_user, login_required
from ..constants import ROLE_OFFICER
from ..security import roles_required, require_store_access
from ..services import compute_visit_audit_score
from ..integrations import MapsAdapter

audit_bp = Blueprint("audit", __name__, url_prefix="/audit")
maps = MapsAdapter()


@audit_bp.route("/visits")
@login_required
@roles_required(ROLE_OFFICER)
def visits():
    """Previous Visits — the auditor's history, one day at a time.
    Defaults to yesterday so the auditor is reviewing recent work, not
    scrolling through their entire history."""
    date_str = request.args.get("date", "")
    try:
        sel_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None
    except ValueError:
        sel_date = None
    if sel_date is None:
        sel_date = date.today() - timedelta(days=1)

    vs = data.all_("visits", officer_id=current_user.id, scheduled_date=sel_date,
                   order_by="id DESC")
    evidence = {}
    for v in vs:
        results = data.all_("checkpoint_results", visit_id=v.id)
        evidence[v.id] = any(r.photo_path for r in results)
    return render_template("audit/visits.html", visits=vs, sel_date=sel_date,
                           evidence=evidence, today=date.today())


@audit_bp.route("/visit/new", methods=["GET", "POST"])
@login_required
@roles_required(ROLE_OFFICER)
def new_visit():
    stores = data.all_("stores", assigned_officer_id=current_user.id, order_by="code")
    if request.method == "POST":
        store = data.get("stores", int(request.form["store_id"]))
        require_store_access(current_user, store)
        vid = data.insert("visits", store_id=store.id, officer_id=current_user.id,
                          scheduled_date=date.today(), status="scheduled",
                          created_at=datetime.utcnow())
        log_action(current_user, "schedule_visit", "Visit", vid, f"store={store.code}")
        data.commit()
        flash(f"Visit scheduled for {store.name}.", "success")
        return redirect(url_for("audit.visit_detail", visit_id=vid))
    return render_template("audit/new_visit.html", stores=stores)


@audit_bp.route("/visit/<int:visit_id>")
@login_required
def visit_detail(visit_id):
    v = data.get("visits", visit_id) or abort(404)
    require_store_access(current_user, v.store)
    checkpoints = data.all_("checkpoint_master", active=True,
                            order_by="category, is_critical DESC, code")
    results = {r.checkpoint_id: r for r in data.all_("checkpoint_results", visit_id=v.id)}
    return render_template("audit/visit_detail.html", v=v,
                           checkpoints=checkpoints, results=results)


@audit_bp.route("/visit/<int:visit_id>/checkin", methods=["POST"])
@login_required
@roles_required(ROLE_OFFICER)
def checkin(visit_id):
    v = data.get("visits", visit_id) or abort(404)
    require_store_access(current_user, v.store)
    try:
        lat = float(request.form.get("lat") or 0)
        lng = float(request.form.get("lng") or 0)
    except ValueError:
        lat = lng = 0
    valid, dist = maps.validate_checkin(v.store, lat, lng)
    data.update("visits", v.id, check_in_at=datetime.utcnow(), check_in_lat=lat,
                check_in_lng=lng, gps_valid=valid, status="in_progress")
    log_action(current_user, "checkin", "Visit", v.id, f"gps_valid={valid} dist={dist}m")
    data.commit()
    if valid:
        flash(f"Checked in. GPS validated ({dist} m from store).", "success")
    else:
        flash(f"Checked in, but GPS is outside the store geofence "
              f"(distance {dist} m). Flagged for review.", "warning")
    return redirect(url_for("audit.visit_detail", visit_id=v.id))


@audit_bp.route("/visit/<int:visit_id>/score", methods=["POST"])
@login_required
@roles_required(ROLE_OFFICER)
def submit_scores(visit_id):
    v = data.get("visits", visit_id) or abort(404)
    require_store_access(current_user, v.store)
    checkpoints = data.all_("checkpoint_master", active=True,
                            order_by="category, is_critical DESC, code")
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    existing = {r.checkpoint_id: r for r in data.all_("checkpoint_results", visit_id=v.id)}

    for cp in checkpoints:
        field = f"score_{cp.id}"
        if field not in request.form:
            continue
        raw = request.form.get(field, "").strip()
        try:
            score = float(raw) if raw != "" else None
        except ValueError:
            score = None
        remark = request.form.get(f"remark_{cp.id}", "").strip()

        photo_path = None
        file = request.files.get(f"photo_{cp.id}")
        if file and file.filename:
            fn = secure_filename(f"v{v.id}_cp{cp.id}_{file.filename}")
            file.save(os.path.join(upload_dir, fn))
            photo_path = fn

        res = existing.get(cp.id)
        if score is None:
            # nothing entered for score this round — still capture a remark
            # or photo if the officer added one without touching the score.
            if res is not None and (remark or photo_path):
                fields = {}
                if remark:
                    fields["remark"] = remark
                if photo_path:
                    fields["photo_path"] = photo_path
                if fields:
                    data.update("checkpoint_results", res.id, **fields)
                    existing[cp.id] = data.get("checkpoint_results", res.id)
            continue

        score = max(0, min(score, cp.max_score))
        passed = score >= (0.5 * cp.max_score)
        if res is None:
            new_id = data.insert("checkpoint_results", visit_id=v.id, checkpoint_id=cp.id,
                                 score=score, remark=remark, passed=passed,
                                 photo_path=photo_path)
            existing[cp.id] = data.get("checkpoint_results", new_id)
        else:
            fields = dict(score=score, remark=remark, passed=passed)
            if photo_path:
                fields["photo_path"] = photo_path
            data.update("checkpoint_results", res.id, **fields)
            existing[cp.id] = data.get("checkpoint_results", res.id)

    data.commit()

    # Critical checkpoints are mandatory: score required, and if the score
    # falls below the pass threshold, a remark AND a photo are required
    # before the audit can be submitted.
    wants_complete = request.form.get("complete") == "1"
    incomplete = []
    if wants_complete:
        for cp in checkpoints:
            if not cp.is_critical:
                continue
            res = existing.get(cp.id)
            if res is None or res.score is None:
                incomplete.append(f"{cp.name}: score required")
                continue
            below_threshold = res.score < (0.5 * cp.max_score)
            if below_threshold and not (res.remark or "").strip():
                incomplete.append(f"{cp.name}: remark required (score below threshold)")
            if below_threshold and not res.photo_path:
                incomplete.append(f"{cp.name}: evidence photo required (score below threshold)")

    v = data.get("visits", visit_id)
    audit_score, critical = compute_visit_audit_score(v)
    fields = dict(remarks=request.form.get("visit_remarks", v.remarks),
                  audit_score=audit_score, has_critical_exception=critical)
    if wants_complete and not incomplete:
        fields["status"] = "completed"
        if not v.check_out_at:
            fields["check_out_at"] = datetime.utcnow()
    data.update("visits", v.id, **fields)
    log_action(current_user, "score_visit", "Visit", v.id,
              f"status={fields.get('status', v.status)}")
    data.commit()

    if wants_complete and incomplete:
        flash(f"{len(incomplete)} critical checkpoint(s) incomplete — audit saved as "
              f"draft, not submitted.", "danger")
        for msg in incomplete:
            flash(msg, "danger")
    elif wants_complete:
        flash("Audit submitted and completed.", "success")
    else:
        flash("Draft saved.", "success")
    return redirect(url_for("audit.visit_detail", visit_id=v.id))


@audit_bp.route("/visit/<int:visit_id>/miss", methods=["POST"])
@login_required
@roles_required(ROLE_OFFICER)
def mark_missed(visit_id):
    v = data.get("visits", visit_id) or abort(404)
    require_store_access(current_user, v.store)
    data.update("visits", v.id, status="missed")
    log_action(current_user, "miss_visit", "Visit", v.id, request.form.get("reason", ""))
    data.commit()
    flash("Visit marked as missed.", "warning")
    return redirect(url_for("dashboard.home"))
