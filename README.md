# Caravan Fresh — Franchise Performance Management & Incentive Platform

A mobile-first web application that digitises store audits, franchise compliance,
customer sentiment and monthly performance scoring, and turns the approved score
into a traceable commission / incentive recommendation.

Built directly from the Business Requirement Document (BRD v1.0, Aug 2026).
Runs on **Python + Flask** with a **standard-library SQLite** backend — no heavy
dependencies, works on a phone browser or desktop.

---

## 1. Quick start

```bash
cd caravan_fresh
python3 -m venv .venv && source .venv/bin/activate    # optional
pip install -r requirements.txt                       # Flask + Werkzeug only
python run.py reset                                    # create tables + demo data
python run.py                                          # start the server
```

Open **http://localhost:5000** on your computer, or on your phone use
**http://<your-computer-ip>:5000** (the server listens on all interfaces).

### Demo logins (password `demo123` for all)

| Role | Email |
|------|-------|
| Administrator | `admin@caravanfresh.com` |
| Management | `manager@caravanfresh.com` |
| Finance / Commercial | `finance@caravanfresh.com` |
| Sales Audit Officer | `officer1@caravanfresh.com` … `officer5@…` |
| Franchise Owner | `owner1@caravanfresh.com` … `owner70@…` |

The login screen has tap-to-fill buttons for each role.

---

## 2. What's implemented (mapped to the BRD)

| BRD | Feature in the app |
|-----|--------------------|
| FR-01 Sales Audit & Route | Store→officer assignment with effective dates, daily visit scheduling, **GPS/time-stamped check-in** with geofence validation, checkpoint scoring with **photo evidence**, critical-exception flagging, daily audit score |
| FR-02 Franchise Compliance | Configurable daily/periodic KPI checklists, franchise-owner submission with evidence, **officer validation (accept/reject)**, weighted compliance score with audit trail |
| FR-03 Customer & Social | Google Reviews integration (mock adapter) — rating, volume, sentiment, complaint trend → customer score, associated to store & period |
| FR-04 Monthly Score | Weighted consolidation (default **50 / 25 / 25**), configurable weights & bands with **versioning**, ranking, trend, exceptions |
| FR-05 Commercial / Commission | Band→incentive mapping, deductions kept separate & traceable, **recommendation only** (never auto-paid), Finance approval → ERP settlement (mock), versioned rules |
| Dashboards | Role dashboards for Management, Officer, Franchisee, Finance, Admin |
| Controls | Role-based access with **strict franchisee isolation**, full **audit log**, GPS/evidence integrity, configurable masters, mobile-first responsive UI |

Every commercial outcome is traceable: **final score → band → component score →
KPI/checkpoint → individual audit / review / evidence** (see the *Score Trace* page).

---

## 3. Project structure

```
caravan_fresh/
├── run.py                     # entry point (init / reset / run)
├── config.py                  # configuration (DB path, upload folder, defaults)
├── requirements.txt           # Flask, Werkzeug
└── caravan/
    ├── __init__.py            # app factory, navigation, context
    ├── data.py                # sqlite3 data layer (records, queries, schema)
    ├── constants.py           # roles & labels
    ├── auth_core.py           # session auth (login_required, current_user)
    ├── security.py            # RBAC + franchisee isolation
    ├── services.py            # scoring engine + commission engine
    ├── seed.py                # 70 stores + a month of demo activity
    ├── integrations/          # swappable adapters (mocked)
    │   ├── google_reviews.py  #   → replace with Google Business Profile API
    │   ├── erp.py             #   → replace with ERP settlement API
    │   └── maps.py            #   → GPS geofence (haversine)
    ├── blueprints/            # auth, dashboard, audit, compliance, scoring, commercial, admin
    ├── templates/             # Bootstrap 5, mobile-first
    └── static/                # css + uploaded evidence photos
```

---

## 4. Integrations — how to go live

The mocked integrations sit behind clean interfaces so real APIs drop in without
touching the rest of the app:

* **Google Reviews** — implement `GoogleReviewsAdapter.fetch_reviews(store)` in
  `caravan/integrations/google_reviews.py` using the Google Business Profile API.
* **ERP / Finance** — implement `ERPAdapter.post_settlement(commission)` in
  `caravan/integrations/erp.py`.
* **Maps / GPS** — `MapsAdapter` already does real haversine geofencing; swap the
  radius or plug a mapping provider in `caravan/integrations/maps.py`.

---

## 5. Configuration (Admin role)

Everything the BRD asks to be configurable is editable in-app under **Admin**:
KPI masters, audit checkpoints (with weights & critical flags), scoring weights
(saved as new **versions** so historical scores stay reproducible), performance
bands & incentive %, store↔officer assignment, and a full audit log.

---

## 6. Scoring methodology

```
Audit score      = weighted checkpoint results per visit, averaged over the month
Compliance score = weighted % of accepted KPI submissions (pending discounted)
Customer score   = (avg rating / 5)×100, penalised by complaint ratio, low-volume dampened
Final score      = Audit×wA + Compliance×wC + Customer×wCu   (weights configurable)
Band             = lookup on final score → incentive %
Commission (net) = base × incentive%  −  deductions (stales/penalties)   [recommendation only]
```

Weights, thresholds, bands and incentive percentages are **illustrative** and must
be confirmed by Business, exactly as noted in the BRD.

---

## 7. Notes

* This is a **Phase-1 pilot build**: scores are calculated but no real money moves —
  settlement to ERP is mocked and gated behind Finance approval.
* Default port is 5000; set `DB_PATH` or `SECRET_KEY` via environment variables for
  production. Use a production WSGI server (gunicorn/uWSGI) instead of the dev server.
* Data resets with `python run.py reset`.

## 8. Deployment

See **`DEPLOY.md`** for step-by-step instructions to deploy on **Render**
(recommended) — it covers the included `render.yaml`, `Procfile`, `wsgi.py`
entrypoint, environment variables, persistent-disk setup, and the `SEED_MODE`
control for demo vs. clean-launch data. The same files also work on Railway,
Fly.io, Heroku, Docker, or a plain Linux VM.
