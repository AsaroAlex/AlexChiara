"""Password recovery, password change and account deletion on the full application.

Emails go to a temporary outbox folder; Stripe is mocked. No provider is contacted.
"""
from email import policy
from email.parser import BytesParser
import re
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from app import billing, mailer
from app.main import create_app


OWNER_PASSWORD = "owner-secret-long"
PASSWORD = "una-password-lunga-12"
NEW_PASSWORD = "nuova-password-sicura-34"


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("FILO_ACCESS_USERNAME", "filo")
    monkeypatch.setenv("FILO_MAIL_FROM", "Spazelia <noreply@spazelia.test>")
    monkeypatch.setenv("FILO_MAIL_OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("FILO_PUBLIC_URL", "https://app.spazelia.test")
    for key in ("FILO_RESEND_API_KEY", "FILO_SMTP_HOST", "STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET", "FILO_REAL_DATA_ONLY"):
        monkeypatch.delenv(key, raising=False)
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield app, client, tmp_path


def csrf(client):
    return client.get("/api/auth/session").json()["csrf_token"]


def post(client, path, body):
    return client.post(path, json=body, headers={"X-CSRF-Token": csrf(client)})


def register(client, email="chiara@example.test"):
    response = post(client, "/api/auth/register", {"name": "Chiara", "email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.get("/api/bootstrap")
    return response.json()["user"]


def login(client, email, password):
    return post(client, "/api/auth/login", {"email": email, "password": password})


def logout(client):
    assert post(client, "/api/auth/logout", {}).status_code == 200


def outbox(tmp_path):
    folder = tmp_path / "outbox"
    return [BytesParser(policy=policy.default).parsebytes(path.read_bytes()) for path in sorted(folder.glob("*.eml"), key=lambda p: p.stat().st_mtime)] if folder.exists() else []


def reset_token(message):
    found = re.search(r"https://app\.spazelia\.test/reset-password\?token=([A-Za-z0-9_-]{43})", message.get_body(("plain",)).get_content())
    assert found, "The email must contain the reset link on the configured public origin."
    return found.group(1)


def test_recovery_pages_are_public(site):
    _, client, _ = site
    for path in ("/forgot-password", "/reset-password"):
        response = client.get(path)
        assert response.status_code == 200
        assert "auth-form" in response.text
    assert client.get("/api/auth/session").json()["password_reset_available"] is True


def test_forgot_password_answers_identically_and_mails_only_members(site):
    _, client, tmp_path = site
    register(client)
    logout(client)
    known = post(client, "/api/auth/password/forgot", {"email": "  CHIARA@example.test "})
    unknown = post(client, "/api/auth/password/forgot", {"email": "nessuno@example.test"})
    owner = post(client, "/api/auth/password/forgot", {"email": "filo"})
    assert known.status_code == unknown.status_code == owner.status_code == 200
    assert known.json() == unknown.json() == owner.json()
    messages = outbox(tmp_path)
    assert len(messages) == 1
    assert messages[0]["To"] == "chiara@example.test"
    assert messages[0]["Subject"] == "Reimposta la password di Spazelia"
    assert "60 minuti" in messages[0].get_body(("plain",)).get_content()
    reset_token(messages[0])


def test_reset_sets_the_password_signs_out_everywhere_and_is_single_use(site):
    app, client, tmp_path = site
    register(client)
    other = TestClient(app)
    other.__enter__()
    try:
        assert login(other, "chiara@example.test", PASSWORD).status_code == 200
        logout(client)
        post(client, "/api/auth/password/forgot", {"email": "chiara@example.test"})
        token = reset_token(outbox(tmp_path)[-1])
        response = post(client, "/api/auth/password/reset", {"token": token, "password": NEW_PASSWORD})
        assert response.status_code == 200, response.text
        assert response.json()["authenticated"] is True
        assert client.get("/api/bootstrap").status_code == 200
        # The other browser, signed in with the old password, is signed out.
        assert other.get("/api/bootstrap").status_code == 401
        replay = post(client, "/api/auth/password/reset", {"token": token, "password": "ancora-un-altra-pass"})
        assert replay.status_code == 400
    finally:
        other.__exit__(None, None, None)
    logout(client)
    assert login(client, "chiara@example.test", PASSWORD).status_code == 401
    assert login(client, "chiara@example.test", NEW_PASSWORD).status_code == 200


def test_only_the_newest_link_works_and_links_expire(site, monkeypatch):
    app, client, tmp_path = site
    register(client)
    logout(client)
    post(client, "/api/auth/password/forgot", {"email": "chiara@example.test"})
    first = reset_token(outbox(tmp_path)[-1])
    time.sleep(0.01)
    post(client, "/api/auth/password/forgot", {"email": "chiara@example.test"})
    second = reset_token(outbox(tmp_path)[-1])
    assert post(client, "/api/auth/password/reset", {"token": first, "password": NEW_PASSWORD}).status_code == 400
    with app.state.accounts.connection() as conn:
        conn.execute("UPDATE password_resets SET expires_at=?", (time.time() - 1,))
    assert post(client, "/api/auth/password/reset", {"token": second, "password": NEW_PASSWORD}).status_code == 400


def test_reset_validates_password_and_token(site):
    _, client, tmp_path = site
    register(client)
    logout(client)
    post(client, "/api/auth/password/forgot", {"email": "chiara@example.test"})
    token = reset_token(outbox(tmp_path)[-1])
    short = post(client, "/api/auth/password/reset", {"token": token, "password": "corta"})
    assert short.status_code == 422
    assert "12" in short.json()["detail"]
    assert post(client, "/api/auth/password/reset", {"token": "x" * 43, "password": NEW_PASSWORD}).status_code == 400
    # A rejected short password does not consume the link.
    assert post(client, "/api/auth/password/reset", {"token": token, "password": NEW_PASSWORD}).status_code == 200


def test_reset_requests_are_throttled(site):
    _, client, tmp_path = site
    register(client)
    logout(client)
    statuses = [post(client, "/api/auth/password/forgot", {"email": "chiara@example.test"}).status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429]
    assert len(outbox(tmp_path)) == 3


def test_unconfigured_email_says_so_without_revealing_accounts(site, monkeypatch):
    _, client, tmp_path = site
    monkeypatch.delenv("FILO_MAIL_OUTBOX_DIR")
    assert client.get("/api/auth/session").json()["password_reset_available"] is False
    response = post(client, "/api/auth/password/forgot", {"email": "chiara@example.test"})
    assert response.status_code == 503
    assert "non è ancora attivo" in response.json()["detail"]


def test_password_change_requires_the_current_password_and_signs_out_other_devices(site):
    app, client, _ = site
    register(client)
    other = TestClient(app)
    other.__enter__()
    try:
        assert login(other, "chiara@example.test", PASSWORD).status_code == 200
        wrong = post(client, "/api/account/password", {"current_password": "sbagliata-del-tutto", "new_password": NEW_PASSWORD})
        assert wrong.status_code == 401
        assert post(client, "/api/account/password", {"current_password": PASSWORD, "new_password": "corta"}).status_code == 422
        assert post(client, "/api/account/password", {"current_password": PASSWORD, "new_password": PASSWORD}).status_code == 422
        changed = post(client, "/api/account/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        assert changed.status_code == 200, changed.text
        assert client.get("/api/bootstrap").status_code == 200
        assert other.get("/api/bootstrap").status_code == 401
    finally:
        other.__exit__(None, None, None)
    logout(client)
    assert login(client, "chiara@example.test", NEW_PASSWORD).status_code == 200


def test_owner_cannot_change_password_or_delete_here(site):
    _, client, _ = site
    assert login(client, "filo", OWNER_PASSWORD).status_code == 200
    assert post(client, "/api/account/password", {"current_password": OWNER_PASSWORD, "new_password": NEW_PASSWORD}).status_code == 409
    deletion = post(client, "/api/account/delete", {"password": OWNER_PASSWORD, "confirmation": "ELIMINA"})
    assert deletion.status_code == 409
    assert login(client, "filo", OWNER_PASSWORD).status_code == 200


def test_account_deletion_erases_account_workspace_and_billing(site, monkeypatch):
    app, client, tmp_path = site
    user = register(client)
    user_id = user["id"]
    assert post(client, "/api/agenda", {"title": "Riunione privata", "starts_at": "2027-01-10T09:00:00+01:00"}).status_code == 201
    workspace_dir = tmp_path / "workspaces" / user_id
    assert (workspace_dir / "alexchiara.sqlite3").exists()
    app.state.billing.save_customer(user_id, "cus_chiara", livemode=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_isolated_never_real")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    calls = []

    async def fake_stripe(method, path, config, **options):
        calls.append((method, path))
        return {"id": "cus_chiara", "deleted": True}

    monkeypatch.setattr(billing, "_stripe_request", fake_stripe)
    assert post(client, "/api/account/delete", {"password": "sbagliata-del-tutto", "confirmation": "ELIMINA"}).status_code == 401
    assert post(client, "/api/account/delete", {"password": PASSWORD, "confirmation": "elimina?"}).status_code == 422
    assert calls == [] and workspace_dir.exists()
    response = post(client, "/api/account/delete", {"password": PASSWORD, "confirmation": "elimina"})
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] is True and response.json()["authenticated"] is False
    assert calls == [("DELETE", "/customers/cus_chiara")]
    assert not workspace_dir.exists()
    assert not list((tmp_path / "workspaces").glob(".deleted-*"))
    assert app.state.accounts.get_user(user_id) is None
    assert app.state.billing.customer_record(user_id) is None
    assert user_id not in app.state.workspaces
    assert client.get("/api/bootstrap").status_code == 401
    assert login(client, "chiara@example.test", PASSWORD).status_code == 401
    # The address can be used again for a brand new, empty account.
    new_user = register(client)
    assert new_user["id"] != user_id
    assert client.get("/api/agenda").json()["items"] == []


def test_stripe_failure_stops_deletion_before_anything_is_erased(site, monkeypatch):
    app, client, tmp_path = site
    user = register(client)
    app.state.billing.save_customer(user["id"], "cus_chiara")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_isolated_never_real")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

    async def unavailable(*_, **__):
        raise HTTPException(503, billing.PROVIDER_UNAVAILABLE)

    monkeypatch.setattr(billing, "_stripe_request", unavailable)
    response = post(client, "/api/account/delete", {"password": PASSWORD, "confirmation": "ELIMINA"})
    assert response.status_code == 503
    assert app.state.accounts.get_user(user["id"]) is not None
    assert (tmp_path / "workspaces" / user["id"]).exists()
    assert client.get("/api/bootstrap").status_code == 200


def test_active_subscription_without_stripe_keys_blocks_deletion(site):
    app, client, _ = site
    user = register(client)
    app.state.billing.save_customer(user["id"], "cus_chiara")
    with app.state.billing.connection() as conn:
        conn.execute("INSERT INTO subscriptions(subscription_id,user_id,customer_id,status,cancel_at_period_end,current_period_end,event_created) VALUES ('sub_x',?,?,'active',0,NULL,1)", (user["id"], "cus_chiara"))
    response = post(client, "/api/account/delete", {"password": PASSWORD, "confirmation": "ELIMINA"})
    assert response.status_code == 409
    assert "assistenza" in response.json()["detail"]
    assert app.state.accounts.get_user(user["id"]) is not None


def test_interrupted_erase_is_completed_at_the_next_start(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", OWNER_PASSWORD)
    leftover = tmp_path / "workspaces" / ".deleted-abc-1234"
    leftover.mkdir(parents=True)
    (leftover / "alexchiara.sqlite3").write_bytes(b"old data")
    create_app(data_dir=tmp_path, start_worker=False)
    assert not leftover.exists()


def test_resend_backend_sends_json_with_idempotency(monkeypatch):
    monkeypatch.setenv("FILO_MAIL_FROM", "Spazelia <noreply@spazelia.test>")
    monkeypatch.setenv("FILO_RESEND_API_KEY", "re_test_never_real")
    sent = []

    def fake_post(url, json, headers, timeout):
        sent.append((url, json, headers))
        return httpx.Response(200, json={"id": "email-1"})

    monkeypatch.setattr(mailer.httpx, "post", fake_post)
    mailer.send_email("chiara@example.test", "Oggetto", "Testo", "<p>Testo</p>", idempotency_key="k1")
    url, payload, headers = sent[0]
    assert url == "https://api.resend.com/emails"
    assert payload == {"from": "Spazelia <noreply@spazelia.test>", "to": ["chiara@example.test"], "subject": "Oggetto", "text": "Testo", "html": "<p>Testo</p>"}
    assert headers["Authorization"] == "Bearer re_test_never_real" and headers["Idempotency-Key"] == "k1"
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: httpx.Response(422, json={"message": "re_test_never_real invalid"}))
    with pytest.raises(mailer.MailDeliveryError) as caught:
        mailer.send_email("chiara@example.test", "Oggetto", "Testo")
    assert "re_test" not in str(caught.value)


def test_smtp_backend_requires_tls(monkeypatch):
    monkeypatch.setenv("FILO_MAIL_FROM", "noreply@spazelia.test")
    monkeypatch.delenv("FILO_RESEND_API_KEY", raising=False)
    monkeypatch.setenv("FILO_SMTP_HOST", "smtp.spazelia.test")
    monkeypatch.setenv("FILO_SMTP_USERNAME", "user")
    monkeypatch.setenv("FILO_SMTP_PASSWORD", "secret")
    events = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            events.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context):
            events.append(("starttls",))

        def login(self, username, password):
            events.append(("login", username))

        def send_message(self, message):
            events.append(("send", message["To"], message["Subject"]))

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    mailer.send_email("chiara@example.test", "Oggetto", "Testo")
    assert events == [("connect", "smtp.spazelia.test", 587), ("starttls",), ("login", "user"), ("send", "chiara@example.test", "Oggetto")]


@pytest.mark.parametrize("sender", ["", "non-un-indirizzo", "Spazelia <a@b.test>\r\nBcc: x@y.test"])
def test_invalid_sender_means_email_is_not_configured(monkeypatch, sender):
    monkeypatch.setenv("FILO_MAIL_FROM", sender)
    monkeypatch.setenv("FILO_RESEND_API_KEY", "re_test")
    assert mailer.mail_configured() is False


def test_header_injection_in_subject_or_recipient_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("FILO_MAIL_FROM", "noreply@spazelia.test")
    monkeypatch.delenv("FILO_RESEND_API_KEY", raising=False)
    monkeypatch.delenv("FILO_SMTP_HOST", raising=False)
    monkeypatch.setenv("FILO_MAIL_OUTBOX_DIR", str(tmp_path))
    for to, subject in (("a@b.test\r\nBcc: x@y.test", "Oggetto"), ("a@b.test", "Oggetto\r\nBcc: x@y.test")):
        with pytest.raises(mailer.MailDeliveryError):
            mailer.send_email(to, subject, "Testo")
