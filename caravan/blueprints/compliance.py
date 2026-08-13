from datetime import datetime, date

from flask import (Blueprint, render_template, redirect, url_for, request,
                   flash, abort)

from .. import data
from ..data import log_action
from ..auth_core import current_user, login_required
from ..constants import ROLE_FRANCHISE, ROLE_OFFICER
from ..security import roles_required, require_store_access, stores_for_user

comp_bp = Blueprint("compliance", __name__, url_prefix="/compliance")


@comp_bp.route("/")
@login_required
def home():
    if current_user.role == ROLE_FRANCHISE:
        return _franchise_view()
    if current_user.role == ROLE_OFFICER:
        return _officer_validation_view()
    abort(403)


def _franchise_view():
    stores = stores_for_user(current_user)
    kpis = data.all_("kpi_master", active=True, order_by="code")
    today = date.today()
    submitted = {}
    for s in stores:
        done = {e.kpi_id: e for e in data.all_("compliance_entries",
                store_id=s.id, entry_date=today)}
        submitted[s.id] = done
    return render_template("compliance/franchise.html", stores=stores, kpis=kpis,
                           submitted=submitted, today=today)


@comp_bp.route("/submit", methods=["POST"])
@login_required
@roles_required(ROLE_FRANCHISE)
def submit():
    store = data.get("stores", int(request.form["store_id"])) or abort(404)
    require_store_access(current_user, store)
    kpis = data.all_("kpi_master", active=True)
    today = date.today()
    count = 0
    for kpi in kpis:
        status = request.form.get(f"status_{kpi.id}")
        if not status:
            continue
        entry = data.first("compliance_entries", store_id=store.id, kpi_id=kpi.id,
                           entry_date=today)
        fields = dict(status=status, evidence=request.form.get(f"evidence_{kpi.id}", ""),
                      remark=request.form.get(f"remark_{kpi.id}", ""),
                      submitted_by_id=current_user.id, submitted_at=datetime.utcnow(),
                      validation_status="pending", validated_by_id=None,
                      validated_at=None)
        if entry is None:
            data.insert("compliance_entries", store_id=store.id, kpi_id=kpi.id,
                        entry_date=today, **fields)
        else:
            data.update("compliance_entries", entry.id, **fields)
        count += 1
    log_action(current_user, "submit_compliance", "Store", store.code, f"{count} KPIs")
    data.commit()
    flash(f"Submitted {count} KPI updates for {store.name}.", "success")
    return redirect(url_for("compliance.home"))


def _officer_validation_view():
    stores = stores_for_user(current_user)
    store_ids = [s.id for s in stores]
    if store_ids:
        ph = ",".join("?" for _ in store_ids)
        pending = data.raw("compliance_entries",
                           f"SELECT * FROM compliance_entries WHERE validation_status='pending' "
                           f"AND store_id IN ({ph}) ORDER BY entry_date DESC", store_ids)
    else:
        pending = []
    return render_template("compliance/validate.html", entries=pending)


@comp_bp.route("/validate/<int:entry_id>", methods=["POST"])
@login_required
@roles_required(ROLE_OFFICER)
def validate(entry_id):
    entry = data.get("compliance_entries", entry_id) or abort(404)
    require_store_access(current_user, entry.store)
    decision = request.form.get("decision")
    status = "accepted" if decision == "accept" else "rejected"
    data.update("compliance_entries", entry.id, validation_status=status,
                validated_by_id=current_user.id, validated_at=datetime.utcnow(),
                validation_remark=request.form.get("remark", ""))
    log_action(current_user, "validate_compliance", "ComplianceEntry", entry.id, status)
    data.commit()
    flash(f"KPI {status}.", "success")
    return redirect(url_for("compliance.home"))
