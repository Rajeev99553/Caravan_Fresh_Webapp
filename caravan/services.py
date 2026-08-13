"""
Business logic services: scoring engine and commission engine.

Scoring methodology (BRD section 5, illustrative & configurable):
    Monthly Store Score = Audit*wA + Compliance*wC + Customer*wCu
All component scores are normalized to 0-100 before weighting.
"""
from datetime import datetime

from . import data
from .data import log_action


# --------------------------------------------------------------------------- #
# Component score calculators
# --------------------------------------------------------------------------- #
def compute_visit_audit_score(visit):
    """Weighted checkpoint score for a visit (0-100). Also flags criticals.
    Returns (score, critical_bool)."""
    results = data.all_("checkpoint_results", visit_id=visit.id)
    if not results:
        return 0.0, False
    total_w = got = 0.0
    critical_fail = False
    for r in results:
        cp = r.checkpoint
        w = cp.weight or 1.0
        maxs = cp.max_score or 10
        total_w += w * maxs
        got += w * (r.score or 0)
        if cp.is_critical and (r.score or 0) < (0.5 * maxs):
            critical_fail = True
    if total_w == 0:
        return 0.0, critical_fail
    return round(100.0 * got / total_w, 2), critical_fail


def audit_score_for_period(store_id, period):
    visits = data.all_("visits", store_id=store_id, status="completed")
    scores = [v.audit_score for v in visits
              if v.audit_score is not None and v.period == period]
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def compliance_score_for_period(store_id, period):
    entries = [e for e in data.all_("compliance_entries", store_id=store_id)
               if e.period == period]
    if not entries:
        return 0.0
    total_w = got = 0.0
    for e in entries:
        kpi = e.kpi
        w = (kpi.weight or 1.0) if kpi else 1.0
        total_w += w
        if e.validation_status == "accepted":
            factor = 1.0 if e.status == "done" else (0.5 if e.status == "partial" else 0.0)
            got += w * factor
        elif e.validation_status == "pending":
            factor = 0.7 if e.status == "done" else (0.35 if e.status == "partial" else 0.0)
            got += w * factor
    return round(100.0 * got / total_w, 2) if total_w else 0.0


def customer_score_for_period(store_id, period, min_reviews=3):
    revs = [r for r in data.all_("reviews", store_id=store_id) if r.period == period]
    if not revs:
        return 0.0
    avg_rating = sum(r.rating for r in revs) / len(revs)
    base = (avg_rating / 5.0) * 100.0
    complaints = sum(1 for r in revs if r.is_complaint)
    complaint_ratio = complaints / len(revs)
    score = base - (complaint_ratio * 15.0)
    if len(revs) < min_reviews:
        score = (score + 60.0) / 2.0
    return round(max(0.0, min(100.0, score)), 2)


# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #
def active_weight_config():
    cfg = data.first("score_weight_config", active=True)
    if cfg is None:
        data.insert("score_weight_config", audit_weight=50, compliance_weight=25,
                    customer_weight=25, version="v1", active=True)
        data.commit()
        cfg = data.first("score_weight_config", active=True)
    return cfg


def band_for_score(score):
    bands = data.raw("performance_bands",
                     "SELECT * FROM performance_bands WHERE active=1 AND ?>=min_score AND ?<=max_score LIMIT 1",
                     (score, score))
    return bands[0] if bands else None


# --------------------------------------------------------------------------- #
# Monthly score generation (FR-04)
# --------------------------------------------------------------------------- #
def generate_monthly_score(store, period, user=None):
    cfg = active_weight_config()
    a = audit_score_for_period(store.id, period)
    c = compliance_score_for_period(store.id, period)
    cu = customer_score_for_period(store.id, period)

    wsum = (cfg.audit_weight + cfg.compliance_weight + cfg.customer_weight) or 1
    final = round((a * cfg.audit_weight + c * cfg.compliance_weight
                   + cu * cfg.customer_weight) / wsum, 2)
    band = band_for_score(final)

    ms = data.first("monthly_scores", store_id=store.id, period=period)
    fields = dict(
        audit_score=a, compliance_score=c, customer_score=cu, final_score=final,
        band_id=band.id if band else None, rule_version=cfg.version,
        weight_snapshot=f"{int(cfg.audit_weight)}/{int(cfg.compliance_weight)}/{int(cfg.customer_weight)}",
        generated_at=datetime.utcnow(), status="draft",
    )
    if ms is None:
        ms_id = data.insert("monthly_scores", store_id=store.id, period=period, **fields)
    else:
        if ms.status == "approved":
            return ms
        data.update("monthly_scores", ms.id, **fields)
        ms_id = ms.id
    log_action(user, "generate_score", "MonthlyScore", f"{store.code}:{period}",
               f"final={final} band={band.name if band else 'n/a'}")
    data.commit()
    return data.get("monthly_scores", ms_id)


def generate_all_scores(period, user=None):
    return [generate_monthly_score(s, period, user)
            for s in data.all_("stores", active=True)]


def approve_monthly_score(ms, user):
    data.update("monthly_scores", ms.id, status="approved",
                approved_by_id=user.id, approved_at=datetime.utcnow())
    log_action(user, "approve_score", "MonthlyScore",
               f"{ms.store.code}:{ms.period}", f"final={ms.final_score}")
    build_commission(data.get("monthly_scores", ms.id), user)
    data.commit()
    return data.get("monthly_scores", ms.id)


# --------------------------------------------------------------------------- #
# Commission engine (FR-05)
# --------------------------------------------------------------------------- #
def build_commission(ms, user=None):
    band = ms.band
    incentive_pct = band.incentive_pct if band else 0.0
    base = ms.store.monthly_base_amount or 0.0
    incentive_amount = round(base * incentive_pct / 100.0, 2)

    deductions = 0.0
    note = ""
    if ms.final_score < 70:
        deductions = round(base * 0.02, 2)
        note = "Improvement-required penalty (illustrative 2% of base)"
    net = round(incentive_amount - deductions, 2)

    rec = data.first("commission_recommendations", monthly_score_id=ms.id)
    fields = dict(base_amount=base, incentive_pct=incentive_pct,
                  incentive_amount=incentive_amount, deductions=deductions,
                  deduction_note=note, net_amount=net, status="recommended",
                  rule_version=ms.rule_version)
    if rec is None:
        rid = data.insert("commission_recommendations", monthly_score_id=ms.id, **fields)
    else:
        if rec.status in ("approved", "settled"):
            return rec
        data.update("commission_recommendations", rec.id, **fields)
        rid = rec.id
    log_action(user, "recommend_commission", "Commission",
               f"{ms.store.code}:{ms.period}", f"net={net}")
    return data.get("commission_recommendations", rid)


def approve_commission(rec, user):
    data.update("commission_recommendations", rec.id, status="approved",
                approved_by_id=user.id, approved_at=datetime.utcnow())
    log_action(user, "approve_commission", "Commission", rec.id, f"net={rec.net_amount}")
    data.commit()
    return data.get("commission_recommendations", rec.id)


def settle_commission(rec, user, settlement_ref):
    data.update("commission_recommendations", rec.id, status="settled",
                settlement_ref=settlement_ref)
    log_action(user, "settle_commission", "Commission", rec.id, f"ref={settlement_ref}")
    data.commit()
    return data.get("commission_recommendations", rec.id)
