"""
Minimal session-based authentication (replaces Flask-Login) using the
standard library + Flask sessions. Exposes a `current_user` proxy and
`login_required` decorator compatible with how the app/templates use them.
"""
from functools import wraps

from flask import session, g, redirect, url_for, request, flash, abort
from werkzeug.local import LocalProxy

from . import data
from .constants import ROLE_LABELS


class Anonymous:
    is_authenticated = False
    is_anonymous = True
    id = None
    name = "Guest"
    role = None
    role_label = ""


def load_logged_in_user():
    uid = session.get("user_id")
    if uid is None:
        g.user = Anonymous()
        return
    rec = data.get("users", uid)
    if rec is None or not rec.active:
        session.clear()
        g.user = Anonymous()
        return
    g.user = rec


def _get_user():
    if "user" not in g:
        load_logged_in_user()
    return g.user


current_user = LocalProxy(_get_user)


def login_user(user):
    session["user_id"] = user.id
    session.permanent = True
    g.user = user


def logout_user():
    session.clear()
    g.user = Anonymous()


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_authenticated:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return fn(*a, **kw)
    return wrapper
