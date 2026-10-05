"""Public Filo site and authenticated, isolated workspaces."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

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

    app = FastAPI(title="Filo — servizi per la tua attività", lifespan=lifespan)
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
            host = urlsplit("//" + request.headers.get("host", "")).hostname
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
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
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
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ConnectorError)
    async def connector_error(request, exc):
        return JSONResponse({"detail": str(exc), "error_step": exc.step, "retryable": exc.retryable}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def workspace_validation_error(request, exc):
        errors = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "scheduler": "running" if scheduler._thread and scheduler._thread.is_alive() else "disabled", "persistence": "sqlite", "can_send": False}

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
        service = db.get_setting("service")
        action = payload.action
        if action == "run":
            return {"run": scheduler.enqueue()}
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
            with db.connection() as conn:
                conn.execute("UPDATE runs SET status='failed',error='Servizio disattivato dall’utente.',error_step='mandato',retryable=0,next_attempt_at=NULL,finished_at=? WHERE status IN ('queued','retry_wait')", (iso_now(),))
        db.set_setting("service", service)
        db.log_action("service_" + action, "priority-email")
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
        return JSONResponse({"name": "Filo", "message": "Interfaccia in preparazione. Le API sono disponibili.", "bootstrap": "/api/bootstrap"})

    return app


def create_app(data_dir=None, start_worker=True):
    """Serve a public site and resolve private data from the signed-in account."""
    from .accounts import AccountStore, OWNER_ID, create_account_router
    from .billing import BillingStore, create_billing_router

    runtime = Path(data_dir or os.environ.get("ALEXCHIARA_DATA_DIR") or Path(__file__).resolve().parent.parent / ".runtime")
    static = Path(__file__).resolve().parent.parent / "static"
    accounts = AccountStore(runtime / "accounts.sqlite3")
    billing = BillingStore(runtime / "billing.sqlite3")
    workspaces = {}
    workspace_lock = threading.Lock()
    running = False

    def workspace(user_id):
        with workspace_lock:
            if user_id in workspaces:
                return workspaces[user_id]
            if user_id != OWNER_ID and not re.fullmatch(r"[a-f0-9]{32}", user_id):
                raise ValueError("Identificativo dello spazio non valido.")
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

    # This remains the original workspace; registering never grants access to it.
    owner_workspace = workspace(OWNER_ID)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal running
        for user in accounts.list_users():
            if (runtime / "workspaces" / user["id"] / "alexchiara.sqlite3").exists():
                workspace(user["id"])
        running = True
        if start_worker:
            for private_app in list(workspaces.values()):
                private_app.state.scheduler.start()
        try:
            yield
        finally:
            running = False
            for private_app in list(workspaces.values()):
                private_app.state.scheduler.stop()

    app = FastAPI(title="Filo", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.accounts = accounts
    app.state.billing = billing
    app.state.workspaces = workspaces
    app.state.runtime = runtime
    app.state.db = owner_workspace.state.db
    app.state.scheduler = owner_workspace.state.scheduler
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver"}
    allowed_hosts.update(host.strip().lower() for host in os.environ.get("ALEXCHIARA_ALLOWED_HOSTS", "").split(",") if host.strip())
    public_pages = {"/", "/login", "/register"}
    auth_paths = {"/api/auth/session", "/api/auth/login", "/api/auth/register", "/api/auth/logout"}

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Validation payloads must never echo a submitted password or token.
        errors = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.middleware("http")
    async def account_boundary(request: Request, call_next):
        try:
            host = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            host = None
        if not host or host.lower() not in allowed_hosts:
            return JSONResponse({"detail": "Host non autorizzato."}, status_code=400)
        path = request.url.path
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
        is_public = path in public_pages or path in auth_paths or path.startswith("/static/") or webhook or (path == "/api/health" and request.method in ("GET", "HEAD"))
        if not is_public and not user:
            if path in ("/app", "/app/", "/account", "/account/") and request.method in ("GET", "HEAD"):
                response = RedirectResponse("/login?next=" + ("/account" if path.startswith("/account") else "/app"), status_code=303)
                response.headers["Cache-Control"] = "no-store"
                return response
            return JSONResponse({"detail": "Accedi a Filo per continuare.", "login_url": "/login"}, status_code=401, headers={"Cache-Control": "no-store"})
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

    @app.get("/")
    def home():
        return FileResponse(static / "landing.html")

    @app.get("/login")
    @app.get("/register")
    def auth_page():
        return FileResponse(static / "auth.html")

    @app.get("/app")
    @app.get("/app/")
    def private_home():
        return FileResponse(static / "index.html")

    @app.get("/account")
    @app.get("/account/")
    def account_page():
        return FileResponse(static / "account.html")

    @app.get("/api/research/market-review")
    def market_review(request: Request):
        if (getattr(request.state, "filo_user", None) or {}).get("id") != OWNER_ID:
            raise HTTPException(status_code=403, detail="La ricerca di prodotto è riservata al gestore di Filo.")
        report = Path(__file__).resolve().parent.parent / "docs" / "research" / "market-review-2026-10-05.html"
        if not report.is_file():
            raise HTTPException(status_code=404, detail="La ricerca non è ancora disponibile.")
        return FileResponse(report, media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "scheduler": "running" if running and start_worker else "disabled", "persistence": "sqlite", "can_send": False}

    class WorkspaceDispatcher:
        async def __call__(self, scope, receive, send):
            user = scope.get("state", {}).get("filo_user")
            if not user:
                await JSONResponse({"detail": "Accedi a Filo per continuare."}, status_code=401)(scope, receive, send)
                return
            await workspace(user["id"])(scope, receive, send)

    app.mount("/", WorkspaceDispatcher())
    return app


# Uvicorn uses create_app as a factory. Importing this module for tests must not
# open/recover the live development database in another process.
