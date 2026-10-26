"""Generate a reproducible, fully synthetic support-ticket dataset.

Origin: every ticket is produced by this script from templates and a fixed
random seed. No real customer data is used. The fictional company is
"Northwind Broadband", a UK home-broadband provider.

Columns
-------
ticket_id, created_at, channel, customer_id, customer_email, customer_tier,
subject, body, agent_tag      -> what a real helpdesk export would contain
true_category, true_issue     -> ground truth, used ONLY by scripts/evaluate.py

`agent_tag` is the category an agent assigned at the time. About 7% of tags are
deliberately wrong, because historical tags in real helpdesks are noisy.

An emerging issue ("app_crash_v52": the mobile app crashing after the v5.2
release) is injected into the final 14 days so that trend detection can be
measured against a known answer.
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 42
START = datetime(2026, 3, 2, 8, 0)
WEEKS = 26
OUT = Path(__file__).parent / "sample" / "tickets.csv"

ISSUES = {
    "billing": {
        "double_charge": [
            "I have been charged twice for {month}",
            "two payments came out of my account for the same bill",
            "duplicate direct debit taken this month",
            "you took my payment twice, please refund one of them",
        ],
        "price_rise": [
            "my bill went up by {amount} without any warning",
            "why has my monthly price increased",
            "unexpected price increase on my latest invoice",
            "nobody told me the price would rise to {amount}",
        ],
        "refund_missing": [
            "still waiting for the refund I was promised {weeks} weeks ago",
            "refund not received yet",
            "you said you would credit my account but nothing has arrived",
        ],
    },
    "connectivity": {
        "evening_slow": [
            "internet is really slow every evening",
            "speeds drop to {speed} Mbps after 7pm",
            "broadband crawls at night, fine in the morning",
            "evening speeds are unusable for video calls",
        ],
        "total_outage": [
            "no internet at all since {day}",
            "complete outage, router lights are red",
            "connection is down in my area",
            "broadband has been dead for {hours} hours",
        ],
        "wifi_drops": [
            "wifi keeps dropping every few minutes",
            "connection disconnects randomly throughout the day",
            "my devices lose wifi constantly",
        ],
    },
    "account_access": {
        "reset_email": [
            "password reset email never arrives",
            "I requested a reset link but got nothing",
            "can't reset my password, no email received",
        ],
        "two_factor": [
            "not receiving the verification code by text",
            "2FA code never comes through",
            "stuck on the security code screen when logging in",
        ],
    },
    "delivery": {
        "router_not_delivered": [
            "my new router has not been delivered",
            "router delivery is {days} days late",
            "tracking says delivered but no router arrived",
        ],
        "wrong_item": [
            "I received the wrong router model",
            "the box contained the wrong equipment",
        ],
    },
    "cancellation": {
        "cancel_request": [
            "I want to cancel my contract",
            "please close my account, I'm moving to another provider",
            "how do I end my contract early",
        ],
        "exit_fee": [
            "why am I being charged an exit fee of {amount}",
            "early termination fee seems wrong",
        ],
    },
    "technical_app": {
        "bill_pdf": [
            "the bill PDF won't open in the app",
            "can't download my invoice from the app",
        ],
        "app_crash_v52": [
            "the app crashes as soon as I log in since the update",
            "after updating to version 5.2 the app closes immediately",
            "app keeps crashing on the login screen",
            "new app update v5.2 crashes on my phone",
            "app freezes and shuts down after the latest update",
        ],
    },
}

# relative weekly frequency of each issue in normal weeks
WEIGHTS = {
    "double_charge": 6, "price_rise": 8, "refund_missing": 5,
    "evening_slow": 10, "total_outage": 7, "wifi_drops": 8,
    "reset_email": 6, "two_factor": 4,
    "router_not_delivered": 5, "wrong_item": 2,
    "cancel_request": 6, "exit_fee": 3,
    "bill_pdf": 3, "app_crash_v52": 0,  # 0 until the release
}

OPENERS = ["", "Hi, ", "Hello, ", "Hi team, ", "Good morning, ", "To whom it may concern, "]
CLOSERS = ["", " Thanks.", " Please help.", " This is really frustrating.", " Can someone call me back?",
           " Account number is {acct}.", " I have contacted you before about this."]
CHANNELS = ["email", "chat", "phone", "web_form"]
TIERS = ["standard"] * 8 + ["premium"] * 2 + ["business"] * 1


def _typo(text: str, rng: random.Random) -> str:
    if len(text) < 12 or rng.random() > 0.15:
        return text
    i = rng.randrange(1, len(text) - 2)
    return text[:i] + text[i + 1] + text[i] + text[i + 2:]


def _fill(t: str, rng: random.Random) -> str:
    return t.format(
        month=rng.choice(["March", "April", "May", "June", "July", "August"]),
        amount=f"£{rng.randint(3, 15)}.{rng.choice(['00', '50', '99'])}",
        weeks=rng.randint(2, 6), speed=rng.randint(2, 9), day=rng.choice(["Monday", "yesterday", "this morning"]),
        hours=rng.randint(4, 48), days=rng.randint(3, 10), acct=rng.randint(100000, 999999),
    )


def generate(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    issue_to_cat = {i: c for c, d in ISSUES.items() for i in d}
    cats = list(ISSUES)
    rows, n = [], 0
    release = START + timedelta(weeks=WEEKS) - timedelta(days=14)
    for week in range(WEEKS):
        for issue, w in WEIGHTS.items():
            count = rng.randint(max(0, w - 2), w + 2) if w else 0
            for _ in range(count):
                rows.append((issue, START + timedelta(weeks=week, minutes=rng.randint(0, 7 * 24 * 60 - 1))))
    # injected emerging issue: last 14 days, ramping up
    for d in range(14):
        for _ in range(1 + d // 2 + rng.randint(0, 2)):
            rows.append(("app_crash_v52", release + timedelta(days=d, minutes=rng.randint(0, 24 * 60 - 1))))
    rows.sort(key=lambda r: r[1])
    out = []
    for issue, ts in rows:
        n += 1
        cat = issue_to_cat[issue]
        body = _typo(_fill(rng.choice(ISSUES[cat][issue]), rng), rng)
        body = _fill(rng.choice(OPENERS), rng) + body + _fill(rng.choice(CLOSERS), rng)
        subject = body.split(",")[-1][:60].strip().capitalize() if rng.random() < 0.5 else cat.replace("_", " ").title()
        tag = cat if rng.random() > 0.07 else rng.choice([c for c in cats if c != cat])
        cid = rng.randint(10000, 99999)
        out.append({
            "ticket_id": f"NWB-{n:05d}", "created_at": ts.strftime("%Y-%m-%d %H:%M"),
            "channel": rng.choice(CHANNELS), "customer_id": f"C{cid}",
            "customer_email": f"customer{cid}@example.com", "customer_tier": rng.choice(TIERS),
            "subject": subject, "body": body, "agent_tag": tag,
            "true_category": cat, "true_issue": issue,
        })
    return out


def main() -> None:
    rows = generate()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} synthetic tickets to {OUT}")


if __name__ == "__main__":
    main()
