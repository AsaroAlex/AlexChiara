"""Server-side Stripe subscriptions; registration itself never charges a card.

The browser cannot choose a price, customer or subscription. The signed webhook
is the only writer of subscription state; a checkout return URL proves nothing.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
import httpx


STRIPE_API = "https://api.stripe.com/v1"
MAX_WEBHOOK_BYTES = 512 * 1024
SIGNATURE_TOLERANCE = 300
CHECKOUT_MIN_LIFETIME = 31 * 60
SUBSCRIPTION_STATES = {
    "active", "trialing", "past_due", "canceled", "unpaid", "incomplete",
    "incomplete_expired", "paused",
}
LIVE_SUBSCRIPTION_STATES = SUBSCRIPTION_STATES - {"canceled", "incomplete_expired"}
NOT_CONFIGURED = "I pagamenti non sono ancora attivi. Puoi continuare a usare il tuo account."
PROVIDER_UNAVAILABLE = "Il servizio di pagamento non è disponibile. Riprova tra poco."


def _configuration():
    return {key: os.environ.get(key, "").strip() for key in (
        "STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET",
        "FILO_PUBLIC_URL", "FILO_PLAN_LABEL", "FILO_PLAN_PRICE_LABEL",
    )}


def _configured(config):
    return all(config[key] for key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"))


def _user(request):
    user = getattr(request.state, "filo_user", None)
    if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"]:
        raise HTTPException(401, "Accedi al tuo account per continuare.")
    return user


def _provider_id(value, prefix):
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) and re.fullmatch(prefix + r"[A-Za-z0-9_]{1,240}", value) else None


def _public_origin(request, config):
    value = config["FILO_PUBLIC_URL"] or str(request.base_url)
    try:
        parsed = urlsplit(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1", "testserver"}
        if (
            not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
        ):
            raise ValueError
        return parsed.scheme + "://" + parsed.netloc
    except ValueError:
        raise HTTPException(503, "L'indirizzo di ritorno del pagamento non è ancora configurato.") from None


def _redirect_url(value, hostname):
    try:
        parsed = urlsplit(value if isinstance(value, str) else "")
        if parsed.scheme == "https" and parsed.hostname == hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    raise HTTPException(503, PROVIDER_UNAVAILABLE)


class StripeResourceMissing(HTTPException):
    """Stripe no longer knows an object, e.g. a customer deleted in the Dashboard
    or created with the other (test/live) key. Uncaught, it is a plain 503."""

    def __init__(self, param):
        super().__init__(503, PROVIDER_UNAVAILABLE)
        self.param = param


def _live(config):
    return config["STRIPE_SECRET_KEY"].startswith(("sk_live_", "rk_live_"))


async def _stripe_request(method, path, config, *, data=None, idempotency_key=None):
    """Do not relay Stripe bodies, credentials or stack traces to the browser."""
    if not config["STRIPE_SECRET_KEY"]:
        raise HTTPException(503, NOT_CONFIGURED)
    headers = {"Stripe-Version": "2024-06-20"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False) as client:
            response = await client.request(
                method, STRIPE_API + path, auth=(config["STRIPE_SECRET_KEY"], ""),
                headers=headers, data=data,
            )
        if not response.is_success:
            if response.status_code == 404:
                try:
                    error = response.json().get("error", {})
                except (ValueError, AttributeError):
                    error = {}
                if isinstance(error, dict) and error.get("code") == "resource_missing":
                    raise StripeResourceMissing(str(error.get("param") or ""))
            raise HTTPException(503, PROVIDER_UNAVAILABLE)
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, PROVIDER_UNAVAILABLE) from None


class BillingStore:
    """Separate durable billing ledger; never stores payment-card details."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS billing_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS customers (
                    user_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS checkout_attempts (
                    user_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL,
                    price_id TEXT NOT NULL, expires_at INTEGER NOT NULL,
                    checkout_id TEXT, url TEXT
                );
                CREATE TABLE IF NOT EXISTS subscriptions (
                    subscription_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL, status TEXT NOT NULL,
                    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                    current_period_end INTEGER, event_created INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS subscriptions_by_user ON subscriptions(user_id, event_created DESC);
                CREATE TABLE IF NOT EXISTS webhook_events (
                    event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
                    received_at INTEGER NOT NULL
                );
            """)
            # Test and live keys see different Stripe objects: tag each mapping.
            for table in ("customers", "subscriptions"):
                if "livemode" not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN livemode INTEGER")
            conn.execute("INSERT OR IGNORE INTO billing_settings VALUES ('installation_id', ?)", (secrets.token_hex(16),))
            self.installation_id = conn.execute("SELECT value FROM billing_settings WHERE key='installation_id'").fetchone()["value"]
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def customer_record(self, user_id):
        with self.connection() as conn:
            row = conn.execute("SELECT customer_id,livemode FROM customers WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def customer_for_user(self, user_id, livemode=None):
        row = self.customer_record(user_id)
        if not row or (livemode is not None and row["livemode"] is not None and row["livemode"] != int(livemode)):
            return None
        return row["customer_id"]

    def save_customer(self, user_id, customer_id, livemode=None, replaces=None):
        livemode = None if livemode is None else int(livemode)
        with self.connection() as conn:
            if replaces:
                conn.execute("UPDATE customers SET customer_id=?,livemode=? WHERE user_id=? AND customer_id=?", (customer_id, livemode, user_id, replaces))
            conn.execute("INSERT OR IGNORE INTO customers(user_id,customer_id,livemode) VALUES (?,?,?)", (user_id, customer_id, livemode))
            row = conn.execute("SELECT customer_id FROM customers WHERE user_id=?", (user_id,)).fetchone()
            if not row or row["customer_id"] != customer_id:
                raise HTTPException(503, PROVIDER_UNAVAILABLE)

    def subscription_for_user(self, user_id, livemode=None):
        with self.connection() as conn:
            rows = conn.execute("""SELECT * FROM subscriptions WHERE user_id=? AND (? IS NULL OR livemode IS NULL OR livemode=?)
                ORDER BY event_created DESC""", (user_id, None if livemode is None else int(livemode), None if livemode is None else int(livemode))).fetchall()
        # A late cancellation of an old subscription cannot hide a newer one.
        rank = {"active": 0, "trialing": 1, "past_due": 2, "unpaid": 3, "paused": 4, "incomplete": 5}
        row = min(rows, key=lambda item: (rank.get(item["status"], 10), -item["event_created"])) if rows else None
        return {
            "status": row["status"] if row else "free",
            "cancel_at_period_end": bool(row["cancel_at_period_end"]) if row else False,
            "current_period_end": row["current_period_end"] if row else None,
        }

    def checkout_attempt(self, user_id, price_id, now=None):
        now = int(time.time()) if now is None else now
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM checkout_attempts WHERE user_id=?", (user_id,)).fetchone()
            # Stripe requires at least 30 minutes until expiry: an attempt that
            # never produced a session is restarted rather than resent too late.
            if not row or row["expires_at"] <= now or row["price_id"] != price_id or (not row["url"] and row["expires_at"] - now < CHECKOUT_MIN_LIFETIME):
                conn.execute("INSERT INTO checkout_attempts VALUES (?, ?, ?, ?, NULL, NULL) ON CONFLICT(user_id) DO UPDATE SET idempotency_key=excluded.idempotency_key,price_id=excluded.price_id,expires_at=excluded.expires_at,checkout_id=NULL,url=NULL", (
                    user_id, "filo-checkout-" + secrets.token_hex(24), price_id, now + 3600,
                ))
                row = conn.execute("SELECT * FROM checkout_attempts WHERE user_id=?", (user_id,)).fetchone()
        return dict(row)

    def reset_checkout(self, user_id):
        with self.connection() as conn:
            conn.execute("DELETE FROM checkout_attempts WHERE user_id=?", (user_id,))

    def subscription_event_created(self, subscription_id):
        with self.connection() as conn:
            row = conn.execute("SELECT event_created,status FROM subscriptions WHERE subscription_id=?", (subscription_id,)).fetchone()
        return dict(row) if row else None

    def forget_user(self, user_id):
        """Delete the account's billing rows; processed webhook ids hold no personal data."""
        with self.connection() as conn:
            for table in ("customers", "subscriptions", "checkout_attempts"):
                conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))

    def save_checkout(self, user_id, key, checkout_id, url, expires_at):
        with self.connection() as conn:
            conn.execute("UPDATE checkout_attempts SET checkout_id=?,url=?,expires_at=? WHERE user_id=? AND idempotency_key=?", (checkout_id, url, expires_at, user_id, key))

    def event_seen(self, event_id):
        with self.connection() as conn:
            return conn.execute("SELECT 1 FROM webhook_events WHERE event_id=?", (event_id,)).fetchone() is not None

    def apply_event(self, event, subscription=None, completed_checkout=None):
        """Mapping checks, deduplication and subscription writes are one transaction."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM webhook_events WHERE event_id=?", (event["id"],)).fetchone():
                return False
            if subscription:
                customer = _provider_id(subscription.get("customer"), "cus_")
                subscription_id = _provider_id(subscription.get("id"), "sub_")
                row = conn.execute("SELECT user_id FROM customers WHERE customer_id=?", (customer,)).fetchone()
                metadata = subscription.get("metadata") or {}
                claimed_user = metadata.get("user_id") if isinstance(metadata, dict) else None
                status = subscription.get("status")
                if row and subscription_id and status in SUBSCRIPTION_STATES and claimed_user in (None, "", row["user_id"]):
                    previous = conn.execute("SELECT * FROM subscriptions WHERE subscription_id=?", (subscription_id,)).fetchone()
                    consistent_owner = not previous or (previous["user_id"] == row["user_id"] and previous["customer_id"] == customer)
                    not_stale = not previous or event["created"] >= previous["event_created"]
                    not_revived = not previous or previous["status"] != "canceled" or status == "canceled"
                    if consistent_owner and not_stale and not_revived:
                        period_end = subscription.get("current_period_end")
                        if not isinstance(period_end, int) or isinstance(period_end, bool) or period_end < 0:
                            period_end = None
                        livemode = event.get("livemode", subscription.get("livemode"))
                        conn.execute("""INSERT INTO subscriptions(subscription_id,user_id,customer_id,status,cancel_at_period_end,current_period_end,event_created,livemode)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(subscription_id) DO UPDATE SET status=excluded.status,cancel_at_period_end=excluded.cancel_at_period_end,current_period_end=excluded.current_period_end,event_created=excluded.event_created,livemode=excluded.livemode""", (
                            subscription_id, row["user_id"], customer, status,
                            int(subscription.get("cancel_at_period_end") is True), period_end, event["created"],
                            int(livemode) if isinstance(livemode, bool) else None,
                        ))
            if completed_checkout:
                conn.execute("UPDATE checkout_attempts SET expires_at=0 WHERE checkout_id=?", (completed_checkout,))
            conn.execute("INSERT INTO webhook_events VALUES (?, ?, ?)", (event["id"], event["type"], int(time.time())))
        return True


def _verify_signature(payload, header, secret, now=None):
    if not header or len(header) > 8192:
        return False
    parts = [item.partition("=") for item in header.split(",")]
    timestamps = [value for name, separator, value in parts if name.strip() == "t" and separator]
    signatures = [value for name, separator, value in parts if name.strip() == "v1" and separator]
    if len(timestamps) != 1:
        return False
    try:
        timestamp = int(timestamps[0])
        if abs((int(time.time()) if now is None else now) - timestamp) > SIGNATURE_TOLERANCE:
            return False
    except (ValueError, OverflowError):
        return False
    expected = hmac.new(secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return any(re.fullmatch(r"[a-fA-F0-9]{64}", item) and hmac.compare_digest(expected, item.lower()) for item in signatures)


async def erase_customer(store, user_id):
    """Before an account is deleted: cancel billing at Stripe, then forget it locally.

    Deleting the Stripe customer immediately cancels its subscriptions; Stripe
    keeps issued invoices for accounting. Without Stripe keys an active
    subscription cannot be cancelled here, so deletion stops with a clear message.
    """
    record = store.customer_record(user_id)
    if record:
        config = _configuration()
        if _configured(config):
            try:
                await _stripe_request("DELETE", "/customers/" + record["customer_id"], config)
            except StripeResourceMissing:
                pass  # Already deleted, or created with the other (test/live) key.
        elif store.subscription_for_user(user_id)["status"] in LIVE_SUBSCRIPTION_STATES:
            raise HTTPException(409, "Il tuo abbonamento risulta attivo e i pagamenti non sono raggiungibili in questo momento. Contatta l'assistenza di Spazelia per chiudere l'account.")
    store.forget_user(user_id)


def create_billing_router(store):
    router = APIRouter(prefix="/api/billing", tags=["billing"])
    user_locks = {}

    @router.get("/status")
    async def billing_status(request: Request):
        user = _user(request)
        config = _configuration()
        live = _live(config) if _configured(config) else None
        subscription = store.subscription_for_user(user["id"], live)
        return {
            "configured": _configured(config),
            "portal_available": _configured(config) and bool(store.customer_for_user(user["id"], live)),
            "subscription_status": subscription["status"], "subscription": subscription,
            "plan": {
                "name": config["FILO_PLAN_LABEL"] or "Spazelia",
                "price_label": config["FILO_PLAN_PRICE_LABEL"] or None,
                "features": ["Priorità email, avvisi e agenda", "Clienti, preventivi e incassi", "Fornitori, acquisti e commesse", "Scadenze e documenti da rivedere"],
            },
        }

    @router.post("/checkout")
    async def checkout(request: Request):
        user = _user(request)
        config = _configuration()
        if not _configured(config):
            raise HTTPException(503, NOT_CONFIGURED)
        origin = _public_origin(request, config)
        if not _provider_id(config["STRIPE_PRICE_ID"], "price_"):
            raise HTTPException(503, NOT_CONFIGURED)
        live = _live(config)

        async def new_customer(replaces=None):
            customer_data = {"metadata[user_id]": user["id"]}
            if isinstance(user.get("email"), str) and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", user["email"]):
                customer_data["email"] = user["email"]
            if user.get("name"):
                customer_data["name"] = user["name"]
            # A replacement (other key mode, or deleted in the Dashboard) needs
            # its own idempotency key, or Stripe would return the old customer.
            seed = store.installation_id + ":" + user["id"] + ("" if not replaces else ":" + ("live" if live else "test") + ":" + replaces)
            customer = await _stripe_request("POST", "/customers", config, data=customer_data,
                idempotency_key="filo-customer-" + hashlib.sha256(seed.encode()).hexdigest())
            created = _provider_id(customer.get("id"), "cus_")
            if not created:
                raise HTTPException(503, PROVIDER_UNAVAILABLE)
            store.save_customer(user["id"], created, live, replaces=replaces)
            return created

        async def create_session(customer_id):
            attempt = store.checkout_attempt(user["id"], config["STRIPE_PRICE_ID"])
            if attempt["url"]:
                return attempt, None
            return attempt, await _stripe_request("POST", "/checkout/sessions", config, data={
                "mode": "subscription", "customer": customer_id,
                "line_items[0][price]": config["STRIPE_PRICE_ID"], "line_items[0][quantity]": "1",
                "client_reference_id": user["id"], "metadata[user_id]": user["id"],
                "subscription_data[metadata][user_id]": user["id"],
                "success_url": origin + "/account?billing=success", "cancel_url": origin + "/account?billing=cancelled",
                "expires_at": str(attempt["expires_at"]),
            }, idempotency_key=attempt["idempotency_key"])

        async with user_locks.setdefault(user["id"], asyncio.Lock()):
            if store.subscription_for_user(user["id"], live)["status"] in LIVE_SUBSCRIPTION_STATES:
                raise HTTPException(409, "Hai già un abbonamento. Gestiscilo dalla tua area personale.")
            customer_id = store.customer_for_user(user["id"], live)
            if not customer_id:
                previous = store.customer_record(user["id"])
                customer_id = await new_customer(previous["customer_id"] if previous else None)
            try:
                attempt, session = await create_session(customer_id)
            except StripeResourceMissing as missing:
                if missing.param != "customer":
                    raise
                # The saved customer no longer exists for this key: start over once.
                store.reset_checkout(user["id"])
                customer_id = await new_customer(customer_id)
                attempt, session = await create_session(customer_id)
            if session is None:
                return {"url": _redirect_url(attempt["url"], "checkout.stripe.com")}
            url = _redirect_url(session.get("url"), "checkout.stripe.com")
            session_id = _provider_id(session.get("id"), "cs_")
            expires_at = session.get("expires_at")
            if not session_id or not isinstance(expires_at, int) or isinstance(expires_at, bool) or expires_at <= int(time.time()):
                raise HTTPException(503, PROVIDER_UNAVAILABLE)
            store.save_checkout(user["id"], attempt["idempotency_key"], session_id, url, expires_at)
            return {"url": url}

    @router.post("/portal")
    async def portal(request: Request):
        user = _user(request)
        config = _configuration()
        if not _configured(config):
            raise HTTPException(503, NOT_CONFIGURED)
        customer = store.customer_for_user(user["id"], _live(config))
        if not customer:
            raise HTTPException(409, "Non hai ancora un abbonamento da gestire.")
        try:
            session = await _stripe_request("POST", "/billing_portal/sessions", config, data={
                "customer": customer, "return_url": _public_origin(request, config) + "/account?billing=returned",
            })
        except StripeResourceMissing:
            raise HTTPException(409, "Non hai ancora un abbonamento da gestire.") from None
        return {"url": _redirect_url(session.get("url"), "billing.stripe.com")}

    @router.post("/webhook")
    async def webhook(request: Request):
        config = _configuration()
        if not config["STRIPE_WEBHOOK_SECRET"]:
            raise HTTPException(503, NOT_CONFIGURED)
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "Notifica di pagamento troppo grande.")
            chunks.append(chunk)
        payload = b"".join(chunks)
        if not _verify_signature(payload, request.headers.get("stripe-signature"), config["STRIPE_WEBHOOK_SECRET"]):
            raise HTTPException(400, "Firma della notifica di pagamento non valida.")
        try:
            event = json.loads(payload)
            if (
                not isinstance(event, dict) or not _provider_id(event.get("id"), "evt_")
                or not isinstance(event.get("type"), str) or len(event["type"]) > 255
                or not isinstance(event.get("created"), int) or isinstance(event["created"], bool) or event["created"] < 0
                or not isinstance(event.get("data"), dict) or not isinstance(event["data"].get("object"), dict)
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(400, "Notifica di pagamento non valida.") from None
        if store.event_seen(event["id"]):
            return {"received": True}
        obj = event["data"]["object"]
        subscription, completed_checkout = None, None
        if event["type"] in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
            subscription = dict(obj)
            if event["type"] == "customer.subscription.deleted":
                subscription["status"] = "canceled"
            else:
                subscription_id = _provider_id(obj.get("id"), "sub_")
                stored = store.subscription_event_created(subscription_id) if subscription_id else None
                if stored and stored["event_created"] == event["created"] and stored["status"] != obj.get("status"):
                    # Events of the same second arrive in any order: ask Stripe
                    # for the current state instead of trusting the last delivery.
                    subscription = await _stripe_request("GET", "/subscriptions/" + subscription_id, config)
        elif event["type"] in {"checkout.session.completed", "checkout.session.async_payment_succeeded"} and obj.get("mode") == "subscription":
            subscription_id = _provider_id(obj.get("subscription"), "sub_")
            if subscription_id:
                subscription = await _stripe_request("GET", "/subscriptions/" + subscription_id, config)
            completed_checkout = _provider_id(obj.get("id"), "cs_")
        elif event["type"] in {"invoice.payment_failed", "invoice.paid", "invoice.payment_succeeded"}:
            details = (obj.get("parent") or {}).get("subscription_details", {}) if isinstance(obj.get("parent"), dict) else {}
            subscription_id = _provider_id(obj.get("subscription") or details.get("subscription"), "sub_")
            if subscription_id:
                subscription = await _stripe_request("GET", "/subscriptions/" + subscription_id, config)
        elif event["type"] == "checkout.session.expired":
            completed_checkout = _provider_id(obj.get("id"), "cs_")
        store.apply_event(event, subscription, completed_checkout)
        return {"received": True}

    return router
