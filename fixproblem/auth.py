"""Roles, plans and permissions.

Roles
  guest    explore the demo read-only (no comments, labels or changes)
  agent    view everything, comment on issues, correct AI labels; customer emails are masked
  analyst  + unmasked data, change issue status and owner
  owner    a signed-up user: full control of their workspace (same powers as admin, minus platform analytics)
  admin    + platform analytics (sign-ups, revenue, activity)

Plans
  free     core product incl. importing your own tickets: overview, tickets, issues, review queue
  trial    7 days of everything in Pro
  pro      £9.59/month: report exports, retraining, AI settings and Slack/Teams alerts
"""
from __future__ import annotations

from functools import wraps

from flask import abort, flash, redirect, render_template, request, session, url_for

ROLE_RANK = {"guest": 0, "agent": 1, "analyst": 2, "owner": 3, "admin": 3}
PERMISSIONS = {
    "view": "guest",
    "comment": "agent",
    "label": "agent",
    "view_pii": "analyst",
    "triage": "analyst",
    "export": "analyst",
    "import": "owner",
    "retrain": "owner",
    "settings": "owner",
}
PRO_FEATURES = {"export", "retrain", "settings"}
PAID_PLANS = {"trial", "pro"}


def role_allows(role: str | None, permission: str) -> bool:
    if permission == "platform":
        return role == "admin"
    if role not in ROLE_RANK or permission not in PERMISSIONS:
        return False
    return ROLE_RANK[role] >= ROLE_RANK[PERMISSIONS[permission]]


def plan_allows(plan: str | None, permission: str) -> bool:
    return permission not in PRO_FEATURES or plan in PAID_PLANS


def can(role: str | None, permission: str, plan: str | None = "pro") -> bool:
    return role_allows(role, permission) and plan_allows(plan, permission)


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "•••"
    name, domain = email.split("@", 1)
    return f"{name[0]}•••@{domain}"


def requires(permission: str):
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            role = session.get("role")
            if role is None:
                return redirect(url_for("login", next=request.path))
            if not role_allows(role, permission):
                abort(403)
            if not plan_allows(session.get("plan"), permission):
                if request.method == "GET":
                    flash("That’s a Pro feature. Start your free 7-day trial to unlock it.", "error")
                    return redirect(url_for("billing"))
                return render_template("error.html", code=402,
                                       message="This is a Pro feature. Start a free trial to use it."), 402
            return view(*args, **kwargs)
        return wrapper
    return deco
