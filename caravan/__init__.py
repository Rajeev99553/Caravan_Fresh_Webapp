"""Application factory for the Caravan Fresh platform (stdlib sqlite3 backend)."""
import os
import tempfile
from datetime import timedelta

from flask import Flask, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config, BASE_DIR
from . import data
from .auth_core import current_user, load_logged_in_user
from .constants import (ROLE_LABELS, ROLE_OFFICER, ROLE_FRANCHISE,
                        ROLE_MANAGEMENT, ROLE_FINANCE, ROLE_ADMIN)


def _writable(path):
    """True if we can create `path` and write a file inside it."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def _resolve_data_dir(preferred):
    """Pick the first writable data directory, falling back gracefully.

    Order: the configured DATA_DIR (e.g. a mounted disk at /var/data) ->
    the project folder -> a temp dir. This prevents a hard crash when
    DATA_DIR points at a disk that isn't mounted (e.g. no persistent disk
    on a free plan)."""
    candidates = [preferred, BASE_DIR, os.path.join(tempfile.gettempdir(),
                                                    "caravan_fresh")]
    for path in candidates:
        if path and _writable(path):
            return path, (path != preferred)
    # last resort: temp dir (should always work)
    fallback = os.path.join(tempfile.gettempdir(), "caravan_fresh")
    os.makedirs(fallback, exist_ok=True)
    return fallback, True


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.permanent_session_lifetime = timedelta(
        days=app.config.get("PERMANENT_SESSION_DAYS", 7))

    # --- Resolve a writable data directory (with fallback) --------------
    data_dir, fell_back = _resolve_data_dir(app.config["DATA_DIR"])
    app.config["DATA_DIR"] = data_dir
    # Recompute DB path / upload folder from the resolved dir unless the
    # operator pinned them explicitly via their own env vars.
    if not os.environ.get("DB_PATH"):
        app.config["DB_PATH"] = os.path.join(data_dir, "caravan_fresh.db")
    if not os.environ.get("UPLOAD_FOLDER"):
        app.config["UPLOAD_FOLDER"] = os.path.join(data_dir, "uploads")
    if fell_back:
        print(f"[config] DATA_DIR not writable; using '{data_dir}' instead. "
              f"Data here is EPHEMERAL — attach a persistent disk for durability.")

    # Fail fast if someone ships to production without a real secret key.
    if app.config.get("IS_PRODUCTION") and \
            app.config.get("SECRET_KEY") in (None, "", "dev-only-insecure-change-me"):
        raise RuntimeError(
            "SECRET_KEY environment variable must be set in production.")

    # Trust the reverse proxy (Render/PaaS) for scheme + client IP.
    hops = app.config.get("PROXY_FIX_HOPS", 1)
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops,
                                x_host=hops, x_port=hops)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    app.teardown_appcontext(data.close_conn)

    @app.before_request
    def _load_user():
        load_logged_in_user()

    # Blueprints
    from .blueprints.auth import auth_bp
    from .blueprints.dashboard import dash_bp
    from .blueprints.audit import audit_bp
    from .blueprints.compliance import comp_bp
    from .blueprints.scoring import score_bp
    from .blueprints.commercial import comm_bp
    from .blueprints.admin import admin_bp

    for bp in (auth_bp, dash_bp, audit_bp, comp_bp, score_bp, comm_bp, admin_bp):
        app.register_blueprint(bp)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard.home"))
        return redirect(url_for("auth.login"))

    @app.route("/media/<path:filename>")
    def media(filename):
        """Serve uploaded audit-evidence photos from the data directory."""
        from flask import send_from_directory, abort
        if not current_user.is_authenticated:
            abort(401)
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    def nav_items():
        if not current_user.is_authenticated:
            return []
        u = url_for
        role = current_user.role
        items = [{"label": "Dashboard", "icon": "bi-speedometer2",
                  "url": u("dashboard.home")}]
        if role == ROLE_OFFICER:
            items += [
                {"label": "My Visits", "icon": "bi-geo-alt", "url": u("audit.visits")},
                {"label": "Validate KPIs", "icon": "bi-check2-square", "url": u("compliance.home")},
            ]
        elif role == ROLE_FRANCHISE:
            items += [{"label": "Daily KPIs", "icon": "bi-clipboard-check", "url": u("compliance.home")}]
        elif role == ROLE_MANAGEMENT:
            items += [
                {"label": "Scores", "icon": "bi-bar-chart", "url": u("scoring.home")},
                {"label": "Commercial", "icon": "bi-cash-coin", "url": u("commercial.home")},
            ]
        elif role == ROLE_FINANCE:
            items += [
                {"label": "Scores", "icon": "bi-bar-chart", "url": u("scoring.home")},
                {"label": "Commission", "icon": "bi-cash-coin", "url": u("commercial.home")},
            ]
        elif role == ROLE_ADMIN:
            items += [
                {"label": "Scores", "icon": "bi-bar-chart", "url": u("scoring.home")},
                {"label": "KPIs", "icon": "bi-list-check", "url": u("admin.kpis")},
                {"label": "Checkpoints", "icon": "bi-ui-checks", "url": u("admin.checkpoints")},
                {"label": "Weights", "icon": "bi-sliders", "url": u("admin.weights")},
                {"label": "Bands", "icon": "bi-trophy", "url": u("admin.bands")},
                {"label": "Stores", "icon": "bi-shop", "url": u("admin.stores")},
                {"label": "Users", "icon": "bi-people", "url": u("admin.users")},
                {"label": "Audit Log", "icon": "bi-journal-text", "url": u("admin.audit_log")},
            ]
        return items

    @app.context_processor
    def inject_globals():
        return {"ROLE_LABELS": ROLE_LABELS, "app_name": "Caravan Fresh",
                "nav_items": nav_items, "current_user": current_user,
                "ROLE_OFFICER": ROLE_OFFICER}

    return app