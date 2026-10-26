"""End-to-end analysis: classify -> discover issues -> detect trends -> prioritise."""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from .classify import TicketClassifier, ticket_text
from .cluster import discover_issues
from .config import Settings
from .priority import raw_priority, scale
from .summarise import get_llm, summarise
from .trends import compute_trend

log = logging.getLogger(__name__)
FMT = "%Y-%m-%d %H:%M"
MIN_OWN_LABELS = 200          # below this, the starter model supplies training examples
_STARTER: list = []


def starter_examples() -> list[tuple[str, str]]:
    """Training examples for the starter model: the bundled synthetic sample (never customer data)."""
    if not _STARTER:
        import csv
        sample = Path(__file__).resolve().parent.parent / "data" / "sample" / "tickets.csv"
        with sample.open(encoding="utf-8") as f:
            _STARTER.extend((ticket_text(r["subject"], r["body"]), r["agent_tag"]) for r in csv.DictReader(f))
    return list(_STARTER)


def match_issues(conn, issues, min_share: float = 0.5) -> list[str]:
    """Give each new issue the key of the previous issue it continues.

    An earlier issue continues into the new group that holds the largest part of its tickets, provided
    that is at least half of them. Matching is greedy by overlap, one-to-one. Unmatched groups are new
    issues. Owners, statuses, comments and notes are stored against the key, so they follow the
    problem even when its title or the clustering changes.
    """
    import secrets
    old: dict[str, set] = {}
    for key, tid in conn.execute("SELECT i.key, t.ticket_id FROM tickets t JOIN issues i ON i.issue_id=t.issue_id"):
        old.setdefault(key, set()).add(tid)
    pairs = []
    for n, issue in enumerate(issues):
        members = set(issue.ticket_ids)
        for key, tids in old.items():
            overlap = len(members & tids)
            if overlap and overlap / len(tids) >= min_share:
                pairs.append((overlap, n, key))
    keys: list[str | None] = [None] * len(issues)
    used = set()
    for overlap, n, key in sorted(pairs, reverse=True):
        if keys[n] is None and key not in used:
            keys[n], used = key, used | {key}
    return [k or f"ISS-{secrets.token_hex(4)}" for k in keys]


def registry_id(conn, key: str) -> int:
    """Stable numeric id for an issue key (used in links); allocated once, never reused."""
    row = conn.execute("SELECT issue_id FROM issue_registry WHERE key=?", (key,)).fetchone()
    if row:
        return row[0]
    nxt = (conn.execute("SELECT MAX(issue_id) FROM issue_registry").fetchone()[0] or 0) + 1
    conn.execute("INSERT INTO issue_registry (key, issue_id) VALUES (?,?)", (key, nxt))
    return nxt


def run(conn: sqlite3.Connection, settings: Settings, model_path: Path | None = None, llm=None) -> dict:
    from .config import with_overrides
    settings = with_overrides(conn, settings)
    rows = conn.execute("SELECT ticket_id, created_at, subject, body, agent_tag, customer_tier FROM tickets").fetchall()
    if not rows:
        return {"tickets": 0, "issues": 0, "emerging": 0}
    texts = [ticket_text(r["subject"], r["body"]) for r in rows]
    corrected = {r[0]: r[1] for r in conn.execute("SELECT ticket_id, label FROM ticket_labels")}
    # human corrections override the original agent tag when training
    labels = [corrected.get(r["ticket_id"], r["agent_tag"]) for r in rows]
    labelled = [(t, l) for t, l in zip(texts, labels) if l]
    starter = False
    if len({l for _, l in labelled}) < 2 or len(labelled) < MIN_OWN_LABELS:
        # Not enough of the workspace's own labelled tickets: start from the starter model trained on
        # the bundled synthetic sample, plus whatever labels and corrections this workspace has.
        labelled = starter_examples() + labelled
        starter = True
    clf = TicketClassifier(settings.review_threshold).fit([t for t, _ in labelled], [l for _, l in labelled])
    if model_path:
        clf.save(model_path)
    preds = clf.predict(texts)
    for i, r in enumerate(rows):                      # a person's label is final
        if r["ticket_id"] in corrected:
            preds[i] = type(preds[i])(corrected[r["ticket_id"]], 1.0, False)
    conn.executemany("UPDATE tickets SET pred_category=?, pred_confidence=?, needs_review=? WHERE ticket_id=?",
                     [(p.category, round(p.confidence, 3), int(p.needs_review), r["ticket_id"])
                      for p, r in zip(preds, rows)])

    ids = [r["ticket_id"] for r in rows]
    issues = discover_issues(ids, texts, [p.category for p in preds])
    by_id = {r["ticket_id"]: r for r in rows}
    text_by_id = dict(zip(ids, texts))
    body_by_id = {r["ticket_id"]: r["body"] for r in rows}
    as_of = max(datetime.strptime(r["created_at"], FMT) for r in rows)
    llm = llm if llm is not None else get_llm()

    keys = match_issues(conn, issues)               # stable identity: follow the same tickets between runs
    conn.execute("DELETE FROM issues")
    records, raws = [], []
    alpha = settings.max_p_value / max(len(issues), 1)   # Bonferroni correction
    for n, issue in enumerate(issues, start=1):
        stamps = [datetime.strptime(by_id[t]["created_at"], FMT) for t in issue.ticket_ids]
        tr = compute_trend(stamps, as_of, settings.recent_days, settings.baseline_weeks,
                           settings.min_recent_count, settings.min_growth_ratio, alpha)
        vol28 = sum(1 for s in stamps if s > as_of - timedelta(days=28))
        tiers = [settings.tier_weight.get(by_id[t]["customer_tier"], 1.0) for t in issue.ticket_ids]
        raws.append(raw_priority(vol28, settings.severity.get(issue.category, 1.0),
                                 sum(tiers) / len(tiers), tr.growth))
        recent_examples = [text_by_id[t] for t in sorted(issue.ticket_ids, key=lambda t: by_id[t]["created_at"])[-15:]]
        summary = summarise(issue.keywords or issue.label, recent_examples if tr.emerging else [text_by_id[t] for t in issue.ticket_ids],
                            body_by_id[issue.representative_id], llm if tr.emerging else None)
        records.append((n, issue, tr, summary))
    conn.execute("UPDATE tickets SET issue_id=NULL")
    renumber = {}
    for (n, issue, tr, summary), pr, key in zip(records, scale(raws), keys):
        stable = registry_id(conn, key)
        renumber[n] = stable
        conn.execute("INSERT INTO issues (issue_id, category, label, summary, size, recent, baseline_weekly, growth, "
                     "p_value, emerging, priority, key, keywords) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (stable, issue.category, issue.label, summary, len(issue.ticket_ids), tr.recent,
                      tr.baseline_weekly, tr.growth, tr.p_value, int(tr.emerging), pr, key, issue.keywords))
        conn.execute("INSERT OR IGNORE INTO issue_meta (key) VALUES (?)", (key,))
        conn.executemany("UPDATE tickets SET issue_id=? WHERE ticket_id=?", [(stable, t) for t in issue.ticket_ids])
    records = [(renumber[n], issue, tr, summary) for n, issue, tr, summary in records]
    alerts = _build_alerts(conn, records, by_id, text_by_id, body_by_id, as_of, settings, llm)
    conn.commit()
    emerging = sum(int(tr.emerging) for _, _, tr, _ in records)
    log.info("analysed %d tickets into %d issues (%d emerging)", len(rows), len(issues), emerging)
    return {"tickets": len(rows), "issues": len(issues), "emerging": emerging,
            "needs_review": sum(p.needs_review for p in preds),
            "alerts": alerts, "starter_model": starter,
            "categories": {str(k): v for k, v in Counter(p.category for p in preds).items()},
            "as_of": as_of.strftime(FMT)}


def _build_alerts(conn, records, by_id, text_by_id, body_by_id, as_of, settings, llm) -> int:
    """Group emerging issues in the same category into one alert.

    Customers describe the same fault in different words, so one incident can
    appear as several text clusters that all spike in the same window. Treating
    co-emerging clusters in a category as one alert avoids duplicate warnings.
    """
    conn.execute("DELETE FROM alerts")
    groups: dict[str, list] = {}
    for n, issue, tr, _ in records:
        if tr.emerging:
            groups.setdefault(issue.category, []).append((n, issue))
    for a, (cat, members) in enumerate(sorted(groups.items()), start=1):
        tids = [t for _, issue in members for t in issue.ticket_ids]
        stamps = sorted(datetime.strptime(by_id[t]["created_at"], FMT) for t in tids)
        tr = compute_trend(stamps, as_of, settings.recent_days, settings.baseline_weeks,
                           settings.min_recent_count, settings.min_growth_ratio, settings.max_p_value)
        recent = [t for t in sorted(tids, key=lambda t: by_id[t]["created_at"])][-15:]
        label = members[0][1].keywords or members[0][1].label
        summary = summarise(label, [text_by_id[t] for t in recent], body_by_id[recent[-1]], llm)
        if summary.startswith(f"{len(recent)} tickets"):
            summary = f"{len(tids)} tickets" + summary[len(f"{len(recent)} tickets"):]
        conn.execute("INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (a, cat, ",".join(str(n) for n, _ in members), len(tids), tr.recent, tr.expected,
                      tr.growth, tr.p_value, stamps[0].strftime(FMT), summary))
    return len(groups)
