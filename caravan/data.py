"""
Lightweight data-access layer built on the Python standard library (sqlite3).

This replaces SQLAlchemy so the app runs with only Flask + Werkzeug installed.
It provides:
  * schema creation (init_schema)
  * typed Record objects with attribute access + lazy relationships
  * small query helpers (get / first / all_ / count / insert / update / raw)
The public API is intentionally close to a tiny active-record so the rest of
the application reads naturally.
"""
import os
import sqlite3
from datetime import date, datetime

from flask import g, current_app

from .constants import ROLE_LABELS

# --------------------------------------------------------------------------- #
# Column type metadata (only columns needing conversion are listed)
# --------------------------------------------------------------------------- #
DATE_COLS = {
    "assignments": {"effective_from", "effective_to"},
    "visits": {"scheduled_date"},
    "compliance_entries": {"entry_date"},
    "reviews": {"review_date"},
    "score_weight_config": {"effective_from"},
    "users": set(),
}
DATETIME_COLS = {
    "users": {"created_at"},
    "visits": {"check_in_at", "check_out_at", "created_at"},
    "compliance_entries": {"submitted_at", "validated_at"},
    "monthly_scores": {"generated_at", "approved_at"},
    "commission_recommendations": {"approved_at"},
    "audit_log": {"timestamp"},
    "checkpoint_results": {"photo_uploaded_at"},
    "action_items": {"created_at", "updated_at", "resolved_at", "denied_at",
                     "resolution_photo_at", "officer_decision_at"},
    "action_item_events": {"created_at"},
}
BOOL_COLS = {
    "users": {"active"},
    "stores": {"active"},
    "checkpoint_master": {"is_critical", "active"},
    "visits": {"gps_valid", "has_critical_exception"},
    "checkpoint_results": {"passed"},
    "kpi_master": {"active"},
    "reviews": {"is_complaint"},
    "score_weight_config": {"active"},
    "performance_bands": {"active"},
}


def _convert(table, col, val):
    if val is None:
        return None
    if col in BOOL_COLS.get(table, ()):
        return bool(val)
    if col in DATE_COLS.get(table, ()):
        return date.fromisoformat(val) if isinstance(val, str) else val
    if col in DATETIME_COLS.get(table, ()):
        return datetime.fromisoformat(val) if isinstance(val, str) else val
    return val


def _serialize(val):
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return val


# --------------------------------------------------------------------------- #
# Relationships & computed attributes
# --------------------------------------------------------------------------- #
def _rel_first(table, **f):
    return first(table, **f)


RELATIONS = {
    "stores": {
        "owner": lambda r: get("users", r._data.get("owner_id")),
        "assigned_officer": lambda r: get("users", r._data.get("assigned_officer_id")),
        "store_staff": lambda r: get("users", r._data.get("store_staff_id")),
    },
    "assignments": {
        "store": lambda r: get("stores", r._data.get("store_id")),
        "officer": lambda r: get("users", r._data.get("officer_id")),
    },
    "visits": {
        "store": lambda r: get("stores", r._data.get("store_id")),
        "officer": lambda r: get("users", r._data.get("officer_id")),
        "results": lambda r: all_("checkpoint_results", visit_id=r._data.get("id")),
    },
    "checkpoint_results": {
        "checkpoint": lambda r: get("checkpoint_master", r._data.get("checkpoint_id")),
    },
    "action_items": {
        "store": lambda r: get("stores", r._data.get("store_id")),
        "visit": lambda r: get("visits", r._data.get("visit_id")),
        "checkpoint": lambda r: get("checkpoint_master", r._data.get("checkpoint_id")),
        "created_by": lambda r: get("users", r._data.get("created_by_id")),
        "events": lambda r: all_("action_item_events", action_item_id=r._data.get("id"),
                                 order_by="id"),
        "officer_photo": lambda r: first("checkpoint_results",
                                         visit_id=r._data.get("visit_id"),
                                         checkpoint_id=r._data.get("checkpoint_id")),
    },
    "action_item_events": {
        "actor": lambda r: get("users", r._data.get("actor_id")),
    },
    "compliance_entries": {
        "store": lambda r: get("stores", r._data.get("store_id")),
        "kpi": lambda r: get("kpi_master", r._data.get("kpi_id")),
        "submitted_by": lambda r: get("users", r._data.get("submitted_by_id")),
        "validated_by": lambda r: get("users", r._data.get("validated_by_id")),
    },
    "reviews": {
        "store": lambda r: get("stores", r._data.get("store_id")),
    },
    "monthly_scores": {
        "store": lambda r: get("stores", r._data.get("store_id")),
        "band": lambda r: get("performance_bands", r._data.get("band_id")),
        "approved_by": lambda r: get("users", r._data.get("approved_by_id")),
        "commission": lambda r: first("commission_recommendations",
                                      monthly_score_id=r._data.get("id")),
    },
    "commission_recommendations": {
        "monthly_score": lambda r: get("monthly_scores", r._data.get("monthly_score_id")),
        "approved_by": lambda r: get("users", r._data.get("approved_by_id")),
    },
    "audit_log": {
        "user": lambda r: get("users", r._data.get("user_id")),
    },
}


def _period_from(attr):
    def _fn(r):
        d = r._data.get(attr)
        if isinstance(d, str):
            d = date.fromisoformat(d)
        if isinstance(d, datetime):
            d = d.date()
        return d.strftime("%Y-%m") if d else None
    return _fn


COMPUTED = {
    "users": {
        "role_label": lambda r: ROLE_LABELS.get(r._data.get("role"), r._data.get("role")),
        "is_authenticated": lambda r: True,
        "is_anonymous": lambda r: False,
    },
    "visits": {"period": _period_from("scheduled_date")},
    "compliance_entries": {"period": _period_from("entry_date")},
    "reviews": {"period": _period_from("review_date")},
}


class Record:
    """A single DB row with attribute access + lazy relationships."""
    __slots__ = ("_table", "_data", "_cache")

    def __init__(self, table, row):
        self._table = table
        self._data = {k: _convert(table, k, row[k]) for k in row.keys()}
        self._cache = {}

    def __getattr__(self, name):
        data = object.__getattribute__(self, "_data")
        if name in data:
            return data[name]
        table = object.__getattribute__(self, "_table")
        cache = object.__getattribute__(self, "_cache")
        if name in cache:
            return cache[name]
        rels = RELATIONS.get(table, {})
        if name in rels:
            val = rels[name](self)
            cache[name] = val
            return val
        comp = COMPUTED.get(table, {})
        if name in comp:
            return comp[name](self)
        raise AttributeError(f"{table} has no attribute '{name}'")

    def __getitem__(self, k):
        return self._data.get(k)

    @property
    def id(self):
        return self._data.get("id")

    def __repr__(self):
        return f"<{self._table} #{self._data.get('id')}>"


# --------------------------------------------------------------------------- #
# Connection management
# --------------------------------------------------------------------------- #
def get_conn():
    if "db_conn" not in g:
        path = current_app.config["DB_PATH"]
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Better concurrency for multi-worker production (gunicorn):
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")   # wait up to 30s on locks
        conn.execute("PRAGMA synchronous = NORMAL")
        g.db_conn = conn
    return g.db_conn


def close_conn(exc=None):
    conn = g.pop("db_conn", None)
    if conn is not None:
        conn.close()


def commit():
    get_conn().commit()


# --------------------------------------------------------------------------- #
# Query helpers
# --------------------------------------------------------------------------- #
def _rows(sql, params=()):
    return get_conn().execute(sql, params).fetchall()


def raw(table, sql, params=()):
    return [Record(table, r) for r in _rows(sql, params)]


def get(table, _id):
    if _id is None:
        return None
    r = get_conn().execute(f"SELECT * FROM {table} WHERE id=?", (_id,)).fetchone()
    return Record(table, r) if r else None


def _where(filters):
    if not filters:
        return "", []
    keys = list(filters.keys())
    clause = " WHERE " + " AND ".join(f"{k}=?" for k in keys)
    vals = [_serialize(filters[k]) for k in keys]
    return clause, vals


def first(table, **filters):
    clause, vals = _where(filters)
    r = get_conn().execute(f"SELECT * FROM {table}{clause} LIMIT 1", vals).fetchone()
    return Record(table, r) if r else None


def all_(table, order_by=None, limit=None, **filters):
    clause, vals = _where(filters)
    sql = f"SELECT * FROM {table}{clause}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [Record(table, r) for r in _rows(sql, vals)]


def count(table, **filters):
    clause, vals = _where(filters)
    return get_conn().execute(f"SELECT COUNT(*) c FROM {table}{clause}", vals).fetchone()["c"]


def insert(table, **values):
    cols = list(values.keys())
    ph = ", ".join("?" for _ in cols)
    vals = [_serialize(values[c]) for c in cols]
    cur = get_conn().execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph})", vals)
    return cur.lastrowid


def update(table, _id, **values):
    if not values:
        return
    cols = list(values.keys())
    sets = ", ".join(f"{c}=?" for c in cols)
    vals = [_serialize(values[c]) for c in cols] + [_id]
    get_conn().execute(f"UPDATE {table} SET {sets} WHERE id=?", vals)


def execute(sql, params=()):
    return get_conn().execute(sql, params)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'franchise_owner',
  phone TEXT, active INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE IF NOT EXISTS stores(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  city TEXT, region TEXT, latitude REAL, longitude REAL,
  address TEXT, postal_code TEXT, phone TEXT,
  monthly_base_amount REAL DEFAULT 0, active INTEGER DEFAULT 1,
  owner_id INTEGER, assigned_officer_id INTEGER, store_staff_id INTEGER);
CREATE TABLE IF NOT EXISTS assignments(
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, officer_id INTEGER NOT NULL,
  effective_from TEXT, effective_to TEXT);
CREATE TABLE IF NOT EXISTS checkpoint_master(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  category TEXT, max_score INTEGER DEFAULT 10, weight REAL DEFAULT 1,
  is_critical INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS visits(
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, officer_id INTEGER NOT NULL,
  scheduled_date TEXT, status TEXT DEFAULT 'scheduled',
  check_in_at TEXT, check_in_lat REAL, check_in_lng REAL, check_out_at TEXT,
  gps_valid INTEGER, no_gps_reason TEXT, audit_score REAL, remarks TEXT,
  has_critical_exception INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS checkpoint_results(
  id INTEGER PRIMARY KEY, visit_id INTEGER NOT NULL, checkpoint_id INTEGER NOT NULL,
  score REAL DEFAULT 0, remark TEXT, photo_path TEXT, photo_uploaded_at TEXT,
  passed INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS action_items(
  id INTEGER PRIMARY KEY, visit_id INTEGER NOT NULL, checkpoint_id INTEGER,
  store_id INTEGER NOT NULL, created_by_id INTEGER,
  description TEXT, assigned_to TEXT DEFAULT 'franchise_owner',
  status TEXT DEFAULT 'open',
  resolution_note TEXT, resolution_photo_path TEXT, resolution_photo_at TEXT, resolved_at TEXT,
  denial_reason TEXT, denied_at TEXT,
  officer_decision TEXT, officer_decision_note TEXT, officer_decision_at TEXT,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS action_item_events(
  id INTEGER PRIMARY KEY, action_item_id INTEGER NOT NULL,
  event_type TEXT, actor_id INTEGER, note TEXT, photo_path TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS kpi_master(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  description TEXT, frequency TEXT DEFAULT 'daily', weight REAL DEFAULT 1,
  active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS compliance_entries(
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, kpi_id INTEGER NOT NULL,
  entry_date TEXT, status TEXT DEFAULT 'done', evidence TEXT, remark TEXT,
  submitted_by_id INTEGER, submitted_at TEXT,
  validation_status TEXT DEFAULT 'pending', validated_by_id INTEGER,
  validated_at TEXT, validation_remark TEXT);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, source TEXT DEFAULT 'google',
  external_id TEXT, rating REAL, text TEXT, sentiment TEXT,
  is_complaint INTEGER DEFAULT 0, review_date TEXT);
CREATE TABLE IF NOT EXISTS score_weight_config(
  id INTEGER PRIMARY KEY, audit_weight REAL DEFAULT 50, compliance_weight REAL DEFAULT 25,
  customer_weight REAL DEFAULT 25, min_audit_threshold REAL DEFAULT 0,
  effective_from TEXT, version TEXT DEFAULT 'v1', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS performance_bands(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, min_score REAL NOT NULL,
  max_score REAL NOT NULL, treatment TEXT, incentive_pct REAL DEFAULT 0,
  color TEXT DEFAULT 'secondary', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS monthly_scores(
  id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL, period TEXT NOT NULL,
  audit_score REAL DEFAULT 0, compliance_score REAL DEFAULT 0,
  customer_score REAL DEFAULT 0, final_score REAL DEFAULT 0, band_id INTEGER,
  status TEXT DEFAULT 'draft', rule_version TEXT, weight_snapshot TEXT,
  generated_at TEXT, approved_by_id INTEGER, approved_at TEXT,
  UNIQUE(store_id, period));
CREATE TABLE IF NOT EXISTS commission_recommendations(
  id INTEGER PRIMARY KEY, monthly_score_id INTEGER NOT NULL,
  base_amount REAL DEFAULT 0, incentive_pct REAL DEFAULT 0,
  incentive_amount REAL DEFAULT 0, deductions REAL DEFAULT 0,
  deduction_note TEXT, net_amount REAL DEFAULT 0, status TEXT DEFAULT 'recommended',
  approved_by_id INTEGER, approved_at TEXT, settlement_ref TEXT, rule_version TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, timestamp TEXT, user_id INTEGER, user_name TEXT,
  action TEXT, entity TEXT, entity_id TEXT, details TEXT);
"""


def init_schema():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate_stores_columns(conn)
    conn.commit()


def _migrate_stores_columns(conn):
    """Add columns that were introduced after the initial CREATE TABLE, so
    databases created by an older version of this app still work."""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(stores)").fetchall()}
    for col in ("address", "postal_code", "phone"):
        if col not in existing:
            conn.execute(f"ALTER TABLE stores ADD COLUMN {col} TEXT")
    if "store_staff_id" not in existing:
        conn.execute("ALTER TABLE stores ADD COLUMN store_staff_id INTEGER")
    existing_visits = {r["name"] for r in conn.execute("PRAGMA table_info(visits)").fetchall()}
    if "no_gps_reason" not in existing_visits:
        conn.execute("ALTER TABLE visits ADD COLUMN no_gps_reason TEXT")
    existing_cr = {r["name"] for r in conn.execute("PRAGMA table_info(checkpoint_results)").fetchall()}
    if "photo_uploaded_at" not in existing_cr:
        conn.execute("ALTER TABLE checkpoint_results ADD COLUMN photo_uploaded_at TEXT")


def wipe_all():
    conn = get_conn()
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in tables:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


def log_action(user, action, entity, entity_id="", details=""):
    insert("audit_log",
           timestamp=datetime.utcnow(),
           user_id=getattr(user, "id", None),
           user_name=getattr(user, "name", "system"),
           action=action, entity=entity, entity_id=str(entity_id), details=details)
