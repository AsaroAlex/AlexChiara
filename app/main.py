"""Public Spazelia site and authenticated, isolated workspaces."""
import asyncio
from contextlib import asynccontextmanager, closing
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import ai
from .agenda import agenda_summary, create_agenda_router
from .business import business_summary, create_business_router
from .business_catalog import list_services
from .business_chat import propose_business_service
from .briefing import mailbox_scope
from .company import COMPANY_FIELDS, OPTIONAL_COMPANY_FIELDS
from .real_data import real_data_only, visible_service, workspace_data
from .watches import build_watch_summary, create_watches_router, propose_watch
from .connectors import ConnectorError, create_router, load_messages
from .mail_providers import REAL_MAIL_PROVIDERS, clear_mail_credentials, create_mail_router
from .db import Database, iso_now
from .models import Action, Activation, ChatInput, CompanyInput, DemoFailure, Preferences
from .service import Scheduler

CATALOG = list_services()
logger = logging.getLogger(__name__)


def _italian_message(error):
    """Readable Italian text for a validation error; the browser shows it as is."""
    kind, ctx = str(error.get("type", "")), error.get("ctx") or {}
    if kind == "value_error":
        return str(error.get("msg", "")).removeprefix("Value error, ") or "Valore non valido."
    if kind == "missing":
        return "Campo obbligatorio."
    if kind == "string_too_short":
        return "Compila questo campo." if ctx.get("min_length") == 1 else f"Inserisci almeno {ctx.get('min_length')} caratteri."
    if kind == "string_too_long":
        return f"Inserisci al massimo {ctx.get('max_length')} caratteri."
    if kind == "too_short":
        return f"Inserisci almeno {ctx.get('min_length')} elementi."
    if kind == "too_long":
        return f"Inserisci al massimo {ctx.get('max_length')} elementi."
    if kind.startswith(("date", "datetime", "time", "timezone")):
        return "Indica una data valida."
    if kind in ("greater_than_equal", "greater_than"):
        return f"Indica un valore di almeno {ctx.get('ge', ctx.get('gt'))}."
    if kind in ("less_than_equal", "less_than"):
        return f"Indica un valore fino a {ctx.get('le', ctx.get('lt'))}."
    if kind.startswith(("int", "float", "decimal")):
        return "Indica un numero valido."
    if kind.startswith("bool"):
        return "Indica sì o no."
    if kind in ("literal_error", "enum"):
        return "Scegli uno dei valori previsti."
    if kind == "string_pattern_mismatch":
        return "Il formato non è valido."
    if kind == "extra_forbidden":
        return "Campo non previsto."
    if kind in ("json_invalid", "model_type", "dict_type", "list_type"):
        return "Il contenuto inviato non è valido."
    if kind.startswith("string"):
        return "Indica un testo valido."
    return "Valore non valido."


def validation_errors(exc):
    # Never echo submitted values (passwords, tokens): location, text and type only.
    return [{"loc": error["loc"], "msg": _italian_message(error), "type": error["type"]} for error in exc.errors()]


def recent_oauth_error(db):
    """The last provider return that failed, shown once the browser is back."""
    error = db.get_setting("mail_oauth_error")
    if not isinstance(error, dict) or not isinstance(error.get("at"), (int, float)) or time.time() - error["at"] > 900:
        return None
    return {"provider": error.get("provider"), "message": error.get("message")}
_HOST_HEADER = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]{2,45}\])(?::[0-9]{1,5})?")


def _request_host(request):
    """Return the lowercase host name only for a well-formed Host header."""
    header = request.headers.get("host", "")
    if not _HOST_HEADER.fullmatch(header):
        return None
    return urlsplit("//" + header).hostname


def create_workspace_app(data_dir=None, start_worker=True):
    """Internal workspace application; the public factory adds account auth."""
    runtime = Path(data_dir or os.environ.get("ALEXCHIARA_DATA_DIR") or Path(__file__).resolve().parent.parent / ".runtime")
    db = Database(runtime / "alexchiara.sqlite3")
    scheduler = Scheduler(db)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(title="Spazelia — servizi per la tua attività", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db
    app.state.scheduler = scheduler
    app.state.runtime = runtime
    app.include_router(create_agenda_router(db))
    app.include_router(create_watches_router(db))
    app.include_router(create_business_router(db))
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver"}
    allowed_hosts.update(host.strip().lower() for host in os.environ.get("ALEXCHIARA_ALLOWED_HOSTS", "").split(",") if host.strip())

    @app.middleware("http")
    async def local_session_boundary(request: Request, call_next):
        try:
            host = _request_host(request)
        except ValueError:
            host = None
        if not host or host.lower() not in allowed_hosts:
            return JSONResponse({"detail": "Host non autorizzato per questo spazio locale."}, status_code=400)
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                same_origin = parsed.scheme in ("http", "https") and parsed.netloc.lower() == request.headers.get("host", "").lower() and not parsed.path and not parsed.query and not parsed.fragment
            except ValueError:
                same_origin = False
            if not same_origin:
                return JSONResponse({"detail": "Origine non autorizzata."}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.scope["path"].startswith("/api/"):
            if not getattr(request.state, "filo_session", None) and not db.csrf_valid(request.cookies.get("alexchiara_session"), request.headers.get("x-csrf-token")):
                return JSONResponse({"detail": "Sessione non valida. Ricarica la pagina prima di continuare."}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            chunks, total = [], 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > 64 * 1024:
                    return JSONResponse({"detail": "Richiesta troppo grande. Il limite è 64 KiB."}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.scope["path"].startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ConnectorError)
    async def connector_error(request, exc):
        return JSONResponse({"detail": str(exc), "error_step": exc.step, "retryable": exc.retryable}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def workspace_validation_error(request, exc):
        return JSONResponse({"detail": validation_errors(exc)}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "scheduler": "running" if scheduler.alive() else "disabled", "persistence": "sqlite", "can_send": False}

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        session = db.session(request.cookies.get("alexchiara_session"))
        company, service, connection, runs, approvals, briefing = workspace_data(db)
        company = {**dict.fromkeys(COMPANY_FIELDS, ""), **company}
        ai_status = ai.ai_status() if hasattr(ai, "ai_status") else {"mode": "deterministic", "configured": False, "enabled": False, "label": "Analisi locale deterministica"}
        result = {
            "user": getattr(request.state, "filo_user", None),
            "company": company, "service": service, "connection": connection,
            "runs": runs, "approvals": approvals, "actions": [] if real_data_only() else db.list_actions(), "catalog": CATALOG,
            "csrf_token": getattr(request.state, "filo_session", {}).get("csrf_token", session["csrf"]), "mode": "real-mail" if real_data_only() else "local-test", "analysis_mode": "deterministic", "ai": ai_status,
            "demo_failure": None if real_data_only() else db.get_setting("demo_failure"),
            "briefing": briefing,
            "briefing_example": None,
            "real_data_only": real_data_only(),
            "mail_oauth_error": recent_oauth_error(db),
            "watches": build_watch_summary(db),
            "agenda": agenda_summary(db),
            "business": business_summary(db),
            "limitations": [
                "Ogni account ha uno spazio separato: il server deve restare attivo per i controlli programmati.",
                "La posta collegata viene letta senza inviare email o modificare la casella.",
                "Le bozze richiedono revisione umana: approvarle le segna come verificate, senza inviarle.",
                "Le bozze usano regole locali e richiedono revisione umana.",
                "I servizi aziendali usano i dati inseriti da te e preparano documenti locali da rivedere.",
                "Calendario esterno, SDI, banche, invio PEC e telefonia richiedono integrazioni dedicate.",
            ],
        }
        response = JSONResponse(result)
        response.set_cookie("alexchiara_session", session["id"], httponly=True, secure=request.url.scheme == "https", samesite="lax", max_age=7 * 24 * 3600, path="/")
        return response

    @app.put("/api/company")
    def company(payload: CompanyInput):
        value = payload.model_dump()
        existing = db.get_setting("company", {})
        # Older clients send only the original four fields. Their updates must
        # preserve saved administrative data; an explicit empty string clears it.
        for field in OPTIONAL_COMPANY_FIELDS:
            if field not in payload.model_fields_set:
                value[field] = existing.get(field, "")
        value["demo"] = False
        db.set_setting("company", value)
        db.log_action("company_updated")
        return {"company": value}

    @app.post("/api/chat")
    def chat(payload: ChatInput):
        watch = propose_watch(db, payload.message)
        if watch:
            return {"supported": bool(watch.get("watch_suggestion")), "service_id": None, **watch}
        business = propose_business_service(payload.message)
        if business:
            return business
        message = payload.message.lower()
        send_requested = bool(re.search(r"\b(invia|inviare|inviami|inviate|manda|mandare|spedisci|send)\b", message))
        is_email = any(word in message for word in ("email", "e-mail", "posta", "client", "preventiv", "prioritar", "bozz"))
        future_service = any(word in message for word in ("agenda", "appuntament", "calendario", "social", "instagram", "facebook"))
        if future_service and not is_email:
            return {"supported": False, "reply": "Puoi aggiungere gli appuntamenti dalla panoramica e scaricare l'ordine del giorno. Per sincronizzare un calendario esterno serve ancora un'integrazione dedicata. Nel catalogo trovi anche verbali e attività delle riunioni.", "service_id": None}
        if not is_email:
            return {"supported": False, "reply": "Dimmi cosa vuoi organizzare: email, preventivi, incassi, acquisti, commesse, scadenze o documenti. Ti propongo il servizio da aprire, poi inserisci i dati della tua attività.", "service_id": None}
        service = visible_service(db)
        preferences = {key: service[key] for key in ("priority_contacts", "hour", "minute", "timezone")}
        hour_match = re.search(r"\balle\s+(\d{1,2})(?:[:.](\d{2}))?\b", message)
        if hour_match and 0 <= int(hour_match[1]) <= 23 and 0 <= int(hour_match[2] or 0) <= 59:
            preferences["hour"] = int(hour_match[1])
            preferences["minute"] = int(hour_match[2] or 0)
        addresses = list(dict.fromkeys(re.findall(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}", message)))[:4]
        if addresses:
            known = {c["email"].lower(): c["name"] for c in preferences["priority_contacts"]}
            preferences["priority_contacts"] = [{"name": known.get(email, email.split("@")[0].replace(".", " ").title()), "email": email} for email in addresses]
        reply = "Ti propongo un controllo quotidiano delle email dei contatti prioritari: individuo richieste in attesa e preparo bozze con la firma della tua attività. Verifica contatti, orario e anteprima; il servizio parte solo quando lo attivi."
        if send_requested:
            reply += " L'invio automatico non è disponibile: ogni bozza resta da verificare e nessuna email viene inviata."
        return {"supported": True, "reply": reply, "service_id": "priority-email", "preferences": preferences}

    @app.post("/api/connection/demo")
    def connect_demo():
        if real_data_only():
            raise HTTPException(403, "Questo spazio usa soltanto email reali. Collega la tua casella.")
        connection = {"provider": "demo", "status": "connected", "label": "Posta dimostrativa — Studio Riva"}
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            service = db.get_setting("service")
            previous = db.get_connection()
            if service.get("provider") not in (None, "demo"):
                service.update(status="inactive", provider=None, mandate=None, activated_at=None)
                conn.execute("UPDATE kv SET value=? WHERE key='service'", (json.dumps(service, ensure_ascii=False),))
                conn.execute("UPDATE runs SET status='failed',error='La casella collegata è cambiata. Attiva un nuovo mandato.',error_step='mandato',retryable=0,next_attempt_at=NULL,finished_at=? WHERE status IN ('queued','retry_wait')", (iso_now(),))
            updates = {"connection": connection, "demo_failure": None}
            if previous.get("provider") in REAL_MAIL_PROVIDERS:
                clear_mail_credentials(conn)
                updates.update(gmail_tokens=None, gmail_oauth_pending=None, gmail_revision=db.get_setting("gmail_revision", 0) + 1)
            for key, value in updates.items():
                conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))
            scheduler._log_in_transaction(conn, "demo_connected", None, {})
        return {"connection": connection}

    @app.post("/api/service/preview")
    def preview(payload: Preferences):
        connection = db.get_connection()
        if real_data_only() and connection.get("provider") not in REAL_MAIL_PROVIDERS:
            raise HTTPException(409, "Collega la tua casella per controllare le email reali.")
        if real_data_only() and db.get_setting("company").get("demo"):
            raise HTTPException(409, "Compila il nome del tuo studio nella pagina La tua azienda prima del primo controllo.")
        if connection["status"] != "connected":
            raise HTTPException(409, "Collega una casella prima dell'anteprima.")
        preferences = payload.model_dump()
        scope = mailbox_scope(db, connection["provider"])
        messages = scheduler._bounded(lambda: load_messages(connection["provider"], preferences["priority_contacts"], db))
        # Preview is always local. External processing starts only in a configured,
        # explicitly activated demo service and never receives Gmail data.
        result = ai.analyze_messages(messages, db.get_setting("company"), preferences["priority_contacts"])
        current = db.get_connection()
        if current.get("status") != "connected" or current.get("provider") != connection["provider"] or mailbox_scope(db, connection["provider"]) != scope:
            raise HTTPException(409, "La casella è cambiata durante l'anteprima. Riprova con la connessione attuale.")
        return {**result, "demo": connection["provider"] == "demo", "provider": connection["provider"]}

    @app.put("/api/service/preferences")
    def preferences(payload: Preferences):
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            service = json.loads(conn.execute("SELECT value FROM kv WHERE key='service'").fetchone()["value"])
            old_contacts = {contact["email"].lower() for contact in service["priority_contacts"]}
            new_contacts = {contact.email for contact in payload.priority_contacts}
            service.update(payload.model_dump())
            conn.execute("UPDATE kv SET value=? WHERE key='service'", (json.dumps(service, ensure_ascii=False),))
            if old_contacts != new_contacts:
                conn.execute("UPDATE runs SET status='failed',error='I contatti prioritari sono cambiati. Avvia un nuovo controllo.',error_step='preferenze',retryable=0,next_attempt_at=NULL,finished_at=? WHERE status IN ('queued','retry_wait')", (iso_now(),))
            scheduler._log_in_transaction(conn, "preferences_updated", "priority-email", {"contacts_changed": old_contacts != new_contacts})
        return {"service": visible_service(db)}

    @app.post("/api/service/activate")
    def activate(payload: Activation):
        if real_data_only() and db.get_setting("company").get("demo"):
            raise HTTPException(409, "Compila il nome del tuo studio nella pagina La tua azienda prima di attivare i controlli.")
        authorization = payload.authorization
        if authorization.send or not authorization.read or not authorization.draft:
            raise HTTPException(403, "Autorizza solo lettura e preparazione di bozze. L'invio non è consentito.")
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            connection = db.get_connection()
            if connection["status"] != "connected" or (real_data_only() and connection.get("provider") not in REAL_MAIL_PROVIDERS):
                raise HTTPException(409, "Collega la tua casella prima di attivare il servizio.")
            service = db.get_setting("service")
            old_contacts = {contact["email"].lower() for contact in service["priority_contacts"]}
            new_contacts = {contact.email for contact in payload.priority_contacts}
            service.update(payload.model_dump(exclude={"authorization"}))
            service.update(status="active", provider=connection["provider"], activated_at=iso_now(), mandate={
                "read": True, "draft": True, "send": False, "authorized_at": iso_now(),
                "description": "Leggere solo i thread dei contatti prioritari e preparare bozze da verificare. Nessuna autorizzazione all'invio.",
            })
            conn.execute("UPDATE kv SET value=? WHERE key='service'", (json.dumps(service, ensure_ascii=False),))
            if old_contacts != new_contacts:
                conn.execute("UPDATE runs SET status='failed',error='I contatti prioritari sono cambiati. Avvia un nuovo controllo.',error_step='preferenze',retryable=0,next_attempt_at=NULL,finished_at=? WHERE status IN ('queued','retry_wait')", (iso_now(),))
            scheduler._log_in_transaction(conn, "service_activated", "priority-email", {"provider": connection["provider"], "send": False})
        return {"service": visible_service(db)}

    @app.post("/api/service/action")
    def service_action(payload: Action):
        action = payload.action
        if action == "run":
            return {"run": scheduler.enqueue()}
        # One write transaction: concurrent clicks or an activation cannot
        # interleave with this read-modify-write of the service.
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            service = json.loads(conn.execute("SELECT value FROM kv WHERE key='service'").fetchone()["value"])
            if action == "pause":
                if service["status"] != "active":
                    raise HTTPException(409, "Il servizio deve essere attivo per metterlo in pausa.")
                service["status"] = "paused"
            elif action == "resume":
                if service["status"] != "paused":
                    raise HTTPException(409, "Il servizio deve essere in pausa per riprenderlo.")
                connection = db.get_connection()
                if connection["status"] != "connected" or connection["provider"] != service["provider"] or (real_data_only() and connection.get("provider") not in REAL_MAIL_PROVIDERS):
                    raise HTTPException(409, "Ricollega la casella autorizzata prima di riprendere il servizio.")
                service.update(status="active", activated_at=iso_now())
            elif action == "deactivate":
                service.update(status="inactive", mandate=None, activated_at=None)
                conn.execute("UPDATE runs SET status='failed',error='Servizio disattivato dall’utente.',error_step='mandato',retryable=0,next_attempt_at=NULL,finished_at=? WHERE status IN ('queued','retry_wait')", (iso_now(),))
            conn.execute("UPDATE kv SET value=? WHERE key='service'", (json.dumps(service, ensure_ascii=False),))
            scheduler._log_in_transaction(conn, "service_" + action, "priority-email", {})
        return {"service": visible_service(db)}

    @app.post("/api/demo/failure")
    def demo_failure(payload: DemoFailure):
        if real_data_only():
            raise HTTPException(403, "Gli scenari fittizi sono disabilitati in questo spazio.")
        connection = db.get_connection()
        if connection["provider"] != "demo":
            raise HTTPException(409, "Le simulazioni di errore funzionano solo con la posta dimostrativa.")
        db.set_setting("demo_failure", None if payload.kind == "clear" else payload.kind)
        if payload.kind == "clear":
            db.set_connection("demo", "connected", "Posta dimostrativa — Studio Riva")
        db.log_action("demo_failure_" + payload.kind)
        return {"demo_failure": db.get_setting("demo_failure"), "connection": db.get_connection()}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        run = db.run(run_id)
        if not run or (real_data_only() and run["demo"]):
            raise HTTPException(404, "Controllo non trovato.")
        return run

    @app.post("/api/runs/{run_id}/retry")
    def retry_run(run_id: str):
        run = db.run(run_id)
        if real_data_only() and run and run["demo"]:
            raise HTTPException(404, "Controllo non trovato.")
        try:
            return {"run": scheduler.retry(run_id)}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/drafts/{draft_id}/approve")
    def approve_draft(draft_id: str):
        draft = db.draft(draft_id)
        if not draft or (real_data_only() and db.run(draft["run_id"])["demo"]):
            raise HTTPException(404, "Bozza non trovata.")
        if draft["status"] != "approved":
            with db.connection() as conn:
                changed = conn.execute("UPDATE drafts SET status='approved',approved_at=? WHERE id=? AND status='pending'", (iso_now(), draft_id)).rowcount
                if changed:
                    scheduler._log_in_transaction(conn, "draft_approved", draft_id, {"sent": False})
        return {"draft": db.draft(draft_id), "message": "Bozza verificata. Nessuna email è stata inviata."}

    app.include_router(create_router(db))
    from .outlook import create_outlook_router
    from .imap_mail import create_imap_router
    app.include_router(create_outlook_router(db))
    app.include_router(create_imap_router(db))
    app.include_router(create_mail_router(db))
    static = Path(__file__).resolve().parent.parent / "static"
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        index_path = static / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return JSONResponse({"name": "Spazelia", "message": "Interfaccia in preparazione. Le API sono disponibili.", "bootstrap": "/api/bootstrap"})

    return app


def _has_scheduled_work(path):
    """Whether a stored workspace has an active service or unfinished checks."""
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)) as conn:
            row = conn.execute("SELECT value FROM kv WHERE key='service'").fetchone()
            pending = conn.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','retry_wait') LIMIT 1").fetchone()
        return bool(pending) or (row is not None and json.loads(row[0]).get("status") == "active")
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        # When in doubt, load it: a missed daily check is worse than a thread.
        return True


class PasswordChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class AccountDeletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(max_length=20)

    @field_validator("confirmation")
    @classmethod
    def typed_confirmation(cls, value):
        if value.strip().upper() != "ELIMINA":
            raise ValueError("Scrivi ELIMINA per confermare la cancellazione.")
        return value


def create_app(data_dir=None, start_worker=True):
    """Serve a public site and resolve private data from the signed-in account."""
    from .accounts import AccountError, AccountStore, OWNER_ID, SESSION_COOKIE, client_address, create_account_router, session_response
    from .billing import BillingStore, create_billing_router, erase_customer

    runtime = Path(data_dir or os.environ.get("ALEXCHIARA_DATA_DIR") or Path(__file__).resolve().parent.parent / ".runtime")
    static = Path(__file__).resolve().parent.parent / "static"
    accounts = AccountStore(runtime / "accounts.sqlite3")
    billing = BillingStore(runtime / "billing.sqlite3")
    workspaces = {}
    workspace_lock = threading.Lock()
    deleted_accounts = set()
    running = False

    def workspace(user_id):
        with workspace_lock:
            if user_id in workspaces:
                return workspaces[user_id]
            if user_id != OWNER_ID and not re.fullmatch(r"[a-f0-9]{32}", user_id):
                raise ValueError("Identificativo dello spazio non valido.")
            if user_id in deleted_accounts:
                # A request still in flight must not recreate a deleted workspace.
                raise LookupError("Account eliminato.")
            directory = runtime if user_id == OWNER_ID else runtime / "workspaces" / user_id
            fresh = not (directory / "alexchiara.sqlite3").exists()
            private_app = create_workspace_app(directory, start_worker=False)
            if fresh and user_id != OWNER_ID:
                private_app.state.db.set_setting("company", {"name": "", "sector": "", "description": "", "signature": "", "demo": False})
                service = private_app.state.db.get_setting("service")
                service["priority_contacts"] = []
                private_app.state.db.set_setting("service", service)
            workspaces[user_id] = private_app
            if running and start_worker:
                private_app.state.scheduler.start()
            return private_app

    def remove_workspace(user_id):
        """Stop the worker and erase every file of a deleted member's workspace."""
        with workspace_lock:
            deleted_accounts.add(user_id)
            private_app = workspaces.pop(user_id, None)
        if private_app is not None:
            private_app.state.scheduler.stop()
        directory = runtime / "workspaces" / user_id
        if directory.is_dir():
            # An atomic rename first: an interrupted erase leaves a folder that
            # is recognisably deleted and removed at the next start.
            trash = runtime / "workspaces" / f".deleted-{user_id}-{secrets.token_hex(4)}"
            os.replace(directory, trash)
            shutil.rmtree(trash, ignore_errors=True)

    for leftover in (runtime / "workspaces").glob(".deleted-*") if (runtime / "workspaces").is_dir() else ():
        shutil.rmtree(leftover, ignore_errors=True)

    # This remains the original workspace; registering never grants access to it.
    owner_workspace = workspace(OWNER_ID)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal running
        # Only workspaces with scheduled work need a worker at startup; the
        # others open on their owner's first request, keeping boot time and
        # threads proportional to active services rather than to sign-ups.
        for user in accounts.list_users():
            path = runtime / "workspaces" / user["id"] / "alexchiara.sqlite3"
            if path.exists() and _has_scheduled_work(path):
                try:
                    workspace(user["id"])
                except Exception:
                    # One damaged workspace must not keep every other account offline.
                    logger.exception("Workspace %s could not be opened at startup", user["id"])
        running = True
        if start_worker:
            for private_app in list(workspaces.values()):
                private_app.state.scheduler.start()
        try:
            yield
        finally:
            running = False
            # Signal every worker first so shutdown waits once, not once per account.
            schedulers = [private_app.state.scheduler for private_app in list(workspaces.values())]
            for scheduler in schedulers:
                scheduler.stop(wait=False)
            for scheduler in schedulers:
                scheduler.stop()

    app = FastAPI(title="Spazelia", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.accounts = accounts
    app.state.billing = billing
    app.state.workspaces = workspaces
    app.state.runtime = runtime
    app.state.db = owner_workspace.state.db
    app.state.scheduler = owner_workspace.state.scheduler
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver"}
    allowed_hosts.update(host.strip().lower() for host in os.environ.get("ALEXCHIARA_ALLOWED_HOSTS", "").split(",") if host.strip())
    public_pages = {"/", "/login", "/register"}
    auth_paths = {"/api/auth/session", "/api/auth/login", "/api/auth/register", "/api/auth/logout",
                  "/api/auth/password/forgot", "/api/auth/password/reset"}
    # Browsers and crawlers request these well-known files without a session.
    public_files = {
        "/favicon.ico": ("brand/favicon.ico", "image/x-icon"),
        "/apple-touch-icon.png": ("brand/apple-touch-icon.png", "image/png"),
        "/apple-touch-icon-precomposed.png": ("brand/apple-touch-icon.png", "image/png"),
        "/robots.txt": ("robots.txt", "text/plain; charset=utf-8"),
    }
    private_pages = {"/app": "/app", "/app/": "/app", "/account": "/account", "/account/": "/account"}

    def not_found_page(status_code=404):
        return FileResponse(static / "404.html", status_code=status_code, media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Validation payloads must never echo a submitted password or token.
        return JSONResponse({"detail": validation_errors(exc)}, status_code=422)

    @app.middleware("http")
    async def account_boundary(request: Request, call_next):
        try:
            host = _request_host(request)
        except ValueError:
            host = None
        if not host or host.lower() not in allowed_hosts:
            return JSONResponse({"detail": "Host non autorizzato."}, status_code=400)
        # Authorize the path the router will dispatch, never one rebuilt from headers.
        path = request.scope["path"]
        webhook = path == "/api/billing/webhook" and request.method == "POST"
        origin = request.headers.get("origin")
        if origin and not webhook:
            try:
                parsed = urlsplit(origin)
                same_origin = parsed.scheme in ("http", "https") and parsed.netloc.lower() == request.headers.get("host", "").lower() and not parsed.path and not parsed.query and not parsed.fragment
            except ValueError:
                same_origin = False
            if not same_origin:
                return JSONResponse({"detail": "Origine non autorizzata."}, status_code=403)
        session = accounts.resolve_session(request.cookies.get("filo_session"))
        user = session.get("user") if session and session.get("authenticated") else None
        request.state.filo_session = session
        request.state.filo_user = user
        # Private data lives only behind /api/ and the two private pages; every
        # other path is a public page, a static file or an HTML 404.
        is_public = path in auth_paths or webhook or (path == "/api/health" and request.method in ("GET", "HEAD")) or (not path.startswith("/api/") and path not in private_pages)
        if not is_public and not user:
            if path in private_pages and request.method in ("GET", "HEAD"):
                response = RedirectResponse("/login?next=" + private_pages[path], status_code=303)
                response.headers["Cache-Control"] = "no-store"
                return response
            if path in ("/api/gmail/oauth/callback", "/api/outlook/oauth/callback") and request.method in ("GET", "HEAD"):
                # A browser returning from the provider after its session ended
                # gets the login page instead of a raw JSON error.
                response = RedirectResponse("/login?next=/app", status_code=303)
                response.headers["Cache-Control"] = "no-store"
                return response
            return JSONResponse({"detail": "Accedi a Spazelia per continuare.", "login_url": "/login"}, status_code=401, headers={"Cache-Control": "no-store"})
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not webhook and path.startswith("/api/") and not accounts.csrf_valid(request.cookies.get("filo_session"), request.headers.get("x-csrf-token")):
                return JSONResponse({"detail": "Sessione non valida. Ricarica la pagina prima di continuare."}, status_code=403, headers={"Cache-Control": "no-store"})
            limit = 512 * 1024 if webhook else 64 * 1024
            chunks, total = [], 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > limit:
                    return JSONResponse({"detail": "Richiesta troppo grande."}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if path.startswith("/api/") or path in ("/login", "/register", "/app", "/app/", "/account", "/account/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(create_account_router(accounts))
    app.include_router(create_billing_router(billing))
    app.mount("/static", StaticFiles(directory=static), name="static")

    page_methods = ["GET", "HEAD"]

    @app.api_route("/", methods=page_methods)
    def home():
        return FileResponse(static / "landing.html")

    @app.api_route("/login", methods=page_methods)
    @app.api_route("/register", methods=page_methods)
    @app.api_route("/forgot-password", methods=page_methods)
    @app.api_route("/reset-password", methods=page_methods)
    def auth_page():
        return FileResponse(static / "auth.html")

    @app.api_route("/app", methods=page_methods)
    @app.api_route("/app/", methods=page_methods)
    def private_home():
        return FileResponse(static / "index.html")

    @app.api_route("/account", methods=page_methods)
    @app.api_route("/account/", methods=page_methods)
    def account_page():
        return FileResponse(static / "account.html")

    def public_file(request: Request):
        name, media_type = public_files[request.scope["path"]]
        return FileResponse(static / name, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})

    for public_path in public_files:
        app.add_api_route(public_path, public_file, methods=page_methods, include_in_schema=False)

    @app.get("/api/research/market-review")
    def market_review(request: Request):
        if (getattr(request.state, "filo_user", None) or {}).get("id") != OWNER_ID:
            raise HTTPException(status_code=403, detail="La ricerca di prodotto è riservata al gestore di Spazelia.")
        report = Path(__file__).resolve().parent.parent / "docs" / "research" / "market-review-2026-10-05.html"
        if not report.is_file():
            raise HTTPException(status_code=404, detail="La ricerca non è ancora disponibile.")
        return FileResponse(report, media_type="text/html", headers={"Cache-Control": "no-store"})

    def account_failure(exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers={"Cache-Control": "no-store"})

    @app.post("/api/account/password")
    def change_password(payload: PasswordChangeInput, request: Request):
        user = request.state.filo_user
        try:
            accounts.change_password(user["id"], payload.current_password, payload.new_password, request.cookies.get(SESSION_COOKIE), client_address(request))
        except AccountError as exc:
            return account_failure(exc)
        return JSONResponse({"message": "Password aggiornata. Gli altri dispositivi dovranno accedere di nuovo."}, headers={"Cache-Control": "no-store"})

    @app.post("/api/account/delete")
    async def delete_account(payload: AccountDeletionInput, request: Request):
        user = request.state.filo_user
        try:
            if user["id"] == OWNER_ID:
                raise AccountError(409, "L'account del gestore non si può eliminare: custodisce lo spazio originale del servizio.")
            await asyncio.to_thread(accounts.confirm_deletion, user["id"], payload.password, client_address(request))
        except AccountError as exc:
            return account_failure(exc)
        # Order matters: Stripe first (it can fail and stop everything), then
        # the account and its sessions, then the workspace files.
        await erase_customer(billing, user["id"])
        await asyncio.to_thread(accounts.delete_member, user["id"])
        await asyncio.to_thread(remove_workspace, user["id"])
        logger.info("Account deleted on request")
        cookie, guest = accounts.new_guest_session()
        return session_response(request, cookie, guest, {"deleted": True, "message": "Il tuo account e i dati del tuo spazio sono stati eliminati."})

    @app.api_route("/api/health", methods=page_methods)
    def health():
        alive = all(private.state.scheduler.alive() for private in list(workspaces.values()))
        state = "disabled" if not (running and start_worker) else "running" if alive else "degraded"
        return {"status": "ok", "scheduler": state, "persistence": "sqlite", "can_send": False}

    class WorkspaceDispatcher:
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return
            if not scope["path"].startswith("/api/"):
                # Workspaces expose only their API; unknown pages get the site 404.
                await not_found_page()(scope, receive, send)
                return
            user = scope.get("state", {}).get("filo_user")
            if not user:
                await JSONResponse({"detail": "Accedi a Spazelia per continuare."}, status_code=401)(scope, receive, send)
                return
            try:
                private_app = workspace(user["id"])
            except LookupError:
                await JSONResponse({"detail": "Accedi a Spazelia per continuare."}, status_code=401)(scope, receive, send)
                return
            except Exception:
                logger.exception("Workspace %s could not be opened", user["id"])
                await JSONResponse({"detail": "Il tuo spazio non è disponibile in questo momento. Riprova tra poco o contatta l'assistenza."}, status_code=503)(scope, receive, send)
                return
            await private_app(scope, receive, send)

    app.mount("/", WorkspaceDispatcher())
    return app


# Uvicorn uses create_app as a factory. Importing this module for tests must not
# open/recover the live development database in another process.
