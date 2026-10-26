"""SQLite storage layer."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id      TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    channel        TEXT,
    customer_id    TEXT,
    customer_email TEXT,
    customer_tier  TEXT DEFAULT 'standard',
    subject        TEXT,
    body           TEXT NOT NULL,
    agent_tag      TEXT,
    pred_category  TEXT,
    pred_confidence REAL,
    needs_review   INTEGER DEFAULT 0,
    issue_id       INTEGER
);
CREATE TABLE IF NOT EXISTS issues (
    issue_id    INTEGER PRIMARY KEY,
    category    TEXT,
    label       TEXT,
    summary     TEXT,
    size        INTEGER,
    recent      INTEGER,
    baseline_weekly REAL,
    growth      REAL,
    p_value     REAL,
    emerging    INTEGER,
    priority    REAL,
    key         TEXT,
    keywords    TEXT
);
CREATE TABLE IF NOT EXISTS issue_registry (     -- stable issue identity across re-analysis
    key         TEXT PRIMARY KEY,
    issue_id    INTEGER UNIQUE NOT NULL,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id    INTEGER PRIMARY KEY,
    category    TEXT,
    issue_ids   TEXT,
    tickets     INTEGER,
    recent      INTEGER,
    expected    REAL,
    growth      REAL,
    p_value     REAL,
    first_seen  TEXT,
    summary     TEXT
);
CREATE TABLE IF NOT EXISTS import_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
    filename    TEXT,
    accepted    INTEGER,
    rejected    INTEGER,
    duplicates  INTEGER,
    errors      TEXT
);
CREATE TABLE IF NOT EXISTS issue_meta (
    key         TEXT PRIMARY KEY,          -- stable across re-analysis: category:label
    status      TEXT DEFAULT 'new',        -- new | investigating | resolved
    owner       TEXT,
    description TEXT,                        -- editable by the team
    confirmed   TEXT,                        -- what the team has verified (root cause, fix)
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS support_requests (
    ref         TEXT PRIMARY KEY,            -- e.g. FP-7K2Q9A, shown to the person who asked
    at          TEXT DEFAULT CURRENT_TIMESTAMP,
    kind        TEXT,                        -- feedback | support | contact | sales
    category    TEXT, name TEXT, email TEXT, description TEXT NOT NULL,
    screenshot  TEXT, diagnostics TEXT, status TEXT DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS issue_comments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    key     TEXT NOT NULL,
    author  TEXT NOT NULL,
    body    TEXT NOT NULL,
    at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ticket_labels (      -- human corrections of the AI category
    ticket_id  TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    labelled_by TEXT NOT NULL,
    prev_category TEXT, prev_confidence REAL,   -- kept so a correction can be undone
    at         TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT DEFAULT CURRENT_TIMESTAMP,
    channel TEXT, alert_key TEXT, ok INTEGER, detail TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT UNIQUE NOT NULL,
    name           TEXT NOT NULL,
    password_hash  TEXT NOT NULL,              -- salted scrypt/pbkdf2 hash; the password itself is never stored
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    plan           TEXT DEFAULT 'free',        -- free | trial | pro
    trial_ends     TEXT,
    pro_until      TEXT,
    signup_offer   INTEGER DEFAULT 1,          -- 10% off the first Pro payment
    offer_used     INTEGER DEFAULT 0,
    marketing_ok   INTEGER DEFAULT 0,
    onboarded      INTEGER DEFAULT 0,
    last_login     TEXT
);
CREATE TABLE IF NOT EXISTS workspaces (       -- platform table: one row per customer workspace
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'customer',   -- demo | sample | customer
    owner_id    INTEGER,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS memberships (
    user_id      INTEGER NOT NULL,
    workspace_id INTEGER NOT NULL,
    role         TEXT NOT NULL DEFAULT 'owner',
    PRIMARY KEY (user_id, workspace_id)
);
CREATE TABLE IF NOT EXISTS payments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
    at            TEXT DEFAULT CURRENT_TIMESTAMP,
    provider      TEXT,                        -- stripe | demo
    reference     TEXT,
    amount_pence  INTEGER,
    discount_pct  INTEGER DEFAULT 0,
    status        TEXT                         -- paid | cancelled | refunded
);
CREATE TABLE IF NOT EXISTS billing_events (     -- Stripe webhook events already processed (duplicate protection)
    event_id   TEXT PRIMARY KEY,
    at         TEXT DEFAULT CURRENT_TIMESTAMP,
    type       TEXT, outcome TEXT
);
CREATE TABLE IF NOT EXISTS activity (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT DEFAULT CURRENT_TIMESTAMP,
    actor     TEXT,                            -- user email, demo username or guest:<id>
    method    TEXT, path TEXT, status INTEGER, action TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_actor ON activity(actor);
CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts USING fts5(ticket_id UNINDEXED, subject, body);
CREATE INDEX IF NOT EXISTS idx_tickets_issue ON tickets(issue_id);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at);
"""


MIGRATIONS = [  # (table, column, definition) added after first release
    ("issue_meta", "description", "TEXT"), ("issue_meta", "confirmed", "TEXT"),
    ("ticket_labels", "prev_category", "TEXT"), ("ticket_labels", "prev_confidence", "REAL"),
    ("issues", "keywords", "TEXT"),
    ("users", "workspace", "TEXT"), ("users", "hide_checklist", "INTEGER DEFAULT 0"),
]


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    changed = False
    if conn.execute("SELECT 1 FROM workspaces WHERE id=1").fetchone() is None:
        conn.execute("INSERT INTO workspaces (id, name, kind) VALUES (1, 'Northwind Broadband (sample)', 'demo')")
        changed = True
    for table, col, typ in MIGRATIONS:          # upgrade databases created by earlier versions
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            changed = True
    if changed:
        conn.commit()                           # never leave a write transaction (and its lock) open
    return conn
