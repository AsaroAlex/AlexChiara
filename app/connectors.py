"""Read-only email integration. Provider data never changes the service mandate."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import time
from datetime import datetime, timezone
from email.utils import parseaddr
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
import httpx

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class ConnectorError(Exception):
    def __init__(self, message: str, step: str = "lettura", retryable: bool = False, expired: bool = False):
        super().__init__(message)
        self.message = message
        self.step = step
        self.retryable = retryable
        self.expired = expired


def _client():
    # Honor the environment's CA bundle/proxy; never turn TLS verification off.
    return httpx.Client(timeout=httpx.Timeout(10.0), follow_redirects=False, trust_env=True)


def _cipher(db):
    key_path = Path(db.path).parent / "gmail.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as key_file:
            key_file.write(Fernet.generate_key())
    try:
        return Fernet(key_path.read_bytes())
    except (ValueError, OSError) as exc:
        raise ConnectorError("Chiave locale delle credenziali non disponibile. Ripristina la chiave o ricollega la casella.", "credenziali", False, True) from exc


def _save_tokens(db, tokens, expected_revision=None):
    value = _cipher(db).encrypt(json.dumps(tokens).encode()).decode()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if expected_revision is not None and _revision_in_transaction(conn) != expected_revision:
            raise ConnectorError("Il collegamento email è cambiato durante il controllo. Nessuna credenziale è stata ripristinata.", "collegamento", False)
        conn.execute("INSERT INTO kv(key,value) VALUES ('gmail_tokens',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(value),))


def _revision_in_transaction(conn):
    row = conn.execute("SELECT value FROM kv WHERE key='gmail_revision'").fetchone()
    return json.loads(row["value"]) if row else 0


def _invalidate_in_transaction(conn, reason):
    row = conn.execute("SELECT value FROM kv WHERE key='service'").fetchone()
    if row:
        service = json.loads(row["value"])
        service.update(status="inactive", mandate=None, activated_at=None)
        conn.execute("UPDATE kv SET value=? WHERE key='service'", (json.dumps(service, ensure_ascii=False),))
    conn.execute("UPDATE runs SET status='failed',retryable=0,next_attempt_at=NULL,error=?,error_step='mandato',finished_at=? WHERE status IN ('queued','retry_wait')", (reason, datetime.now(timezone.utc).isoformat()))
    conn.execute("INSERT INTO actions(action,created_at,details) VALUES ('connection_mandate_invalidated',?,?)", (datetime.now(timezone.utc).isoformat(), json.dumps({"reason": reason})))


def _invalidate_mandate(db, reason):
    """A connection change must not reuse authorization for another mailbox."""
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _invalidate_in_transaction(conn, reason)


def _read_tokens(db):
    encrypted = db.get_setting("gmail_tokens", None)
    if not encrypted:
        raise ConnectorError("Gmail non è collegato. Collega nuovamente la casella.", "autorizzazione", False, True)
    try:
        return json.loads(_cipher(db).decrypt(encrypted.encode()))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ConnectorError("Non è possibile leggere le credenziali salvate. Ricollega Gmail.", "credenziali", False, True) from exc


def _credentials():
    return {
        "client_id": os.environ.get("FILO_GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("FILO_GOOGLE_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("FILO_GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/gmail/oauth/callback"),
    }


def _request(client, method, url, **kwargs):
    try:
        response = client.request(method, url, **kwargs)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ConnectorError("Google non risponde entro il tempo previsto. Il controllo può essere riprovato.", "connessione Google", True) from exc
    if response.status_code == 401:
        raise ConnectorError("L'autorizzazione Gmail è scaduta o è stata revocata. Ricollega la casella.", "autorizzazione", False, True)
    if response.status_code == 429 or response.status_code >= 500:
        raise ConnectorError("Google è temporaneamente indisponibile o ha raggiunto il limite di richieste.", "lettura Gmail", True)
    if response.status_code >= 400:
        # Never expose response bodies that might contain tokens or personal data.
        raise ConnectorError("Google ha negato l'operazione. Verifica accessi, consenso e domini consentiti.", "accesso Google", False)
    try:
        return response.json()
    except ValueError as exc:
        raise ConnectorError("Google ha restituito una risposta non leggibile.", "lettura Gmail", True) from exc


def _access_token(db, client, force_refresh=False):
    revision = db.get_setting("gmail_revision", 0)
    tokens = _read_tokens(db)
    if not force_refresh and tokens.get("expires_at", 0) > time.time() + 60:
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        raise ConnectorError("Il consenso Gmail deve essere rinnovato. Ricollega la casella.", "rinnovo autorizzazione", False, True)
    credentials = _credentials()
    if not credentials["client_id"] or not credentials["client_secret"]:
        raise ConnectorError("La configurazione Google del servizio è incompleta. Serve l'intervento del gestore.", "configurazione Google", False)
    try:
        response = client.post(TOKEN_URL, data={
            "client_id": credentials["client_id"], "client_secret": credentials["client_secret"],
            "refresh_token": tokens["refresh_token"], "grant_type": "refresh_token",
        })
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ConnectorError("Impossibile rinnovare l'accesso: Google non risponde. Riproveremo.", "rinnovo autorizzazione", True) from exc
    if response.status_code in (400, 401):
        raise ConnectorError("Google richiede un nuovo consenso. Ricollega Gmail.", "rinnovo autorizzazione", False, True)
    if response.status_code == 429 or response.status_code >= 500:
        raise ConnectorError("Rinnovo Gmail temporaneamente indisponibile.", "rinnovo autorizzazione", True)
    if response.status_code >= 400:
        raise ConnectorError("Rinnovo Gmail negato. Verifica la configurazione del servizio.", "rinnovo autorizzazione", False)
    refreshed = response.json()
    if "access_token" not in refreshed:
        raise ConnectorError("Google non ha restituito un accesso valido.", "rinnovo autorizzazione", False, True)
    tokens.update(refreshed)
    tokens["expires_at"] = time.time() + int(refreshed.get("expires_in", 3600))
    _save_tokens(db, tokens, expected_revision=revision)
    return tokens["access_token"]


def _decode_part(payload):
    """Only text, bounded; attachments and HTML instructions are never executed."""
    parts = payload.get("parts", [])
    if parts:
        plain = [p for p in parts if p.get("mimeType") == "text/plain"]
        return "\n".join(_decode_part(p) for p in (plain or parts))[:12000]
    if payload.get("filename"):
        return ""
    encoded = payload.get("body", {}).get("data", "")
    if not encoded:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", errors="replace")[:12000]
    except (ValueError, TypeError):
        return "[contenuto non leggibile]"
    if payload.get("mimeType") == "text/html":
        decoded = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", decoded, flags=re.I | re.S)
        decoded = html.unescape(re.sub(r"<[^>]+>", " ", decoded))
    return decoded.strip()


def _gmail_messages(db, contacts):
    addresses = [c["email"].strip().lower() for c in contacts]
    # Defense in depth: callers cannot inject additional Gmail search operators.
    if not addresses or len(addresses) > 4 or any(not re.fullmatch(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", a) for a in addresses):
        raise ConnectorError("Configura da uno a quattro indirizzi email validi.", "preferenze", False)
    query = "newer_than:7d {" + " ".join("from:" + a for a in addresses) + "}"
    with _client() as client:
        deadline = time.monotonic() + 22
        revision = db.get_setting("gmail_revision", 0)
        access = _access_token(db, client)
        headers = {"Authorization": "Bearer " + access}
        try:
            listing = _request(client, "GET", GMAIL_API + "/messages", headers=headers, params={"q": query, "maxResults": 25})
        except ConnectorError as exc:
            if not exc.expired:
                raise
            headers["Authorization"] = "Bearer " + _access_token(db, client, force_refresh=True)
            listing = _request(client, "GET", GMAIL_API + "/messages", headers=headers, params={"q": query, "maxResults": 25})
        thread_ids = list(dict.fromkeys(m["threadId"] for m in listing.get("messages", []) if m.get("threadId")))[:20]
        result = []
        for thread_id in thread_ids:
            if db.get_setting("gmail_revision", 0) != revision:
                raise ConnectorError("Gmail è stato scollegato o sostituito durante il controllo. L'elaborazione è stata fermata.", "collegamento", False)
            if time.monotonic() > deadline:
                raise ConnectorError("Il controllo Gmail ha raggiunto il tempo massimo prima di completare tutte le conversazioni. Nessun risultato parziale è stato salvato.", "tempo massimo lettura", True)
            thread = _request(client, "GET", GMAIL_API + "/threads/" + thread_id, headers=headers, params={"format": "full"})
            messages = sorted(thread.get("messages", []), key=lambda m: int(m.get("internalDate", 0)))
            context = []
            latest = None
            for message in messages[-12:]:
                payload = message.get("payload", {})
                values = {h["name"].lower(): h.get("value", "") for h in payload.get("headers", [])}
                sender = values.get("from", "")
                body = _decode_part(payload)
                context.append("Da: " + sender + "\n" + body[:4000])
                latest = {"id": message["id"], "thread_id": thread_id, "sender": sender,
                          "subject": values.get("subject", "(senza oggetto)"), "body": body,
                          "received_at": datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, timezone.utc).isoformat(),
                          "from_client": parseaddr(sender)[1].strip().lower() in addresses}
            if latest:
                # Keep the last observed message even after an outgoing reply.
                # Analysis excludes from_client=False from drafts, while the
                # briefing can retire an older suggestion using this evidence.
                latest["context"] = "\n\n".join(context)[-18000:]
                result.append(latest)
        return result


def load_messages(provider, contacts, db):
    if provider == "demo":
        from .demo import get_messages
        return get_messages(contacts)
    if provider == "gmail":
        return _gmail_messages(db, contacts)
    if provider == "outlook":
        from .outlook import load_outlook_messages
        return load_outlook_messages(db, contacts)
    if provider == "imap":
        from .imap_mail import load_imap_messages
        return load_imap_messages(db, contacts)
    raise ConnectorError("Collega una casella prima di avviare il servizio.", "collegamento", False)


def create_router(db):
    router = APIRouter(prefix="/api/gmail", tags=["gmail"])

    @router.get("/status")
    def status():
        credentials = _credentials()
        connection = db.get_connection()
        return {"configured": bool(credentials["client_id"] and credentials["client_secret"]),
                "redirect_uri": credentials["redirect_uri"],
                "connected": connection.get("provider") == "gmail" and connection.get("status") == "connected",
                "label": connection.get("label", "Gmail"), "scope": GMAIL_SCOPE,
                "limitations": ["Sola lettura: nessun invio e nessuna bozza scritta su Gmail.",
                                 "Collegamento reale da verificare con un account autorizzato.",
                                 "Bozze locali da rivedere prima di usare."]}

    @router.post("/oauth/start")
    def start(request: Request):
        credentials = _credentials()
        if not credentials["client_id"] or not credentials["client_secret"]:
            raise HTTPException(409, "Mancano FILO_GOOGLE_CLIENT_ID e FILO_GOOGLE_CLIENT_SECRET nelle variabili del sito. Apri la guida al collegamento Gmail.")
        if not credentials["redirect_uri"].startswith(("http://127.0.0.1:", "http://localhost:", "https://")):
            raise HTTPException(409, "Il gestore deve configurare un indirizzo di ritorno HTTPS o locale valido.")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(state.encode()).hexdigest()
        session_id = request.cookies.get("alexchiara_session")
        if not session_id:
            raise HTTPException(403, "Apri l'applicazione prima di collegare Gmail.")
        db.set_setting("oauth:" + digest, {"expires": time.time() + 600, "session_id": session_id,
                                         "revision": db.get_setting("gmail_revision", 0),
                                         "verifier": _cipher(db).encrypt(verifier.encode()).decode()})
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {"client_id": credentials["client_id"], "redirect_uri": credentials["redirect_uri"],
                  "response_type": "code", "scope": GMAIL_SCOPE, "access_type": "offline", "prompt": "consent",
                  "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return {"authorization_url": AUTHORIZE_URL + "?" + urlencode(params)}

    @router.get("/oauth/callback")
    def callback(request: Request, state: str = Query(default="", max_length=200), code: str = Query(default="", max_length=4000), error: str = Query(default="", max_length=200)):
        digest = hashlib.sha256(state.encode()).hexdigest()
        # Consume once in a single write transaction, even if provider exchange fails.
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM kv WHERE key = ?", ("oauth:" + digest,)).fetchone()
            conn.execute("DELETE FROM kv WHERE key = ?", ("oauth:" + digest,))
        if not row:
            raise HTTPException(400, "Richiesta di collegamento non valida o già utilizzata.")
        pending = json.loads(row["value"])
        if not secrets.compare_digest(pending.get("session_id", ""), request.cookies.get("alexchiara_session", "")):
            raise HTTPException(400, "Completa il collegamento nello stesso browser che lo ha avviato.")
        if pending["expires"] < time.time():
            raise HTTPException(400, "Il collegamento è scaduto. Avvialo nuovamente.")
        if error or not code:
            raise HTTPException(400, "Collegamento annullato o consenso non concesso. Nessun accesso è stato salvato.")
        credentials = _credentials()
        verifier = _cipher(db).decrypt(pending["verifier"].encode()).decode()
        try:
            with _client() as client:
                tokens = _request(client, "POST", TOKEN_URL, data={**credentials, "code": code,
                                  "code_verifier": verifier, "grant_type": "authorization_code"})
                if "access_token" not in tokens:
                    raise ConnectorError("Google non ha fornito un accesso valido.", "autorizzazione", False)
                granted = tokens.get("scope", GMAIL_SCOPE).split()
                if GMAIL_SCOPE not in granted:
                    raise ConnectorError("Il consenso di sola lettura Gmail non è stato concesso.", "autorizzazione", False)
                # No wider token may be silently accepted by this product.
                if any(s != GMAIL_SCOPE for s in granted):
                    raise ConnectorError("Sono presenti permessi più ampi del servizio. Revoca il consenso e ripeti il collegamento di sola lettura.", "autorizzazione", False)
                profile = _request(client, "GET", GMAIL_API + "/profile", headers={"Authorization": "Bearer " + tokens["access_token"]})
                tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 3600))
                encrypted = _cipher(db).encrypt(json.dumps(tokens).encode()).decode()
                with db.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    revision = _revision_in_transaction(conn)
                    if revision != pending["revision"]:
                        raise ConnectorError("Il collegamento è cambiato mentre Google rispondeva. Avvia un nuovo collegamento.", "collegamento", False)
                    from .mail_providers import clear_mail_credentials
                    clear_mail_credentials(conn)
                    _invalidate_in_transaction(conn, "La casella Gmail è stata collegata. Attiva nuovamente il servizio per autorizzare questa connessione.")
                    updates = {"gmail_tokens": encrypted, "gmail_revision": revision + 1,
                               "connection": {"provider": "gmail", "status": "connected", "label": profile.get("emailAddress", "Casella Gmail")}}
                    for key, value in updates.items():
                        conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        except ConnectorError as exc:
            raise HTTPException(502, exc.message) from exc
        return RedirectResponse("/app?gmail=connected" if getattr(request.state, "filo_user", None) else "/?gmail=connected", status_code=303)

    @router.post("/disconnect")
    def disconnect():
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM kv WHERE key='connection'").fetchone()
            current = json.loads(row["value"]) if row else {}
            if current.get("provider") not in (None, "gmail", "demo"):
                raise HTTPException(409, "Questa casella non è Gmail. Usa il collegamento attivo per scollegarla.")
            _invalidate_in_transaction(conn, "Gmail è stato scollegato. Il mandato ricorrente è stato rimosso.")
            from .mail_providers import clear_mail_credentials
            clear_mail_credentials(conn)
            updates = {"gmail_tokens": None, "gmail_revision": _revision_in_transaction(conn) + 1,
                       "connection": {"provider": "gmail", "status": "disconnected", "label": "Gmail scollegato"}}
            for key, value in updates.items():
                conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        # Local unlink only. Provider-wide revocation is deliberately user-controlled.
        return {"disconnected": True, "message": "Accesso locale rimosso. Puoi revocare anche il consenso dalle impostazioni del tuo account Google."}

    return router
