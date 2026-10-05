"""Mailbox choices and local unlinking inside each private workspace."""
import json

from fastapi import APIRouter

from .connectors import ConnectorError, _credentials, _invalidate_in_transaction, _revision_in_transaction

REAL_MAIL_PROVIDERS = frozenset({"gmail", "outlook", "imap"})


def clear_mail_credentials(conn):
    """Keep a single mailbox, including no dormant credentials for former accounts."""
    conn.execute("DELETE FROM kv WHERE key IN ('gmail_tokens','gmail_oauth_pending','outlook_tokens','imap_tokens','imap_credentials')")
    conn.execute("DELETE FROM kv WHERE key LIKE 'oauth:%' OR key LIKE 'outlook_oauth:%' OR key LIKE 'imap_oauth:%'")


def create_mail_router(db):
    router = APIRouter(prefix="/api/mail", tags=["mail"])

    @router.get("/providers")
    def providers():
        from .outlook import _credentials as microsoft_credentials, _valid_redirect
        from .imap_mail import IMAP_PRESETS

        connection = db.get_connection()
        selected = connection.get("mail_provider", "imap") if connection.get("provider") == "imap" else connection.get("provider")
        connected = connection.get("status") == "connected"
        google = _credentials()
        try:
            microsoft = microsoft_credentials()
        except ConnectorError:
            microsoft = {"client_id": "", "client_secret": "", "redirect_uri": ""}
        choices = [
            {"id": "gmail", "label": "Gmail", "kind": "oauth",
             "configured": bool(google["client_id"] and google["client_secret"]),
             "redirect_uri": google["redirect_uri"],
             "setup_note": "Il gestore deve configurare l'app Google prima del primo collegamento."},
            {"id": "outlook", "label": "Outlook e Microsoft 365", "kind": "oauth",
             "configured": bool(microsoft["client_id"] and microsoft["client_secret"] and _valid_redirect(microsoft["redirect_uri"])),
             "redirect_uri": microsoft["redirect_uri"],
             "setup_note": "Il gestore deve configurare l'app Microsoft prima del primo collegamento."},
        ]
        labels = {"icloud": "iCloud", "yahoo": "Yahoo", "aruba": "Aruba", "libero": "Libero"}
        for provider, label in labels.items():
            preset = IMAP_PRESETS[provider]
            host = preset["host"] if isinstance(preset, dict) else preset
            choices.append({"id": provider, "label": label, "kind": "imap", "configured": True,
                            "host": host, "setup_note": "Usa una password per app quando richiesta dal tuo provider."})
        choices.append({"id": "imap", "label": "Altro account IMAP", "kind": "imap", "configured": True,
                        "setup_note": "Serve un server IMAP pubblico con connessione TLS sulla porta 993."})
        for choice in choices:
            choice["connected"] = connected and selected == choice["id"]
        return {"providers": choices, "connection": connection}

    @router.post("/disconnect")
    def disconnect():
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _invalidate_in_transaction(conn, "La casella è stata scollegata. I controlli automatici sono stati fermati.")
            revision = _revision_in_transaction(conn)
            clear_mail_credentials(conn)
            updates = {"gmail_revision": revision + 1,
                       "connection": {"provider": None, "status": "disconnected", "label": "Nessun account collegato"}}
            for key, value in updates.items():
                conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        return {"disconnected": True, "message": "Accesso locale rimosso e controlli fermati. Puoi revocare il consenso o la password per app anche nelle impostazioni del provider."}

    return router
