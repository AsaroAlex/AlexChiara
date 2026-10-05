"""Personal reply reminders from observations already saved by authorized checks.

Creating a reminder never adds a contact, activates a mandate, reads a mailbox,
or sends a notification outside this application. A bounded mailbox check can
prove an observed arrival, but cannot prove that somebody never replied.
"""
from datetime import date, datetime, timedelta, timezone
import json
import re
import unicodedata
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .briefing import mailbox_scope, migrate_scope_keys


ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc
REAL_PROVIDERS = {"gmail", "outlook", "imap", "imported"}
NOTICE = "L'avviso apparirà qui nell'app al prossimo controllo autorizzato: non invio email o notifiche esterne."
EMAIL_PATTERN = r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}"


def _email(value):
    return str(value or "").strip().casefold()


def _valid_email(value):
    if not re.fullmatch(EMAIL_PATTERN, value) or len(value) > 254:
        return False
    local, domain = value.rsplit("@", 1)
    return (len(local) <= 64 and not local.startswith(".") and not local.endswith(".")
            and ".." not in local and all(label and len(label) <= 63
                                             and not label.startswith("-") and not label.endswith("-")
                                             for label in domain.split(".")))


class WatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    day: date

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not _valid_email(value):
            raise ValueError("Indirizzo email non valido.")
        return _email(value)

    @field_validator("name")
    @classmethod
    def readable_name(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Il nome contiene caratteri non consentiti.")
        return value

    @field_validator("day", mode="before")
    @classmethod
    def iso_date(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Indica il giorno nel formato AAAA-MM-GG.")
        return value


def _initialize(db):
    with db.connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS watch_requests (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                day TEXT NOT NULL,
                created_at TEXT NOT NULL,
                done_at TEXT,
                provider TEXT NOT NULL,
                scope_key TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS watch_requests_scope
                ON watch_requests(provider, scope_key, done_at, day);
        """)
        migrate_scope_keys(conn, "watch_requests")


def _now(value=None):
    value = value or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now deve includere il fuso orario.")
    return value.astimezone(UTC)


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return _now(parsed)
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


def _context(db):
    connection = db.get_connection() or {}
    service = db.get_setting("service", {}) or {}
    provider = connection.get("provider") or service.get("provider")
    return connection, service, provider, mailbox_scope(db, provider)


def _match(watch, snapshots, now):
    created = _stamp(watch["created_at"])
    if not created:
        return None
    candidates = []
    for snapshot in snapshots:
        observed = _stamp(snapshot["checked_at"])
        if not observed or observed < created or observed > now:
            continue
        try:
            messages = json.loads(snapshot["messages"])
        except (ValueError, TypeError):
            continue
        for message in messages:
            if not isinstance(message, dict) or message.get("from_client") is not True:
                continue
            received = _stamp(message.get("received_at"))
            if (_email(message.get("email")) != watch["email"] or not received
                    or received < created or received > observed
                    or received.astimezone(ROME).date().isoformat() != watch["day"]):
                continue
            source_id = message.get("id")
            if source_id:
                candidates.append((received, str(source_id), {
                    "source_id": str(source_id),
                    "thread_id": str(message.get("thread_id") or source_id),
                    "received_at": received.isoformat(),
                }))
    return min(candidates, key=lambda candidate: candidate[:2])[2] if candidates else None


def build_watch_summary(db, now=None):
    """Pure database read, scoped to the current mailbox; stale arrivals remain evidence."""
    now = _now(now)
    today = now.astimezone(ROME).date().isoformat()
    connection, service, provider, scope_key = _context(db)
    active_emails = {_email(contact.get("email")) for contact in service.get("priority_contacts", [])}
    with db.connection() as conn:
        initialized = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watch_requests'").fetchone()
        watches = [dict(row) for row in conn.execute("""
            SELECT * FROM watch_requests WHERE provider=? AND scope_key=? AND done_at IS NULL
            ORDER BY day, created_at, id
        """, (provider, scope_key))] if initialized and provider in REAL_PROVIDERS else []
        # Only checks made after the oldest reminder can match it, and mail
        # adapters read a seven-day window, so later checks cannot see older days.
        created = [_stamp(watch["created_at"]) for watch in watches]
        window_start = (min(created) - timedelta(days=1)).isoformat() if created and all(created) else ""
        window_end = (date.fromisoformat(max(watch["day"] for watch in watches)) + timedelta(days=9)).isoformat() if watches else ""
        snapshots = [dict(row) for row in conn.execute("""
            SELECT checked_at, messages FROM briefing_snapshots WHERE provider=? AND scope_key=?
            AND checked_at>=? AND checked_at<=?
            ORDER BY checked_at,run_id
        """, (provider, scope_key, window_start, window_end))] if watches else []
    items = []
    for watch in watches:
        match = _match(watch, snapshots, now)
        status = "matched" if match else "expired" if watch["day"] < today else "waiting"
        label = (f"{watch['name']} ha scritto" if match else
                 "Periodo concluso, esito non verificato" if status == "expired" else
                 "In attesa del prossimo controllo")
        next_step = ("Apri il messaggio e verifica se richiede una risposta." if match else
                     "Puoi chiudere questo promemoria o crearne uno per un altro giorno."
                     if status == "expired" else NOTICE)
        item = {key: watch[key] for key in ("id", "name", "email", "day")}
        item.update(status=status, label=label, next_step=next_step,
                    contact_active=watch["email"] in active_emails)
        if match:
            item["match"] = match
        items.append(item)
    ranks = {"matched": 0, "waiting": 1, "expired": 2}
    items.sort(key=lambda item: (ranks[item["status"]], item["day"], item["id"]))
    mandate = service.get("mandate") or {}
    monitoring_active = bool(connection.get("status") == "connected"
                             and connection.get("provider") == provider
                             and provider in REAL_PROVIDERS and service.get("provider") == provider
                             and service.get("status") == "active"
                             and mandate.get("read") and mandate.get("draft") and not mandate.get("send"))
    from .service import next_slot
    next_check_at = next_slot(service, now) if monitoring_active else None
    watching_today = provider in {"gmail", "outlook", "imap"} and any(item["status"] == "waiting" and item["day"] == today
                                                and item["contact_active"] for item in items)
    if monitoring_active and watching_today:
        with db.connection() as conn:
            inflight = conn.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','retry_wait') LIMIT 1").fetchone()
            recent = conn.execute("""
                SELECT COALESCE(finished_at,started_at) AS checked_at FROM runs WHERE provider=?
                AND COALESCE(finished_at,started_at) IS NOT NULL ORDER BY checked_at DESC,id DESC LIMIT 1
            """, (provider,)).fetchone()
        checked = _stamp(recent["checked_at"]) if recent else None
        next_check_at = None if inflight else max(now, checked + timedelta(minutes=5) if checked else now).isoformat()
    return {"items": items, "matched": [item for item in items if item["status"] == "matched"],
            "next_check_at": next_check_at,
            "monitoring_active": monitoring_active, "notice": NOTICE}


def needs_watch_poll(db, now=None):
    """Whether today's unresolved email reminder needs an authorized recheck."""
    now = _now(now)
    connection = db.get_connection() or {}
    if connection.get("provider") not in {"gmail", "outlook", "imap"} or connection.get("status") != "connected":
        return False
    today = now.astimezone(ROME).date().isoformat()
    with db.connection() as conn:
        # Called every scheduler tick: skip the full summary without a reminder for today.
        initialized = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watch_requests'").fetchone()
        pending = initialized and conn.execute("SELECT 1 FROM watch_requests WHERE day=? AND done_at IS NULL LIMIT 1", (today,)).fetchone()
    if not pending:
        return False
    summary = build_watch_summary(db, now)
    today = now.astimezone(ROME).date().isoformat()
    return bool(summary["monitoring_active"] and any(
        item["status"] == "waiting" and item["day"] == today and item["contact_active"]
        for item in summary["items"]
    ))


def create_watches_router(db, *, clock=None):
    """Mount inside the application's existing authentication and CSRF middleware."""
    _initialize(db)
    router = APIRouter(prefix="/api/watches", tags=["watches"])

    def current_time():
        return _now(clock() if clock else None)

    @router.get("")
    def summary():
        return build_watch_summary(db, current_time())

    @router.post("", status_code=201)
    def add_watch(payload: WatchInput):
        now = current_time()
        today = now.astimezone(ROME).date()
        if not today <= payload.day <= today + timedelta(days=30):
            raise HTTPException(status_code=422, detail="Scegli un giorno da oggi ai prossimi 30 giorni (Europe/Rome).")
        watch_id = uuid4().hex
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            connection, service, provider, scope_key = _context(db)
            if (provider not in REAL_PROVIDERS or connection.get("provider") != provider
                    or connection.get("status") != "connected"):
                raise HTTPException(status_code=409, detail="Collega la tua casella prima di creare l'avviso.")
            contact = next((contact for contact in service.get("priority_contacts", [])
                            if _email(contact.get("email")) == payload.email), None)
            if contact is None:
                raise HTTPException(status_code=400, detail="Scegli un contatto già presente tra i contatti prioritari. Aggiungilo prima nelle impostazioni.")
            # Canonical contact identity avoids turning an arbitrary submitted
            # name into a claimed observation of a different person.
            name = str(contact.get("name") or payload.email)
            existing = conn.execute("""
                SELECT id FROM watch_requests WHERE provider=? AND scope_key=? AND email=? AND day=? AND done_at IS NULL
                ORDER BY created_at,id LIMIT 1
            """, (provider, scope_key, payload.email, payload.day.isoformat())).fetchone()
            if existing:
                watch_id = existing["id"]
            else:
                conn.execute("""
                    INSERT INTO watch_requests(id,name,email,day,created_at,provider,scope_key)
                    VALUES (?,?,?,?,?,?,?)
                """, (watch_id, name, payload.email, payload.day.isoformat(), now.isoformat(), provider, scope_key))
        item = next((item for item in build_watch_summary(db, now)["items"] if item["id"] == watch_id), {
            "id": watch_id, "name": name, "email": payload.email, "day": payload.day.isoformat(),
            "status": "waiting", "label": "In attesa del prossimo controllo", "next_step": NOTICE,
            "contact_active": True,
        })
        return {"item": item, "notice": NOTICE}

    def dismiss(watch_id):
        _, _, provider, scope_key = _context(db)
        with db.connection() as conn:
            changed = conn.execute("""
                UPDATE watch_requests SET done_at=? WHERE id=? AND provider=? AND scope_key=? AND done_at IS NULL
            """, (current_time().isoformat(), watch_id, provider, scope_key)).rowcount
        if not changed:
            raise HTTPException(status_code=404, detail="Promemoria non trovato.")
        return {"dismissed": True, "id": watch_id}

    @router.delete("/{watch_id}")
    def delete_watch(watch_id: str = Path(min_length=1, max_length=64)):
        return dismiss(watch_id)

    @router.post("/{watch_id}/dismiss")
    def dismiss_watch(watch_id: str = Path(min_length=1, max_length=64)):
        return dismiss(watch_id)

    return router


def _fold(value):
    return "".join(char for char in unicodedata.normalize("NFKD", str(value).casefold())
                   if not unicodedata.combining(char))


def propose_watch(db, message, now=None):
    """Propose a known contact only, with no persistence or automatic authorization."""
    folded = _fold(message)
    if not (re.search(r"\b(?:domani|oggi)\b", folded) and re.search(r"\bse\b", folded)
            and re.search(r"\b(?:risponde|risponda|scrive|scriva)\b", folded)
            and re.search(r"\b(?:avvisami|dimmelo|dimmi|segnalamelo|fammi sapere)\b", folded)):
        return None
    connection, service, provider, _ = _context(db)
    if provider not in REAL_PROVIDERS or connection.get("status") != "connected":
        return {"reply": "Per seguire una risposta vera, collega la tua casella e scegli i contatti prioritari. " + NOTICE}
    contacts = [contact for contact in service.get("priority_contacts", [])
                if _valid_email(_email(contact.get("email")))]
    addresses = list(dict.fromkeys(_email(value) for value in re.findall(EMAIL_PATTERN, message)))
    if addresses:
        matches = [contact for contact in contacts if _email(contact["email"]) in addresses]
        if len(addresses) != 1 or len(matches) != 1:
            return {"reply": "Indicami una sola email già presente tra i contatti prioritari. Se manca, aggiungila prima nelle impostazioni: non aggiungo contatti automaticamente."}
    else:
        full_matches, partial_matches = [], []
        for contact in contacts:
            name = _fold(contact.get("name", "")).strip()
            tokens = [token for token in re.findall(r"\w+", name) if len(token) >= 3]
            if name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", folded):
                full_matches.append(contact)
            elif any(re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", folded) for token in tokens):
                partial_matches.append(contact)
        matches = full_matches or partial_matches
        if len(matches) != 1:
            return {"reply": "Mi serve un'email precisa per questo avviso: indica quella di un contatto prioritario già configurato, così seguo la persona giusta."}
    contact = matches[0]
    day = _now(now).astimezone(ROME).date() + timedelta(days=int(bool(re.search(r"\bdomani\b", folded))))
    return {"reply": f"Ti propongo un avviso per {contact['name']} il {day.strftime('%d/%m')}. Conferma per salvarlo. " + NOTICE,
            "watch_suggestion": {"name": contact["name"], "email": _email(contact["email"]), "day": day.isoformat()}}
