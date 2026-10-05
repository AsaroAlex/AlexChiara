"""Read-only Microsoft Outlook / Microsoft 365 mailbox connection.

Only delegated Mail.Read and the identity needed to label the mailbox are
requested. No Graph write, send or draft endpoint is used by this connector.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
import httpx

from .connectors import ConnectorError, _cipher, _invalidate_in_transaction, _revision_in_transaction

GRAPH_API = "https://graph.microsoft.com/v1.0"
MAIL_SCOPE = "https://graph.microsoft.com/Mail.Read"
IDENTITY_SCOPE = "https://graph.microsoft.com/User.Read"
OUTLOOK_SCOPE = "offline_access " + MAIL_SCOPE + " " + IDENTITY_SCOPE
_EMAIL = re.compile(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_MESSAGE_FIELDS = "id,conversationId,from,sender,subject,body,receivedDateTime,sentDateTime,toRecipients,ccRecipients,bccRecipients,isDraft"


def _client():
    return httpx.Client(timeout=httpx.Timeout(10.0), follow_redirects=False, trust_env=True)


def _credentials():
    tenant = os.environ.get("FILO_MICROSOFT_TENANT", "common").strip()
    if not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", tenant):
        raise ConnectorError("La configurazione Microsoft del servizio non è valida. Contatta il gestore.", "configurazione Microsoft")
    public_url = os.environ.get("FILO_PUBLIC_URL", "").strip().rstrip("/")
    if not public_url and os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
        public_url = "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"].strip().rstrip("/")
    default_redirect = (public_url or "http://127.0.0.1:8000") + "/api/outlook/oauth/callback"
    return {
        "client_id": os.environ.get("FILO_MICROSOFT_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("FILO_MICROSOFT_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("FILO_MICROSOFT_REDIRECT_URI", default_redirect),
        "tenant": tenant,
    }


def _endpoint(credentials, kind):
    return "https://login.microsoftonline.com/" + credentials["tenant"] + "/oauth2/v2.0/" + kind


def _valid_redirect(uri):
    try:
        parsed = urlsplit(uri)
        return bool(parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment
                    and (parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"})))
    except ValueError:
        return False


def _request(client, method, url, **kwargs):
    try:
        response = client.request(method, url, **kwargs)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ConnectorError("Microsoft non risponde entro il tempo previsto. Il controllo può essere riprovato.", "connessione Microsoft", True) from exc
    if response.status_code == 401:
        raise ConnectorError("L'autorizzazione Outlook è scaduta o è stata revocata. Ricollega la casella.", "autorizzazione", False, True)
    if response.status_code == 429 or response.status_code >= 500:
        raise ConnectorError("Microsoft è temporaneamente indisponibile o ha raggiunto il limite di richieste.", "lettura Outlook", True)
    if response.status_code >= 400:
        raise ConnectorError("Microsoft ha negato l'accesso. Verifica il consenso e le regole della tua organizzazione.", "accesso Microsoft")
    try:
        value = response.json()
    except ValueError as exc:
        raise ConnectorError("Microsoft ha restituito una risposta non leggibile.", "lettura Outlook", True) from exc
    if not isinstance(value, dict):
        raise ConnectorError("Microsoft ha restituito una risposta non valida.", "lettura Outlook", True)
    return value


def _validate_scopes(tokens):
    scope = tokens.get("scope")
    if not isinstance(scope, str):
        raise ConnectorError("Microsoft non ha confermato i permessi concessi. Ripeti il collegamento di sola lettura.", "autorizzazione")
    normalized = set()
    for value in scope.split():
        value = value.lower()
        if value.startswith("https://graph.microsoft.com/"):
            value = value[len("https://graph.microsoft.com/"):]
        normalized.add(value)
    # Identity scopes may be included by Microsoft, but wider Graph permissions
    # must never be accepted silently, including previously consented Mail.Send.
    if not {"mail.read", "user.read"}.issubset(normalized):
        raise ConnectorError("Il consenso di sola lettura Outlook non è stato concesso.", "autorizzazione")
    if normalized - {"mail.read", "user.read", "offline_access", "openid", "profile", "email"}:
        raise ConnectorError("Sono presenti permessi più ampi del servizio. Revoca il consenso e ripeti il collegamento di sola lettura.", "autorizzazione")


def _token_expiry(tokens):
    try:
        lifetime = int(tokens.get("expires_in", 3600))
    except (ValueError, TypeError) as exc:
        raise ConnectorError("Microsoft ha restituito una durata di accesso non valida.", "autorizzazione") from exc
    if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"] or lifetime <= 0:
        raise ConnectorError("Microsoft non ha fornito un accesso valido.", "autorizzazione", False, True)
    return time.time() + min(lifetime, 86400)


def _read_tokens(db):
    encrypted = db.get_setting("outlook_tokens", None)
    if not encrypted:
        raise ConnectorError("Outlook non è collegato. Collega nuovamente la casella.", "autorizzazione", False, True)
    try:
        tokens = json.loads(_cipher(db).decrypt(encrypted.encode()))
    except (InvalidToken, ValueError, TypeError, AttributeError) as exc:
        raise ConnectorError("Non è possibile leggere le credenziali salvate. Ricollega Outlook.", "credenziali", False, True) from exc
    if not isinstance(tokens, dict):
        raise ConnectorError("Le credenziali salvate non sono valide. Ricollega Outlook.", "credenziali", False, True)
    return tokens


def _provider_revision(conn):
    row = conn.execute("SELECT value FROM kv WHERE key='outlook_revision'").fetchone()
    return json.loads(row["value"]) if row else 0


def _save_tokens(db, tokens, expected_revision=None, expected_provider_revision=None):
    encrypted = _cipher(db).encrypt(json.dumps(tokens).encode()).decode()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if ((expected_revision is not None and _revision_in_transaction(conn) != expected_revision)
                or (expected_provider_revision is not None and _provider_revision(conn) != expected_provider_revision)):
            raise ConnectorError("Il collegamento Outlook è cambiato durante il controllo. Nessuna credenziale è stata ripristinata.", "collegamento")
        conn.execute("INSERT INTO kv(key,value) VALUES ('outlook_tokens',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(encrypted),))


def _access_token(db, client, force_refresh=False, deadline=None):
    revision = db.get_setting("gmail_revision", 0)
    provider_revision = db.get_setting("outlook_revision", 0)
    tokens = _read_tokens(db)
    if not force_refresh and tokens.get("expires_at", 0) > time.time() + 60:
        if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
            raise ConnectorError("L'accesso salvato non è valido. Ricollega Outlook.", "credenziali", False, True)
        if db.get_setting("gmail_revision", 0) != revision or db.get_setting("outlook_revision", 0) != provider_revision:
            raise ConnectorError("La casella è stata sostituita durante il controllo.", "collegamento")
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        raise ConnectorError("Il consenso Outlook deve essere rinnovato. Ricollega la casella.", "rinnovo autorizzazione", False, True)
    credentials = _credentials()
    if not credentials["client_id"] or not credentials["client_secret"]:
        raise ConnectorError("La configurazione Microsoft del servizio è incompleta. Serve l'intervento del gestore.", "configurazione Microsoft")
    request_options = {}
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConnectorError("Il controllo Outlook ha raggiunto il tempo massimo. Nessun risultato parziale è stato salvato.", "tempo massimo lettura", True)
        request_options["timeout"] = max(0.1, min(10.0, remaining))
    try:
        response = client.post(_endpoint(credentials, "token"), data={
            "client_id": credentials["client_id"], "client_secret": credentials["client_secret"],
            "refresh_token": tokens["refresh_token"], "grant_type": "refresh_token", "scope": OUTLOOK_SCOPE,
        }, **request_options)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ConnectorError("Impossibile rinnovare l'accesso: Microsoft non risponde. Riproveremo.", "rinnovo autorizzazione", True) from exc
    if response.status_code in (400, 401):
        raise ConnectorError("Microsoft richiede un nuovo consenso. Ricollega Outlook.", "rinnovo autorizzazione", False, True)
    if response.status_code == 429 or response.status_code >= 500:
        raise ConnectorError("Rinnovo Outlook temporaneamente indisponibile.", "rinnovo autorizzazione", True)
    if response.status_code >= 400:
        raise ConnectorError("Rinnovo Outlook negato. Verifica la configurazione del servizio.", "rinnovo autorizzazione")
    try:
        refreshed = response.json()
    except ValueError as exc:
        raise ConnectorError("Microsoft ha restituito un rinnovo non leggibile.", "rinnovo autorizzazione", True) from exc
    if not isinstance(refreshed, dict):
        raise ConnectorError("Microsoft ha restituito un rinnovo non valido.", "rinnovo autorizzazione", True)
    _validate_scopes(refreshed)
    expires_at = _token_expiry(refreshed)
    tokens.update(refreshed)
    tokens["expires_at"] = expires_at
    _save_tokens(db, tokens, expected_revision=revision, expected_provider_revision=provider_revision)
    return tokens["access_token"]


def _clear_credentials(conn):
    conn.execute("DELETE FROM kv WHERE key IN ('gmail_tokens','gmail_oauth_pending','outlook_tokens','imap_tokens','imap_credentials') OR key LIKE 'oauth:%' OR key LIKE 'outlook_oauth:%' OR key LIKE 'imap_oauth:%'")


def _text_body(message):
    body = message.get("body") or {}
    value = str(body.get("content", ""))[:40000]
    if str(body.get("contentType", "")).lower() == "html":
        value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.I | re.S)
        value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return value.strip()[:12000]


def _email_address(recipient):
    if not isinstance(recipient, dict) or not isinstance(recipient.get("emailAddress"), dict):
        return ""
    return str(recipient["emailAddress"].get("address", "")).strip().lower()


def _message_time(message, outgoing=False):
    field = "sentDateTime" if outgoing else "receivedDateTime"
    raw = message.get(field) or message.get("sentDateTime") or message.get("receivedDateTime")
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("Missing timezone")
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ConnectorError("Una email Outlook ha una data non leggibile. Il controllo può essere riprovato.", "lettura Outlook", True) from exc


def load_outlook_messages(db, contacts):
    """Bounded 7-day observations, including sent replies to retire old drafts.

    Pagination is intentionally not followed. The latest 25 incoming messages
    per contact and latest 100 sent messages are observations, not evidence that
    an email or response is absent from the mailbox.
    """
    addresses = [str(c.get("email", "")).strip().lower() for c in contacts]
    if not addresses or len(addresses) > 4 or any(not _EMAIL.fullmatch(a) for a in addresses):
        raise ConnectorError("Configura da uno a quattro indirizzi email validi.", "preferenze")
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
    deadline = time.monotonic() + 22
    revision = db.get_setting("gmail_revision", 0)
    provider_revision = db.get_setting("outlook_revision", 0)
    connection = db.get_connection()
    if connection.get("provider") != "outlook" or connection.get("status") != "connected":
        raise ConnectorError("Outlook non è collegato. Collega nuovamente la casella.", "collegamento", False, True)

    def ensure_current():
        if db.get_setting("gmail_revision", 0) != revision or db.get_setting("outlook_revision", 0) != provider_revision:
            raise ConnectorError("La casella è stata scollegata o sostituita durante il controllo. L'elaborazione è stata fermata.", "collegamento")
        if time.monotonic() > deadline:
            raise ConnectorError("Il controllo Outlook ha raggiunto il tempo massimo. Nessun risultato parziale è stato salvato.", "tempo massimo lettura", True)

    observed = {}
    with _client() as client:
        headers = {"Authorization": "Bearer " + _access_token(db, client, deadline=deadline),
                   "Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"'}
        refreshed = False

        def listing(url, params):
            nonlocal refreshed
            ensure_current()
            try:
                result = _request(client, "GET", url, headers=headers, params=params,
                                  timeout=max(0.1, min(10.0, deadline - time.monotonic())))
            except ConnectorError as exc:
                if not exc.expired or refreshed:
                    raise
                refreshed = True
                headers["Authorization"] = "Bearer " + _access_token(db, client, force_refresh=True, deadline=deadline)
                ensure_current()
                result = _request(client, "GET", url, headers=headers, params=params,
                                  timeout=max(0.1, min(10.0, deadline - time.monotonic())))
            ensure_current()
            values = result.get("value", [])
            if not isinstance(values, list) or any(not isinstance(m, dict) for m in values):
                raise ConnectorError("Microsoft ha restituito un elenco email non valido.", "lettura Outlook", True)
            return values

        for address in dict.fromkeys(addresses):
            # Escape the only user value inserted in OData, even after validation.
            escaped = address.replace("'", "''")
            messages = listing(GRAPH_API + "/me/messages", {
                "$filter": f"receivedDateTime ge {cutoff_text} and isDraft eq false and from/emailAddress/address eq '{escaped}'",
                "$orderby": "receivedDateTime desc", "$top": 25, "$select": _MESSAGE_FIELDS,
            })
            for message in messages[:25]:
                if message.get("isDraft") or _email_address(message.get("from")) != address:
                    continue
                received = _message_time(message)
                if received >= cutoff and isinstance(message.get("id"), str) and message["id"]:
                    observed[message["id"]] = (message, received, True)
        sent = listing(GRAPH_API + "/me/mailFolders/sentitems/messages", {
            "$filter": f"sentDateTime ge {cutoff_text} and isDraft eq false",
            "$orderby": "sentDateTime desc", "$top": 100, "$select": _MESSAGE_FIELDS,
        })
        sent_counts = dict.fromkeys(addresses, 0)
        for message in sent[:100]:
            if message.get("isDraft"):
                continue
            recipients = {_email_address(recipient) for field in ("toRecipients", "ccRecipients", "bccRecipients")
                          for recipient in (message.get(field) or [])}
            matched = [a for a in addresses if a in recipients and sent_counts[a] < 25]
            if not matched:
                continue
            received = _message_time(message, outgoing=True)
            if received < cutoff or not isinstance(message.get("id"), str) or not message["id"]:
                continue
            for address in matched:
                sent_counts[address] += 1
            observed[message["id"]] = (message, received, False)
        ensure_current()

    conversations = {}
    for message, received, from_client in observed.values():
        conversation_id = str(message.get("conversationId") or message["id"])
        conversations.setdefault(conversation_id, []).append((received, message["id"], message, from_client))
    result = []
    for conversation_id in sorted(conversations):
        messages = sorted(conversations[conversation_id], key=lambda item: (item[0], item[1]))
        received, _, latest, from_client = messages[-1]
        sender = latest.get("from") or latest.get("sender") or {}
        address = _email_address(sender)
        name = str((sender.get("emailAddress") or {}).get("name", "")).strip()
        context = []
        for _, _, item, _ in messages[-12:]:
            context.append("Da: " + _email_address(item.get("from") or item.get("sender")) + "\n" + _text_body(item)[:4000])
        result.append({"id": latest["id"], "thread_id": conversation_id,
                       "sender": (name + " <" + address + ">") if name else address,
                       "subject": str(latest.get("subject") or "(senza oggetto)"),
                       "body": _text_body(latest), "received_at": received.isoformat(),
                       "from_client": from_client, "context": "\n\n".join(context)[-18000:]})
    ensure_current()
    return result


def create_outlook_router(db):
    router = APIRouter(prefix="/api/outlook", tags=["outlook"])

    @router.get("/status")
    def status():
        try:
            credentials = _credentials()
            configured = bool(credentials["client_id"] and credentials["client_secret"] and _valid_redirect(credentials["redirect_uri"]))
            redirect_uri = credentials["redirect_uri"]
        except ConnectorError:
            configured, redirect_uri = False, ""
        connection = db.get_connection()
        return {"configured": configured, "redirect_uri": redirect_uri,
                "connected": connection.get("provider") == "outlook" and connection.get("status") == "connected",
                "label": connection.get("label", "Outlook / Microsoft 365"), "scope": OUTLOOK_SCOPE,
                "limitations": ["Sola lettura: nessun invio e nessuna bozza scritta su Outlook.",
                                 "Fino a 25 email ricevute per contatto negli ultimi sette giorni e un campione delle risposte inviate.",
                                 "Gli account aziendali possono richiedere il consenso dell'amministratore."]}

    @router.post("/oauth/start")
    def start(request: Request):
        try:
            credentials = _credentials()
        except ConnectorError as exc:
            raise HTTPException(409, exc.message) from exc
        if not credentials["client_id"] or not credentials["client_secret"]:
            raise HTTPException(409, "Il collegamento Microsoft non è ancora configurato dal gestore. Contatta l'assistenza o scegli un altro provider.")
        if not _valid_redirect(credentials["redirect_uri"]):
            raise HTTPException(409, "Il gestore deve configurare un indirizzo di ritorno HTTPS o locale valido.")
        session_id = request.cookies.get("alexchiara_session")
        if not session_id:
            raise HTTPException(403, "Apri l'applicazione prima di collegare Outlook.")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(state.encode()).hexdigest()
        # Revision + pending state are written under the same lock as connection
        # changes, so a callback cannot reuse an old authorization.
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = {"expires": time.time() + 600, "session_id": session_id,
                       "revision": _revision_in_transaction(conn), "outlook_revision": _provider_revision(conn),
                       "verifier": _cipher(db).encrypt(verifier.encode()).decode()}
            conn.execute("INSERT INTO kv(key,value) VALUES (?,?)", ("outlook_oauth:" + digest, json.dumps(pending)))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {"client_id": credentials["client_id"], "redirect_uri": credentials["redirect_uri"],
                  "response_type": "code", "response_mode": "query", "scope": OUTLOOK_SCOPE,
                  "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return {"authorization_url": _endpoint(credentials, "authorize") + "?" + urlencode(params)}

    @router.get("/oauth/callback")
    def callback(request: Request, state: str = Query(default="", max_length=200), code: str = Query(default="", max_length=4000), error: str = Query(default="", max_length=200)):
        digest = hashlib.sha256(state.encode()).hexdigest()
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM kv WHERE key=?", ("outlook_oauth:" + digest,)).fetchone()
            conn.execute("DELETE FROM kv WHERE key=?", ("outlook_oauth:" + digest,))
        if not row:
            raise HTTPException(400, "Richiesta di collegamento non valida o già utilizzata.")
        pending = json.loads(row["value"])
        if not secrets.compare_digest(pending.get("session_id", ""), request.cookies.get("alexchiara_session", "")):
            raise HTTPException(400, "Completa il collegamento nello stesso browser che lo ha avviato.")
        if pending.get("expires", 0) < time.time():
            raise HTTPException(400, "Il collegamento è scaduto. Avvialo nuovamente.")
        if error or not code:
            raise HTTPException(400, "Collegamento annullato o consenso non concesso. Nessun accesso è stato salvato.")
        try:
            credentials = _credentials()
            verifier = _cipher(db).decrypt(pending["verifier"].encode()).decode()
            with _client() as client:
                tokens = _request(client, "POST", _endpoint(credentials, "token"), data={
                    "client_id": credentials["client_id"], "client_secret": credentials["client_secret"],
                    "redirect_uri": credentials["redirect_uri"], "code": code, "code_verifier": verifier,
                    "grant_type": "authorization_code", "scope": OUTLOOK_SCOPE,
                })
                _validate_scopes(tokens)
                tokens["expires_at"] = _token_expiry(tokens)
                profile = _request(client, "GET", GRAPH_API + "/me", headers={"Authorization": "Bearer " + tokens["access_token"]}, params={"$select": "mail,userPrincipalName"})
            label = profile.get("mail") or profile.get("userPrincipalName") or "Casella Outlook"
            if not isinstance(label, str) or len(label) > 320:
                label = "Casella Outlook"
            encrypted = _cipher(db).encrypt(json.dumps(tokens).encode()).decode()
            with db.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                revision = _revision_in_transaction(conn)
                provider_revision = _provider_revision(conn)
                if revision != pending["revision"] or provider_revision != pending["outlook_revision"]:
                    raise ConnectorError("Il collegamento è cambiato mentre Microsoft rispondeva. Avvia un nuovo collegamento.", "collegamento")
                _invalidate_in_transaction(conn, "La casella Outlook è stata collegata. Attiva nuovamente il servizio per autorizzare questa connessione.")
                _clear_credentials(conn)
                updates = {"outlook_tokens": encrypted, "gmail_revision": revision + 1, "outlook_revision": provider_revision + 1,
                           "connection": {"provider": "outlook", "status": "connected", "label": label}}
                for key, value in updates.items():
                    conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        except ConnectorError as exc:
            raise HTTPException(502, exc.message) from exc
        except (InvalidToken, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, "Le credenziali di collegamento non sono più disponibili. Avvia un nuovo collegamento.") from exc
        return RedirectResponse("/app?provider=outlook&connected=1" if getattr(request.state, "filo_user", None) else "/?provider=outlook&connected=1", status_code=303)

    @router.post("/disconnect")
    def disconnect():
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM kv WHERE key='connection'").fetchone()
            connection = json.loads(row["value"]) if row else {}
            if connection.get("provider") not in {None, "outlook"}:
                raise HTTPException(409, "La casella collegata è cambiata. Ricarica la pagina prima di scollegarla.")
            _invalidate_in_transaction(conn, "Outlook è stato scollegato. Il mandato ricorrente è stato rimosso.")
            _clear_credentials(conn)
            updates = {"gmail_revision": _revision_in_transaction(conn) + 1, "outlook_revision": _provider_revision(conn) + 1,
                       "connection": {"provider": "outlook", "status": "disconnected", "label": "Outlook scollegato"}}
            for key, value in updates.items():
                conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        return {"disconnected": True, "message": "Accesso locale rimosso. Puoi revocare anche il consenso dalle impostazioni del tuo account Microsoft."}

    return router
