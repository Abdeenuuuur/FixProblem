"""Send new emerging-issue alerts to a chat webhook (Slack, Microsoft Teams or any
endpoint accepting JSON). Each alert is sent once; failures are logged, never raised,
so a chat outage can never break data import."""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)


def format_alert(a) -> str:
    return (f":rotating_light: Fixproblem emerging issue in {a['category'].replace('_', ' ')}: "
            f"{a['recent']} tickets in the recent window vs {a['expected']:.1f} expected "
            f"({a['growth']:.0f}x). {a['summary']}")


def post(url: str, text: str, sender=None, timeout: int = 8) -> tuple[bool, str]:
    sender = sender or _http_post
    try:
        sender(url, {"text": text}, timeout)
        return True, "sent"
    except Exception as exc:          # network, DNS, 4xx/5xx, timeout
        log.warning("webhook failed: %s", exc)
        return False, str(exc)[:200]


def _http_post(url: str, payload: dict, timeout: int) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


def notify_new_alerts(conn, url: str | None, sender=None) -> int:
    """Send alerts that have not been sent before. Returns how many were sent."""
    if not url:
        return 0
    sent_keys = {r[0] for r in conn.execute("SELECT alert_key FROM notifications WHERE ok=1")}
    n = 0
    for a in conn.execute("SELECT * FROM alerts").fetchall():
        key = f"{a['category']}:{a['first_seen']}"
        if key in sent_keys:
            continue
        ok, detail = post(url, format_alert(a), sender)
        conn.execute("INSERT INTO notifications (channel, alert_key, ok, detail) VALUES (?,?,?,?)",
                     ("webhook", key, int(ok), detail))
        n += ok
    conn.commit()
    return n
