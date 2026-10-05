"""Persistent accounts and revocable browser sessions, independent of workspaces.

The existing deployment password belongs only to the explicit owner account.
Registration never grants ownership of the original workspace.
"""
from contextlib import contextmanager
import hashlib
import html
import ipaddress
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit
import uuid

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .mailer import mail_configured, send_safely


logger = logging.getLogger(__name__)
OWNER_ID = "owner"
SESSION_COOKIE = "filo_session"
SESSION_TTL = 7 * 24 * 3600
GUEST_TTL = 3600
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}\Z")
# Each scrypt derivation allocates 16 MiB: bound concurrent hashing so a burst
# of logins cannot exhaust a small instance's memory.
_HASHING = threading.BoundedSemaphore(4)
_LOGIN_ERROR = "Email o password non corrette."
RESET_TTL = 3600
# (window in seconds, attempts per address and identity, attempts per address)
THROTTLE_LIMITS = {
    "login": (600, 8, 60), "register": (3600, 5, 12),
    "reset-request": (3600, 3, 12), "reset-confirm": (3600, 10, 30), "reauth": (600, 8, 60),
}
_PASSWORD_LENGTH = f"La password deve contenere da {PASSWORD_MIN_LENGTH} a {PASSWORD_MAX_LENGTH} caratteri."
_REGISTER_ERROR = "Registrazione non riuscita. Se hai già un account, accedi."


class AccountError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _password_hash(password):
    salt = secrets.token_bytes(16)
    with _HASHING:
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def _password_matches(password, encoded):
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        # Only our current parameters are accepted; a corrupt record cannot
        # turn an authentication request into an unbounded allocation.
        if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1") or len(salt) != 32 or len(expected) != 64:
            return False
        with _HASHING:
            derived = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
        return secrets.compare_digest(derived, bytes.fromhex(expected))
    except (AttributeError, ValueError, TypeError, UnicodeError):
        return False


def _identifier(value):
    return value.strip().lower() if isinstance(value, str) else ""


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AccountStore:
    def __init__(self, path, legacy_username=None, legacy_password=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        legacy_username = os.environ.get("FILO_ACCESS_USERNAME", "filo") if legacy_username is None else legacy_username
        legacy_password = os.environ.get("FILO_ACCESS_PASSWORD", "") if legacy_password is None else legacy_password
        if isinstance(legacy_username, bytes):
            legacy_username = legacy_username.decode("utf-8")
        if isinstance(legacy_password, bytes):
            legacy_password = legacy_password.decode("utf-8")
        self.owner_identifier = _identifier(legacy_username) or "filo"
        self.owner_enabled = bool(legacy_password)
        self._dummy_hash = _password_hash(secrets.token_urlsafe(32))
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner', 'member')),
                    password_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS account_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS account_session_expiry ON account_sessions(expires_at);
                CREATE TABLE IF NOT EXISTS auth_attempts (
                    key TEXT PRIMARY KEY,
                    count INTEGER NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_attempt_expiry ON auth_attempts(expires_at);
                CREATE TABLE IF NOT EXISTS password_resets (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    used_at REAL
                );
                CREATE INDEX IF NOT EXISTS password_reset_user ON password_resets(user_id);
            """)
            if self.owner_enabled:
                owner = conn.execute("SELECT email,password_hash FROM users WHERE id=?", (OWNER_ID,)).fetchone()
                taken = conn.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE AND id<>?", (self.owner_identifier, OWNER_ID)).fetchone()
                if taken:
                    # Never shadow a registered member or crash the whole service
                    # because the configured owner name collides with an account.
                    logger.error("FILO_ACCESS_USERNAME coincide con l'email di un account registrato: accesso del gestore disattivato finché non scegli un altro nome utente.")
                    self.owner_enabled = False
                elif owner is None:
                    # A legacy email-shaped username is reserved before any
                    # registration is accepted, so it cannot be claimed.
                    conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (OWNER_ID, self.owner_identifier, "Amministratore", "owner", _password_hash(legacy_password), time.time()))
                else:
                    if owner["email"] != self.owner_identifier:
                        # A renamed owner keeps the same workspace and frees the old name.
                        conn.execute("UPDATE users SET email=? WHERE id=?", (self.owner_identifier, OWNER_ID))
                    if not _password_matches(legacy_password, owner["password_hash"]):
                        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (_password_hash(legacy_password), OWNER_ID))
                        conn.execute("DELETE FROM account_sessions WHERE user_id=?", (OWNER_ID,))
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _public_user(row):
        return {key: row[key] for key in ("id", "email", "name", "role")}

    def get_user(self, user_id):
        with self.connection() as conn:
            row = conn.execute("SELECT id,email,name,role FROM users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(row) if row else None

    def list_users(self):
        with self.connection() as conn:
            rows = conn.execute("SELECT id,email,name,role FROM users ORDER BY created_at,id").fetchall()
        return [self._public_user(row) for row in rows]

    def resolve_session(self, cookie):
        if not isinstance(cookie, str) or not _TOKEN.fullmatch(cookie):
            return None
        now = time.time()
        with self.connection() as conn:
            row = conn.execute("""SELECT s.user_id,s.csrf_token,s.expires_at,u.id,u.email,u.name,u.role
                FROM account_sessions s LEFT JOIN users u ON u.id=s.user_id WHERE s.token_hash=?""", (_digest(cookie),)).fetchone()
            if not row:
                return None
            if row["expires_at"] <= now or (row["user_id"] and not row["id"]) or (row["user_id"] == OWNER_ID and not self.owner_enabled):
                conn.execute("DELETE FROM account_sessions WHERE token_hash=?", (_digest(cookie),))
                return None
        user = self._public_user(row) if row["user_id"] else None
        return {"authenticated": user is not None, "user": user, "csrf_token": row["csrf_token"], "expires_at": row["expires_at"]}

    def _new_session(self, user_id=None, old_cookie=None):
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        expiry = now + (SESSION_TTL if user_id else GUEST_TTL)
        with self.connection() as conn:
            conn.execute("DELETE FROM account_sessions WHERE expires_at<=?", (now,))
            if old_cookie and isinstance(old_cookie, str) and _TOKEN.fullmatch(old_cookie):
                conn.execute("DELETE FROM account_sessions WHERE token_hash=?", (_digest(old_cookie),))
            # Guests have no workspace or account data; keep their storage
            # bounded even when clients repeatedly discard cookies.
            conn.execute("""DELETE FROM account_sessions WHERE user_id IS NULL AND token_hash IN
                (SELECT token_hash FROM account_sessions WHERE user_id IS NULL ORDER BY created_at DESC LIMIT -1 OFFSET 1999)""")
            conn.execute("INSERT INTO account_sessions VALUES (?,?,?,?,?)", (_digest(cookie), user_id, csrf, now, expiry))
        return cookie, {"authenticated": bool(user_id), "user": self.get_user(user_id) if user_id else None, "csrf_token": csrf, "expires_at": expiry}

    def new_guest_session(self):
        return self._new_session()

    def csrf_valid(self, cookie, header):
        if not isinstance(header, str) or not _TOKEN.fullmatch(header):
            return False
        session = self.resolve_session(cookie)
        return bool(session and secrets.compare_digest(session["csrf_token"], header))

    def logout(self, cookie):
        if isinstance(cookie, str) and _TOKEN.fullmatch(cookie):
            with self.connection() as conn:
                conn.execute("DELETE FROM account_sessions WHERE token_hash=?", (_digest(cookie),))

    def _require_session(self, cookie):
        if not self.resolve_session(cookie):
            raise AccountError(403, "Sessione scaduta. Ricarica la pagina prima di continuare.")

    @staticmethod
    def _pair_key(kind, ip, identifier):
        return _digest(f"{kind}\0{ip}\0{identifier}")

    def _throttle(self, kind, ip, identifier):
        now = time.time()
        # Hash identifiers/IPs to avoid storing submitted addresses for failed
        # attempts. Both pair and IP limits prevent easy username spraying.
        duration, pair_limit, ip_limit = THROTTLE_LIMITS[kind]
        pair = self._pair_key(kind, ip, identifier)
        address = _digest(f"{kind}\0{ip}")
        blocked = False
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM auth_attempts WHERE expires_at<=?", (now,))
            for key, limit in ((pair, pair_limit), (address, ip_limit)):
                row = conn.execute("SELECT count FROM auth_attempts WHERE key=?", (key,)).fetchone()
                count = row["count"] if row else 0
                if count >= limit:
                    blocked = True
                elif row:
                    conn.execute("UPDATE auth_attempts SET count=count+1 WHERE key=?", (key,))
                else:
                    conn.execute("INSERT INTO auth_attempts VALUES (?,?,?)", (key, 1, now + duration))
            # At most 20,000 throttle buckets remain; the newest live buckets
            # are retained and old/expired buckets are removed first.
            conn.execute("DELETE FROM auth_attempts WHERE key IN (SELECT key FROM auth_attempts ORDER BY expires_at DESC LIMIT -1 OFFSET 20000)")
        if blocked:
            raise AccountError(429, "Troppi tentativi. Attendi qualche minuto e riprova.")

    def login(self, identifier, password, cookie, ip=""):
        self._require_session(cookie)
        identifier, ip = _identifier(identifier), str(ip)[:200]
        self._throttle("login", ip, identifier[:254])
        valid_input = isinstance(password, str) and 0 < len(password) <= PASSWORD_MAX_LENGTH and 0 < len(identifier) <= 254
        with self.connection() as conn:
            if identifier == self.owner_identifier and self.owner_enabled:
                row = conn.execute("SELECT * FROM users WHERE id=?", (OWNER_ID,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE AND role='member'", (identifier,)).fetchone() if len(identifier) <= 254 else None
        # Unknown identities still perform the same expensive hash operation.
        matched = _password_matches(password if valid_input else "invalid-input", row["password_hash"] if row else self._dummy_hash)
        if not valid_input or not row or not matched:
            raise AccountError(401, _LOGIN_ERROR)
        # Only failed guesses accumulate for this address and identity.
        with self.connection() as conn:
            conn.execute("DELETE FROM auth_attempts WHERE key=?", (self._pair_key("login", ip, identifier[:254]),))
        return self._new_session(row["id"], cookie)

    def register(self, name, email, password, cookie, ip=""):
        self._require_session(cookie)
        email, name = _identifier(email), name.strip() if isinstance(name, str) else ""
        self._throttle("register", str(ip)[:200], email[:254])
        if not name or len(name) > 120 or any(ord(char) < 32 for char in name):
            raise AccountError(422, "Inserisci un nome valido, fino a 120 caratteri.")
        local, _, domain = email.partition("@")
        if len(email) > 254 or len(local) > 64 or not _EMAIL.fullmatch(email) or ".." in email or local.startswith(".") or local.endswith(".") or any(label.startswith("-") or label.endswith("-") or not label for label in domain.split(".")):
            raise AccountError(422, "Inserisci un indirizzo email valido.")
        if not isinstance(password, str) or not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            raise AccountError(422, "La password deve contenere da 12 a 128 caratteri.")
        if email == self.owner_identifier:
            raise AccountError(409, _REGISTER_ERROR)
        password_hash = _password_hash(password)
        user_id = uuid.uuid4().hex
        try:
            with self.connection() as conn:
                conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (user_id, email, name, "member", password_hash, time.time()))
        except sqlite3.IntegrityError:
            raise AccountError(409, _REGISTER_ERROR) from None
        return self._new_session(user_id, cookie)

    # Recovery, password change and deletion apply to registered members only:
    # the owner keeps its deployment password (FILO_ACCESS_PASSWORD).

    def request_password_reset(self, email, cookie, ip=""):
        """Return (user, token) for a member, or None; callers answer identically."""
        self._require_session(cookie)
        email = _identifier(email)
        self._throttle("reset-request", str(ip)[:200], email[:254])
        if not email or len(email) > 254:
            return None
        now = time.time()
        token = secrets.token_urlsafe(32)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM password_resets WHERE expires_at<=? OR used_at IS NOT NULL", (now,))
            row = conn.execute("SELECT id,email,name FROM users WHERE email=? COLLATE NOCASE AND role='member'", (email,)).fetchone()
            if not row:
                return None
            # Only the newest link works: a forwarded older email becomes useless.
            conn.execute("DELETE FROM password_resets WHERE user_id=?", (row["id"],))
            conn.execute("INSERT INTO password_resets VALUES (?,?,?,?,NULL)", (_digest(token), row["id"], now, now + RESET_TTL))
        return {"id": row["id"], "email": row["email"], "name": row["name"]}, token

    def reset_password(self, token, password, cookie, ip=""):
        self._require_session(cookie)
        self._throttle("reset-confirm", str(ip)[:200], "")
        if not isinstance(password, str) or not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            raise AccountError(422, _PASSWORD_LENGTH)
        invalid = AccountError(400, "Il link non è valido o è scaduto. Chiedi un nuovo link per reimpostare la password.")
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise invalid
        password_hash = _password_hash(password)
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT r.user_id FROM password_resets r JOIN users u ON u.id=r.user_id
                WHERE r.token_hash=? AND r.used_at IS NULL AND r.expires_at>? AND u.role='member'""", (_digest(token), now)).fetchone()
            if not row:
                raise invalid
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, row["user_id"]))
            conn.execute("DELETE FROM password_resets WHERE user_id=?", (row["user_id"],))
            # Whoever knew the old password is signed out everywhere.
            conn.execute("DELETE FROM account_sessions WHERE user_id=?", (row["user_id"],))
        return self._new_session(row["user_id"], cookie)

    def _reauthenticate(self, user_id, password, ip):
        self._throttle("reauth", str(ip)[:200], user_id)
        with self.connection() as conn:
            row = conn.execute("SELECT password_hash,role FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or row["role"] != "member":
            raise AccountError(409, "La password del gestore si cambia dalla variabile FILO_ACCESS_PASSWORD del servizio Railway.")
        valid = isinstance(password, str) and 0 < len(password) <= PASSWORD_MAX_LENGTH
        if not _password_matches(password if valid else "invalid-input", row["password_hash"]) or not valid:
            raise AccountError(401, "La password attuale non è corretta.")

    def change_password(self, user_id, current, new, cookie, ip=""):
        self._reauthenticate(user_id, current, ip)
        if not isinstance(new, str) or not PASSWORD_MIN_LENGTH <= len(new) <= PASSWORD_MAX_LENGTH:
            raise AccountError(422, _PASSWORD_LENGTH)
        if new == current:
            raise AccountError(422, "Scegli una password diversa da quella attuale.")
        password_hash = _password_hash(new)
        current_hash = _digest(cookie) if isinstance(cookie, str) else ""
        with self.connection() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
            # Other browsers are signed out; this one stays signed in.
            conn.execute("DELETE FROM account_sessions WHERE user_id=? AND token_hash<>?", (user_id, current_hash))
            conn.execute("DELETE FROM password_resets WHERE user_id=?", (user_id,))

    def confirm_deletion(self, user_id, password, ip=""):
        self._reauthenticate(user_id, password, ip)

    def delete_member(self, user_id):
        """Remove the member, their sessions and reset links (cascade)."""
        with self.connection() as conn:
            return conn.execute("DELETE FROM users WHERE id=? AND role='member'", (user_id,)).rowcount == 1


def client_address(request):
    """The client IP used for throttling.

    Behind Railway the edge sets X-Real-IP; X-Forwarded-For can carry a value
    chosen by the client, so it is never used here.
    """
    header = os.environ.get("FILO_CLIENT_IP_HEADER", "").strip()
    if header:
        try:
            return str(ipaddress.ip_address(request.headers.get(header, "").strip()))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @model_validator(mode="before")
    @classmethod
    def accept_identifier(cls, value):
        if isinstance(value, dict) and "identifier" in value and "email" not in value:
            value = {**value, "email": value["identifier"]}
            del value["identifier"]
        return value


class RegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class PasswordForgotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=254)


class PasswordResetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=43, max_length=43)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


def session_response(request, cookie, session, extra=None):
    response = JSONResponse({**session, **(extra or {})})
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(SESSION_COOKIE, cookie, max_age=SESSION_TTL if session["authenticated"] else GUEST_TTL, path="/", httponly=True, secure=request.url.scheme == "https", samesite="lax")
    return response


def public_origin(request):
    """Origin for links sent by email: configured, never taken from the Host header."""
    configured = os.environ.get("FILO_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip().rstrip("/")
    if domain:
        return "https://" + domain
    return str(request.base_url).rstrip("/")


def reset_email(user, link):
    name = user["name"].strip() or "ciao"
    text = (f"Ciao {name},\n\n"
            f"abbiamo ricevuto una richiesta per reimpostare la password del tuo account Spazelia ({user['email']}).\n\n"
            f"Per scegliere una nuova password apri questo link entro 60 minuti:\n{link}\n\n"
            "Se non hai chiesto tu il cambio, ignora questa email: la password attuale resta valida.\n\n"
            "Il team di Spazelia\n")
    safe_name, safe_link, safe_email = html.escape(name), html.escape(link, quote=True), html.escape(user["email"])
    body = (f'<p>Ciao {safe_name},</p><p>abbiamo ricevuto una richiesta per reimpostare la password del tuo account Spazelia ({safe_email}).</p>'
            f'<p><a href="{safe_link}" style="display:inline-block;padding:12px 18px;border-radius:10px;background:#b44922;color:#ffffff;text-decoration:none;font-weight:600">Scegli una nuova password</a></p>'
            f'<p>Il link vale 60 minuti. Se il pulsante non funziona, copia questo indirizzo nel browser:<br>{safe_link}</p>'
            '<p>Se non hai chiesto tu il cambio, ignora questa email: la password attuale resta valida.</p><p>Il team di Spazelia</p>')
    return "Reimposta la password di Spazelia", text, body


def create_account_router(store):
    router = APIRouter(prefix="/api/auth", tags=["accounts"])
    response_session = session_response

    def check_csrf(request):
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                same_origin = parsed.scheme in ("http", "https") and parsed.netloc.lower() == request.headers.get("host", "").lower() and not parsed.path and not parsed.query and not parsed.fragment
            except ValueError:
                same_origin = False
            if not same_origin:
                raise AccountError(403, "Origine non autorizzata.")
        if not store.csrf_valid(request.cookies.get(SESSION_COOKIE), request.headers.get("x-csrf-token")):
            raise AccountError(403, "Sessione non valida. Ricarica la pagina prima di continuare.")

    def failure(exc):
        headers = {"Cache-Control": "no-store"}
        if exc.status_code == 429:
            headers["Retry-After"] = "600"
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)

    @router.get("/session")
    def session(request: Request):
        cookie = request.cookies.get(SESSION_COOKIE)
        current = store.resolve_session(cookie)
        features = {"password_reset_available": mail_configured()}
        if current:
            # Expiration is absolute, not extended by ordinary polling.
            return JSONResponse({**current, **features}, headers={"Cache-Control": "no-store"})
        cookie, current = store.new_guest_session()
        return response_session(request, cookie, current, features)

    @router.post("/password/forgot")
    def forgot_password(payload: PasswordForgotInput, request: Request, background: BackgroundTasks):
        try:
            check_csrf(request)
            if not mail_configured():
                raise AccountError(503, "Il recupero della password via email non è ancora attivo. Contatta l'assistenza di Spazelia.")
            requested = store.request_password_reset(payload.email, request.cookies.get(SESSION_COOKIE), client_address(request))
            if requested:
                user, token = requested
                subject, text, body = reset_email(user, f"{public_origin(request)}/reset-password?token={token}")
                # Sent after the response, so its timing does not reveal whether the account exists.
                background.add_task(send_safely, user["email"], subject, text, body, "spazelia-reset-" + _digest(token)[:32])
            return JSONResponse({"message": "Se l'indirizzo appartiene a un account Spazelia, riceverai a breve un'email con il link per scegliere una nuova password. Il link vale 60 minuti."}, headers={"Cache-Control": "no-store"})
        except AccountError as exc:
            return failure(exc)

    @router.post("/password/reset")
    def reset_password(payload: PasswordResetInput, request: Request):
        try:
            check_csrf(request)
            cookie, current = store.reset_password(payload.token, payload.password, request.cookies.get(SESSION_COOKIE), client_address(request))
            return response_session(request, cookie, current, {"message": "Password aggiornata. Sei di nuovo nel tuo spazio."})
        except AccountError as exc:
            return failure(exc)

    @router.post("/login")
    def login(payload: LoginInput, request: Request):
        try:
            check_csrf(request)
            cookie, current = store.login(payload.email, payload.password, request.cookies.get(SESSION_COOKIE), client_address(request))
            return response_session(request, cookie, current)
        except AccountError as exc:
            return failure(exc)

    @router.post("/register")
    def register(payload: RegistrationInput, request: Request):
        try:
            check_csrf(request)
            current = store.resolve_session(request.cookies.get(SESSION_COOKIE))
            if current and current["authenticated"]:
                raise AccountError(409, "Hai già effettuato l'accesso. Esci prima di creare un altro account.")
            cookie, current = store.register(payload.name, payload.email, payload.password, request.cookies.get(SESSION_COOKIE), client_address(request))
            return response_session(request, cookie, current)
        except AccountError as exc:
            return failure(exc)

    @router.post("/logout")
    def logout(request: Request):
        try:
            check_csrf(request)
            store.logout(request.cookies.get(SESSION_COOKIE))
            cookie, current = store.new_guest_session()
            return response_session(request, cookie, current)
        except AccountError as exc:
            return failure(exc)

    return router
