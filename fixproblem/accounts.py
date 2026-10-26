"""Accounts, plans, trials, payments and activity tracking.

Security and privacy choices
----------------------------
* Passwords are stored only as salted hashes (werkzeug: scrypt). Nobody, including
  administrators, can read a user's password.
* Card details never reach this application: payment is handled by Stripe Checkout,
  or by a clearly labelled demo mode when no Stripe key is configured.
* Every request by a signed-in user or guest is recorded in `activity` (who, what,
  when). Users are told this in the privacy notice they accept at sign-up, can
  download everything held about them, and can delete their account.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

PRO_PRICE_PENCE = 959                 # £9.59 / month
SIGNUP_DISCOUNT_PCT = 0               # optional first-payment discount; 0 = no discount (configurable)
TRIAL_DAYS = 7
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
FMT = "%Y-%m-%d %H:%M:%S"


class AccountError(ValueError):
    """Shown to the user."""


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def price_for(user: dict | None) -> tuple[int, int]:
    """(amount in pence, discount %) for this user's next payment."""
    if user and user["signup_offer"] and not user["offer_used"]:
        return round(PRO_PRICE_PENCE * (100 - SIGNUP_DISCOUNT_PCT) / 100), SIGNUP_DISCOUNT_PCT
    return PRO_PRICE_PENCE, 0


def gbp(pence: int) -> str:
    return f"£{pence / 100:,.2f}"


# ---------------- users ----------------

def create_user(conn: sqlite3.Connection, email: str, name: str, password: str, marketing_ok: bool = False,
                workspace: str = "") -> dict:
    email, name = (email or "").strip().lower(), (name or "").strip()
    if not EMAIL_RE.match(email):
        raise AccountError("Enter a valid email address.")
    if not 1 <= len(name) <= 80:
        raise AccountError("Enter your name.")
    if len(password or "") < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise AccountError("Password must be at least 8 characters and include a letter and a number.")
    if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise AccountError("An account with this email already exists. Sign in instead.")
    workspace = (workspace or "").strip()[:80] or f"{name.split()[0]}'s workspace"
    conn.execute("INSERT INTO users (email, name, password_hash, marketing_ok, workspace) VALUES (?,?,?,?,?)",
                 (email, name, generate_password_hash(password), int(bool(marketing_ok)), workspace))
    conn.commit()
    return get_user(conn, email=email)


def get_user(conn, user_id: int | None = None, email: str | None = None) -> dict | None:
    row = (conn.execute("SELECT * FROM users WHERE id=?", (user_id,)) if user_id is not None
           else conn.execute("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),))).fetchone()
    return refresh_plan(conn, dict(row)) if row else None


def authenticate(conn, email: str, password: str) -> dict | None:
    user = get_user(conn, email=email)
    if user and check_password_hash(user["password_hash"], password or ""):
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (now().strftime(FMT), user["id"]))
        conn.commit()
        return user
    return None


def refresh_plan(conn, user: dict) -> dict:
    """Downgrade expired trials and lapsed subscriptions to Free."""
    t = now().strftime(FMT)
    if user["plan"] == "trial" and user["trial_ends"] and user["trial_ends"] < t:
        user["plan"] = "free"
    elif user["plan"] == "pro" and user["pro_until"] and user["pro_until"] < t:
        user["plan"] = "free"
    else:
        return user
    conn.execute("UPDATE users SET plan='free' WHERE id=?", (user["id"],))
    conn.commit()
    return user


def mark_onboarded(conn, user_id: int) -> None:
    conn.execute("UPDATE users SET onboarded=1 WHERE id=?", (user_id,))
    conn.commit()


# ---------------- workspaces ----------------
# Each workspace has its own database file (database-per-tenant), so customer records are
# physically separated. Workspace 1 is the shared, read-only demo workspace.

DEMO_WORKSPACE = 1


def create_workspace(conn, name: str, owner_id: int, kind: str = "customer") -> int:
    name = (name or "").strip()[:80] or "My workspace"
    cur = conn.execute("INSERT INTO workspaces (name, kind, owner_id) VALUES (?,?,?)", (name, kind, owner_id))
    conn.execute("INSERT INTO memberships (user_id, workspace_id, role) VALUES (?,?, 'owner')", (owner_id, cur.lastrowid))
    conn.commit()
    return cur.lastrowid


def user_workspaces(conn, user_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT w.*, m.role FROM memberships m JOIN workspaces w ON w.id=m.workspace_id WHERE m.user_id=? ORDER BY w.id",
        (user_id,))]


def membership(conn, user_id: int, workspace_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM memberships WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)).fetchone()
    return dict(row) if row else None


def workspace(conn, workspace_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    return dict(row) if row else None


# ---------------- plans and billing ----------------

def start_trial(conn, user: dict) -> dict:
    if user["trial_ends"]:
        raise AccountError("You have already used your free trial.")
    if user["plan"] == "pro":
        raise AccountError("You are already on Pro.")
    ends = (now() + timedelta(days=TRIAL_DAYS)).strftime(FMT)
    conn.execute("UPDATE users SET plan='trial', trial_ends=? WHERE id=?", (ends, user["id"]))
    conn.commit()
    return get_user(conn, user["id"])


def record_payment(conn, user: dict, provider: str, reference: str) -> dict:
    """Activate Pro for one month after a successful payment."""
    amount, discount = price_for(user)
    start = max(now(), datetime.strptime(user["pro_until"], FMT)) if user["pro_until"] else now()
    until = (start + timedelta(days=30)).strftime(FMT)
    conn.execute("INSERT INTO payments (user_id, provider, reference, amount_pence, discount_pct, status) "
                 "VALUES (?,?,?,?,?, 'paid')", (user["id"], provider, reference, amount, discount))
    conn.execute("UPDATE users SET plan='pro', pro_until=?, offer_used=1 WHERE id=?", (until, user["id"]))
    conn.commit()
    return get_user(conn, user["id"])


def cancel(conn, user: dict) -> dict:
    """Cancel renewal: Pro stays active until the end of the paid month."""
    if user["plan"] != "pro":
        raise AccountError("There is no active subscription to cancel.")
    conn.execute("INSERT INTO payments (user_id, provider, reference, amount_pence, status) VALUES (?,?,?,0,'cancelled')",
                 (user["id"], "system", "cancel renewal"))
    conn.commit()
    return user


def is_cancelled(conn, user_id: int) -> bool:
    last = conn.execute("SELECT status FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    return bool(last and last[0] == "cancelled")


# ---------------- activity and data rights ----------------

def log_activity(conn, actor: str, method: str, path: str, status: int, action: str = "") -> None:
    conn.execute("INSERT INTO activity (actor, method, path, status, action) VALUES (?,?,?,?,?)",
                 (actor, method, path[:300], status, action[:200]))
    conn.commit()


def export_user_data(conn, user_id: int) -> dict:
    user = dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
    user.pop("password_hash")
    return {"account": user,
            "payments": [dict(r) for r in conn.execute("SELECT * FROM payments WHERE user_id=?", (user_id,))],
            "activity": [dict(r) for r in conn.execute("SELECT at, method, path, status, action FROM activity "
                                                       "WHERE actor=? ORDER BY id", (user["email"],))],
            "comments": [dict(r) for r in conn.execute("SELECT key, body, at FROM issue_comments WHERE author=?",
                                                       (user["email"],))]}


def delete_user(conn, user_id: int) -> None:
    """Right to erasure: remove the account, its payments record and its activity trail."""
    email = conn.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
    if not email:
        return
    conn.execute("DELETE FROM activity WHERE actor=?", (email[0],))
    conn.execute("DELETE FROM memberships WHERE user_id=?", (user_id,))
    # support requests are kept for their documented retention period, but de-identified
    conn.execute("UPDATE support_requests SET name='deleted user', email=NULL WHERE email=?", (email[0],))
    conn.execute("DELETE FROM workspaces WHERE owner_id=?", (user_id,))
    conn.execute("UPDATE issue_comments SET author='deleted user' WHERE author=?", (email[0],))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


def analytics(conn) -> dict:
    users = conn.execute("SELECT plan, COUNT(*) n FROM users GROUP BY plan").fetchall()
    by_plan = {r["plan"]: r["n"] for r in users}
    total = sum(by_plan.values())
    paid = conn.execute("SELECT COUNT(DISTINCT user_id) FROM payments WHERE status='paid'").fetchone()[0]
    revenue = conn.execute("SELECT COALESCE(SUM(amount_pence),0) FROM payments WHERE status='paid'").fetchone()[0]
    trials = conn.execute("SELECT COUNT(*) FROM users WHERE trial_ends IS NOT NULL").fetchone()[0]
    since = (now() - timedelta(days=7)).strftime(FMT)
    return {
        "users": total, "by_plan": by_plan,
        "workspaces": conn.execute("SELECT COUNT(*) FROM workspaces WHERE kind != 'demo'").fetchone()[0], "paying": paid, "trials_started": trials,
        "conversion": paid / total if total else 0.0,
        "mrr_pence": by_plan.get("pro", 0) * PRO_PRICE_PENCE, "revenue_pence": revenue,
        "guests_7d": conn.execute("SELECT COUNT(DISTINCT actor) FROM activity WHERE actor LIKE 'guest:%' AND at > ?",
                                  (since,)).fetchone()[0],
        "actions_7d": conn.execute("SELECT COUNT(*) FROM activity WHERE at > ?", (since,)).fetchone()[0],
        "top_pages": [dict(r) for r in conn.execute(
            "SELECT path, COUNT(*) n FROM activity WHERE method='GET' GROUP BY path ORDER BY n DESC LIMIT 8")],
        "recent_users": [dict(r) for r in conn.execute(
            "SELECT id, email, name, plan, created_at, last_login, marketing_ok FROM users ORDER BY id DESC LIMIT 20")],
        "recent_activity": [dict(r) for r in conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 40")],
    }


def new_guest_id() -> str:
    return secrets.token_hex(4)


class LoginLimiter:
    """Block an IP after 5 failed sign-ins in 10 minutes (simple brute-force protection)."""

    def __init__(self, limit: int = 5, window: int = 600):
        self.limit, self.window, self.fails = limit, window, defaultdict(deque)

    def blocked(self, key: str) -> bool:
        q = self.fails[key]
        while q and q[0] < time.time() - self.window:
            q.popleft()
        return len(q) >= self.limit

    def fail(self, key: str) -> None:
        self.fails[key].append(time.time())

    def reset(self, key: str) -> None:
        self.fails.pop(key, None)
