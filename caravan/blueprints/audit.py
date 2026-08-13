import os
from datetime import datetime, date

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
    vs = data.all_("visits", officer_id=current_user.id,
                   order_by="scheduled_date DESC, id DESC")
    return render_template("audit/visits.html", visits=vs, today=date.today())


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
                            order_by="category, code")
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
    checkpoints = data.all_("checkpoint_master", active=True)
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    existing = {r.checkpoint_id: r for r in data.all_("checkpoint_results", visit_id=v.id)}

    for cp in checkpoints:
        field = f"score_{cp.id}"
        if field not in request.form:
            continue
        try:
            score = float(request.form.get(field) or 0)
        except ValueError:
            score = 0
        score = max(0, min(score, cp.max_score))
        remark = request.form.get(f"remark_{cp.id}", "")
        passed = score >= (0.5 * cp.max_score)

        photo_path = None
        file = request.files.get(f"photo_{cp.id}")
        if file and file.filename:
            fn = secure_filename(f"v{v.id}_cp{cp.id}_{file.filename}")
            file.save(os.path.join(upload_dir, fn))
            photo_path = fn

        res = existing.get(cp.id)
        if res is None:
            data.insert("checkpoint_results", visit_id=v.id, checkpoint_id=cp.id,
                        score=score, remark=remark, passed=passed, photo_path=photo_path)
        else:
            fields = dict(score=score, remark=remark, passed=passed)
            if photo_path:
                fields["photo_path"] = photo_path
            data.update("checkpoint_results", res.id, **fields)

    data.commit()
    v = data.get("visits", visit_id)
    audit_score, critical = compute_visit_audit_score(v)
    fields = dict(remarks=request.form.get("visit_remarks", v.remarks),
                  audit_score=audit_score, has_critical_exception=critical)
    if request.form.get("complete") == "1":
        fields["status"] = "completed"
        if not v.check_out_at:
            fields["check_out_at"] = datetime.utcnow()
    data.update("visits", v.id, **fields)
    log_action(current_user, "score_visit", "Visit", v.id,
               f"audit_score={audit_score} status={fields.get('status', v.status)}")
    data.commit()
    flash(f"Audit saved. Visit score: {audit_score}/100"
          + (" — completed." if fields.get("status") == "completed" else "."), "success")
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
    return redirect(url_for("audit.visits"))
