"""Read-only IMAP boundaries, network safety and workspace credential lifecycle."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import imaplib
import ipaddress
import json
import socket
import ssl
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import imap_mail
from app.briefing import capture_snapshot
from app.connectors import ConnectorError, _cipher
from app.db import Database


PASSWORD = "test-app-password-never-returned"
CONTACTS = [{"name": "Cliente", "email": "cliente@example.test"}]
PUBLIC = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 993))


def raw_message(sender="cliente@example.test", recipient="studio@example.test", subject="Richiesta di preventivo",
                body="Potete inviare il preventivo?", message_id="<request@example.test>", references=None):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if references:
        message["References"] = references
        message["In-Reply-To"] = references.split()[-1]
    message.set_content(body)
    return message.as_bytes()


class FakeSocket:
    def __init__(self):
        self.timeouts = []
        self.connected = None
        self.closed = False

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        self.connected = address

    def close(self):
        self.closed = True


class FakeIMAP:
    def __init__(self, folders=None, sent=True, reject_login=False, on_command=None):
        self.folders = folders or {"INBOX": {}}
        self.sent = sent
        self.reject_login = reject_login
        self.on_command = on_command
        self.commands = []
        self.mailbox = "INBOX"
        self.sock = FakeSocket()

    def login(self, username, password):
        self.commands.append(("login", username))
        assert password == PASSWORD
        if self.reject_login:
            raise imaplib.IMAP4.error("provider body with " + PASSWORD)
        if self.on_command:
            self.on_command("login", self)
        return "OK", [b"Logged in"]

    def select(self, mailbox, readonly=False):
        assert readonly is True
        self.mailbox = mailbox.strip('"')
        self.commands.append(("select", self.mailbox, readonly))
        return "OK", [str(len(self.folders.get(self.mailbox, {}))).encode()]

    def list(self):
        self.commands.append(("list",))
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"'] + ([b'(\\Sent) "/" "Sent Items"'] if self.sent else [])

    def response(self, name):
        assert name == "UIDVALIDITY"
        return "UIDVALIDITY", [b"17"]

    def uid(self, command, *args):
        self.commands.append(("uid", self.mailbox, command, args))
        if self.on_command:
            self.on_command(command, self)
        messages = self.folders.get(self.mailbox, {})
        if command == "search":
            assert args[0] is None
            assert args[1] == "SINCE"
            assert args[3] in ("FROM", "TO")
            assert args[4] == '"cliente@example.test"'
            return "OK", [b" ".join(str(uid).encode() for uid in sorted(messages))]
        assert command == "fetch"
        uid, query = args
        item = messages[int(uid)]
        if "RFC822.SIZE" in query:
            received = item.get("received", datetime.now(timezone.utc) - timedelta(hours=1))
            size = item.get("size", len(item["raw"]))
            return "OK", [f'{uid} (UID {uid} RFC822.SIZE {size} INTERNALDATE "{received.strftime("%d-%b-%Y %H:%M:%S %z")}")'.encode()]
        assert query == f"(UID BODY.PEEK[]<0.{imap_mail.MAX_MESSAGE_BYTES}>)"
        raw = item["raw"]
        return "OK", [(f"{uid} (UID {uid} BODY[]<0> {{{len(raw)}}}".encode(), raw), b")"]

    def logout(self):
        self.commands.append(("logout",))
        return "BYE", [b"Closed"]


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "workspace" / "mail.sqlite3")


def install_fake(monkeypatch, fake):
    monkeypatch.setattr(imap_mail.socket, "getaddrinfo", lambda *args, **kwargs: [PUBLIC])
    monkeypatch.setattr(imap_mail, "_PinnedIMAP4SSL", lambda host, addresses, timeout=8: fake)


def api(db):
    app = FastAPI()
    app.include_router(imap_mail.create_imap_router(db))
    return TestClient(app)


def connect_body(**extra):
    return {"provider": "imap", "email": "studio@example.test", "password": PASSWORD,
            "host": "imap.example.test", **extra}


def save_credentials(db, password=PASSWORD):
    credentials = imap_mail._credentials_from_payload(imap_mail.ImapConnect(**connect_body(password=password)))
    db.set_setting("imap_credentials", _cipher(db).encrypt(json.dumps(credentials).encode()).decode())
    db.set_setting("gmail_revision", 1)
    db.set_connection("imap", "connected", "studio@example.test", mail_provider="imap")


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.2", "172.16.0.2", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "198.18.0.1", "192.0.2.10", "224.0.0.1", "0.0.0.0",
    "::1", "::", "fe80::1", "fd12::1", "2001:db8::1", "::ffff:127.0.0.1", "2002:7f00:1::",
])
def test_nonpublic_addresses_are_rejected_before_any_connection(monkeypatch, address):
    family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
    endpoint = (address, 993, 0, 0) if family == socket.AF_INET6 else (address, 993)
    monkeypatch.setattr(imap_mail.socket, "getaddrinfo", lambda *args, **kwargs: [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", endpoint)])
    with pytest.raises(ConnectorError, match="pubblico"):
        imap_mail._public_addresses("imap.example.test")


def test_mixed_dns_answer_is_rejected_instead_of_trying_only_public_address(monkeypatch):
    monkeypatch.setattr(imap_mail.socket, "getaddrinfo", lambda *args, **kwargs: [PUBLIC, (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.1", 993))])
    with pytest.raises(ConnectorError, match="pubblico"):
        imap_mail._public_addresses("imap.example.test")


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:4700:4700::1111"])
def test_public_ipv4_and_ipv6_endpoints_are_retained(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    endpoint = (address, 993, 0, 0) if family == socket.AF_INET6 else (address, 993)
    monkeypatch.setattr(imap_mail.socket, "getaddrinfo", lambda *args, **kwargs: [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", endpoint)])
    assert imap_mail._public_addresses("imap.example.test") == [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, endpoint)]


def test_tls_context_requires_valid_chain_hostname_and_tls12(monkeypatch):
    captured = {}

    def constructor(self, host, port, ssl_context, timeout):
        captured.update(host=host, port=port, context=ssl_context, timeout=timeout)

    monkeypatch.setattr(imaplib.IMAP4_SSL, "__init__", constructor)
    imap_mail._PinnedIMAP4SSL("imap.example.test", [(PUBLIC[0], PUBLIC[1], PUBLIC[2], PUBLIC[4])])
    assert captured["port"] == 993
    assert captured["context"].verify_mode == ssl.CERT_REQUIRED
    assert captured["context"].check_hostname is True
    assert captured["context"].minimum_version >= ssl.TLSVersion.TLSv1_2


def test_pinned_socket_does_not_resolve_dns_again_and_preserves_certificate_hostname(monkeypatch):
    sock = FakeSocket()
    seen = {}

    class Context:
        def wrap_socket(self, raw, server_hostname):
            seen["hostname"] = server_hostname
            assert raw is sock
            return "wrapped"

    client = object.__new__(imap_mail._PinnedIMAP4SSL)
    client.host = "imap.example.test"
    client.ssl_context = Context()
    client._validated_addresses = [(PUBLIC[0], PUBLIC[1], PUBLIC[2], PUBLIC[4])]
    monkeypatch.setattr(imap_mail.socket, "socket", lambda *args: sock)
    monkeypatch.setattr(imap_mail.socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("second DNS resolution"))
    assert client._create_socket(8) == "wrapped"
    assert sock.connected == ("93.184.216.34", 993)
    assert seen["hostname"] == "imap.example.test"
    assert 0 < sock.timeouts[0] <= 8


def test_certificate_failure_closes_socket_without_insecure_retry(monkeypatch):
    sock = FakeSocket()

    class Context:
        def wrap_socket(self, *args, **kwargs):
            raise ssl.SSLCertVerificationError("bad certificate")

    client = object.__new__(imap_mail._PinnedIMAP4SSL)
    client.host = "imap.example.test"
    client.ssl_context = Context()
    client._validated_addresses = [(PUBLIC[0], PUBLIC[1], PUBLIC[2], PUBLIC[4])]
    monkeypatch.setattr(imap_mail.socket, "socket", lambda *args: sock)
    with pytest.raises(ssl.SSLCertVerificationError):
        client._create_socket(8)
    assert sock.closed is True


def test_literal_is_bounded_before_imaplib_reads_or_allocates_it():
    client = object.__new__(imap_mail._PinnedIMAP4SSL)
    with pytest.raises(imaplib.IMAP4.abort, match="limit"):
        client.read(imap_mail.MAX_MESSAGE_BYTES + 1)


@pytest.mark.parametrize("host", ["https://imap.example.test", "imap.example.test:143", "user@imap.example.test", "imap.example.test/a", "localhost", "[fe80::1%eth0]"])
def test_server_format_prevents_url_port_userinfo_and_local_scope(host):
    with pytest.raises(ConnectorError):
        imap_mail._credentials_from_payload(imap_mail.ImapConnect(**connect_body(host=host)))


@pytest.mark.parametrize("extra", [{"host": "imap.gmail.com"}, {"host": "outlook.office365.com"}, {"email": "utente@gmail.com"}, {"email": "utente@outlook.it"}])
def test_oauth_providers_cannot_be_connected_with_password_fallback(extra):
    with pytest.raises(ConnectorError, match="Google|Microsoft|Gmail|Outlook"):
        imap_mail._credentials_from_payload(imap_mail.ImapConnect(**connect_body(**extra)))


@pytest.mark.parametrize("provider,email,host", [
    ("icloud", "studio@icloud.com", "imap.mail.me.com"),
    ("yahoo", "studio@yahoo.com", "imap.mail.yahoo.com"),
    ("aruba", "studio@example.test", "imaps.aruba.it"),
    ("aruba", "studio@aruba.it", "imap.aruba.it"),
    ("libero", "studio@libero.it", "imapmail.libero.it"),
])
def test_official_presets_and_aruba_free_mail_use_tls993(provider, email, host):
    payload = imap_mail.ImapConnect(provider=provider, email=email, password=PASSWORD)
    credentials = imap_mail._credentials_from_payload(payload)
    assert credentials["host"] == host
    assert credentials["port"] == 993
    assert credentials["username"] == email


def test_failed_login_preserves_previous_mailbox_mandate_and_does_not_return_password(db, monkeypatch):
    db.set_connection("gmail", "connected", "previous@example.test")
    db.set_setting("gmail_tokens", "previous-encrypted-data")
    service = db.get_setting("service")
    service.update(status="active", mandate={"read": True, "draft": True, "send": False})
    db.set_setting("service", service)
    fake = FakeIMAP(reject_login=True)
    install_fake(monkeypatch, fake)
    response = api(db).post("/api/mail/imap/connect", json=connect_body())
    assert response.status_code == 400
    assert PASSWORD not in response.text
    assert db.get_connection()["provider"] == "gmail"
    assert db.get_setting("service")["status"] == "active"
    assert db.get_setting("gmail_tokens") == "previous-encrypted-data"
    assert db.get_setting("imap_credentials") is None
    assert fake.commands[-1] == ("logout",)


def test_successful_verified_switch_encrypts_credentials_and_invalidates_old_authorization(db, monkeypatch):
    db.set_setting("gmail_revision", 7)
    for key in ("gmail_tokens", "outlook_tokens", "imap_tokens", "oauth:old", "outlook_oauth:old"):
        db.set_setting(key, "discard-me")
    service = db.get_setting("service")
    service.update(status="active", mandate={"read": True, "draft": True, "send": False})
    db.set_setting("service", service)
    fake = FakeIMAP()
    install_fake(monkeypatch, fake)
    response = api(db).post("/api/mail/imap/connect", json=connect_body())
    assert response.status_code == 200
    assert response.json()["label"] == "studio@example.test"
    assert PASSWORD not in response.text
    assert fake.commands == [("login", "studio@example.test"), ("select", "INBOX", True), ("logout",)]
    assert db.get_connection() == {"provider": "imap", "status": "connected", "label": "studio@example.test", "mail_provider": "imap"}
    assert db.get_setting("gmail_revision") == 8
    assert db.get_setting("imap_revision") == 1
    assert db.get_setting("service")["status"] == "inactive"
    assert db.get_setting("service")["mandate"] is None
    for key in ("gmail_tokens", "outlook_tokens", "imap_tokens", "oauth:old", "outlook_oauth:old"):
        assert db.get_setting(key) is None
    encrypted = db.get_setting("imap_credentials")
    assert PASSWORD not in encrypted
    assert imap_mail._read_credentials(db)["password"] == PASSWORD
    assert PASSWORD.encode() not in db.path.read_bytes()
    assert (db.path.parent / "gmail.key").stat().st_mode & 0o777 == 0o600


def test_connect_race_cannot_replace_newer_mailbox(db, monkeypatch):
    def changed(command, fake):
        if command == "login":
            db.set_setting("gmail_revision", 14)
            db.set_connection("outlook", "connected", "newer@example.test")

    install_fake(monkeypatch, FakeIMAP(on_command=changed))
    response = api(db).post("/api/mail/imap/connect", json=connect_body())
    assert response.status_code == 409
    assert db.get_connection()["label"] == "newer@example.test"
    assert db.get_setting("imap_credentials") is None
    assert db.get_setting("gmail_revision") == 14


def test_login_exceeding_whole_operation_budget_does_not_save_credentials(db, monkeypatch):
    clock = [100.0]

    def elapsed(command, fake):
        if command == "login":
            clock[0] += imap_mail.READ_DEADLINE_SECONDS + 1

    fake = FakeIMAP(on_command=elapsed)
    install_fake(monkeypatch, fake)
    monkeypatch.setattr(imap_mail, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    response = api(db).post("/api/mail/imap/connect", json=connect_body())
    assert response.status_code == 502
    assert db.get_setting("imap_credentials") is None
    assert fake.commands == [("login", "studio@example.test"), ("logout",)]


def test_switch_fails_queued_work_inside_same_transaction(db, monkeypatch):
    created = datetime.now(timezone.utc).isoformat()
    with db.connection() as conn:
        conn.execute("""INSERT INTO runs
            (id,trigger,provider,status,created_at,preferences,company)
            VALUES ('old-queued','scheduled','gmail','queued',?,'{}','{}')""", (created,))
    install_fake(monkeypatch, FakeIMAP())
    assert api(db).post("/api/mail/imap/connect", json=connect_body()).status_code == 200
    previous = db.run("old-queued")
    assert previous["status"] == "failed"
    assert previous["error_step"] == "mandato"
    assert previous["retryable"] is False
    assert previous["finished_at"] is not None


def test_stale_imap_disconnect_cannot_unlink_another_provider(db):
    db.set_connection("outlook", "connected", "current@example.test")
    db.set_setting("outlook_tokens", "keep-me")
    response = api(db).post("/api/mail/imap/disconnect")
    assert response.status_code == 409
    assert db.get_setting("outlook_tokens") == "keep-me"
    assert db.get_connection()["provider"] == "outlook"


def test_disconnect_deletes_credentials_and_invalidates_mandate(db):
    save_credentials(db)
    service = db.get_setting("service")
    service.update(status="active", mandate={"read": True})
    db.set_setting("service", service)
    response = api(db).post("/api/mail/imap/disconnect")
    assert response.status_code == 200
    assert db.get_setting("imap_credentials") is None
    assert db.get_connection()["status"] == "disconnected"
    assert db.get_setting("service")["mandate"] is None
    assert db.get_setting("gmail_revision") == 2


def test_workspace_encryption_key_cannot_decrypt_another_workspace(tmp_path, db):
    save_credentials(db)
    other = Database(tmp_path / "other" / "mail.sqlite3")
    other.set_setting("imap_credentials", db.get_setting("imap_credentials"))
    with pytest.raises(ConnectorError) as raised:
        imap_mail._read_credentials(other)
    assert raised.value.expired is True
    assert PASSWORD not in raised.value.message


def test_loader_reads_peek_only_and_observed_sent_reply_closes_old_request(db, monkeypatch):
    now = datetime.now(timezone.utc)
    incoming = raw_message()
    reply = raw_message(sender="studio@example.test", recipient="cliente@example.test", body="Ecco il preventivo richiesto.",
                        message_id="<reply@example.test>", references="<request@example.test>")
    fake = FakeIMAP(folders={"INBOX": {1: {"raw": incoming, "received": now - timedelta(hours=2)}},
                             "Sent Items": {4: {"raw": reply, "received": now - timedelta(minutes=30)}}})
    install_fake(monkeypatch, fake)
    save_credentials(db)
    result = imap_mail.load_imap_messages(db, CONTACTS)
    assert len(result) == 1
    assert result[0]["from_client"] is False
    assert result[0]["sender"] == "studio@example.test"
    assert "Potete inviare il preventivo?" in result[0]["context"]
    assert "Ecco il preventivo richiesto." in result[0]["context"]
    assert result[0]["id"].endswith(":17:4")
    assert all(command[2] is True for command in fake.commands if command[0] == "select")
    assert not any(command[0] in ("store", "close", "expunge", "append") for command in fake.commands)
    assert all("PEEK" in command[3][1] for command in fake.commands if command[0] == "uid" and command[2] == "fetch" and "BODY" in command[3][1])
    snapshot = capture_snapshot(result, CONTACTS, "imap", now, "imap:1:studio@example.test")
    assert snapshot["coverage_complete"] is False


def test_no_sent_folder_still_reads_inbox_without_claiming_complete_coverage(db, monkeypatch):
    fake = FakeIMAP(folders={"INBOX": {1: {"raw": raw_message()}}}, sent=False)
    install_fake(monkeypatch, fake)
    save_credentials(db)
    result = imap_mail.load_imap_messages(db, CONTACTS)
    assert len(result) == 1
    assert result[0]["from_client"] is True
    assert capture_snapshot(result, CONTACTS, "imap", datetime.now(timezone.utc), "scope")["coverage_complete"] is False


def test_oversized_message_is_skipped_before_body_fetch(db, monkeypatch):
    fake = FakeIMAP(folders={"INBOX": {1: {"raw": raw_message(), "size": imap_mail.MAX_MESSAGE_BYTES + 1}}}, sent=False)
    install_fake(monkeypatch, fake)
    save_credentials(db)
    assert imap_mail.load_imap_messages(db, CONTACTS) == []
    queries = [command[3][1] for command in fake.commands if command[0] == "uid" and command[2] == "fetch"]
    assert queries == ["(UID RFC822.SIZE INTERNALDATE)"]


@pytest.mark.parametrize("delta", [-7, 7, 300])
def test_estimated_rfc822_size_still_reads_the_message(db, monkeypatch, delta):
    # Exchange reports estimated sizes unless EnableExactRFC822Size is set.
    raw = raw_message()
    fake = FakeIMAP(folders={"INBOX": {1: {"raw": raw, "size": len(raw) + delta}}}, sent=False)
    install_fake(monkeypatch, fake)
    save_credentials(db)
    [message] = imap_mail.load_imap_messages(db, CONTACTS)
    assert message["body"] == "Potete inviare il preventivo?"


def test_literal_cut_at_the_fetch_limit_is_not_parsed_as_a_complete_message(db, monkeypatch):
    raw = raw_message(body="x" * imap_mail.MAX_MESSAGE_BYTES)[:imap_mail.MAX_MESSAGE_BYTES]
    fake = FakeIMAP(folders={"INBOX": {1: {"raw": raw, "size": imap_mail.MAX_MESSAGE_BYTES}}}, sent=False)
    install_fake(monkeypatch, fake)
    save_credentials(db)
    assert imap_mail.load_imap_messages(db, CONTACTS) == []


@pytest.mark.parametrize("header,value", [("Message-ID", "<[20261005@mailer.example>"), ("To", "m@"), ("References", "<a@b> <[c@d>")])
def test_one_malformed_header_does_not_block_the_other_messages(db, monkeypatch, header, value):
    bad = raw_message().replace(b"Message-ID: <request@example.test>", f"{header}: {value}".encode() if header != "Message-ID" else f"Message-ID: {value}".encode())
    if header != "Message-ID":
        bad = bad.replace(b"Subject:", f"{header}: {value}\r\nSubject:".encode(), 1)
    good = raw_message(message_id="<good@example.test>")
    fake = FakeIMAP(folders={"INBOX": {1: {"raw": bad}, 2: {"raw": good}}}, sent=False)
    install_fake(monkeypatch, fake)
    save_credentials(db)
    result = imap_mail.load_imap_messages(db, CONTACTS)
    assert any(message["body"] == "Potete inviare il preventivo?" for message in result)


@pytest.mark.parametrize("code", ["UNAVAILABLE", "LIMIT", "INUSE", "SERVERBUG"])
def test_temporary_login_refusals_are_retried_without_expiring_the_mailbox(db, monkeypatch, code):
    fake = FakeIMAP(sent=False)

    def refuse(username, password):
        raise imaplib.IMAP4.error(f"[{code}] Try again later")
    fake.login = refuse
    install_fake(monkeypatch, fake)
    save_credentials(db)
    with pytest.raises(ConnectorError) as raised:
        imap_mail.load_imap_messages(db, CONTACTS)
    assert raised.value.retryable is True
    assert raised.value.expired is False


def test_rejected_credentials_still_require_a_new_connection(db, monkeypatch):
    fake = FakeIMAP(sent=False)

    def refuse(username, password):
        raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
    fake.login = refuse
    install_fake(monkeypatch, fake)
    save_credentials(db)
    with pytest.raises(ConnectorError) as raised:
        imap_mail.load_imap_messages(db, CONTACTS)
    assert raised.value.retryable is False
    assert raised.value.expired is True


def test_non_ascii_password_uses_sasl_plain(db, monkeypatch):
    fake = FakeIMAP(sent=False)
    fake.capabilities = ("IMAP4REV1", "AUTH=PLAIN")
    calls = []

    def authenticate(mechanism, authobject):
        calls.append((mechanism, authobject(b"")))
        return "OK", [b"Authenticated"]
    fake.authenticate = authenticate
    fake.login = lambda *_: pytest.fail("LOGIN cannot carry UTF-8")
    install_fake(monkeypatch, fake)
    save_credentials(db, password="pàssword-è-sicura")
    imap_mail.load_imap_messages(db, CONTACTS)
    assert calls == [("PLAIN", "\0studio@example.test\0pàssword-è-sicura".encode())]


@pytest.mark.parametrize("error,retryable", [(ssl.SSLEOFError("EOF occurred"), True), (ssl.SSLCertVerificationError("bad cert"), False)])
def test_tls_errors_are_classified(db, monkeypatch, error, retryable):
    fake = FakeIMAP(sent=False)

    def fail(*_):
        raise error
    fake.list = fail
    install_fake(monkeypatch, fake)
    save_credentials(db)
    with pytest.raises(ConnectorError) as raised:
        imap_mail.load_imap_messages(db, CONTACTS)
    assert raised.value.retryable is retryable


def test_older_than_seven_days_message_is_not_fetched(db, monkeypatch):
    fake = FakeIMAP(folders={"INBOX": {1: {"raw": raw_message(), "received": datetime.now(timezone.utc) - timedelta(days=8)}}}, sent=False)
    install_fake(monkeypatch, fake)
    save_credentials(db)
    assert imap_mail.load_imap_messages(db, CONTACTS) == []
    assert not any(command[0] == "uid" and command[2] == "fetch" and "BODY" in command[3][1] for command in fake.commands)


def test_total_message_and_thread_bounds_include_both_incoming_and_sent(db, monkeypatch):
    inbox = {uid: {"raw": raw_message(message_id=f"<in-{uid}@example.test>")} for uid in range(1, 41)}
    sent = {uid: {"raw": raw_message(sender="studio@example.test", recipient="cliente@example.test", message_id=f"<sent-{uid}@example.test>")} for uid in range(1, 41)}
    fake = FakeIMAP(folders={"INBOX": inbox, "Sent Items": sent})
    install_fake(monkeypatch, fake)
    save_credentials(db)
    result = imap_mail.load_imap_messages(db, CONTACTS)
    size_fetches = [command for command in fake.commands if command[0] == "uid" and command[2] == "fetch" and "RFC822.SIZE" in command[3][1]]
    body_fetches = [command for command in fake.commands if command[0] == "uid" and command[2] == "fetch" and "BODY" in command[3][1]]
    assert len(size_fetches) == 25
    assert len(body_fetches) == 25
    assert len(result) == 20
    assert {command[1] for command in body_fetches} == {"INBOX", "Sent Items"}
    assert len([command for command in fake.commands if command[0] == "select"]) <= 5


def test_revision_switch_during_read_discards_partial_results(db, monkeypatch):
    def changed(command, fake):
        if command == "fetch":
            db.set_setting("gmail_revision", 2)

    fake = FakeIMAP(folders={"INBOX": {1: {"raw": raw_message()}}}, sent=False, on_command=changed)
    install_fake(monkeypatch, fake)
    save_credentials(db)
    with pytest.raises(ConnectorError) as raised:
        imap_mail.load_imap_messages(db, CONTACTS)
    assert raised.value.step == "collegamento"
    assert fake.commands[-1] == ("logout",)


def test_revoked_password_is_reported_as_expired_without_provider_response(db, monkeypatch):
    fake = FakeIMAP(reject_login=True)
    install_fake(monkeypatch, fake)
    save_credentials(db)
    with pytest.raises(ConnectorError) as raised:
        imap_mail.load_imap_messages(db, CONTACTS)
    assert raised.value.expired is True
    assert raised.value.retryable is False
    assert PASSWORD not in raised.value.message


def test_plain_text_preferred_and_attachments_never_used_as_context():
    message = EmailMessage()
    message.set_content("Contenuto utile.")
    message.add_alternative('<html><script>secret-script</script><b>HTML fallback</b></html>', subtype="html")
    message.add_attachment(b"secret-attachment", maintype="text", subtype="plain", filename="document.txt")
    assert imap_mail._mime_body(message) == "Contenuto utile."


def test_html_only_is_converted_to_text_without_script_styles_or_tags():
    message = EmailMessage()
    message.set_content('<html><script>evil()</script><style>.x{}</style><p>Ciao &amp; grazie.</p></html>', subtype="html")
    body = imap_mail._mime_body(message)
    assert "Ciao & grazie." in body
    assert "evil" not in body
    assert ".x" not in body
    assert "<" not in body


@pytest.mark.parametrize("contacts", [[], [{"email": "x@example.test"}] * 5, [{"email": 'x@example.test" OR ALL'}]])
def test_contact_bounds_and_query_injection_are_rejected_before_network(db, contacts, monkeypatch):
    monkeypatch.setattr(imap_mail, "_session", lambda *_: pytest.fail("must not connect"))
    with pytest.raises(ConnectorError, match="quattro"):
        imap_mail.load_imap_messages(db, contacts)
