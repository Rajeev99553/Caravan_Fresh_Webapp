from datetime import date

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort

from .. import data
from ..auth_core import current_user, login_required
from ..constants import ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_FINANCE
from ..security import roles_required, require_store_access
from ..services import generate_all_scores, approve_monthly_score

score_bp = Blueprint("scoring", __name__, url_prefix="/scoring")


@score_bp.route("/")
@login_required
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_FINANCE)
def home():
    period = request.args.get("period", date.today().strftime("%Y-%m"))
    scores = data.all_("monthly_scores", period=period)
    smap = {m.store_id: m for m in scores}
    stores = data.all_("stores", active=True, order_by="code")
    rows = [{"store": s, "score": smap.get(s.id)} for s in stores]
    rows.sort(key=lambda r: (r["score"].final_score if r["score"] else -1), reverse=True)
    bands = data.all_("performance_bands", active=True, order_by="min_score DESC")
    return render_template("scoring/list.html", rows=rows, period=period, bands=bands)


@score_bp.route("/generate", methods=["POST"])
@login_required
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN)
def generate():
    period = request.form.get("period", date.today().strftime("%Y-%m"))
    results = generate_all_scores(period, current_user)
    flash(f"Generated/updated {len(results)} monthly scores for {period}.", "success")
    return redirect(url_for("scoring.home", period=period))


@score_bp.route("/store/<int:store_id>/<period>")
@login_required
def trace(store_id, period):
    store = data.get("stores", store_id) or abort(404)
    require_store_access(current_user, store)
    ms = data.first("monthly_scores", store_id=store_id, period=period)
    visits = [v for v in data.all_("visits", store_id=store_id, status="completed")
              if v.period == period]
    entries = [e for e in data.all_("compliance_entries", store_id=store_id)
               if e.period == period]
    reviews = [r for r in data.all_("reviews", store_id=store_id) if r.period == period]
    return render_template("scoring/trace.html", store=store, ms=ms, visits=visits,
                           entries=entries, reviews=reviews, period=period)


@score_bp.route("/approve/<int:score_id>", methods=["POST"])
@login_required
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN)
def approve(score_id):
    ms = data.get("monthly_scores", score_id) or abort(404)
    approve_monthly_score(ms, current_user)
    flash(f"Score for {ms.store.code} ({ms.period}) approved and commission "
          f"recommendation generated.", "success")
    return redirect(url_for("scoring.home", period=ms.period))
