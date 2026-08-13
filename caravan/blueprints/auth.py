from flask import Blueprint, render_template, redirect, url_for, request, flash
from werkzeug.security import check_password_hash

from .. import data
from ..data import log_action
from ..auth_core import current_user, login_user, logout_user, login_required

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        user = data.first("users", email=email)
        if user and user.active and check_password_hash(user.password_hash, pw):
            login_user(user)
            log_action(user, "login", "User", user.id)
            data.commit()
            nxt = request.args.get("next")
            return redirect(nxt or url_for("dashboard.home"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    log_action(current_user, "logout", "User", current_user.id)
    data.commit()
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
