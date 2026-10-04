"""Synthetic inbox used only when the user chooses the demo account."""

from datetime import datetime, timedelta, timezone
from typing import Any


DEFAULT_CONTACTS = [
    {"name": "Giulia Conti", "email": "giulia.conti@example.com"},
    {"name": "Marco Bianchi", "email": "marco.bianchi@example.com"},
    {"name": "Sara Rossi", "email": "sara.rossi@example.com"},
]


def _field(record: Any, key: str, default: str = "") -> str:
    value = record.get(key, default) if isinstance(record, dict) else getattr(record, key, default)
    return str(value or default)


def generate_messages(contacts: list[Any] | None = None) -> list[dict[str, str]]:
    """Return synthetic inbound messages; this never reads or sends real mail.

    Configured contacts replace the sample senders, so the demo can demonstrate
    the same priority-contact filtering used by the real Gmail connection.
    """
    selected = contacts if contacts else DEFAULT_CONTACTS
    now = datetime.now(timezone.utc).replace(microsecond=0)
    templates = [
        (
            "Preventivo per il nuovo sito web",
            "Ciao Studio Riva,\n\nvi ho inviato il brief per il nuovo sito web. "
            "Resto in attesa del preventivo e di una prima indicazione sui tempi "
            "di realizzazione. Possiamo partire dal materiale che avete già?\n\nGrazie, {name}",
        ),
        (
            "Progetto identità visiva: conferma dei prossimi passi",
            "Ciao Studio Riva,\n\nla proposta per l'identità visiva ci interessa. "
            "Aspettiamo una vostra conferma sulle prossime attività e sul calendario "
            "del progetto. Potete darci un aggiornamento?\n\nGrazie, {name}",
        ),
        (
            "Revisione progetto: in attesa di un aggiornamento",
            "Ciao Studio Riva,\n\nabbiamo inviato i commenti sulla revisione del progetto "
            "e non abbiamo ancora ricevuto una risposta. Restiamo in attesa di un "
            "aggiornamento e dei prossimi passi.\n\nA presto, {name}",
        ),
    ]
    messages = []
    for index, contact in enumerate(selected):
        subject, template = templates[index % len(templates)]
        name = _field(contact, "name", "Cliente")
        email = _field(contact, "email").strip().lower()
        if not email:
            continue
        messages.append(
            {
                "id": f"demo-message-{index + 1}",
                "thread_id": f"demo-thread-{index + 1}",
                "sender": email,
                "subject": subject,
                "body": template.format(name=name),
                "received_at": (now - timedelta(hours=18 + index * 7)).isoformat(),
            }
        )
    messages.append(
        {
            "id": "demo-newsletter",
            "thread_id": "demo-newsletter-thread",
            "sender": "newsletter@example.com",
            "subject": "La newsletter settimanale del design",
            "body": "Le novità di questa settimana: mostre, eventi e ispirazioni creative.",
            "received_at": (now - timedelta(hours=2)).isoformat(),
        }
    )
    return messages


# Stable entry point used by the provider adapter.
get_messages = generate_messages
