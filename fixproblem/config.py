"""Central configuration. Values can be overridden with environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


INSECURE_SECRET = "dev-only-change-me"


@dataclass
class Settings:
    db_path: Path = Path(os.getenv("FIXPROBLEM_DB", ROOT / "instance" / "fixproblem.db"))
    secret_key: str = field(default_factory=lambda: os.getenv("FIXPROBLEM_SECRET", INSECURE_SECRET))
    environment: str = field(default_factory=lambda: os.getenv("FIXPROBLEM_ENV", "development"))
    csrf: bool = field(default_factory=lambda: os.getenv("FIXPROBLEM_CSRF", "1") == "1")
    # Emerging-issue detection
    recent_days: int = int(os.getenv("FIXPROBLEM_RECENT_DAYS", 14))
    baseline_weeks: int = int(os.getenv("FIXPROBLEM_BASELINE_WEEKS", 8))
    min_recent_count: int = 10
    min_growth_ratio: float = 2.0
    max_p_value: float = 0.01
    # Classification: below this confidence a ticket is sent for human review
    review_threshold: float = 0.55
    # Severity weights used in the priority score (documented in README)
    severity: dict = field(default_factory=lambda: {
        "connectivity": 1.5, "technical_app": 1.3, "billing": 1.2,
        "account_access": 1.1, "cancellation": 1.4, "delivery": 1.0,
    })
    tier_weight: dict = field(default_factory=lambda: {"standard": 1.0, "premium": 1.2, "business": 1.5})


settings = Settings()

# Demo users. In production these would come from the company identity provider.
DEMO_USERS = {
    "agent": {"password": os.getenv("FIXPROBLEM_AGENT_PW", "agent-demo"), "role": "agent"},
    "analyst": {"password": os.getenv("FIXPROBLEM_ANALYST_PW", "analyst-demo"), "role": "analyst"},
    "admin": {"password": os.getenv("FIXPROBLEM_ADMIN_PW", "admin-demo"), "role": "admin"},
}


# Settings an admin may change in the web app (stored in the app_settings table).
EDITABLE = {
    "review_threshold": (float, 0.3, 0.95, "Confidence below which a ticket goes to human review"),
    "recent_days": (int, 7, 28, "Days in the 'recent' window for emerging-issue detection"),
    "baseline_weeks": (int, 4, 16, "Weeks of history used as the baseline"),
    "min_recent_count": (int, 3, 100, "Minimum tickets before an issue can be called emerging"),
    "min_growth_ratio": (float, 1.2, 10.0, "Minimum growth versus baseline to raise an alert"),
    "webhook_url": (str, None, None, "Slack / Teams / generic webhook for new alerts (optional)"),
    "assistant_share_text": (str, None, None, "Allow the assistant to send ticket excerpts to the external AI provider"),
}


def with_overrides(conn, base: "Settings") -> "Settings":
    """Return a copy of the settings with any values saved by an admin applied."""
    import copy
    s = copy.deepcopy(base)
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    except Exception:
        return s
    for key, value in rows:
        if key in EDITABLE and key not in ("webhook_url", "assistant_share_text"):
            setattr(s, key, EDITABLE[key][0](value))
    return s


def validate_setting(key: str, raw: str):
    if key not in EDITABLE:
        raise ValueError(f"unknown setting '{key}'")
    typ, lo, hi, _ = EDITABLE[key]
    if key == "assistant_share_text":
        if raw not in ("0", "1"):
            raise ValueError("choose on or off")
        return raw
    if typ is str:
        raw = raw.strip()
        if raw and not raw.startswith("https://"):
            raise ValueError("webhook URL must start with https://")
        return raw
    try:
        v = typ(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return v


# ---------------------------------------------------------------------------
# Plans and offers. These are ASSUMPTIONS for a pilot: prices and allowances
# must be validated against processing, storage, AI and support costs before
# launch. Keep them here so they can be changed without touching code.
# ---------------------------------------------------------------------------
PLANS = {
    "demo": {"name": "Demo", "price": None, "blurb": "Explore Fixproblem with clearly labelled sample data.",
             "tickets": "Sample data only", "seats": "No account needed", "features": [
                 "Interactive sample workspace", "Issue evidence and priorities", "Read-only"]},
    "free": {"name": "Starter", "price": 0, "blurb": "One workspace for a small support team (free during the pilot).",
             "tickets": "Up to 5,000 tickets / month (not yet enforced)", "seats": "1 user (team invitations coming soon)", "features": [
                 "Import your own tickets (CSV)", "Core analysis and emerging-issue detection",
                 "Issue tracking with owners and comments",
                 "Review queue", "Standard management report (HTML)"]},
    "pro": {"name": "Team", "price": 959, "blurb": "Alerts, exports and AI settings. Sold as a one-month pass while recurring billing is built.",
            "tickets": "Up to 25,000 tickets / month (not yet enforced)", "seats": "1 user (team invitations coming soon)", "features": [
                "Everything in Starter", "Slack / Teams alerts", "CSV and PDF report exports",
                "Retraining and detection settings"]},
    "business": {"name": "Business", "price": None, "blurb": "Larger requirements. Talk to us.",
                 "tickets": "By agreement", "seats": "By agreement", "features": [
                     "Higher limits", "Assisted onboarding (one-off service)", "Requirements discussed before any commitment"]},
}
TRIAL_DAYS = 7
# Optional launch offer, OFF by default. Do not enable until margins are checked.
LAUNCH_OFFER = {"enabled": False, "percent": 20, "periods": 3, "monthly_only": True}


class InsecureConfiguration(RuntimeError):
    """Raised at start-up when production is configured with development defaults."""


def check_production(cfg) -> None:
    """Refuse to start in production with insecure defaults. Demo accounts and demo checkout are
    disabled in production by the application itself."""
    if cfg.environment != "production":
        return
    problems = []
    if cfg.secret_key == INSECURE_SECRET or len(cfg.secret_key) < 32:
        problems.append("set FIXPROBLEM_SECRET to a random value of at least 32 characters")
    if not cfg.csrf:
        problems.append("CSRF protection cannot be disabled in production")
    if problems:
        raise InsecureConfiguration("Refusing to start in production: " + "; ".join(problems))
