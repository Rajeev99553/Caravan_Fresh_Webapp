from datetime import date, datetime

from flask import Blueprint, render_template, request, abort

from .. import data
from ..auth_core import current_user, login_required
from ..constants import (ROLE_OFFICER, ROLE_FRANCHISE, ROLE_STORE_STAFF, ROLE_MANAGEMENT,
                         ROLE_FINANCE, ROLE_ADMIN)
from ..security import stores_for_user

dash_bp = Blueprint("dashboard", __name__)


def current_period():
    return date.today().strftime("%Y-%m")


@dash_bp.route("/dashboard")
@login_required
def home():
    role = current_user.role
    if role == ROLE_OFFICER:
        return _officer_dashboard()
    if role == ROLE_FRANCHISE:
        return _franchise_dashboard()
    if role == ROLE_STORE_STAFF:
        from flask import redirect, url_for
        return redirect(url_for("action_items.home"))
    if role == ROLE_FINANCE:
        return _finance_dashboard()
    if role == ROLE_ADMIN:
        return _admin_dashboard()
    return _management_dashboard()


def _ensure_todays_visits(officer):
    """Auto-populate today's due-audit queue for an officer.

    The demo/seed data only ever creates a 'today' visit for the day it was
    seeded. On any later real day an officer would see an empty queue with
    no way to get visits scheduled (visit creation was intentionally removed
    from the officer UI). So every time an officer opens My Activity, make
    sure each of their active assigned stores has a visit for today —
    creating one if it doesn't exist yet."""
    today = date.today()
    stores = data.all_("stores", assigned_officer_id=officer.id, active=True)
    existing_today = {v.store_id for v in data.all_("visits", officer_id=officer.id,
                                                     scheduled_date=today)}
    created = False
    for s in stores:
        if s.id not in existing_today:
            data.insert("visits", store_id=s.id, officer_id=officer.id,
                       scheduled_date=today, status="scheduled",
                       created_at=datetime.utcnow())
            created = True
    if created:
        data.commit()


def _officer_dashboard():
    today = date.today()
    _ensure_todays_visits(current_user)
    stores = stores_for_user(current_user)
    visits = data.all_("visits", officer_id=current_user.id)
    todays = [v for v in visits if v.scheduled_date == today]
    completed = [v for v in todays if v.status == "completed"]
    remaining = [v for v in todays if v.status in ("scheduled", "in_progress")]
    missed_today = [v for v in todays if v.status == "missed"]
    return render_template("dashboards/officer.html", stores=stores, todays=todays,
                           completed=completed, remaining=remaining,
                           missed_today=missed_today, today=today)


@dash_bp.route("/dashboard/summary")
@login_required
def officer_summary():
    """Previous-visit performance summary for an audit officer, with charts.
    Officers see their own; management/finance/admin can view any officer's
    by passing ?officer_id=."""
    if current_user.role == ROLE_OFFICER:
        officer_id = current_user.id
    elif current_user.role in (ROLE_MANAGEMENT, ROLE_FINANCE, ROLE_ADMIN):
        officer_id = request.args.get("officer_id", type=int) or current_user.id
    else:
        abort(403)
    officer = data.get("users", officer_id) or abort(404)
    all_officers = data.all_("users", role=ROLE_OFFICER, order_by="name")

    visits = data.all_("visits", officer_id=officer.id, order_by="scheduled_date")
    completed = [v for v in visits if v.status == "completed"]
    missed = [v for v in visits if v.status == "missed"]

    score_trend = [{"date": v.scheduled_date.isoformat(), "score": round(v.audit_score or 0, 1)}
                  for v in completed if v.audit_score is not None]
    critical_count = sum(1 for v in completed if v.has_critical_exception)

    status_counts = {"completed": len(completed), "missed": len(missed),
                     "scheduled": sum(1 for v in visits if v.status == "scheduled"),
                     "in_progress": sum(1 for v in visits if v.status == "in_progress")}

    avg_score = round(sum(s["score"] for s in score_trend) / len(score_trend), 1) if score_trend else 0
    gps_valid_count = sum(1 for v in completed if v.gps_valid)
    gps_no_gps_count = sum(1 for v in completed if v.gps_valid is None)
    gps_invalid_count = sum(1 for v in completed if v.gps_valid is False)

    stores = stores_for_user(officer) if officer.role == ROLE_OFFICER else \
        data.all_("stores", assigned_officer_id=officer.id)
    store_ids = [s.id for s in stores]
    action_items = []
    if store_ids:
        ph = ",".join("?" for _ in store_ids)
        action_items = data.raw("action_items",
                                f"SELECT * FROM action_items WHERE created_by_id=? "
                                f"OR store_id IN ({ph})", [officer.id] + store_ids)
    ai_status_counts = {}
    for ai in action_items:
        if ai.created_by_id != officer.id:
            continue
        ai_status_counts[ai.status] = ai_status_counts.get(ai.status, 0) + 1

    return render_template("dashboards/officer_summary.html", officer=officer,
                           all_officers=all_officers, visits=visits, completed=completed,
                           missed=missed, score_trend=score_trend, status_counts=status_counts,
                           avg_score=avg_score, critical_count=critical_count,
                           gps_valid_count=gps_valid_count, gps_no_gps_count=gps_no_gps_count,
                           gps_invalid_count=gps_invalid_count,
                           ai_status_counts=ai_status_counts)


def _franchise_dashboard():
    period = current_period()
    stores = stores_for_user(current_user)
    rows = []
    for s in stores:
        ms = data.first("monthly_scores", store_id=s.id, period=period)
        pending = data.count("compliance_entries", store_id=s.id,
                             validation_status="pending")
        reviews = data.all_("reviews", store_id=s.id,
                            order_by="review_date DESC", limit=5)
        rows.append({"store": s, "score": ms, "pending": pending, "reviews": reviews})
    return render_template("dashboards/franchise.html", data=rows, period=period)


def _management_dashboard():
    period = current_period()
    stores = data.all_("stores", active=True, order_by="code")
    scores = data.all_("monthly_scores", period=period)
    smap = {m.store_id: m for m in scores}
    scored = [{"store": s, "score": smap[s.id]} for s in stores if s.id in smap]
    scored.sort(key=lambda r: r["score"].final_score, reverse=True)
    top = scored[:5]
    bottom = list(reversed(scored[-5:])) if len(scored) >= 5 else list(reversed(scored))
    avg = round(sum(r["score"].final_score for r in scored) / len(scored), 1) if scored else 0

    bands = data.all_("performance_bands", active=True, order_by="min_score DESC")
    band_counts = {b.name: 0 for b in bands}
    for r in scored:
        b = r["score"].band
        if b:
            band_counts[b.name] = band_counts.get(b.name, 0) + 1

    officers = data.all_("users", role=ROLE_OFFICER, order_by="name")
    officer_stats = []
    for o in officers:
        vs = data.all_("visits", officer_id=o.id)
        done = sum(1 for v in vs if v.status == "completed")
        officer_stats.append({"officer": o, "total": len(vs), "done": done})

    exceptions = data.count("visits", has_critical_exception=True)
    all_r = data.all_("reviews")
    avg_sentiment = round(sum(r.rating for r in all_r) / len(all_r), 2) if all_r else 0

    return render_template("dashboards/management.html", rows=scored, top=top,
                           bottom=bottom, avg=avg, bands=bands, band_counts=band_counts,
                           officer_stats=officer_stats, exceptions=exceptions,
                           period=period, total_stores=len(stores),
                           scored_count=len(scored), avg_sentiment=avg_sentiment)


def _finance_dashboard():
    period = current_period()
    scores = data.all_("monthly_scores", period=period, status="approved")
    rows = []
    for ms in scores:
        rec = data.first("commission_recommendations", monthly_score_id=ms.id)
        rows.append({"score": ms, "rec": rec})
    total_incentive = sum((r["rec"].incentive_amount if r["rec"] else 0) for r in rows)
    total_net = sum((r["rec"].net_amount if r["rec"] else 0) for r in rows)
    pending_approval = sum(1 for r in rows if r["rec"] and r["rec"].status == "recommended")
    return render_template("dashboards/finance.html", rows=rows, period=period,
                           total_incentive=total_incentive, total_net=total_net,
                           pending_approval=pending_approval)


def _admin_dashboard():
    stats = {
        "users": data.count("users"),
        "stores": data.count("stores"),
        "officers": data.count("users", role=ROLE_OFFICER),
        "franchises": data.count("users", role=ROLE_FRANCHISE),
        "kpis": data.count("kpi_master", active=True),
        "checkpoints": data.count("checkpoint_master", active=True),
        "bands": data.count("performance_bands", active=True),
    }
    recent_logs = data.all_("audit_log", order_by="timestamp DESC", limit=15)
    return render_template("dashboards/admin.html", stats=stats, recent_logs=recent_logs)
