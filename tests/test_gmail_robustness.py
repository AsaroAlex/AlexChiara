"""Gmail payload shapes seen in real mailboxes, tested with a mocked transport."""
import base64
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from app import ai, connectors
from app.db import Database
from app.main import create_workspace_app


CONTACTS = [{"name": "Cliente Alfa", "email": "alfa@example.test"}]


def encoded(data):
    return base64.urlsafe_b64encode(data if isinstance(data, bytes) else data.encode()).decode()


def leaf(mime, data, charset="utf-8", filename=""):
    return {"mimeType": mime, "filename": filename,
            "headers": [{"name": "Content-Type", "value": f'{mime}; charset="{charset}"'}],
            "body": {"data": encoded(data)}}


def message(source_id, sender, payload, minutes, labels=("INBOX",)):
    payload = {**payload, "headers": [*payload.get("headers", []), {"name": "From", "value": sender},
                                      {"name": "Subject", "value": "Richiesta"}]}
    return {"id": source_id, "labelIds": list(labels), "internalDate": str(1_790_000_000_000 + minutes * 60_000), "payload": payload}


@pytest.fixture
def gmail(tmp_path, monkeypatch):
    db = Database(tmp_path / "gmail.sqlite3")
    db.set_connection("gmail", "connected", "studio@example.test")
    connectors._save_tokens(db, {"access_token": "fake-test-token", "expires_at": time.time() + 3600})
    thread = []

    def handler(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "thread-1"}]})
        return httpx.Response(200, json={"messages": list(thread)})

    monkeypatch.setattr(connectors, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return db, thread


def test_unsent_draft_trash_and_spam_never_count_as_the_studio_reply(gmail):
    db, thread = gmail
    thread.append(message("request", "Cliente Alfa <alfa@example.test>", leaf("text/plain", "Mi inviate il preventivo?"), 1))
    thread.append(message("draft", "Studio <studio@example.test>", leaf("text/plain", "Bozza in corso"), 2, labels=("DRAFT",)))
    thread.append(message("deleted", "Studio <studio@example.test>", leaf("text/plain", "Risposta cestinata"), 3, labels=("TRASH",)))
    thread.append(message("junk", "Studio <studio@example.test>", leaf("text/plain", "Spam"), 4, labels=("SPAM",)))
    [latest] = connectors.load_messages("gmail", CONTACTS, db)
    assert latest["id"] == "request"
    assert latest["from_client"] is True
    assert "Bozza in corso" not in latest["context"]


def test_text_attachment_does_not_replace_the_message_body(gmail):
    db, thread = gmail
    body = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "multipart/alternative", "parts": [leaf("text/plain", "Testo vero della richiesta"), leaf("text/html", "<p>Testo vero della richiesta</p>")]},
        leaf("text/plain", "contenuto dell'allegato", filename="note.txt"),
        {"mimeType": "image/png", "filename": "", "body": {"data": encoded(b"\x89PNG\r\n")}},
    ]}
    thread.append(message("request", "alfa@example.test", body, 1))
    [latest] = connectors.load_messages("gmail", CONTACTS, db)
    assert latest["body"] == "Testo vero della richiesta"


def test_html_only_alternative_is_read_as_text(gmail):
    db, thread = gmail
    body = {"mimeType": "multipart/alternative", "parts": [leaf("text/html", "<div>Serve <b>urgente</b> il preventivo<script>x()</script></div>")]}
    thread.append(message("request", "alfa@example.test", body, 1))
    [latest] = connectors.load_messages("gmail", CONTACTS, db)
    assert "urgente" in latest["body"] and "x()" not in latest["body"] and "<" not in latest["body"]


def test_declared_latin_charset_keeps_accents(gmail):
    db, thread = gmail
    thread.append(message("request", "alfa@example.test", leaf("text/plain", "non è urgente, però grazie".encode("iso-8859-1"), charset="iso-8859-1"), 1))
    [latest] = connectors.load_messages("gmail", CONTACTS, db)
    assert latest["body"] == "non è urgente, però grazie"


def test_sender_with_unquoted_comma_is_recognised(gmail):
    db, thread = gmail
    thread.append(message("request", "Alfa, Cliente <ALFA@example.test>", leaf("text/plain", "Richiesta"), 1))
    [latest] = connectors.load_messages("gmail", CONTACTS, db)
    assert latest["from_client"] is True


@pytest.mark.parametrize("value,expected", [
    ('"Rossi, Mario" <m@x.it>', "m@x.it"), ("Rossi, Mario <M@X.IT>", "m@x.it"), ("m@x.it", "m@x.it"), ("", ""),
])
def test_sender_address_parsing(value, expected):
    assert ai.sender_address(value) == expected


@pytest.mark.parametrize("error", [httpx.RemoteProtocolError("disconnected"), httpx.ProxyError("proxy"), httpx.ReadError("reset")])
def test_any_transport_failure_is_a_retryable_connector_error(tmp_path, monkeypatch, error):
    db = Database(tmp_path / "gmail.sqlite3")
    connectors._save_tokens(db, {"access_token": "fake", "expires_at": time.time() + 3600})

    def handler(request):
        raise error
    monkeypatch.setattr(connectors, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(connectors.ConnectorError) as raised:
        connectors.load_messages("gmail", CONTACTS, db)
    assert raised.value.retryable is True


@pytest.mark.parametrize("listing,thread", [
    ([], {}), ({"messages": "x"}, {}), ({"messages": [{"id": "m1", "threadId": "thread-1"}]}, []),
    ({"messages": [{"id": "m1", "threadId": "thread-1"}]}, {"messages": [{"payload": {}}, "x", {"id": 5}]}),
    ({"messages": [{"id": "m1", "threadId": "thread-1"}]}, {"messages": [{"id": "a", "internalDate": "nan", "payload": {"headers": "x", "parts": "y"}}]}),
])
def test_malformed_google_responses_never_crash(tmp_path, monkeypatch, listing, thread):
    db = Database(tmp_path / "gmail.sqlite3")
    connectors._save_tokens(db, {"access_token": "fake", "expires_at": time.time() + 3600})

    def handler(request):
        return httpx.Response(200, json=listing if request.url.path.endswith("/messages") else thread)
    monkeypatch.setattr(connectors, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        result = connectors.load_messages("gmail", CONTACTS, db)
    except connectors.ConnectorError as exc:
        assert exc.retryable is True
    else:
        assert isinstance(result, list)


def test_gmail_rate_limit_403_is_retried_but_other_403_is_not(tmp_path, monkeypatch):
    db = Database(tmp_path / "gmail.sqlite3")
    connectors._save_tokens(db, {"access_token": "fake", "expires_at": time.time() + 3600})
    reply = {}
    monkeypatch.setattr(connectors, "_client", lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403, json=reply))))
    reply.update(error={"errors": [{"reason": "userRateLimitExceeded"}]})
    with pytest.raises(connectors.ConnectorError) as raised:
        connectors.load_messages("gmail", CONTACTS, db)
    assert raised.value.retryable is True
    reply.update(error={"errors": [{"reason": "insufficientPermissions"}]})
    with pytest.raises(connectors.ConnectorError) as raised:
        connectors.load_messages("gmail", CONTACTS, db)
    assert raised.value.retryable is False


def test_non_json_token_refresh_is_retryable(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("FILO_GOOGLE_CLIENT_SECRET", "secret")
    db = Database(tmp_path / "gmail.sqlite3")
    connectors._save_tokens(db, {"access_token": "old", "refresh_token": "refresh", "expires_at": 0})
    monkeypatch.setattr(connectors, "_client", lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>"))))
    with pytest.raises(connectors.ConnectorError) as raised:
        connectors.load_messages("gmail", CONTACTS, db)
    assert raised.value.retryable is True


def test_redirect_uri_follows_the_public_railway_domain(monkeypatch):
    monkeypatch.delenv("FILO_GOOGLE_REDIRECT_URI", raising=False)
    monkeypatch.delenv("FILO_PUBLIC_URL", raising=False)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "filo.example.test")
    assert connectors._credentials()["redirect_uri"] == "https://filo.example.test/api/gmail/oauth/callback"
    monkeypatch.setenv("FILO_PUBLIC_URL", "https://app.example.test/")
    assert connectors._credentials()["redirect_uri"] == "https://app.example.test/api/gmail/oauth/callback"
    monkeypatch.setenv("FILO_GOOGLE_REDIRECT_URI", "https://explicit.example.test/api/gmail/oauth/callback")
    assert connectors._credentials()["redirect_uri"] == "https://explicit.example.test/api/gmail/oauth/callback"


def test_cancelled_consent_returns_to_the_app_with_a_readable_message(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("FILO_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("FILO_GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/gmail/oauth/callback")
    app = create_workspace_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        csrf = client.get("/api/bootstrap").json()["csrf_token"]
        start = client.post("/api/gmail/oauth/start", headers={"X-CSRF-Token": csrf}).json()
        state = httpx.URL(start["authorization_url"]).params["state"]
        response = client.get("/api/gmail/oauth/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/?mail_error=gmail#connections"
        failure = client.get("/api/bootstrap").json()["mail_oauth_error"]
    assert failure["provider"] == "gmail"
    assert "consenso non concesso" in failure["message"]
    assert app.state.db.get_setting("gmail_tokens") is None
