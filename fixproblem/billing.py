"""Payments through Stripe Checkout, with a demo mode for local use only.

Set STRIPE_SECRET_KEY (a *test* key, sk_test_...) to take payments through Stripe's hosted checkout.
Card details are entered on Stripe's page and never touch this application.

Payment ownership is never taken from the browser. A checkout only activates Pro when Stripe confirms:
  * the session was paid,
  * it was created for THIS account (client_reference_id),
  * the amount, currency and product match what this account was offered.
Webhooks (STRIPE_WEBHOOK_SECRET) are verified with Stripe's signature scheme, and each event and each
checkout session is processed at most once.

This version sells a one-month pass per payment. Recurring Stripe Subscriptions are planned.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

API = "https://api.stripe.com/v1"
PRODUCT = "Fixproblem Team (1 month)"
CURRENCY = "gbp"
SIGNATURE_TOLERANCE = 300          # seconds


class PaymentError(Exception):
    """The payment could not be verified for this account."""


def stripe_enabled() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY"))


def _call(method: str, path: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(f"{API}{path}", method=method,
                                 data=urllib.parse.urlencode(data).encode() if data else None,
                                 headers={"Authorization": f"Bearer {os.environ['STRIPE_SECRET_KEY']}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def create_checkout(user_id: int, email: str, amount_pence: int, success_url: str, cancel_url: str) -> str:
    """Create a Stripe Checkout Session tied to this account and return its URL."""
    s = _call("POST", "/checkout/sessions", {
        "mode": "payment", "customer_email": email, "client_reference_id": str(user_id),
        "metadata[user_id]": str(user_id), "metadata[product]": PRODUCT,
        "line_items[0][quantity]": 1,
        "line_items[0][price_data][currency]": CURRENCY,
        "line_items[0][price_data][unit_amount]": amount_pence,
        "line_items[0][price_data][product_data][name]": PRODUCT,
        "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}", "cancel_url": cancel_url})
    return s["url"]


def fetch_session(session_id: str) -> dict:
    return _call("GET", f"/checkout/sessions/{urllib.parse.quote(session_id)}")


def check_session(s: dict, user_id: int, expected_amount: int) -> None:
    """Raise PaymentError unless the session is a paid checkout for this account and this price."""
    if s.get("payment_status") != "paid":
        raise PaymentError("payment not completed")
    if str(s.get("client_reference_id")) != str(user_id):
        raise PaymentError("checkout belongs to a different account")
    if int(s.get("amount_total") or -1) != int(expected_amount):
        raise PaymentError("amount does not match the price offered")
    if (s.get("currency") or "").lower() != CURRENCY:
        raise PaymentError("unexpected currency")
    if (s.get("metadata") or {}).get("product") != PRODUCT:
        raise PaymentError("unexpected product")


def verify_webhook(payload: bytes, sig_header: str, secret: str, now: float | None = None) -> dict:
    """Verify a Stripe webhook signature (t=timestamp, v1=HMAC-SHA256) and return the event."""
    parts = dict(p.split("=", 1) for p in (sig_header or "").split(",") if "=" in p)
    try:
        ts = int(parts.get("t", ""))
    except ValueError as exc:
        raise PaymentError("missing timestamp") from exc
    if abs((now or time.time()) - ts) > SIGNATURE_TOLERANCE:
        raise PaymentError("signature timestamp outside tolerance")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    sigs = [v for k, v in (p.split("=", 1) for p in sig_header.split(",") if "=" in p) if k == "v1"]
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise PaymentError("invalid signature")
    return json.loads(payload)
