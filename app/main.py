"""Local single-workspace email service. No API or provider can send email."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import base64
import binascii
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import ai
from .agenda import agenda_summary, create_agenda_router
from .briefing import build_briefing, build_example_briefing
from .connectors import ConnectorError, create_router, load_messages
from .db import Database, iso_now
from .models import Action, Activation, ChatInput, CompanyInput, DemoFailure, Preferences
from .service import Scheduler, public_service

CATALOG = [
    {"id": "priority-email", "name": "Risposte ai clienti prioritari", "category": "Email", "available": True, "status": "available", "description": "Controlla ogni giorno le richieste in attesa e prepara bozze da verificare."},
    {"id": "agenda", "name": "Calendario collegato", "category": "Agenda", "available": False, "status": "coming_soon", "description": "In arrivo: sincronizzazione automatica del calendario. L'agenda manuale è già nella panoramica."},
    {"id": "social", "name": "Contenuti social", "category": "Social", "available": False, "status": "coming_soon", "description": "In arrivo. La pubblicazione sui social non è disponibile."},
]


def create_app(data_dir=None, start_worker=True):
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
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver"}
    allowed_hosts.update(host.strip().lower() for host in os.environ.get("ALEXCHIARA_ALLOWED_HOSTS", "").split(",") if host.strip())
    access_password = os.environ.get("FILO_ACCESS_PASSWORD", "").encode("utf-8")
    access_username = os.environ.get("FILO_ACCESS_USERNAME", "filo").encode("utf-8")

    @app.middleware("http")
    async def local_session_boundary(request: Request, call_next):
        try:
            host = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            host = None
        if not host or host.lower() not in allowed_hosts:
            return JSONResponse({"detail": "Host non autorizzato per questo spazio locale."}, status_code=400)
        is_health_check = request.method == "GET" and request.url.path == "/api/health"
        if access_password and not is_health_check:
            scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
            username, password = b"", b""
            valid_format = False
            if scheme.lower() == "basic":
                try:
                    decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
                    user, separator, secret = decoded.partition(":")
                    username, password = user.encode("utf-8"), secret.encode("utf-8")
                    valid_format = bool(separator)
                except (binascii.Error, UnicodeError, ValueError):
                    pass
            username_valid = secrets.compare_digest(username, access_username)
            password_valid = secrets.compare_digest(password, access_password)
            if not (valid_format and username_valid and password_valid):
                return JSONResponse(
                    {"detail": "Autenticazione richiesta."},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Filo", charset="UTF-8"'},
                )
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
            if not db.csrf_valid(request.cookies.get("alexchiara_session"), request.headers.get("x-csrf-token")):
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

    @app.get("/api/health")
    def health():
        return {"status": "ok", "scheduler": "running" if scheduler._thread and scheduler._thread.is_alive() else "disabled", "persistence": "sqlite", "can_send": False}

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        session = db.session(request.cookies.get("alexchiara_session"))
        briefing = build_briefing(db)
        ai_status = ai.ai_status() if hasattr(ai, "ai_status") else {"mode": "deterministic", "configured": False, "enabled": False, "label": "Analisi locale deterministica"}
        result = {
            "company": db.get_setting("company"), "service": public_service(db), "connection": db.get_connection(),
            "runs": db.list_runs(), "approvals": db.pending_drafts(), "actions": db.list_actions(), "catalog": CATALOG,
            "csrf_token": session["csrf"], "mode": "local-demo", "analysis_mode": "deterministic", "ai": ai_status,
            "demo_failure": db.get_setting("demo_failure"),
            "briefing": briefing,
            "briefing_example": build_example_briefing(db) if briefing["status"] == "not_started" and not briefing["priorities"] else None,
            "agenda": agenda_summary(db),
            "limitations": [
                "Spazio locale con un solo utente: il processo server deve restare acceso per i controlli programmati.",
                "La casella demo contiene dati fittizi. Gmail legge solo i messaggi con consenso OAuth e non può inviare email.",
                "Le bozze richiedono revisione umana: approvarle le segna come verificate, senza inviarle.",
                "L'analisi Gmail è deterministica e locale; l'AI esterna opzionale si usa solo sui dati dimostrativi.",
                "L'agenda contiene solo gli appuntamenti aggiunti da te. Calendario collegato e social sono in programma.",
            ],
        }
        response = JSONResponse(result)
        response.set_cookie("alexchiara_session", session["id"], httponly=True, secure=request.url.scheme == "https", samesite="lax", max_age=7 * 24 * 3600, path="/")
        return response

    @app.put("/api/company")
    def company(payload: CompanyInput):
        value = payload.model_dump()
        value["demo"] = False
        db.set_setting("company", value)
        db.log_action("company_updated")
        return {"company": value}

    @app.post("/api/chat")
    def chat(payload: ChatInput):
        message = payload.message.lower()
        send_requested = bool(re.search(r"\b(invia|inviare|inviami|inviate|manda|mandare|spedisci|send)\b", message))
        is_email = any(word in message for word in ("email", "e-mail", "posta", "client", "preventiv", "prioritar", "bozz"))
        future_service = any(word in message for word in ("agenda", "appuntament", "calendario", "social", "instagram", "facebook"))
        if future_service and not is_email:
            return {"supported": False, "reply": "Ti propongo di iniziare dalla panoramica: puoi aggiungere i tuoi appuntamenti e scaricare l'ordine del giorno. Il calendario collegato e i social sono in arrivo. Posso anche aiutarti a configurare un controllo quotidiano delle email importanti e le bozze da verificare.", "service_id": None}
        if not is_email:
            return {"supported": False, "reply": "Posso aiutarti con le risposte ai clienti prioritari: dimmi quali contatti seguire e a che ora controllare la posta ogni giorno.", "service_id": None}
        service = db.get_setting("service")
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
            if previous.get("provider") == "gmail":
                updates.update(gmail_tokens=None, gmail_oauth_pending=None, gmail_revision=db.get_setting("gmail_revision", 0) + 1)
            for key, value in updates.items():
                conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))
            scheduler._log_in_transaction(conn, "demo_connected", None, {})
        return {"connection": connection}

    @app.post("/api/service/preview")
    def preview(payload: Preferences):
        connection = db.get_connection()
        if connection["status"] != "connected":
            raise HTTPException(409, "Collega la posta dimostrativa o Gmail prima dell'anteprima.")
        preferences = payload.model_dump()
        messages = scheduler._bounded(lambda: load_messages(connection["provider"], preferences["priority_contacts"], db))
        # Preview is always local. External processing starts only in a configured,
        # explicitly activated demo service and never receives Gmail data.
        result = ai.analyze_messages(messages, db.get_setting("company"), preferences["priority_contacts"])
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
        return {"service": public_service(db)}

    @app.post("/api/service/activate")
    def activate(payload: Activation):
        authorization = payload.authorization
        if authorization.send or not authorization.read or not authorization.draft:
            raise HTTPException(403, "Autorizza solo lettura e preparazione di bozze. L'invio non è consentito.")
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            connection = db.get_connection()
            if connection["status"] != "connected":
                raise HTTPException(409, "Collega una casella prima di attivare il servizio.")
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
        return {"service": public_service(db)}

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
            if connection["status"] != "connected" or connection["provider"] != service["provider"]:
                raise HTTPException(409, "Ricollega la casella autorizzata prima di riprendere il servizio.")
            service.update(status="active", activated_at=iso_now())
        elif action == "deactivate":
            service.update(status="inactive", mandate=None, activated_at=None)
            with db.connection() as conn:
                conn.execute("UPDATE runs SET status='failed',error='Servizio disattivato dall’utente.',error_step='mandato',retryable=0,next_attempt_at=NULL,finished_at=? WHERE status IN ('queued','retry_wait')", (iso_now(),))
        db.set_setting("service", service)
        db.log_action("service_" + action, "priority-email")
        return {"service": public_service(db)}

    @app.post("/api/demo/failure")
    def demo_failure(payload: DemoFailure):
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
        if not run:
            raise HTTPException(404, "Controllo non trovato.")
        return run

    @app.post("/api/runs/{run_id}/retry")
    def retry_run(run_id: str):
        try:
            return {"run": scheduler.retry(run_id)}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/drafts/{draft_id}/approve")
    def approve_draft(draft_id: str):
        draft = db.draft(draft_id)
        if not draft:
            raise HTTPException(404, "Bozza non trovata.")
        if draft["status"] != "approved":
            with db.connection() as conn:
                changed = conn.execute("UPDATE drafts SET status='approved',approved_at=? WHERE id=? AND status='pending'", (iso_now(), draft_id)).rowcount
                if changed:
                    scheduler._log_in_transaction(conn, "draft_approved", draft_id, {"sent": False})
        return {"draft": db.draft(draft_id), "message": "Bozza verificata. Nessuna email è stata inviata."}

    app.include_router(create_router(db))
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


# Uvicorn uses create_app as a factory. Importing this module for tests must not
# open/recover the live development database in another process.
