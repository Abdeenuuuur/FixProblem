"""Measure Fixproblem against the ground truth in the synthetic dataset.

Run:  python scripts/evaluate.py
Writes results/metrics.json and results/RESULTS.md.

Methods
-------
1. Classification: train on the oldest 70% of tickets using the (noisy) agent
   tags, test on the newest 30% against the true category. Compared with a
   majority-class baseline and with the agents' own tags.
2. Issue discovery: compare clusters with the true issue labels using
   homogeneity, completeness and V-measure (scikit-learn).
3. Emerging-issue detection (backtest): replay history. At each cut-off date we
   load only tickets received up to that date and run the full pipeline.
   Weekly cut-offs before the v5.2 release measure false alarms; daily cut-offs
   after the release measure how quickly the problem is detected.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sklearn.metrics import (accuracy_score, classification_report, completeness_score, f1_score,  # noqa: E402
                             homogeneity_score, v_measure_score)

from fixproblem import db, pipeline  # noqa: E402
from fixproblem.classify import TicketClassifier, ticket_text  # noqa: E402
from fixproblem.config import Settings  # noqa: E402
from fixproblem.ingest import import_csv  # noqa: E402

FMT = "%Y-%m-%d %H:%M"
SAMPLE = ROOT / "data" / "sample" / "tickets.csv"
OUT = ROOT / "results"


def load():
    with SAMPLE.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def eval_classifier(rows):
    rows = sorted(rows, key=lambda r: r["created_at"])
    cut = int(len(rows) * 0.7)
    train, test = rows[:cut], rows[cut:]
    clf = TicketClassifier(0.55).fit([ticket_text(r["subject"], r["body"]) for r in train],
                                     [r["agent_tag"] for r in train])
    preds = clf.predict([ticket_text(r["subject"], r["body"]) for r in test])
    y_true = [r["true_category"] for r in test]
    y_pred = [p.category for p in preds]
    majority = max(set(r["agent_tag"] for r in train), key=[r["agent_tag"] for r in train].count)
    auto = [(t, p.category) for t, p in zip(y_true, preds) if not p.needs_review]
    report = classification_report(y_true, y_pred, output_dict=True, zero_division=0)
    return {
        "train_size": len(train), "test_size": len(test),
        "accuracy": round(accuracy_score(y_true, y_pred), 4),
        "macro_f1": round(f1_score(y_true, y_pred, average="macro"), 4),
        "majority_baseline_accuracy": round(accuracy_score(y_true, [majority] * len(test)), 4),
        "agent_tag_accuracy": round(accuracy_score(y_true, [r["agent_tag"] for r in test]), 4),
        "sent_to_review": sum(p.needs_review for p in preds),
        "auto_routed_accuracy": round(sum(a == b for a, b in auto) / len(auto), 4),
        "per_class_f1": {k: round(v["f1-score"], 3) for k, v in report.items() if k in set(y_true)},
    }


def run_until(rows, cutoff: datetime):
    conn = db.connect(":memory:")
    subset = [r for r in rows if datetime.strptime(r["created_at"], FMT) <= cutoff]
    header = ["ticket_id", "created_at", "channel", "customer_id", "customer_email", "customer_tier",
              "subject", "body", "agent_tag"]
    lines = [",".join(header)] + [",".join('"' + r[h].replace('"', '""') + '"' for h in header) for r in subset]
    import_csv(conn, "\n".join(lines))
    pipeline.run(conn, Settings(), llm=None)
    return conn


def eval_issues_and_alerts(rows):
    truth = {r["ticket_id"]: r for r in rows}
    end = max(datetime.strptime(r["created_at"], FMT) for r in rows)
    conn = run_until(rows, end)
    assigned = conn.execute("SELECT ticket_id, issue_id FROM tickets").fetchall()
    t_lab = [truth[a["ticket_id"]]["true_issue"] for a in assigned]
    p_lab = [a["issue_id"] for a in assigned]
    issues = {"true_issues": len(set(t_lab)), "clusters_found": len(set(p_lab)),
              "homogeneity": round(homogeneity_score(t_lab, p_lab), 4),
              "completeness": round(completeness_score(t_lab, p_lab), 4),
              "v_measure": round(v_measure_score(t_lab, p_lab), 4)}

    crash = [datetime.strptime(r["created_at"], FMT) for r in rows if r["true_issue"] == "app_crash_v52"]
    release = min(crash).replace(hour=0, minute=0)
    false_alarm_runs, weekly = 0, []
    for w in range(12, 0, -1):                       # 12 weekly cut-offs before the release
        cutoff = release - timedelta(weeks=w) + timedelta(days=6, hours=23)
        n = run_until(rows, cutoff).execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        weekly.append({"cutoff": cutoff.strftime("%Y-%m-%d"), "alerts": n})
        false_alarm_runs += n > 0
    first_day, daily = None, []
    for d in range(1, 15):                           # daily cut-offs after the release
        cutoff = release + timedelta(days=d)
        c = run_until(rows, cutoff)
        hit = c.execute("SELECT COUNT(*) FROM alerts WHERE category='technical_app'").fetchone()[0] > 0
        daily.append({"day": d, "crash_tickets_so_far": sum(t <= cutoff for t in crash), "alerted": hit})
        if hit and first_day is None:
            first_day = d
    alert = conn.execute("SELECT issue_ids FROM alerts WHERE category='technical_app'").fetchone()
    ids = alert["issue_ids"].split(",") if alert else []
    in_alert = [r[0] for r in conn.execute(
        f"SELECT ticket_id FROM tickets WHERE issue_id IN ({','.join('?' * len(ids))})", ids)] if ids else []
    tp = sum(truth[t]["true_issue"] == "app_crash_v52" for t in in_alert)
    detection = {
        "pre_release_runs": len(weekly), "pre_release_runs_with_false_alarm": false_alarm_runs,
        "days_to_first_alert": first_day, "crash_tickets_before_alert":
            next((x["crash_tickets_so_far"] for x in daily if x["alerted"]), None),
        "final_alert_ticket_precision": round(tp / len(in_alert), 4) if in_alert else None,
        "final_alert_ticket_recall": round(tp / len(crash), 4),
        "final_alerts_total": conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
        "daily": daily, "weekly_pre_release": weekly,
    }
    return issues, detection


def main():
    rows = load()
    m = {"dataset": {"tickets": len(rows), "origin": "synthetic, data/generate_sample_data.py, seed 42"},
         "classification": eval_classifier(rows)}
    m["issue_discovery"], m["emerging_detection"] = eval_issues_and_alerts(rows)
    OUT.mkdir(exist_ok=True)
    (OUT / "metrics.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    c, i, e = m["classification"], m["issue_discovery"], m["emerging_detection"]
    md = f"""# Results

All numbers below are produced by `python scripts/evaluate.py` on the synthetic
dataset ({len(rows):,} tickets, seed 42). Raw output: [`metrics.json`](metrics.json).

## 1. Ticket classification

Trained on the oldest {c['train_size']:,} tickets using agent tags (about 7% wrong on purpose),
tested on the newest {c['test_size']:,} against the true category.

| Method | Accuracy |
|---|---|
| Majority-class baseline | {c['majority_baseline_accuracy']:.1%} |
| Agents' own tags | {c['agent_tag_accuracy']:.1%} |
| **Fixproblem classifier** | **{c['accuracy']:.1%}** (macro F1 {c['macro_f1']:.3f}) |
| Fixproblem, auto-routed tickets only | {c['auto_routed_accuracy']:.1%} ({c['sent_to_review']} sent to human review) |

Per-category F1: {", ".join(f"{k} {v}" for k, v in c['per_class_f1'].items())}.

The model beats the agent tags it was trained on because it learns the consistent pattern and
ignores the random mislabels.

## 2. Recurring-issue discovery

| Metric | Value |
|---|---|
| True issues in data | {i['true_issues']} |
| Clusters found | {i['clusters_found']} |
| Homogeneity (each cluster contains one issue) | {i['homogeneity']:.3f} |
| Completeness (each issue sits in one cluster) | {i['completeness']:.3f} |
| V-measure | {i['v_measure']:.3f} |

Clusters are very pure but over-split: customers describe one problem in different words, and
TF-IDF does not know that "crashes" and "closes immediately" mean the same thing. Alerts
compensate by grouping clusters that spike together (section 3). Sentence embeddings are the
planned fix (see README, Limitations).

## 3. Emerging-issue detection (historical backtest)

| Metric | Value |
|---|---|
| False alarms in the 12 weeks before the release | {e['pre_release_runs_with_false_alarm']} of {e['pre_release_runs']} weekly runs |
| Days from release to first alert | {e['days_to_first_alert']} |
| Crash tickets received before the alert fired | {e['crash_tickets_before_alert']} |
| Share of alert tickets that are truly the crash issue (precision) | {e['final_alert_ticket_precision']:.1%} |
| Share of crash tickets captured by the alert (recall) | {e['final_alert_ticket_recall']:.1%} |

## Caveats

The data is synthetic and generated from templates, so these numbers are an upper bound.
Real tickets are longer, messier and multilingual; expect lower accuracy and re-measure on
an anonymised export before relying on the tool.
"""
    (OUT / "RESULTS.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
