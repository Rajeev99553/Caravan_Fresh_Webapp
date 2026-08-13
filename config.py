"""Application configuration for the Caravan Fresh platform."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _as_bool(val, default=False):
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


class Config:
    # --- Environment -------------------------------------------------------
    # ENV=production disables debug and enables secure cookies.
    ENV = os.environ.get("ENV", os.environ.get("FLASK_ENV", "development")).lower()
    IS_PRODUCTION = ENV == "production"
    DEBUG = _as_bool(os.environ.get("DEBUG"), default=not IS_PRODUCTION)

    # --- Secret key --------------------------------------------------------
    # MUST be set via environment in production (Render generates one for you).
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-only-insecure-change-me"

    # --- Storage -----------------------------------------------------------
    # On Render, point DATA_DIR at the mounted persistent disk (e.g. /var/data)
    # so the SQLite file and uploaded evidence survive restarts/redeploys.
    DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
    DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "caravan_fresh.db"))
    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER", os.path.join(DATA_DIR, "uploads"))
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB per upload

    # --- Seeding -----------------------------------------------------------
    # On first boot with an empty database: "demo" loads the 70-store sample,
    # "minimal" creates only config masters + one admin, "none" leaves it empty.
    SEED_MODE = os.environ.get("SEED_MODE", "demo").lower()

    # --- Sessions / cookies ------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _as_bool(
        os.environ.get("SESSION_COOKIE_SECURE"), default=IS_PRODUCTION)
    PERMANENT_SESSION_DAYS = int(os.environ.get("PERMANENT_SESSION_DAYS", "7"))
    PREFERRED_URL_SCHEME = "https" if IS_PRODUCTION else "http"

    # Number of proxy hops in front of the app (Render/most PaaS = 1).
    PROXY_FIX_HOPS = int(os.environ.get("PROXY_FIX_HOPS", "1"))

    # --- Scoring defaults (configurable at runtime via Admin) --------------
    DEFAULT_AUDIT_WEIGHT = 50
    DEFAULT_COMPLIANCE_WEIGHT = 25
    DEFAULT_CUSTOMER_WEIGHT = 25
