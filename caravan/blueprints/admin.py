from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from werkzeug.security import generate_password_hash

from .. import data
from ..data import log_action
from ..auth_core import current_user, login_required
from ..constants import (ROLE_ADMIN, ROLE_OFFICER, ROLE_FRANCHISE,
                         ROLE_MANAGEMENT, ROLE_FINANCE, ROLE_LABELS)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def restrict():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.path))
    if current_user.role != ROLE_ADMIN:
        abort(403)


# ---- KPIs ---------------------------------------------------------------- #
@admin_bp.route("/kpis", methods=["GET", "POST"])
def kpis():
    if request.method == "POST":
        code = request.form["code"].strip()
        data.insert("kpi_master", code=code, name=request.form["name"].strip(),
                    description=request.form.get("description", ""),
                    frequency=request.form.get("frequency", "daily"),
                    weight=float(request.form.get("weight") or 1), active=True)
        log_action(current_user, "create_kpi", "KpiMaster", code)
        data.commit()
        flash("KPI added.", "success")
        return redirect(url_for("admin.kpis"))
    return render_template("admin/kpis.html", kpis=data.all_("kpi_master", order_by="code"))


@admin_bp.route("/kpis/<int:kid>/toggle", methods=["POST"])
def toggle_kpi(kid):
    k = data.get("kpi_master", kid) or abort(404)
    data.update("kpi_master", kid, active=not k.active)
    data.commit()
    return redirect(url_for("admin.kpis"))


# ---- Checkpoints --------------------------------------------------------- #
@admin_bp.route("/checkpoints", methods=["GET", "POST"])
def checkpoints():
    if request.method == "POST":
        code = request.form["code"].strip()
        data.insert("checkpoint_master", code=code, name=request.form["name"].strip(),
                    category=request.form.get("category", ""),
                    max_score=int(request.form.get("max_score") or 10),
                    weight=float(request.form.get("weight") or 1),
                    is_critical=bool(request.form.get("is_critical")), active=True)
        log_action(current_user, "create_checkpoint", "CheckpointMaster", code)
        data.commit()
        flash("Checkpoint added.", "success")
        return redirect(url_for("admin.checkpoints"))
    cps = data.all_("checkpoint_master", order_by="category, code")
    return render_template("admin/checkpoints.html", checkpoints=cps)


@admin_bp.route("/checkpoints/<int:cid>/toggle", methods=["POST"])
def toggle_checkpoint(cid):
    c = data.get("checkpoint_master", cid) or abort(404)
    data.update("checkpoint_master", cid, active=not c.active)
    data.commit()
    return redirect(url_for("admin.checkpoints"))


# ---- Weights ------------------------------------------------------------- #
@admin_bp.route("/weights", methods=["GET", "POST"])
def weights():
    if request.method == "POST":
        for c in data.all_("score_weight_config", active=True):
            data.update("score_weight_config", c.id, active=False)
        n = data.count("score_weight_config") + 1
        data.insert("score_weight_config",
                    audit_weight=float(request.form["audit_weight"]),
                    compliance_weight=float(request.form["compliance_weight"]),
                    customer_weight=float(request.form["customer_weight"]),
                    min_audit_threshold=float(request.form.get("min_audit_threshold") or 0),
                    effective_from=date.today(), version=f"v{n}", active=True)
        log_action(current_user, "update_weights", "ScoreWeightConfig", f"v{n}")
        data.commit()
        flash(f"Weight configuration v{n} saved.", "success")
        return redirect(url_for("admin.weights"))
    configs = data.all_("score_weight_config", order_by="id DESC")
    current = next((c for c in configs if c.active), None)
    return render_template("admin/weights.html", configs=configs, current=current)


# ---- Bands --------------------------------------------------------------- #
@admin_bp.route("/bands", methods=["GET", "POST"])
def bands():
    if request.method == "POST":
        name = request.form["name"].strip()
        data.insert("performance_bands", name=name,
                    min_score=float(request.form["min_score"]),
                    max_score=float(request.form["max_score"]),
                    treatment=request.form.get("treatment", ""),
                    incentive_pct=float(request.form.get("incentive_pct") or 0),
                    color=request.form.get("color", "secondary"), active=True)
        log_action(current_user, "create_band", "PerformanceBand", name)
        data.commit()
        flash("Performance band added.", "success")
        return redirect(url_for("admin.bands"))
    bands = data.all_("performance_bands", order_by="min_score DESC")
    return render_template("admin/bands.html", bands=bands)


@admin_bp.route("/bands/<int:bid>/update", methods=["POST"])
def update_band(bid):
    b = data.get("performance_bands", bid) or abort(404)
    data.update("performance_bands", bid,
                min_score=float(request.form["min_score"]),
                max_score=float(request.form["max_score"]),
                incentive_pct=float(request.form.get("incentive_pct") or 0),
                treatment=request.form.get("treatment", b.treatment))
    log_action(current_user, "update_band", "PerformanceBand", b.name)
    data.commit()
    flash("Band updated.", "success")
    return redirect(url_for("admin.bands"))


# ---- Users & stores ------------------------------------------------------ #
ALL_ROLES = [ROLE_ADMIN, ROLE_MANAGEMENT, ROLE_FINANCE, ROLE_OFFICER, ROLE_FRANCHISE]

# Tables/columns that reference a user, used to decide whether a hard delete
# is safe or whether the user must be deactivated instead.
USER_REFERENCES = [
    ("stores", "owner_id"), ("stores", "assigned_officer_id"),
    ("assignments", "officer_id"), ("visits", "officer_id"),
    ("compliance_entries", "submitted_by_id"), ("compliance_entries", "validated_by_id"),
    ("monthly_scores", "approved_by_id"), ("commission_recommendations", "approved_by_id"),
]


def _user_reference_count(user_id):
    total = 0
    for table, col in USER_REFERENCES:
        total += data.count(table, **{col: user_id})
    return total


@admin_bp.route("/users")
def users():
    role_filter = request.args.get("role", "")
    filters = {"role": role_filter} if role_filter in ALL_ROLES else {}
    users_list = data.all_("users", order_by="role, name", **filters)
    counts = {r: data.count("users", role=r) for r in ALL_ROLES}
    return render_template("admin/users.html", users=users_list, labels=ROLE_LABELS,
                           roles=ALL_ROLES, active_role=role_filter, counts=counts)


@admin_bp.route("/users/add", methods=["POST"])
def add_user():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "")
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "").strip()

    if role not in ALL_ROLES:
        flash("Invalid role selected.", "danger")
        return redirect(url_for("admin.users"))
    if not name or not email or not password:
        flash("Name, email and password are all required.", "danger")
        return redirect(url_for("admin.users", role=role))
    if len(password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("admin.users", role=role))
    if data.first("users", email=email):
        flash(f"A user with email {email} already exists.", "danger")
        return redirect(url_for("admin.users", role=role))

    uid = data.insert("users", name=name, email=email, role=role, phone=phone,
                      password_hash=generate_password_hash(password), active=True,
                      created_at=datetime.utcnow())
    log_action(current_user, "create_user", "User", uid, f"role={role} email={email}")
    data.commit()
    flash(f"{name} added as {ROLE_LABELS.get(role, role)}. Share their email and the "
          f"password you set — ask them to change it after first login.", "success")
    return redirect(url_for("admin.users", role=role))


@admin_bp.route("/users/<int:uid>/toggle", methods=["POST"])
def toggle_user(uid):
    u = data.get("users", uid) or abort(404)
    if u.id == current_user.id:
        flash("You can't deactivate your own account.", "danger")
        return redirect(url_for("admin.users", role=u.role))
    if u.active and u.role == ROLE_ADMIN and data.count("users", role=ROLE_ADMIN, active=True) <= 1:
        flash("Can't deactivate the last active administrator.", "danger")
        return redirect(url_for("admin.users", role=u.role))
    data.update("users", uid, active=not u.active)
    log_action(current_user, "deactivate_user" if u.active else "activate_user",
              "User", uid, u.email)
    data.commit()
    flash(f"{u.name} {'deactivated' if u.active else 'activated'}.", "success")
    return redirect(url_for("admin.users", role=u.role))


@admin_bp.route("/users/<int:uid>/delete", methods=["POST"])
def delete_user(uid):
    u = data.get("users", uid) or abort(404)
    if u.id == current_user.id:
        flash("You can't delete your own account.", "danger")
        return redirect(url_for("admin.users", role=u.role))
    refs = _user_reference_count(uid)
    if refs > 0:
        flash(f"Can't delete {u.name} — they're linked to {refs} store/visit/compliance "
              f"record(s). Deactivate them instead to preserve history.", "danger")
        return redirect(url_for("admin.users", role=u.role))
    role, email = u.role, u.email
    data.execute("DELETE FROM users WHERE id=?", (uid,))
    log_action(current_user, "delete_user", "User", uid, email)
    data.commit()
    flash(f"{u.name} permanently deleted.", "success")
    return redirect(url_for("admin.users", role=role))


STORE_REFERENCES = [
    ("visits", "store_id"), ("assignments", "store_id"),
    ("compliance_entries", "store_id"), ("reviews", "store_id"),
    ("monthly_scores", "store_id"),
]


def _store_reference_count(store_id):
    total = 0
    for table, col in STORE_REFERENCES:
        total += data.count(table, **{col: store_id})
    return total


@admin_bp.route("/stores", methods=["GET", "POST"])
def stores():
    if request.method == "POST":
        store = data.get("stores", int(request.form["store_id"])) or abort(404)
        officer_id = int(request.form["officer_id"])
        for a in data.all_("assignments", store_id=store.id, effective_to=None):
            data.update("assignments", a.id, effective_to=date.today())
        data.insert("assignments", store_id=store.id, officer_id=officer_id,
                    effective_from=date.today())
        data.update("stores", store.id, assigned_officer_id=officer_id)
        log_action(current_user, "reassign_store", "Store", store.code,
                   f"officer={officer_id}")
        data.commit()
        flash(f"{store.code} reassigned.", "success")
        return redirect(url_for("admin.stores"))
    stores_list = data.all_("stores", order_by="code")
    officers = data.all_("users", role=ROLE_OFFICER, active=True, order_by="name")
    owners = data.all_("users", role=ROLE_FRANCHISE, active=True, order_by="name")
    return render_template("admin/stores.html", stores=stores_list, officers=officers,
                           owners=owners)


@admin_bp.route("/stores/add", methods=["POST"])
def add_store():
    code = request.form.get("code", "").strip()
    name = request.form.get("name", "").strip()
    city = request.form.get("city", "").strip()
    region = request.form.get("region", "").strip()
    address = request.form.get("address", "").strip()
    postal_code = request.form.get("postal_code", "").strip()
    phone = request.form.get("phone", "").strip()
    lat = request.form.get("latitude", "").strip()
    lng = request.form.get("longitude", "").strip()
    base_amount = request.form.get("monthly_base_amount", "0").strip()
    owner_id = request.form.get("owner_id") or None
    officer_id = request.form.get("officer_id") or None

    if not code or not name:
        flash("Store code and name are required.", "danger")
        return redirect(url_for("admin.stores"))
    if data.first("stores", code=code):
        flash(f"Store code {code} already exists.", "danger")
        return redirect(url_for("admin.stores"))
    try:
        lat_v = float(lat) if lat else None
        lng_v = float(lng) if lng else None
        base_v = float(base_amount or 0)
    except ValueError:
        flash("Latitude, longitude and base amount must be numbers.", "danger")
        return redirect(url_for("admin.stores"))

    sid = data.insert("stores", code=code, name=name, city=city, region=region,
                      latitude=lat_v, longitude=lng_v, address=address,
                      postal_code=postal_code, phone=phone,
                      monthly_base_amount=base_v, active=True,
                      owner_id=int(owner_id) if owner_id else None,
                      assigned_officer_id=int(officer_id) if officer_id else None)
    if officer_id:
        data.insert("assignments", store_id=sid, officer_id=int(officer_id),
                    effective_from=date.today())
    log_action(current_user, "create_store", "Store", code)
    data.commit()
    flash(f"Store {code} — {name} added.", "success")
    return redirect(url_for("admin.stores"))


@admin_bp.route("/stores/<int:sid>/toggle", methods=["POST"])
def toggle_store(sid):
    s = data.get("stores", sid) or abort(404)
    data.update("stores", sid, active=not s.active)
    log_action(current_user, "deactivate_store" if s.active else "activate_store",
              "Store", s.code)
    data.commit()
    flash(f"{s.code} {'deactivated' if s.active else 'activated'}.", "success")
    return redirect(url_for("admin.stores"))


@admin_bp.route("/stores/<int:sid>/delete", methods=["POST"])
def delete_store(sid):
    s = data.get("stores", sid) or abort(404)
    refs = _store_reference_count(sid)
    if refs > 0:
        flash(f"Can't delete {s.code} — it has {refs} linked visit/compliance/review "
              f"record(s). Deactivate it instead to preserve history.", "danger")
        return redirect(url_for("admin.stores"))
    code = s.code
    data.execute("DELETE FROM stores WHERE id=?", (sid,))
    log_action(current_user, "delete_store", "Store", code)
    data.commit()
    flash(f"{code} permanently deleted.", "success")
    return redirect(url_for("admin.stores"))


# ---- Audit log ----------------------------------------------------------- #
@admin_bp.route("/audit-log")
def audit_log():
    logs = data.all_("audit_log", order_by="timestamp DESC", limit=300)
    return render_template("admin/audit_log.html", logs=logs)
