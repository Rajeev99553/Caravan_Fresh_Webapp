from datetime import date

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort

from .. import data
from ..data import log_action
from ..auth_core import current_user, login_required
from ..constants import ROLE_ADMIN, ROLE_OFFICER, ROLE_LABELS

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
@admin_bp.route("/users")
def users():
    users = data.all_("users", order_by="role, name")
    return render_template("admin/users.html", users=users, labels=ROLE_LABELS)


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
    stores = data.all_("stores", order_by="code")
    officers = data.all_("users", role=ROLE_OFFICER, order_by="name")
    return render_template("admin/stores.html", stores=stores, officers=officers)


# ---- Audit log ----------------------------------------------------------- #
@admin_bp.route("/audit-log")
def audit_log():
    logs = data.all_("audit_log", order_by="timestamp DESC", limit=300)
    return render_template("admin/audit_log.html", logs=logs)
