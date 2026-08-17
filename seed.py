"""
Seed the Caravan Fresh database with realistic demo data:
70 stores, 5 audit officers, franchise owners, admin/management/finance,
checkpoint & KPI masters, performance bands, and a month of activity.
Deterministic so demos are stable.
"""
from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash

from . import data
from .constants import (ROLE_OFFICER, ROLE_FRANCHISE, ROLE_MANAGEMENT,
                        ROLE_FINANCE, ROLE_ADMIN)
from .integrations import GoogleReviewsAdapter
from .real_stores import REAL_STORES
from .services import compute_visit_audit_score, generate_all_scores

DEMO_PW = "demo123"

# All 70 real Caravan Fresh outlets sit in Sri Lanka's Western Province
# (Colombo, Gampaha and Kalutara districts).
REGION = "Western Province"

CHECKPOINTS = [
    ("CP-01", "Store cleanliness & hygiene", "Hygiene", 10, 1.5, 0),
    ("CP-02", "Cold chain / temperature logs", "Food Safety", 10, 2.0, 1),
    ("CP-03", "Product freshness & expiry check", "Food Safety", 10, 2.0, 1),
    ("CP-04", "Shelf merchandising & planogram", "Merchandising", 10, 1.0, 0),
    ("CP-05", "Stock availability of key SKUs", "Stock", 10, 1.5, 0),
    ("CP-06", "Price tags & offer accuracy", "Merchandising", 10, 1.0, 0),
    ("CP-07", "Staff grooming & uniform", "Staff", 10, 1.0, 0),
    ("CP-08", "Billing counter & queue mgmt", "Operations", 10, 1.0, 0),
    ("CP-09", "Fire safety & exits", "Compliance", 10, 1.5, 1),
    ("CP-10", "Customer feedback register", "Operations", 10, 0.5, 0),
]

KPIS = [
    ("KPI-01", "Store opened on time", "Shutter up and staff present at opening hour", "daily", 1.0),
    ("KPI-02", "Daily hygiene checklist done", "Cleaning and sanitation completed and logged", "daily", 1.5),
    ("KPI-03", "Temperature log updated", "Chiller/freezer temperatures recorded", "daily", 2.0),
    ("KPI-04", "Stale/expired stock removed", "Expired items pulled from shelves", "daily", 2.0),
    ("KPI-05", "Stock replenished", "Fast-moving SKUs replenished", "daily", 1.0),
    ("KPI-06", "Promotions displayed", "Current offers set up correctly", "weekly", 1.0),
    ("KPI-07", "Staff attendance marked", "All staff attendance recorded", "daily", 0.5),
    ("KPI-08", "Cash & billing reconciled", "End-of-day reconciliation done", "daily", 1.5),
]

BANDS = [
    ("Platinum", 90, 100, "Highest incentive", 10.0),
    ("Gold", 80, 89.999, "Higher incentive", 7.0),
    ("Standard", 70, 79.999, "Standard commission", 5.0),
    ("Improvement Required", 0, 69.999, "Lower incentive / approved penalties", 2.0),
]


def _quality(i):
    return (i * 37) % 100


def _seed_masters():
    """Config masters only: checkpoints, KPIs, bands, default weights."""
    for code, name, cat, mx, wt, crit in CHECKPOINTS:
        data.insert("checkpoint_master", code=code, name=name, category=cat,
                    max_score=mx, weight=wt, is_critical=crit, active=1)
    for code, name, desc, freq, wt in KPIS:
        data.insert("kpi_master", code=code, name=name, description=desc,
                    frequency=freq, weight=wt, active=1)
    for name, lo, hi, treat, pct in BANDS:
        data.insert("performance_bands", name=name, min_score=lo, max_score=hi,
                    treatment=treat, incentive_pct=pct, active=1)
    data.insert("score_weight_config", audit_weight=50, compliance_weight=25,
                customer_weight=25, version="v1", active=1, effective_from=date.today())


def seed_minimal(force=False):
    """Production-clean start: config masters + a single admin account."""
    if data.count("users") and not force:
        return "already-seeded"
    if force:
        data.wipe_all()
    _seed_masters()
    _mkuser("Administrator", "admin@caravanfresh.com", ROLE_ADMIN)
    data.commit()
    return "seeded-minimal"


def bootstrap(mode="demo", force=False):
    """Dispatch initial data load based on SEED_MODE."""
    mode = (mode or "demo").lower()
    if mode == "none":
        return "no-seed"
    if mode == "minimal":
        return seed_minimal(force=force)
    return seed(force=force)


def _mkuser(name, email, role, phone=""):
    return data.insert("users", name=name, email=email, role=role, phone=phone,
                       password_hash=generate_password_hash(DEMO_PW), active=True,
                       created_at=datetime.utcnow())


def seed(force=False):
    if data.count("users") and not force:
        return "already-seeded"
    if force:
        data.wipe_all()

    _seed_masters()

    admin_id = _mkuser("Asha Menon", "admin@caravanfresh.com", ROLE_ADMIN)
    _mkuser("Rahul Verma", "manager@caravanfresh.com", ROLE_MANAGEMENT)
    _mkuser("Priya Nair", "finance@caravanfresh.com", ROLE_FINANCE)

    onames = ["Vikram Singh", "Meera Iyer", "Arjun Rao", "Sana Sheikh", "Karan Patel"]
    officer_ids = [_mkuser(nm, f"officer{i}@caravanfresh.com", ROLE_OFFICER,
                           f"+91-90000-000{i:02d}") for i, nm in enumerate(onames, 1)]
    data.commit()

    google = GoogleReviewsAdapter()
    checkpoints = data.all_("checkpoint_master")
    kpis = data.all_("kpi_master")
    today = date.today()
    month_start = today.replace(day=1)

    store_ids = []
    for store in REAL_STORES:
        i = store["num"]
        city = store["locality"]
        owner_id = _mkuser(f"Owner {i}", f"owner{i}@caravanfresh.com",
                           ROLE_FRANCHISE, store["phone"])
        officer_id = officer_ids[(i - 1) % 5]
        base_amount = 150000 + (i % 10) * 12000
        sid = data.insert("stores", code=f"CF-{i:03d}",
                          name=f"Caravan Fresh {city} #{i}", city=city, region=REGION,
                          latitude=store["lat"], longitude=store["lng"],
                          address=store["address"], postal_code=store["postal_code"],
                          phone=store["phone"],
                          monthly_base_amount=base_amount, active=1,
                          owner_id=owner_id, assigned_officer_id=officer_id)
        data.insert("assignments", store_id=sid, officer_id=officer_id,
                    effective_from=today - timedelta(days=90))
        store_ids.append((sid, i, officer_id, owner_id))
    data.commit()

    for sid, idx, officer_id, owner_id in store_ids:
        q = _quality(idx)
        # completed visits earlier this month
        for vday in (5, 18):
            vdate = min(month_start + timedelta(days=vday - 1), today)
            ci = datetime.combine(vdate, datetime.min.time()) + timedelta(hours=10)
            co = ci + timedelta(hours=1)
            vid = data.insert("visits", store_id=sid, officer_id=officer_id,
                              scheduled_date=vdate, status="completed",
                              check_in_at=ci, check_out_at=co, gps_valid=1,
                              created_at=ci)
            store = data.get("stores", sid)
            data.execute("UPDATE visits SET check_in_lat=?, check_in_lng=? WHERE id=?",
                         (store.latitude, store.longitude, vid))
            for cix, cp in enumerate(checkpoints):
                base = 5.0 + q / 20.0          # ~5.0–9.95 → audit avg ~75
                delta = ((idx * 7 + cix * 11) % 5) - 2
                sc = max(0, min(cp.max_score, round(base + delta)))
                data.insert("checkpoint_results", visit_id=vid, checkpoint_id=cp.id,
                            score=sc, remark="", passed=1 if sc >= cp.max_score * 0.5 else 0)
            v = data.get("visits", vid)
            score, critical = compute_visit_audit_score(v)
            data.update("visits", vid, audit_score=score,
                        has_critical_exception=1 if critical else 0)

        # today's scheduled visit
        data.insert("visits", store_id=sid, officer_id=officer_id,
                    scheduled_date=today, status="scheduled", created_at=datetime.utcnow())

        # compliance entries
        for kix, kpi in enumerate(kpis):
            roll = (idx * 5 + kix * 9) % 100
            status = "done" if roll < 75 else ("partial" if roll < 90 else "not_done")
            vstatus = "accepted" if roll < 80 else ("pending" if roll < 92 else "rejected")
            edate = min(month_start + timedelta(days=(kix % 20)), today)
            data.insert("compliance_entries", store_id=sid, kpi_id=kpi.id,
                        entry_date=edate, status=status, evidence="photo/log",
                        submitted_by_id=owner_id, submitted_at=datetime.utcnow(),
                        validation_status=vstatus,
                        validated_by_id=officer_id if vstatus != "pending" else None,
                        validated_at=datetime.utcnow() if vstatus != "pending" else None)

        # reviews via mock Google adapter
        store = data.get("stores", sid)
        for rv in google.fetch_reviews(store):
            data.insert("reviews", store_id=sid, source=rv["source"],
                        external_id=rv["external_id"], rating=rv["rating"],
                        text=rv["text"], sentiment=rv["sentiment"],
                        is_complaint=1 if rv["is_complaint"] else 0,
                        review_date=rv["review_date"])
        data.commit()

    admin = data.get("users", admin_id)
    generate_all_scores(today.strftime("%Y-%m"), admin)
    data.commit()
    return "seeded"
