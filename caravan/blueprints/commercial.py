from datetime import date

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort

from .. import data
from ..data import log_action
from ..auth_core import current_user, login_required
from ..constants import ROLE_FINANCE, ROLE_MANAGEMENT, ROLE_ADMIN
from ..security import roles_required
from ..services import approve_commission, settle_commission
from ..integrations import ERPAdapter

comm_bp = Blueprint("commercial", __name__, url_prefix="/commercial")
erp = ERPAdapter()


@comm_bp.route("/")
@login_required
@roles_required(ROLE_FINANCE, ROLE_MANAGEMENT, ROLE_ADMIN)
def home():
    period = request.args.get("period", date.today().strftime("%Y-%m"))
    scores = data.all_("monthly_scores", period=period, status="approved")
    rows = []
    for ms in scores:
        rec = data.first("commission_recommendations", monthly_score_id=ms.id)
        rows.append({"score": ms, "rec": rec})
    rows.sort(key=lambda r: (r["rec"].net_amount if r["rec"] else 0), reverse=True)
    return render_template("commercial/list.html", rows=rows, period=period)


@comm_bp.route("/approve/<int:rec_id>", methods=["POST"])
@login_required
@roles_required(ROLE_FINANCE, ROLE_ADMIN)
def approve(rec_id):
    rec = data.get("commission_recommendations", rec_id) or abort(404)
    approve_commission(rec, current_user)
    flash("Commission approved. Ready for settlement.", "success")
    return redirect(url_for("commercial.home", period=rec.monthly_score.period))


@comm_bp.route("/reject/<int:rec_id>", methods=["POST"])
@login_required
@roles_required(ROLE_FINANCE, ROLE_ADMIN)
def reject(rec_id):
    rec = data.get("commission_recommendations", rec_id) or abort(404)
    data.update("commission_recommendations", rec.id, status="rejected")
    log_action(current_user, "reject_commission", "Commission", rec.id,
               request.form.get("reason", ""))
    data.commit()
    flash("Commission recommendation rejected.", "warning")
    return redirect(url_for("commercial.home", period=rec.monthly_score.period))


@comm_bp.route("/settle/<int:rec_id>", methods=["POST"])
@login_required
@roles_required(ROLE_FINANCE, ROLE_ADMIN)
def settle(rec_id):
    rec = data.get("commission_recommendations", rec_id) or abort(404)
    if rec.status != "approved":
        flash("Only approved commissions can be settled.", "danger")
        return redirect(url_for("commercial.home", period=rec.monthly_score.period))
    ref = erp.post_settlement(rec)
    settle_commission(rec, current_user, ref)
    flash(f"Settled to ERP. Reference: {ref}", "success")
    return redirect(url_for("commercial.home", period=rec.monthly_score.period))
