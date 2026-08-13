"""Application factory for the Caravan Fresh platform (stdlib sqlite3 backend)."""
import os
from datetime import timedelta

from flask import Flask, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from . import data
from .auth_core import current_user, load_logged_in_user
from .constants import (ROLE_LABELS, ROLE_OFFICER, ROLE_FRANCHISE,
                        ROLE_MANAGEMENT, ROLE_FINANCE, ROLE_ADMIN)


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config["DB_PATH"] = config_class.DB_PATH
    app.permanent_session_lifetime = timedelta(
        days=app.config.get("PERMANENT_SESSION_DAYS", 7))

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

    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
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
                "nav_items": nav_items, "current_user": current_user}

    return app
