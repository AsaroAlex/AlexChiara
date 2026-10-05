"""Microsoft mailbox tests use mock HTTP only; no real account is contacted."""
import hashlib
import json
import stat
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import outlook
from app.connectors import ConnectorError
from app.db import Database


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("FILO_MICROSOFT_CLIENT_ID", "test-microsoft-client")
    monkeypatch.setenv("FILO_MICROSOFT_CLIENT_SECRET", "test-microsoft-secret")
    monkeypatch.setenv("FILO_MICROSOFT_REDIRECT_URI", "http://127.0.0.1:8000/api/outlook/oauth/callback")
    monkeypatch.setenv("FILO_MICROSOFT_TENANT", "common")


@pytest.fixture
def environment(tmp_path):
    db = Database(tmp_path / "mail.sqlite3")
    app = FastAPI()

    @app.middleware("http")
    async def authenticated_parent(request, call_next):
        request.state.filo_user = {"id": "test-only-owner"}
        return await call_next(request)

    app.include_router(outlook.create_outlook_router(db))
    with TestClient(app) as client:
        client.cookies.set("alexchiara_session", "test-only-session")
        yield db, client


def mock_http(monkeypatch, handler):
    monkeypatch.setattr(outlook, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def token_response(**overrides):
    return {"access_token": "fake-outlook-access", "refresh_token": "fake-outlook-refresh",
            "expires_in": 3600, "scope": "Mail.Read User.Read", **overrides}


def begin(client):
    response = client.post("/api/outlook/oauth/start")
    assert response.status_code == 200, response.text
    url = urlparse(response.json()["authorization_url"])
    assert url.scheme == "https"
    assert url.netloc == "login.microsoftonline.com"
    query = parse_qs(url.query)
    assert query["scope"] == [outlook.OUTLOOK_SCOPE]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 40
    assert query["response_type"] == ["code"]
    assert query["response_mode"] == ["query"]
    return query["state"][0]


def connected(db, *, expired=False):
    db.set_connection("outlook", "connected", "studio@example.test")
    tokens = token_response(expires_at=time.time() - 1 if expired else time.time() + 3600)
    outlook._save_tokens(db, tokens)
    return tokens


def message(source_id, sender, *, when=None, conversation="thread-a", body="Serve il preventivo.", outgoing=False, draft=False):
    stamp = (when or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    return {"id": source_id, "conversationId": conversation,
            "from": {"emailAddress": {"address": sender, "name": "Mittente"}},
            "subject": "Preventivo", "body": {"contentType": "text", "content": body},
            "receivedDateTime": stamp, "sentDateTime": stamp,
            "toRecipients": [{"emailAddress": {"address": "alfa@example.test" if outgoing else "studio@example.test"}}],
            "isDraft": draft}


def test_missing_configuration_is_honest_and_contacts_no_remote(environment, monkeypatch):
    _, client = environment
    monkeypatch.delenv("FILO_MICROSOFT_CLIENT_ID", raising=False)
    monkeypatch.delenv("FILO_MICROSOFT_CLIENT_SECRET", raising=False)
    mock_http(monkeypatch, lambda _: pytest.fail("Missing configuration must not contact Microsoft"))
    assert client.get("/api/outlook/status").json()["configured"] is False
    assert client.post("/api/outlook/oauth/start").status_code == 409


def test_default_redirect_uses_public_origin(monkeypatch, configured):
    monkeypatch.delenv("FILO_MICROSOFT_REDIRECT_URI", raising=False)
    monkeypatch.setenv("FILO_PUBLIC_URL", "https://filo.example.test/")
    assert outlook._credentials()["redirect_uri"] == "https://filo.example.test/api/outlook/oauth/callback"
    monkeypatch.delenv("FILO_PUBLIC_URL")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "filo-production.example.test")
    assert outlook._credentials()["redirect_uri"] == "https://filo-production.example.test/api/outlook/oauth/callback"


def assert_oauth_failure(response):
    """A failed Microsoft return lands back in the app with a stored message."""
    assert response.status_code == 303
    assert response.headers["location"].endswith("?mail_error=outlook#connections")


@pytest.mark.parametrize("redirect", ["http://public.example.test/callback", "https://user:secret@example.test/callback", "https://example.test/callback#fragment", "javascript:alert(1)"])
def test_invalid_redirect_is_rejected(environment, configured, monkeypatch, redirect):
    _, client = environment
    monkeypatch.setenv("FILO_MICROSOFT_REDIRECT_URI", redirect)
    assert client.get("/api/outlook/status").json()["configured"] is False
    assert client.post("/api/outlook/oauth/start").status_code == 409


def test_oauth_pkce_once_encrypted_and_replaces_all_mailbox_credentials(environment, configured, monkeypatch):
    db, client = environment
    db.set_connection("gmail", "connected", "old@example.test")
    db.set_setting("gmail_tokens", "old-encrypted-token")
    db.set_setting("imap_credentials", "old-encrypted-imap")
    db.set_setting("oauth:old-pending", {"expires": time.time() + 600})
    service = db.get_setting("service")
    service.update(status="active", provider="gmail", mandate={"read": True, "draft": True, "send": False})
    db.set_setting("service", service)
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/token"):
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert len(form["code_verifier"][0]) >= 43
            assert form["scope"] == [outlook.OUTLOOK_SCOPE]
            return httpx.Response(200, json=token_response())
        assert request.url.path == "/v1.0/me"
        assert request.url.params["$select"] == "mail,userPrincipalName"
        return httpx.Response(200, json={"mail": "studio@example.test"})

    mock_http(monkeypatch, handler)
    state = begin(client)
    response = client.get("/api/outlook/oauth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app?provider=outlook&connected=1"
    assert client.get("/api/outlook/status").json()["connected"] is True
    encrypted = db.get_setting("outlook_tokens")
    assert "fake-outlook-access" not in encrypted and "fake-outlook-refresh" not in encrypted
    assert outlook._read_tokens(db)["refresh_token"] == "fake-outlook-refresh"
    assert stat.S_IMODE((db.path.parent / "gmail.key").stat().st_mode) == 0o600
    assert db.get_setting("gmail_tokens") is None
    assert db.get_setting("imap_credentials") is None
    assert db.get_setting("oauth:old-pending") is None
    assert db.get_setting("gmail_revision") == 1 and db.get_setting("outlook_revision") == 1
    assert db.get_setting("service")["status"] == "inactive"
    assert db.get_setting("service")["mandate"] is None
    assert_oauth_failure(client.get("/api/outlook/oauth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False))
    assert requests == [("POST", "/common/oauth2/v2.0/token"), ("GET", "/v1.0/me")]


@pytest.mark.parametrize("kind", ["expired", "unknown", "wrong-browser", "cancelled"])
def test_invalid_or_cancelled_state_consumed_without_network(environment, configured, monkeypatch, kind):
    db, client = environment
    mock_http(monkeypatch, lambda _: pytest.fail("Invalid callback must not contact Microsoft"))
    state = begin(client)
    key = "outlook_oauth:" + hashlib.sha256(state.encode()).hexdigest()
    params = {"state": state, "code": "fake-code"}
    if kind == "expired":
        pending = db.get_setting(key)
        pending["expires"] = time.time() - 10
        db.set_setting(key, pending)
    elif kind == "unknown":
        params["state"] = "unknown-state"
    elif kind == "wrong-browser":
        client.cookies.set("alexchiara_session", "different-session")
    else:
        params["error"] = "access_denied"
    assert_oauth_failure(client.get("/api/outlook/oauth/callback", params=params, follow_redirects=False))
    assert db.get_setting("outlook_tokens") is None
    if kind != "unknown":
        assert db.get_setting(key) is None


@pytest.mark.parametrize("scope", ["Mail.Read User.Read Mail.Send", "Mail.ReadWrite User.Read", "Mail.Read User.Read Files.Read", "Mail.Read", ""])
def test_scope_rejects_broad_or_missing_permissions(environment, configured, monkeypatch, scope):
    db, client = environment
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=token_response(scope=scope))

    mock_http(monkeypatch, handler)
    state = begin(client)
    response = client.get("/api/outlook/oauth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False)
    assert_oauth_failure(response)
    assert db.get_setting("outlook_tokens") is None
    assert calls == ["/common/oauth2/v2.0/token"]


def test_callback_switch_in_flight_cannot_restore_credentials(environment, configured, monkeypatch):
    db, client = environment
    state = begin(client)

    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json=token_response())
        db.set_setting("gmail_revision", 7)
        db.set_connection("gmail", "connected", "new@example.test")
        return httpx.Response(200, json={"mail": "old@example.test"})

    mock_http(monkeypatch, handler)
    response = client.get("/api/outlook/oauth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False)
    assert_oauth_failure(response)
    assert db.get_setting("outlook_tokens") is None
    assert db.get_connection()["provider"] == "gmail"
    assert db.get_setting("gmail_revision") == 7


def test_disconnect_removes_credentials_pending_state_and_mandate(environment, configured):
    db, client = environment
    connected(db)
    state = begin(client)
    db.set_setting("imap_tokens", "leftover")
    response = client.post("/api/outlook/disconnect")
    assert response.status_code == 200
    assert db.get_setting("outlook_tokens") is None
    assert db.get_setting("imap_tokens") is None
    assert db.get_setting("outlook_oauth:" + hashlib.sha256(state.encode()).hexdigest()) is None
    assert db.get_connection()["status"] == "disconnected"
    assert db.get_setting("gmail_revision") == 1 and db.get_setting("outlook_revision") == 1


def test_stale_disconnect_does_not_unlink_a_different_provider(environment):
    db, client = environment
    db.set_connection("gmail", "connected", "current@example.test")
    db.set_setting("gmail_tokens", "encrypted-current")
    assert client.post("/api/outlook/disconnect").status_code == 409
    assert db.get_connection()["provider"] == "gmail"
    assert db.get_setting("gmail_tokens") == "encrypted-current"


def test_refresh_disconnect_cannot_restore_tokens(environment, configured, monkeypatch):
    db, client = environment
    connected(db, expired=True)

    def handler(request):
        assert request.url.path.endswith("/token")
        assert client.post("/api/outlook/disconnect").status_code == 200
        return httpx.Response(200, json=token_response(access_token="new-fake-access"))

    mock_http(monkeypatch, handler)
    with pytest.raises(ConnectorError, match="cambiato"):
        outlook.load_outlook_messages(db, [{"email": "alfa@example.test"}])
    assert db.get_setting("outlook_tokens") is None


def test_reads_bounded_inbound_and_sent_replies_deterministically(environment, monkeypatch):
    db, _ = environment
    connected(db)
    now = datetime.now(timezone.utc)
    earlier = message("incoming", "alfa@example.test", when=now - timedelta(hours=2))
    reply = message("sent", "studio@example.test", when=now - timedelta(hours=1), outgoing=True, body="Ecco il preventivo.")
    open_mail = message("open", "alfa@example.test", when=now, conversation="thread-b", body="Nuova richiesta")
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer fake-outlook-access"
        assert "ImmutableId" in request.headers["Prefer"]
        if request.url.path == "/v1.0/me/messages":
            assert request.url.params["$top"] == "25"
            assert "from/emailAddress/address eq 'alfa@example.test'" in request.url.params["$filter"]
            return httpx.Response(200, json={"value": [open_mail, earlier], "@odata.nextLink": "https://evil.example.test/never-follow"})
        if request.url.path == "/v1.0/me/messages/sent":
            # Only a reply to a priority contact is opened, and only its body.
            assert request.url.params["$select"] == "body,subject"
            return httpx.Response(200, json={"body": reply["body"], "subject": reply["subject"]})
        assert request.url.path == "/v1.0/me/mailFolders/sentitems/messages"
        assert request.url.params["$top"] == "100"
        assert "body" not in request.url.params["$select"].split(",")
        return httpx.Response(200, json={"value": [{key: value for key, value in reply.items() if key not in ("body", "subject")}]})

    mock_http(monkeypatch, handler)
    result = outlook.load_outlook_messages(db, [{"email": "alfa@example.test"}])
    assert [m["id"] for m in result] == ["sent", "open"]
    assert result[0]["from_client"] is False
    assert result[1]["from_client"] is True
    assert "Serve il preventivo" in result[0]["context"] and "Ecco il preventivo" in result[0]["context"]
    assert requests[-1] == ("GET", "/v1.0/me/messages/sent")
    assert len(requests) == 3


def test_filters_old_drafts_unrelated_sent_and_strips_html(environment, monkeypatch):
    db, _ = environment
    connected(db)
    old = message("old", "alfa@example.test", when=datetime.now(timezone.utc) - timedelta(days=8))
    draft = message("draft", "alfa@example.test", draft=True)
    fresh = message("fresh", "alfa@example.test")
    fresh["body"] = {"contentType": "HTML", "content": '<script>STEAL()</script><b>Ciao &amp; grazie</b>'}
    unrelated = message("unrelated", "studio@example.test", outgoing=True)
    unrelated["toRecipients"][0]["emailAddress"]["address"] = "other@example.test"

    def handler(request):
        return httpx.Response(200, json={"value": [old, draft, fresh] if request.url.path == "/v1.0/me/messages" else [unrelated]})

    mock_http(monkeypatch, handler)
    result = outlook.load_outlook_messages(db, [{"email": "alfa@example.test"}])
    assert len(result) == 1 and result[0]["id"] == "fresh"
    assert result[0]["body"] == "Ciao & grazie"


def test_contact_query_injection_is_rejected_and_apostrophe_escaped(environment, monkeypatch):
    db, _ = environment
    connected(db)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"value": []})

    mock_http(monkeypatch, handler)
    with pytest.raises(ConnectorError, match="validi"):
        outlook.load_outlook_messages(db, [{"email": "alfa@example.test' or true"}])
    assert requests == []
    outlook.load_outlook_messages(db, [{"email": "o'connor@example.test"}])
    assert "eq 'o''connor@example.test'" in requests[0].url.params["$filter"]


def test_graph_401_refreshes_once_and_never_writes(environment, configured, monkeypatch):
    db, _ = environment
    connected(db)
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/token"):
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["refresh_token"]
            return httpx.Response(200, json=token_response(access_token="refreshed-fake-access", refresh_token="rotated-fake-refresh"))
        if request.headers["Authorization"] == "Bearer fake-outlook-access":
            return httpx.Response(401, json={"secret-error": "must-not-leak"})
        assert request.headers["Authorization"] == "Bearer refreshed-fake-access"
        return httpx.Response(200, json={"value": []})

    mock_http(monkeypatch, handler)
    assert outlook.load_outlook_messages(db, [{"email": "alfa@example.test"}]) == []
    assert requests == [("GET", "/v1.0/me/messages"), ("POST", "/common/oauth2/v2.0/token"),
                        ("GET", "/v1.0/me/messages"), ("GET", "/v1.0/me/mailFolders/sentitems/messages")]
    assert outlook._read_tokens(db)["refresh_token"] == "rotated-fake-refresh"


def test_switch_during_read_discards_partial_results(environment, monkeypatch):
    db, _ = environment
    connected(db)

    def handler(request):
        db.set_setting("gmail_revision", 4)
        return httpx.Response(200, json={"value": [message("private-old-mail", "alfa@example.test")]})

    mock_http(monkeypatch, handler)
    with pytest.raises(ConnectorError, match="sostituita"):
        outlook.load_outlook_messages(db, [{"email": "alfa@example.test"}])


@pytest.mark.parametrize("status,retryable,expired", [(401, False, True), (403, False, False), (429, True, False), (503, True, False)])
def test_provider_errors_are_friendly_and_never_leak_response(status, retryable, expired):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"token": "SECRET-PROVIDER-DETAIL"}))) as client:
        with pytest.raises(ConnectorError) as caught:
            outlook._request(client, "GET", outlook.GRAPH_API + "/me")
    assert "SECRET-PROVIDER-DETAIL" not in str(caught.value)
    assert caught.value.retryable is retryable and caught.value.expired is expired


def test_bad_remote_json_and_deadline_fail_recoverably(environment, monkeypatch):
    db, _ = environment
    connected(db)
    mock_http(monkeypatch, lambda _: httpx.Response(200, text="not-json SECRET"))
    with pytest.raises(ConnectorError) as caught:
        outlook.load_outlook_messages(db, [{"email": "alfa@example.test"}])
    assert caught.value.retryable and "SECRET" not in str(caught.value)
    readings = iter([0, 23])
    monkeypatch.setattr(outlook.time, "monotonic", lambda: next(readings, 23))
    mock_http(monkeypatch, lambda _: pytest.fail("Deadline must stop before mailbox request"))
    with pytest.raises(ConnectorError, match="tempo massimo"):
        outlook.load_outlook_messages(db, [{"email": "alfa@example.test"}])


def test_authorization_lets_microsoft_decide_when_consent_is_needed(environment, configured):
    _, client = environment
    url = urlparse(client.post("/api/outlook/oauth/start").json()["authorization_url"])
    assert parse_qs(url.query)["prompt"] == ["select_account"]


@pytest.mark.parametrize("error", [httpx.RemoteProtocolError("server disconnected"), httpx.ProxyError("proxy"), httpx.ReadError("reset")])
def test_any_transport_failure_is_retryable(environment, monkeypatch, error):
    db, _ = environment
    connected(db)

    def handler(request):
        raise error
    mock_http(monkeypatch, handler)
    with pytest.raises(ConnectorError) as raised:
        outlook.load_outlook_messages(db, [{"email": "alfa@example.test"}])
    assert raised.value.retryable is True


def test_transport_failure_during_callback_returns_to_the_app(environment, configured, monkeypatch):
    db, client = environment

    def handler(request):
        raise httpx.RemoteProtocolError("server disconnected")
    mock_http(monkeypatch, handler)
    state = begin(client)
    assert_oauth_failure(client.get("/api/outlook/oauth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False))
    assert db.get_setting("mail_oauth_error")["provider"] == "outlook"
    assert db.get_setting("outlook_tokens") is None
