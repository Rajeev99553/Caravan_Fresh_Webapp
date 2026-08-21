"""
Action item lifecycle: an officer raises an item on a failed ("No") checkpoint,
assigned to the Franchise Owner and/or Outlet People. The franchise owner
either resolves it (with a photo) or denies it (with a mandatory reason);
the officer then accepts or rejects that response. Rejected responses
reopen the item so the cycle can repeat. All transitions are logged to
action_item_events for a full timeline, visible to admin/management.
"""
import os
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, current_app
from werkzeug.utils import secure_filename

from .. import data
from ..data import log_action
from ..auth_core import current_user, login_required
from ..constants import ROLE_OFFICER, ROLE_FRANCHISE, ROLE_STORE_STAFF, ROLE_MANAGEMENT, ROLE_FINANCE, ROLE_ADMIN
from ..security import roles_required, require_store_access, stores_for_user

ai_bp = Blueprint("action_items", __name__, url_prefix="/action-items")

ASSIGNEE_LABELS = {
    "franchise_owner": "Franchise Owner",
    "outlet_people": "Outlet People",
    "both": "Franchise Owner & Outlet People",
}
STATUS_LABELS = {
    "open": "Open",
    "resolved_pending_review": "Resolved — pending officer review",
    "denied_pending_review": "Denied — pending officer review",
    "verified_resolved": "Verified resolved",
    "verified_denied": "Denial accepted",
}


# --------------------------------------------------------------------------- #
# Franchise owner: respond to open action items assigned to them
# --------------------------------------------------------------------------- #
@ai_bp.route("/")
@login_required
def home():
    if current_user.role in (ROLE_FRANCHISE, ROLE_STORE_STAFF):
        return _franchise_view()
    if current_user.role == ROLE_OFFICER:
        return _officer_review_view()
    if current_user.role in (ROLE_MANAGEMENT, ROLE_FINANCE, ROLE_ADMIN):
        return _oversight_view()
    abort(403)


def _franchise_view():
    """Franchise Owner and Store People each only see action items actually
    assigned to their role (or to 'both') for stores they're linked to —
    not every item raised on that store regardless of who it's for."""
    stores = stores_for_user(current_user)
    store_ids = [s.id for s in stores]
    assignee_key = "franchise_owner" if current_user.role == ROLE_FRANCHISE else "outlet_people"
    items = []
    if store_ids:
        ph = ",".join("?" for _ in store_ids)
        items = data.raw("action_items",
                         f"SELECT * FROM action_items WHERE store_id IN ({ph}) "
                         f"AND assigned_to IN (?, 'both') "
                         f"ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, id DESC",
                         store_ids + [assignee_key])
    return render_template("action_items/franchise.html", items=items,
                           assignee_labels=ASSIGNEE_LABELS, status_labels=STATUS_LABELS)


def _require_assigned(item):
    """Only the role(s) an action item is actually assigned to may respond
    to it — a franchise owner can't act on an outlet-only item and vice versa."""
    key = "franchise_owner" if current_user.role == ROLE_FRANCHISE else "outlet_people"
    if item.assigned_to not in (key, "both"):
        abort(403)


@ai_bp.route("/<int:item_id>/resolve", methods=["POST"])
@login_required
@roles_required(ROLE_FRANCHISE, ROLE_STORE_STAFF)
def resolve(item_id):
    item = data.get("action_items", item_id) or abort(404)
    require_store_access(current_user, item.store)
    _require_assigned(item)
    if item.status != "open":
        flash("This action item isn't awaiting a response.", "danger")
        return redirect(url_for("action_items.home"))
    note = request.form.get("note", "").strip()
    file = request.files.get("photo")
    photo_path = None
    if file and file.filename:
        fn = secure_filename(f"ai{item.id}_{file.filename}")
        file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], fn))
        photo_path = fn
    if not photo_path:
        flash("Please attach a photo showing the issue has been resolved.", "danger")
        return redirect(url_for("action_items.home"))
    now = datetime.utcnow()
    data.update("action_items", item.id, status="resolved_pending_review",
               resolution_note=note, resolution_photo_path=photo_path,
               resolution_photo_at=now, resolved_at=now, updated_at=now)
    data.insert("action_item_events", action_item_id=item.id, event_type="resolved_submitted",
               actor_id=current_user.id, note=note, photo_path=photo_path, created_at=now)
    log_action(current_user, "resolve_action_item", "ActionItem", item.id, note)
    data.commit()
    flash("Marked as resolved. Sent to the audit officer for verification.", "success")
    return redirect(url_for("action_items.home"))


@ai_bp.route("/<int:item_id>/deny", methods=["POST"])
@login_required
@roles_required(ROLE_FRANCHISE, ROLE_STORE_STAFF)
def deny(item_id):
    item = data.get("action_items", item_id) or abort(404)
    require_store_access(current_user, item.store)
    _require_assigned(item)
    if item.status != "open":
        flash("This action item isn't awaiting a response.", "danger")
        return redirect(url_for("action_items.home"))
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("A reason is required to deny an action item.", "danger")
        return redirect(url_for("action_items.home"))
    now = datetime.utcnow()
    data.update("action_items", item.id, status="denied_pending_review",
               denial_reason=reason, denied_at=now, updated_at=now)
    data.insert("action_item_events", action_item_id=item.id, event_type="denied_submitted",
               actor_id=current_user.id, note=reason, created_at=now)
    log_action(current_user, "deny_action_item", "ActionItem", item.id, reason)
    data.commit()
    flash("Denial submitted. Sent to the audit officer for review.", "warning")
    return redirect(url_for("action_items.home"))


# --------------------------------------------------------------------------- #
# Officer: verify resolved/denied items
# --------------------------------------------------------------------------- #
def _officer_review_view():
    stores = stores_for_user(current_user)
    store_ids = [s.id for s in stores]
    pending = []
    if store_ids:
        ph = ",".join("?" for _ in store_ids)
        pending = data.raw("action_items",
                           f"SELECT * FROM action_items WHERE store_id IN ({ph}) "
                           f"AND status IN ('resolved_pending_review','denied_pending_review') "
                           f"ORDER BY updated_at ASC", store_ids)
    return render_template("action_items/verify.html", items=pending,
                           assignee_labels=ASSIGNEE_LABELS)


@ai_bp.route("/<int:item_id>/verify", methods=["POST"])
@login_required
@roles_required(ROLE_OFFICER)
def verify(item_id):
    item = data.get("action_items", item_id) or abort(404)
    require_store_access(current_user, item.store)
    if item.status not in ("resolved_pending_review", "denied_pending_review"):
        flash("Nothing awaiting verification on this item.", "danger")
        return redirect(url_for("action_items.home"))
    decision = request.form.get("decision")  # accept | reject
    note = request.form.get("note", "").strip()
    now = datetime.utcnow()
    was_denial = item.status == "denied_pending_review"

    if decision == "accept":
        new_status = "verified_denied" if was_denial else "verified_resolved"
        event_type = "officer_accepted"
    elif decision == "reject":
        new_status = "open"  # reopened — franchise owner must respond again
        event_type = "officer_rejected"
    else:
        flash("Invalid decision.", "danger")
        return redirect(url_for("action_items.home"))

    data.update("action_items", item.id, status=new_status, officer_decision=decision,
               officer_decision_note=note, officer_decision_at=now, updated_at=now)
    data.insert("action_item_events", action_item_id=item.id, event_type=event_type,
               actor_id=current_user.id, note=note, created_at=now)
    log_action(current_user, "verify_action_item", "ActionItem", item.id,
              f"{decision} (was {'denial' if was_denial else 'resolution'})")
    data.commit()
    if decision == "accept":
        flash("Verified. Action item closed.", "success")
    else:
        flash("Rejected — action item reopened for the franchise owner.", "warning")
    return redirect(url_for("action_items.home"))


# --------------------------------------------------------------------------- #
# Management / Finance / Admin: full oversight view
# --------------------------------------------------------------------------- #
def _oversight_view():
    store_id = request.args.get("store_id", type=int)
    filters = {}
    if store_id:
        filters["store_id"] = store_id
    items = data.all_("action_items", order_by="CASE status WHEN 'open' THEN 0 "
                      "WHEN 'resolved_pending_review' THEN 1 WHEN 'denied_pending_review' THEN 1 "
                      "ELSE 2 END, updated_at DESC", **filters)
    stores = data.all_("stores", order_by="code")
    open_count = sum(1 for i in items if i.status == "open")
    pending_count = sum(1 for i in items if i.status in
                        ("resolved_pending_review", "denied_pending_review"))
    closed_count = sum(1 for i in items if i.status in
                       ("verified_resolved", "verified_denied"))
    return render_template("action_items/oversight.html", items=items, stores=stores,
                           selected_store_id=store_id, assignee_labels=ASSIGNEE_LABELS,
                           status_labels=STATUS_LABELS, open_count=open_count,
                           pending_count=pending_count, closed_count=closed_count)
