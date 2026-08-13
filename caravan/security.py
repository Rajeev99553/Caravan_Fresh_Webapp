"""Role-based access helpers and franchisee isolation."""
from functools import wraps

from flask import abort

from . import data
from .auth_core import current_user
from .constants import (ROLE_OFFICER, ROLE_FRANCHISE, ROLE_MANAGEMENT,
                        ROLE_FINANCE, ROLE_ADMIN)


def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def stores_for_user(user):
    """Return the list of stores a user is permitted to see."""
    if user.role in (ROLE_MANAGEMENT, ROLE_FINANCE, ROLE_ADMIN):
        return data.all_("stores", order_by="code")
    if user.role == ROLE_OFFICER:
        return data.all_("stores", order_by="code", assigned_officer_id=user.id)
    if user.role == ROLE_FRANCHISE:
        return data.all_("stores", order_by="code", owner_id=user.id)
    return []


def can_access_store(user, store):
    if user.role in (ROLE_MANAGEMENT, ROLE_FINANCE, ROLE_ADMIN):
        return True
    if user.role == ROLE_OFFICER:
        return store.assigned_officer_id == user.id
    if user.role == ROLE_FRANCHISE:
        return store.owner_id == user.id
    return False


def require_store_access(user, store):
    if store is None or not can_access_store(user, store):
        abort(403)
