"""Mocked Stripe integration: server-owned identity, signed truth and persistence."""
import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app import billing
from app.billing import BillingStore, create_billing_router


SECRET = "whsec_isolated_test_webhook_secret"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    for key in billing._configuration():
        monkeypatch.delenv(key, raising=False)
    store = BillingStore(tmp_path / "billing.sqlite3")
    app = FastAPI()

    @app.middleware("http")
    async def fixture_identity(request, call_next):
        identity = request.headers.get("x-test-user")
        if identity:
            request.state.filo_user = {"id": identity, "email": request.headers.get("x-test-email", identity + "@example.test"), "name": identity.title()}
        return await call_next(request)

    app.include_router(create_billing_router(store))
    with TestClient(app) as client:
        yield store, client


def configured(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_isolated_never_real")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_server_only")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("FILO_PUBLIC_URL", "https://filo.example.test")


def headers(user="alice"):
    return {"x-test-user": user}


def signed(event, timestamp=None, signature=None):
    payload = json.dumps(event, separators=(",", ":")).encode()
    stamp = int(time.time()) if timestamp is None else timestamp
    mac = signature or hmac.new(SECRET.encode(), str(stamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return payload, {"stripe-signature": f"t={stamp},v1={mac}"}


def event(event_id="evt_1", kind="customer.subscription.updated", *, created=None, **obj):
    return {
        "id": event_id, "type": kind,
        "created": int(time.time()) if created is None else created,
        "data": {"object": {
            "id": "sub_alice", "customer": "cus_alice", "status": "active",
            "metadata": {"user_id": "alice"}, "cancel_at_period_end": False,
            "current_period_end": int(time.time()) + 86400, **obj,
        }},
    }


def send_event(client, value, **signature_options):
    payload, signature = signed(value, **signature_options)
    return client.post("/api/billing/webhook", content=payload, headers=signature)


def state(client, user="alice"):
    response = client.get("/api/billing/status", headers=headers(user))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("path, method", [("status", "get"), ("checkout", "post"), ("portal", "post")])
def test_billing_requires_an_authenticated_server_identity(environment, path, method):
    _, client = environment
    response = getattr(client, method)("/api/billing/" + path)
    assert response.status_code == 401
    assert "www-authenticate" not in response.headers


def test_unconfigured_billing_is_truthful_and_registration_never_implies_payment(environment, monkeypatch):
    _, client = environment
    result = state(client)
    assert result["configured"] is False
    assert result["subscription"] == {"status": "free", "cancel_at_period_end": False, "current_period_end": None}
    assert result["plan"]["name"] == "Filo"
    assert result["plan"]["price_label"] is None
    assert result["portal_available"] is False
    for path in ("checkout", "portal"):
        response = client.post("/api/billing/" + path, headers=headers())
        assert response.status_code == 503
        assert response.json()["detail"] == billing.NOT_CONFIGURED
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_isolated_never_real")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_server_only")
    assert state(client)["configured"] is False  # A signed webhook is required too.


def test_plan_label_can_be_set_but_price_is_never_invented(environment, monkeypatch):
    _, client = environment
    configured(monkeypatch)
    monkeypatch.setenv("FILO_PLAN_LABEL", "Filo Studio")
    assert state(client)["plan"]["name"] == "Filo Studio"
    assert state(client)["plan"]["price_label"] is None
    monkeypatch.setenv("FILO_PLAN_PRICE_LABEL", "Importo approvato nel listino")
    assert state(client)["plan"]["price_label"] == "Importo approvato nel listino"


def test_checkout_owns_price_customer_user_and_return_urls_and_reuses_durable_session(environment, monkeypatch):
    store, client = environment
    configured(monkeypatch)
    calls = []

    async def fake_stripe(method, path, config, **options):
        calls.append((method, path, options))
        if path == "/customers":
            return {"id": "cus_alice"}
        assert path == "/checkout/sessions"
        return {"id": "cs_test_alice", "url": "https://checkout.stripe.com/c/pay/alice", "expires_at": int(time.time()) + 3600}

    monkeypatch.setattr(billing, "_stripe_request", fake_stripe)
    attacker_payload = {"price_id": "price_free", "customer": "cus_bob", "user_id": "bob", "success_url": "https://attacker.example.test"}
    response = client.post("/api/billing/checkout", headers=headers(), json=attacker_payload)
    assert response.status_code == 200
    assert response.json() == {"url": "https://checkout.stripe.com/c/pay/alice"}
    assert store.customer_for_user("alice") == "cus_alice"
    assert store.customer_for_user("bob") is None
    customer_data = calls[0][2]["data"]
    assert customer_data == {"metadata[user_id]": "alice", "email": "alice@example.test", "name": "Alice"}
    checkout_data = calls[1][2]["data"]
    assert checkout_data["customer"] == "cus_alice"
    assert checkout_data["line_items[0][price]"] == "price_server_only"
    assert checkout_data["metadata[user_id]"] == checkout_data["subscription_data[metadata][user_id]"] == "alice"
    assert checkout_data["mode"] == "subscription"
    assert checkout_data["success_url"] == "https://filo.example.test/account?billing=success"
    assert checkout_data["cancel_url"] == "https://filo.example.test/account?billing=cancelled"
    assert calls[0][2]["idempotency_key"] and calls[1][2]["idempotency_key"]
    assert client.post("/api/billing/checkout", headers=headers()).json() == response.json()
    assert len(calls) == 2
    reopened = BillingStore(store.path)
    assert reopened.customer_for_user("alice") == "cus_alice"
    assert reopened.checkout_attempt("alice", "price_server_only")["url"] == response.json()["url"]
    # Returning from checkout is not evidence of a subscription.
    assert state(client)["subscription_status"] == "free"


def test_concurrent_checkout_uses_one_customer_and_one_session(environment, monkeypatch):
    _, client = environment
    configured(monkeypatch)
    calls = []

    async def fake_stripe(method, path, config, **options):
        calls.append(path)
        await asyncio.sleep(0.03)
        return {"id": "cus_alice"} if path == "/customers" else {
            "id": "cs_test_alice", "url": "https://checkout.stripe.com/c/pay/alice", "expires_at": int(time.time()) + 3600,
        }

    monkeypatch.setattr(billing, "_stripe_request", fake_stripe)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://testserver") as browser:
            return await asyncio.gather(*(browser.post("/api/billing/checkout", headers=headers()) for _ in range(3)))

    results = asyncio.run(run())
    assert all(response.status_code == 200 for response in results)
    assert calls == ["/customers", "/checkout/sessions"]


def test_legacy_owner_username_is_not_sent_as_an_invalid_stripe_email(environment, monkeypatch):
    _, client = environment
    configured(monkeypatch)
    calls = []

    async def fake_stripe(method, path, config, **options):
        calls.append((path, options))
        return {"id": "cus_owner"} if path == "/customers" else {
            "id": "cs_test_owner", "url": "https://checkout.stripe.com/c/pay/owner", "expires_at": int(time.time()) + 3600,
        }

    monkeypatch.setattr(billing, "_stripe_request", fake_stripe)
    response = client.post("/api/billing/checkout", headers={"x-test-user": "owner", "x-test-email": "filo"})
    assert response.status_code == 200
    assert calls[0][1]["data"] == {"metadata[user_id]": "owner", "name": "Owner"}


@pytest.mark.parametrize("origin", ["https://attacker.test/path", "http://filo.example.test", "https://user:secret@filo.test", "https://filo.test?next=https://attacker.test"])
def test_invalid_public_origin_cannot_create_a_customer(environment, monkeypatch, origin):
    _, client = environment
    configured(monkeypatch)
    monkeypatch.setenv("FILO_PUBLIC_URL", origin)
    response = client.post("/api/billing/checkout", headers=headers())
    assert response.status_code == 503
    assert "indirizzo di ritorno" in response.json()["detail"]


def test_portal_uses_only_current_users_customer_even_when_browser_supplies_another(environment, monkeypatch):
    store, client = environment
    configured(monkeypatch)
    store.save_customer("alice", "cus_alice")
    store.save_customer("bob", "cus_bob")
    calls = []

    async def fake_stripe(method, path, config, **options):
        calls.append((path, options))
        return {"url": "https://billing.stripe.com/p/session/alice"}

    monkeypatch.setattr(billing, "_stripe_request", fake_stripe)
    assert client.post("/api/billing/portal", headers=headers(), json={"customer": "cus_bob"}).status_code == 200
    assert calls[0][1]["data"] == {"customer": "cus_alice", "return_url": "https://filo.example.test/account?billing=returned"}
    assert state(client)["portal_available"] is True
    assert client.post("/api/billing/portal", headers=headers("charlie")).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize("path, bad_host", [("/checkout/sessions", "https://checkout.stripe.com.attacker.test/"), ("/billing_portal/sessions", "javascript:alert(1)")])
def test_provider_redirect_is_validated(environment, monkeypatch, path, bad_host):
    store, client = environment
    configured(monkeypatch)
    store.save_customer("alice", "cus_alice")

    async def fake_stripe(method, called_path, config, **options):
        assert called_path == path
        return {"id": "cs_test_alice", "url": bad_host, "expires_at": int(time.time()) + 3600}

    monkeypatch.setattr(billing, "_stripe_request", fake_stripe)
    endpoint = "checkout" if path.startswith("/checkout") else "portal"
    assert client.post("/api/billing/" + endpoint, headers=headers()).status_code == 503


@pytest.mark.parametrize("signature_options", [{"signature": "0" * 64}, {"timestamp": 1}, {"timestamp": 9999999999}])
def test_bad_or_replayed_signature_cannot_change_billing(environment, monkeypatch, signature_options):
    store, client = environment
    configured(monkeypatch)
    store.save_customer("alice", "cus_alice")
    response = send_event(client, event(), **signature_options)
    assert response.status_code == 400
    assert state(client)["subscription_status"] == "free"
    assert not store.event_seen("evt_1")


def test_webhook_requires_raw_body_and_accepts_rotated_v1_signatures(environment, monkeypatch):
    store, client = environment
    configured(monkeypatch)
    store.save_customer("alice", "cus_alice")
    payload, signature = signed(event())
    assert client.post("/api/billing/webhook", content=payload + b" ", headers=signature).status_code == 400
    signature["stripe-signature"] += ",v1=" + "f" * 64
    assert client.post("/api/billing/webhook", content=payload, headers=signature).status_code == 200
    assert state(client)["subscription_status"] == "active"


def test_signed_webhook_updates_status_once_and_survives_restart(environment, monkeypatch):
    store, client = environment
    configured(monkeypatch)
    store.save_customer("alice", "cus_alice")
    value = event(cancel_at_period_end=True, current_period_end=1792000000)
    assert send_event(client, value).status_code == 200  # No browser session required.
    assert state(client)["subscription"] == {"status": "active", "cancel_at_period_end": True, "current_period_end": 1792000000}
    # Duplicate delivery cannot change the signed first-applied contents.
    modified = event(status="past_due")
    assert send_event(client, modified).status_code == 200
    assert state(client)["subscription_status"] == "active"
    reopened = BillingStore(store.path)
    assert reopened.event_seen("evt_1")
    assert reopened.subscription_for_user("alice")["status"] == "active"
    assert reopened.subscription_for_user("bob")["status"] == "free"
    assert client.post("/api/billing/checkout", headers=headers()).status_code == 409


def test_unknown_or_conflicting_customer_metadata_cannot_attach_a_subscription(environment, monkeypatch):
    store, client = environment
    configured(monkeypatch)
    store.save_customer("alice", "cus_alice")
    store.save_customer("bob", "cus_bob")
    assert send_event(client, event(customer="cus_unknown")).status_code == 200
    assert send_event(client, event("evt_conflict", metadata={"user_id": "bob"})).status_code == 200
    assert state(client)["subscription_status"] == state(client, "bob")["subscription_status"] == "free"
    assert store.event_seen("evt_conflict")


def test_old_events_do_not_overwrite_newer_status_and_canceled_subscription_does_not_revive(environment, monkeypatch):
    store, client = environment
    configured(monkeypatch)
    store.save_customer("alice", "cus_alice")
    assert send_event(client, event("evt_new", created=200, status="past_due")).status_code == 200
    assert send_event(client, event("evt_old", created=100, status="active")).status_code == 200
    assert state(client)["subscription_status"] == "past_due"
    assert send_event(client, event("evt_delete", "customer.subscription.deleted", created=300)).status_code == 200
    assert state(client)["subscription_status"] == "canceled"
    assert send_event(client, event("evt_after_delete", created=400, status="active")).status_code == 200
    assert state(client)["subscription_status"] == "canceled"
    # A genuinely new subscription is independent of the old cancellation.
    assert send_event(client, event("evt_other_subscription", created=350, id="sub_new", status="active")).status_code == 200
    assert send_event(client, event("evt_late_old_delete", "customer.subscription.deleted", created=500)).status_code == 200
    assert state(client)["subscription_status"] == "active"


@pytest.mark.parametrize("kind, status", [("checkout.session.completed", "incomplete"), ("invoice.payment_failed", "past_due"), ("invoice.paid", "active")])
def test_checkout_and_invoice_events_retrieve_actual_subscription_not_query_or_payment_label(environment, monkeypatch, kind, status):
    store, client = environment
    configured(monkeypatch)
    store.save_customer("alice", "cus_alice")
    calls = []

    async def fake_stripe(method, path, config, **options):
        calls.append((method, path))
        return event(status=status)["data"]["object"]

    monkeypatch.setattr(billing, "_stripe_request", fake_stripe)
    value = event(kind=kind, id="cs_test_alice" if kind.startswith("checkout") else "in_alice", mode="subscription", subscription="sub_alice", payment_status="paid")
    assert send_event(client, value).status_code == 200
    assert calls == [("GET", "/subscriptions/sub_alice")]
    assert state(client)["subscription_status"] == status


def test_subscription_retrieval_failure_is_retryable_and_event_is_not_acknowledged(environment, monkeypatch):
    store, client = environment
    configured(monkeypatch)
    store.save_customer("alice", "cus_alice")

    async def fake_stripe(*args, **kwargs):
        from fastapi import HTTPException
        raise HTTPException(503, billing.PROVIDER_UNAVAILABLE)

    monkeypatch.setattr(billing, "_stripe_request", fake_stripe)
    assert send_event(client, event(kind="invoice.payment_failed", subscription="sub_alice")).status_code == 503
    assert not store.event_seen("evt_1")
    assert state(client)["subscription_status"] == "free"


def test_stripe_http_failures_hide_provider_response_and_secret(monkeypatch):
    configured(monkeypatch)
    seen = []
    real_client = httpx.AsyncClient

    def handler(request):
        seen.append(request)
        return httpx.Response(401, json={"error": {"message": "sk_test_isolated_never_real sensitive internal error"}})

    monkeypatch.setattr(billing.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(Exception) as caught:
        asyncio.run(billing._stripe_request("POST", "/customers", billing._configuration(), data={"metadata[user_id]": "alice"}, idempotency_key="test-key"))
    assert caught.value.status_code == 503
    assert caught.value.detail == billing.PROVIDER_UNAVAILABLE
    assert "sk_test" not in str(caught.value)
    assert seen[0].url.host == "api.stripe.com"
    assert seen[0].headers["stripe-version"] == "2024-06-20"
    assert seen[0].headers["idempotency-key"] == "test-key"
    assert parse_qs(seen[0].content.decode()) == {"metadata[user_id]": ["alice"]}


def test_webhook_size_limit_and_malformed_payload_do_not_create_events(environment, monkeypatch):
    store, client = environment
    configured(monkeypatch)
    assert client.post("/api/billing/webhook", content=b"x" * (billing.MAX_WEBHOOK_BYTES + 1)).status_code == 413
    for value in ({}, {**event(), "created": True}, {**event(), "data": {"object": []}}):
        payload, signature = signed(value)
        assert client.post("/api/billing/webhook", content=payload, headers=signature).status_code == 400
    assert not store.event_seen("evt_1")


def test_checkout_attempt_idempotency_survives_retry_but_new_price_creates_new_attempt(environment):
    store, _ = environment
    first = store.checkout_attempt("alice", "price_a", now=100)
    assert BillingStore(store.path).checkout_attempt("alice", "price_a", now=200)["idempotency_key"] == first["idempotency_key"]
    changed = store.checkout_attempt("alice", "price_b", now=200)
    assert changed["idempotency_key"] != first["idempotency_key"]
    expired = store.checkout_attempt("alice", "price_b", now=4000)
    assert expired["idempotency_key"] != changed["idempotency_key"]
