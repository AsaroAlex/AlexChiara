"""Bounded, read-only IMAP over verified TLS; credentials stay in one workspace.

This adapter deliberately cannot use OAuth providers' account passwords. DNS is
validated once and the resulting address is pinned for the TLS socket, while
the original host remains the certificate/SNI identity. Only INBOX and an
observed sent folder are selected, always with EXAMINE and BODY.PEEK.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.errors import MessageError
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
import hashlib
import html
import imaplib
import ipaddress
import json
import re
import socket
import ssl
import time
from typing import Literal

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from .connectors import ConnectorError, _cipher, _invalidate_in_transaction, _revision_in_transaction
from .mail_providers import clear_mail_credentials

# Official support: Apple 102525; Yahoo SLN4075; Aruba configuration parameters;
# Libero Aiuto "Configurare Libero Mail con client di posta Imap e Smtp".
IMAP_PRESETS = {
    "icloud": {"host": "imap.mail.me.com", "label": "iCloud"},
    "yahoo": {"host": "imap.mail.yahoo.com", "label": "Yahoo"},
    "aruba": {"host": "imaps.aruba.it", "label": "Aruba"},
    "libero": {"host": "imapmail.libero.it", "label": "Libero"},
}
MAX_MESSAGES = 25
MAX_THREADS = 20
MAX_MESSAGE_BYTES = 512 * 1024
MAX_BODY_CHARS = 12000
READ_DEADLINE_SECONDS = 22
SOCKET_TIMEOUT = 8
_EMAIL = re.compile(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_OAUTH_HOST_SUFFIXES = ("gmail.com", "googlemail.com", "outlook.com", "office365.com", "outlook.office.com")
_OAUTH_EMAIL_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "outlook.it", "hotmail.com", "hotmail.it", "live.com", "live.it", "msn.com"}


class ImapConnect(BaseModel):
    provider: Literal["icloud", "yahoo", "aruba", "libero", "imap"]
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=512)
    host: str | None = Field(default=None, max_length=253)
    username: str | None = Field(default=None, max_length=320)


def _invalid_settings(message="Verifica indirizzo email, nome utente e server IMAP."):
    return ConnectorError(message, "configurazione IMAP", False)


def _credentials_from_payload(payload):
    email = payload.email.strip().lower()
    username = (payload.username or email).strip()
    password = payload.password.get_secret_value()
    if not _EMAIL.fullmatch(email) or not username or any(ord(c) < 32 or ord(c) == 127 for c in username + password):
        raise _invalid_settings()
    if email.rsplit("@", 1)[-1] in _OAUTH_EMAIL_DOMAINS:
        raise _invalid_settings("Per Gmail e Outlook usa il collegamento con Google o Microsoft, senza inserire la password della casella.")
    host = payload.host.strip() if payload.host else ""
    if payload.provider != "imap":
        expected = IMAP_PRESETS[payload.provider]["host"]
        if payload.provider == "aruba" and email.rsplit("@", 1)[-1] in {"aruba.it", "technet.it"}:
            expected = "imap.aruba.it"
        if host and _normalize_host(host) != expected:
            raise _invalid_settings("Il server non corrisponde al provider scelto. Usa «Altro account IMAP» per un server diverso.")
        host = expected
    host = _normalize_host(host)
    if any(host == suffix or host.endswith("." + suffix) for suffix in _OAUTH_HOST_SUFFIXES):
        raise _invalid_settings("Per Gmail e Outlook usa il collegamento con Google o Microsoft.")
    return {"host": host, "port": 993, "email": email, "username": username,
            "password": password, "mail_provider": payload.provider}


def _normalize_host(host):
    if not host or any(c.isspace() for c in host) or any(c in host for c in "/\\@?#%"):
        raise _invalid_settings("Indica soltanto il nome del server IMAP, senza URL o porta.")
    # Bracketed IPv6 literals are accepted but zone identifiers are never allowed.
    raw = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        pass
    try:
        normalized = raw.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise _invalid_settings() from exc
    if len(normalized) > 253 or ":" in normalized or not re.fullmatch(r"[a-z0-9.-]+", normalized):
        raise _invalid_settings("Indica un nome di server IMAP valido.")
    labels = normalized.split(".")
    if len(labels) < 2 or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
        raise _invalid_settings("Il server IMAP deve avere un nome pubblico valido.")
    return normalized


def _public_addresses(host):
    """Reject the entire DNS answer when any address is unsafe, not just one."""
    try:
        answers = socket.getaddrinfo(host, 993, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ConnectorError("Il server IMAP non è raggiungibile. Verifica il nome del server.", "connessione IMAP", True) from exc
    if not answers:
        raise _invalid_settings("Il server IMAP non ha un indirizzo pubblico valido.")
    validated = []
    for family, kind, proto, _canonical, address in answers:
        if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
            raise _invalid_settings("Il server IMAP deve essere pubblico.")
        try:
            ip = ipaddress.ip_address(address[0])
        except ValueError as exc:
            raise _invalid_settings("Il server IMAP deve essere pubblico.") from exc
        # is_global alone admits multicast on some Python versions.
        if not ip.is_global or ip.is_multicast or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            raise _invalid_settings("Il server IMAP deve essere pubblico. Gli indirizzi locali o privati non sono consentiti.")
        if isinstance(ip, ipaddress.IPv6Address):
            if ip.ipv4_mapped and not ip.ipv4_mapped.is_global:
                raise _invalid_settings("Il server IMAP deve essere pubblico.")
            if ip.sixtofour and not ip.sixtofour.is_global:
                raise _invalid_settings("Il server IMAP deve essere pubblico.")
            if ip.teredo and any(not embedded.is_global for embedded in ip.teredo):
                raise _invalid_settings("Il server IMAP deve essere pubblico.")
            if len(address) > 3 and address[3] != 0:
                raise _invalid_settings("Gli indirizzi IMAP con ambito locale non sono consentiti.")
        pinned = (family, kind, proto, address)
        if pinned not in validated:
            validated.append(pinned)
    return validated


class _PinnedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host, addresses, timeout=SOCKET_TIMEOUT):
        self._validated_addresses = addresses
        # create_default_context verifies trust chain and hostname, including SNI.
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(host, port=993, ssl_context=context, timeout=timeout)

    def read(self, size):
        # imaplib's line bound does not bound server-announced literal bodies.
        # Enforce that limit before it allocates/reads the literal itself.
        if size > MAX_MESSAGE_BYTES:
            raise imaplib.IMAP4.abort("IMAP literal exceeds limit")
        return super().read(size)

    def _create_socket(self, timeout):
        deadline = time.monotonic() + (timeout or SOCKET_TIMEOUT)
        last_error = None
        for family, kind, proto, address in self._validated_addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(remaining)
                # No getaddrinfo/create_connection here: the exact validated
                # numeric address is used, preventing a second DNS resolution.
                sock.connect(address)
                return self.ssl_context.wrap_socket(sock, server_hostname=self.host)
            except ssl.SSLError:
                sock.close()
                raise
            except OSError as exc:
                sock.close()
                last_error = exc
        if last_error:
            raise last_error
        raise TimeoutError("IMAP connection timeout")


def _revision_guard(db, revision):
    if db.get_setting("gmail_revision", 0) != revision:
        raise ConnectorError("La casella è stata scollegata o sostituita. Il controllo è stato fermato.", "collegamento", False)


def _operation_budget(client, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ConnectorError("Il controllo IMAP ha raggiunto il tempo massimo. Nessun risultato parziale è stato salvato.", "tempo massimo lettura", True)
    sock = getattr(client, "sock", None)
    if sock is not None:
        sock.settimeout(min(SOCKET_TIMEOUT, remaining))


@contextmanager
def _session(credentials, deadline=None):
    client = None
    deadline = deadline if deadline is not None else time.monotonic() + READ_DEADLINE_SECONDS
    try:
        addresses = _public_addresses(credentials["host"])
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConnectorError("Il server IMAP non ha completato il collegamento nel tempo previsto. Riprova.", "tempo massimo lettura", True)
        client = _PinnedIMAP4SSL(credentials["host"], addresses, timeout=min(SOCKET_TIMEOUT, remaining))
        try:
            _operation_budget(client, deadline)
            status, _ = client.login(credentials["username"], credentials["password"])
            if status != "OK":
                raise imaplib.IMAP4.error("Rejected credentials")
        except imaplib.IMAP4.abort:
            raise
        except (imaplib.IMAP4.error, UnicodeError) as exc:
            raise ConnectorError("Il provider non ha accettato l'accesso. Verifica nome utente e password per app, poi ricollega la casella.", "autorizzazione IMAP", False, True) from exc
        _operation_budget(client, deadline)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise ConnectorError("Il provider non consente la lettura della posta in arrivo. Verifica che IMAP sia attivo.", "accesso IMAP", False)
        _operation_budget(client, deadline)
        yield client
    except ssl.SSLError as exc:
        raise ConnectorError("Il certificato TLS del server IMAP non è valido. Verifica il server; la connessione sicura è obbligatoria.", "sicurezza IMAP", False) from exc
    except (OSError, TimeoutError, imaplib.IMAP4.abort) as exc:
        raise ConnectorError("Il server IMAP non risponde entro il tempo previsto. Il controllo può essere riprovato.", "connessione IMAP", True) from exc
    except imaplib.IMAP4.error as exc:
        raise ConnectorError("Il server IMAP ha negato la lettura. Verifica il collegamento e che IMAP sia attivo.", "lettura IMAP", False) from exc
    finally:
        if client is not None:
            try:
                # Never CLOSE (which can expunge); LOGOUT only.
                if getattr(client, "sock", None) is not None:
                    client.sock.settimeout(1)
                client.logout()
            except Exception:
                pass


def _read_credentials(db):
    encrypted = db.get_setting("imap_credentials", None)
    if not encrypted:
        raise ConnectorError("La casella IMAP non è collegata. Collegala nuovamente.", "autorizzazione IMAP", False, True)
    try:
        credentials = json.loads(_cipher(db).decrypt(encrypted.encode()))
        if not isinstance(credentials, dict) or credentials.get("port") != 993 or not all(isinstance(credentials.get(k), str) and credentials[k] for k in ("host", "username", "password", "email")):
            raise ValueError("Invalid IMAP credentials")
        # Stored credentials cannot introduce a URL or another port.
        credentials["host"] = _normalize_host(credentials["host"])
        return credentials
    except (InvalidToken, ValueError, TypeError, AttributeError) as exc:
        raise ConnectorError("Le credenziali IMAP salvate non sono più disponibili. Ricollega la casella.", "credenziali IMAP", False, True) from exc


def _sent_mailbox(client, deadline):
    _operation_budget(client, deadline)
    status, entries = client.list()
    if status != "OK":
        return None
    known = {"sent", "sent items", "sent messages", "inbox.sent", "inbox.sent items", "posta inviata"}
    fallback = None
    for entry in (entries or [])[:100]:
        if not isinstance(entry, bytes) or len(entry) > 2048 or b"\r" in entry or b"\n" in entry:
            continue
        match = re.fullmatch(rb'\(([^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(.+)', entry)
        if not match:
            continue
        try:
            mailbox = match.group(2).decode("ascii")
        except UnicodeError:
            continue
        flags = match.group(1).lower().split()
        if b"\\noselect" in flags:
            continue
        if b"\\sent" in flags:
            return mailbox
        name = mailbox.strip('"').lower()
        if name in known:
            fallback = mailbox
    return fallback


def _folder_candidates(client, mailbox, addresses, direction, cutoff, deadline):
    _operation_budget(client, deadline)
    status, _ = client.select(mailbox, readonly=True)
    if status != "OK":
        return None
    # UIDVALIDITY prevents a server's renumbering from reusing source identities.
    _, validity_values = client.response("UIDVALIDITY")
    validity = next((value.decode("ascii") for value in (validity_values or [])
                     if isinstance(value, bytes) and value.isdigit()), "0")
    candidates = set()
    for address in addresses:
        _operation_budget(client, deadline)
        status, data = client.uid("search", None, "SINCE", cutoff.strftime("%d-%b-%Y"), direction, '"' + address + '"')
        if status != "OK":
            raise ConnectorError("Il server IMAP non ha completato la ricerca. Nessun risultato parziale è stato salvato.", "lettura IMAP", True)
        for part in data or []:
            if isinstance(part, bytes):
                # imaplib already bounds protocol lines; cap retained IDs too.
                candidates.update(int(value) for value in part.split()[-100:] if value.isdigit())
    return {"mailbox": mailbox, "validity": validity,
            "uids": sorted(candidates, reverse=True)[:MAX_MESSAGES]}


def _mime_body(message):
    plain, rich = [], []

    def visit(part):
        if part.get_content_disposition() == "attachment" or part.get_filename():
            return
        if part.get_content_type() == "message/rfc822":
            return
        if part.is_multipart():
            for child in part.iter_parts():
                visit(child)
            return
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            return
        value = part.get_payload(decode=True)
        if not isinstance(value, bytes):
            return
        try:
            decoded = value.decode(part.get_content_charset() or "utf-8", errors="replace")[:MAX_BODY_CHARS]
        except LookupError:
            decoded = value.decode("utf-8", errors="replace")[:MAX_BODY_CHARS]
        if kind == "text/html":
            decoded = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", decoded, flags=re.I | re.S)
            decoded = html.unescape(re.sub(r"<[^>]+>", " ", decoded))
            rich.append(decoded)
        else:
            plain.append(decoded)

    visit(message)
    return "\n".join(plain or rich).strip()[:MAX_BODY_CHARS]


def _fetch_message(client, folder, uid, addresses, is_sent, cutoff, deadline):
    _operation_budget(client, deadline)
    status, size_data = client.uid("fetch", str(uid), "(UID RFC822.SIZE INTERNALDATE)")
    if status != "OK":
        raise ConnectorError("Il server IMAP non ha completato la lettura. Nessun risultato parziale è stato salvato.", "lettura IMAP", True)
    metadata = b" ".join(item for item in (size_data or []) if isinstance(item, bytes))
    size_match = re.search(rb"\bRFC822\.SIZE\s+(\d+)", metadata, re.I)
    date_match = re.search(rb'\bINTERNALDATE\s+"([^"\r\n]+)"', metadata, re.I)
    if not size_match or not date_match or int(size_match.group(1)) > MAX_MESSAGE_BYTES:
        return None
    try:
        received = parsedate_to_datetime(date_match.group(1).decode("ascii")).astimezone(timezone.utc)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None
    if received < cutoff:
        return None
    _operation_budget(client, deadline)
    # Partial BODY.PEEK puts a wire limit on content even if SIZE was dishonest.
    status, parts = client.uid("fetch", str(uid), f"(UID BODY.PEEK[]<0.{MAX_MESSAGE_BYTES}>)")
    if status != "OK":
        raise ConnectorError("Il server IMAP non ha completato la lettura. Nessun risultato parziale è stato salvato.", "lettura IMAP", True)
    raw = next((item[1] for item in (parts or []) if isinstance(item, tuple)
                and len(item) > 1 and isinstance(item[1], bytes)), None)
    if raw is None or len(raw) > MAX_MESSAGE_BYTES or len(raw) != int(size_match.group(1)):
        return None
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        sender = str(message.get("From", ""))[:1024]
        from_client = parseaddr(sender)[1].lower() in addresses
        recipients = {address.lower() for _, address in getaddresses([str(message.get("To", "")), str(message.get("Cc", ""))])}
        if is_sent:
            if not recipients.intersection(addresses):
                return None
            from_client = False
        elif not from_client:
            return None
        reference_ids = re.findall(r"<[^<>\s]{1,200}>", str(message.get("References", ""))[:4000])
        parent_ids = re.findall(r"<[^<>\s]{1,200}>", str(message.get("In-Reply-To", ""))[:1000])
        message_ids = re.findall(r"<[^<>\s]{1,200}>", str(message.get("Message-ID", ""))[:1000])
        source_id = "imap:" + hashlib.sha256(folder["mailbox"].encode()).hexdigest()[:12] + ":" + folder["validity"] + ":" + str(uid)
        root_id = (reference_ids or parent_ids or message_ids or [source_id])[0]
        return {"id": source_id, "thread_id": "imap-thread:" + hashlib.sha256(root_id.encode()).hexdigest(),
                "sender": sender, "subject": str(message.get("Subject", "(senza oggetto)"))[:1000],
                "body": _mime_body(message), "received_at": received.isoformat(), "from_client": from_client}
    except (ValueError, TypeError, MessageError, RecursionError):
        # A malformed or oversized message never makes the mailbox look empty
        # with complete coverage: this entire adapter is explicitly incomplete.
        return None


def load_imap_messages(db, contacts):
    addresses = [contact.get("email", "").strip().lower() for contact in contacts]
    if not addresses or len(addresses) > 4 or any(not _EMAIL.fullmatch(address) for address in addresses):
        raise ConnectorError("Configura da uno a quattro indirizzi email validi.", "preferenze", False)
    revision = db.get_setting("gmail_revision", 0)
    credentials = _read_credentials(db)
    _revision_guard(db, revision)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    deadline = time.monotonic() + READ_DEADLINE_SECONDS
    observed = []
    with _session(credentials, deadline=deadline) as client:
        _revision_guard(db, revision)
        sent = _sent_mailbox(client, deadline)
        inbox = _folder_candidates(client, "INBOX", addresses, "FROM", cutoff, deadline)
        if not inbox:
            raise ConnectorError("La posta in arrivo non è leggibile. Verifica che IMAP sia attivo.", "lettura IMAP", False)
        folders = [inbox]
        if sent:
            folder = _folder_candidates(client, sent, addresses, "TO", cutoff, deadline)
            if folder:
                folders.append(folder)
        # Interleave INBOX and sent candidates so outgoing replies are not
        # silently crowded out by a full incoming sample. At most 25 bodies.
        choices = []
        for index in range(MAX_MESSAGES):
            for folder in folders:
                if index < len(folder["uids"]):
                    choices.append((folder, folder["uids"][index]))
        selected = choices[:MAX_MESSAGES]
        # Keep the sample interleaved, then read each folder once to avoid an
        # extra EXAMINE round trip between every incoming and outgoing message.
        selected.sort(key=lambda choice: folders.index(choice[0]))
        active_mailbox = None
        for folder, uid in selected:
            _revision_guard(db, revision)
            if active_mailbox != folder["mailbox"]:
                _operation_budget(client, deadline)
                status, _ = client.select(folder["mailbox"], readonly=True)
                if status != "OK":
                    raise ConnectorError("La cartella IMAP non è più leggibile. Nessun risultato parziale è stato salvato.", "lettura IMAP", True)
                active_mailbox = folder["mailbox"]
            message = _fetch_message(client, folder, uid, addresses, folder is not folders[0], cutoff, deadline)
            _revision_guard(db, revision)
            if message:
                observed.append(message)
    _revision_guard(db, revision)
    threads = {}
    for message in observed:
        threads.setdefault(message["thread_id"], []).append(message)
    result = []
    for messages in threads.values():
        messages.sort(key=lambda message: (message["received_at"], message["id"]))
        latest = dict(messages[-1])
        latest["context"] = "\n\n".join("Da: " + message["sender"] + "\n" + message["body"][:4000]
                                          for message in messages[-12:])[-18000:]
        result.append(latest)
    return sorted(result, key=lambda message: message["received_at"], reverse=True)[:MAX_THREADS]


def create_imap_router(db):
    router = APIRouter(prefix="/api/mail/imap", tags=["imap"])

    @router.post("/connect")
    def connect(payload: ImapConnect):
        try:
            credentials = _credentials_from_payload(payload)
            revision = db.get_setting("gmail_revision", 0)
            with _session(credentials):
                pass
            _revision_guard(db, revision)
            encrypted = _cipher(db).encrypt(json.dumps(credentials).encode()).decode()
            with db.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if _revision_in_transaction(conn) != revision:
                    raise ConnectorError("La casella è cambiata durante il collegamento. Riprova.", "collegamento", False)
                _invalidate_in_transaction(conn, "La casella IMAP è stata collegata. Attiva nuovamente il servizio per autorizzare questa connessione.")
                clear_mail_credentials(conn)
                old_imap = conn.execute("SELECT value FROM kv WHERE key='imap_revision'").fetchone()
                updates = {"imap_credentials": encrypted, "gmail_revision": revision + 1,
                           "imap_revision": (json.loads(old_imap["value"]) if old_imap else 0) + 1,
                           "connection": {"provider": "imap", "status": "connected", "label": credentials["email"],
                                          "mail_provider": credentials["mail_provider"]}}
                for key, value in updates.items():
                    conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        except ConnectorError as exc:
            status = 409 if exc.step == "collegamento" else 502 if exc.retryable else 400
            raise HTTPException(status, exc.message) from exc
        return {"connected": True, "provider": "imap", "label": credentials["email"], "mail_provider": credentials["mail_provider"]}

    @router.post("/disconnect")
    def disconnect():
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM kv WHERE key='connection'").fetchone()
            connection = json.loads(row["value"]) if row else {}
            if connection.get("provider") != "imap":
                raise HTTPException(409, "Questa casella IMAP è già stata scollegata o sostituita.")
            _invalidate_in_transaction(conn, "La casella IMAP è stata scollegata. Il mandato ricorrente è stato rimosso.")
            clear_mail_credentials(conn)
            old_imap = conn.execute("SELECT value FROM kv WHERE key='imap_revision'").fetchone()
            updates = {"gmail_revision": _revision_in_transaction(conn) + 1,
                       "imap_revision": (json.loads(old_imap["value"]) if old_imap else 0) + 1,
                       "connection": {"provider": None, "status": "disconnected", "label": "Nessun account collegato"}}
            for key, value in updates.items():
                conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        return {"disconnected": True, "message": "Accesso locale rimosso. Puoi revocare la password per app anche nelle impostazioni del provider."}

    return router
