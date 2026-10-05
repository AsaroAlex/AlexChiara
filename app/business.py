"""Private, manually entered business work and local document preparation.

This module never sends a message, charges money, or files a fiscal document.
Its database is supplied by the authenticated workspace, never by a client.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

from .business_catalog import get_service, list_services
from .business_playbooks import get_playbook
from .company import get_document_company


ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc
SOURCE = "Attività aggiunte da te"
STATUS_VALUES = ("todo", "in_progress", "waiting", "done", "cancelled")
OPEN_STATUSES = ("todo", "in_progress", "waiting")
PRIORITY_VALUES = ("urgent", "high", "normal", "low")
Status = Literal["todo", "in_progress", "waiting", "done", "cancelled"]
Priority = Literal["low", "normal", "high", "urgent"]
StatusFilter = Literal["open", "todo", "in_progress", "waiting", "done", "cancelled"]
MAX_RECORDS = 5000
MONEY_LIMIT = Decimal("1000000000")


def _readable_text(value, *, multiline=False, maximum=2000):
    if not isinstance(value, str):
        raise ValueError("Indica un testo.")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError("Il testo è troppo lungo.")
    allowed = "\n\t" if multiline else ""
    if any((ord(char) < 32 and char not in allowed) or ord(char) == 127 for char in value):
        raise ValueError("Il testo contiene caratteri non consentiti.")
    return value


def _calendar_date(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Indica una data nel formato AAAA-MM-GG.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("La data non è valida.") from exc
    if parsed.isoformat() != value or not 1900 <= parsed.year <= 2200:
        raise ValueError("Indica una data compresa tra il 1900 e il 2200.")
    return value


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    title: str = Field(min_length=1, max_length=160)
    contact: str = Field(default="", max_length=200)
    due_date: str | None = None
    status: Status = "todo"
    priority: Priority = "normal"
    notes: str = Field(default="", max_length=2000)
    details: dict[str, Any] = Field(default_factory=dict, max_length=24)
    steps: dict[str, StrictBool] = Field(default_factory=dict, max_length=6)
    saved_minutes: int = Field(default=0, ge=0, le=100000, strict=True)

    @field_validator("title", "contact", "notes")
    @classmethod
    def validate_text(cls, value, info):
        return _readable_text(value, multiline=info.field_name == "notes")

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_date(cls, value):
        return _calendar_date(value)


class RecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=160)
    contact: str | None = Field(default=None, max_length=200)
    due_date: str | None = None
    status: Status | None = None
    priority: Priority | None = None
    notes: str | None = Field(default=None, max_length=2000)
    details: dict[str, Any] | None = Field(default=None, max_length=24)
    steps: dict[str, StrictBool] | None = Field(default=None, max_length=6)
    saved_minutes: int | None = Field(default=None, ge=0, le=100000, strict=True)

    @field_validator("title", "contact", "notes")
    @classmethod
    def validate_text(cls, value, info):
        if value is None:
            return value
        return _readable_text(value, multiline=info.field_name == "notes")

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_date(cls, value):
        return _calendar_date(value)


class ConversionInput(BaseModel):
    """Only editable proposal fields; conversion never changes source progress."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_service_id: Literal["invoices", "receivables"]
    title: str | None = Field(default=None, min_length=1, max_length=160)
    contact: str | None = Field(default=None, max_length=200)
    due_date: str | None = None
    priority: Priority | None = None
    notes: str | None = Field(default=None, max_length=2000)
    details: dict[str, Any] | None = Field(default=None, max_length=24)

    @field_validator("title", "contact", "notes")
    @classmethod
    def validate_text(cls, value, info):
        if value is None:
            return value
        return _readable_text(value, multiline=info.field_name == "notes")

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_date(cls, value):
        return _calendar_date(value)


class RepetitionInput(BaseModel):
    """A reviewed new activity; progress and source authority are never copied."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    next_date: str
    title: str | None = Field(default=None, min_length=1, max_length=160)
    contact: str | None = Field(default=None, max_length=200)
    priority: Priority | None = None
    notes: str | None = Field(default=None, max_length=2000)
    details: dict[str, Any] | None = Field(default=None, max_length=24)

    @field_validator("title", "contact", "notes")
    @classmethod
    def validate_text(cls, value, info):
        if value is None:
            return value
        return _readable_text(value, multiline=info.field_name == "notes")

    @field_validator("next_date", mode="before")
    @classmethod
    def validate_date(cls, value):
        parsed = _calendar_date(value)
        if parsed is None:
            raise ValueError("Indica la data della prossima attività.")
        return parsed


def _initialize(db):
    with db.connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS business_records (
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                title TEXT NOT NULL,
                contact TEXT NOT NULL DEFAULT '',
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'todo',
                priority TEXT NOT NULL DEFAULT 'normal',
                notes TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '{}',
                steps TEXT NOT NULL DEFAULT '{}',
                saved_minutes INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS business_records_service ON business_records(service_id, status);
            CREATE INDEX IF NOT EXISTS business_records_due ON business_records(status, due_date, id);
            CREATE TABLE IF NOT EXISTS business_links (
                source_record_id TEXT NOT NULL REFERENCES business_records(id) ON DELETE CASCADE,
                target_record_id TEXT NOT NULL UNIQUE REFERENCES business_records(id) ON DELETE CASCADE,
                target_service_id TEXT NOT NULL,
                PRIMARY KEY (source_record_id, target_service_id)
            );
            CREATE TABLE IF NOT EXISTS business_repeats (
                source_record_id TEXT NOT NULL REFERENCES business_records(id) ON DELETE CASCADE,
                target_record_id TEXT NOT NULL UNIQUE REFERENCES business_records(id) ON DELETE CASCADE,
                next_date TEXT NOT NULL,
                PRIMARY KEY (source_record_id, next_date)
            );
        """)
        # A serialized additive migration preserves every existing activity.
        conn.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(business_records)")}
        if "steps" not in columns:
            conn.execute("ALTER TABLE business_records ADD COLUMN steps TEXT NOT NULL DEFAULT '{}'")


def _service(service_id, *, writable=False):
    service = get_service(service_id)
    if service is None:
        raise ValueError("Servizio non riconosciuto.")
    if writable and (service.get("kind", "business") != "business" or service.get("availability", "available") != "available"):
        raise ValueError("Questo servizio richiede un collegamento o usa una propria area di lavoro.")
    return service


def _decimal(value, field):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f'Indica un numero per «{field["label"]}».')
    if len(str(value)) > 64:
        raise ValueError(f'Il valore di «{field["label"]}» è troppo lungo.')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f'Indica un numero per «{field["label"]}».') from exc
    minimum = Decimal(str(field.get("min", 0)))
    maximum = Decimal(str(field.get("max", MONEY_LIMIT)))
    if not number.is_finite() or number < minimum or number > maximum:
        raise ValueError(f'Il valore di «{field["label"]}» è fuori intervallo.')
    if number.as_tuple().exponent < (-2 if field.get("type") == "money" else -6):
        raise ValueError(f'Usa al massimo due decimali per «{field["label"]}».' if field.get("type") == "money" else f'Troppi decimali per «{field["label"]}».')
    if field.get("type") == "money":
        if number != number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
            raise ValueError(f'Usa al massimo due decimali per «{field["label"]}».')
        return format(number.quantize(Decimal("0.01")), "f")
    step = Decimal(str(field.get("step", 1)))
    if step > 0 and number % step != 0:
        raise ValueError(f'Il valore di «{field["label"]}» non rispetta l’incremento richiesto.')
    return int(number) if number == number.to_integral_value() else format(number, "f")


def _validated_details(service, values):
    fields = {field["id"]: field for field in service.get("fields", [])}
    if any(key not in fields for key in values):
        raise ValueError("Sono presenti campi non previsti per questo servizio.")
    output = {}
    for key, field in fields.items():
        value = values.get(key, field.get("default"))
        empty = value is None or value == ""
        if empty:
            if field.get("required", False):
                raise ValueError(f'Compila «{field["label"]}».')
            continue
        kind = field["type"]
        if kind in ("text", "textarea", "email", "url"):
            normalized = _readable_text(value, multiline=kind == "textarea", maximum=field.get("max_length", 500))
            if field.get("required", False) and not normalized:
                raise ValueError(f'Compila «{field["label"]}».')
            if normalized:
                output[key] = normalized
        elif kind == "date":
            output[key] = _calendar_date(value)
        elif kind in ("money", "number"):
            output[key] = _decimal(value, field)
        elif kind == "select":
            options = [option if isinstance(option, str) else option.get("value", option.get("id")) for option in field.get("options", [])]
            if not isinstance(value, str) or value not in options:
                raise ValueError(f'Scegli un valore previsto per «{field["label"]}».')
            output[key] = value
        elif kind == "checkbox":
            if not isinstance(value, bool):
                raise ValueError(f'Indica sì o no per «{field["label"]}».')
            output[key] = value
        else:
            raise ValueError("Tipo di campo non supportato.")
    if service["id"] == "leave" and output.get("starts_on") and output.get("ends_on") and output["ends_on"] < output["starts_on"]:
        raise ValueError("La fine dell’assenza non può precedere l’inizio.")
    return output


def _money_text(value):
    whole, decimal = format(Decimal(str(value)).quantize(Decimal("0.01")), "f").split(".")
    sign = "−" if whole.startswith("-") else ""
    digits = whole.lstrip("-")
    # Formatting independently of the server locale keeps documents Italian.
    grouped = f'{int(digits):,}'.replace(",", ".")
    return f"{sign}{grouped},{decimal} €"


def _totals(item):
    details = item["details"]
    if item["service_id"] in ("quotes", "invoices") and "net_amount" in details and "vat_rate" in details:
        net = Decimal(details["net_amount"])
        rate = Decimal(details["vat_rate"])
        vat = (net * rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return {
            "currency": "EUR", "net_amount": format(net, ".2f"), "vat_rate": format(rate, "f"),
            "vat_amount": format(vat, ".2f"), "gross_amount": format(net + vat, ".2f"),
        }
    return None


def _measurement(item):
    if item["service_id"] != "time-measurement":
        return None
    details = item["details"]
    before = Decimal(str(details["minutes_before"]))
    after = Decimal(str(details["minutes_after"]))
    sessions = int(details["session_count"])
    difference = before - after
    return {
        "minutes_before": format(before, "f"), "minutes_after": format(after, "f"),
        "session_count": sessions, "difference_per_session": format(difference, "f"),
        "total_difference_minutes": format(difference * sessions, "f"),
        "source": "Calcolo sui tempi inseriti dall’utente; non misurato automaticamente.",
    }


def _validated_steps(service_id, values):
    playbook = get_playbook(service_id) or {"steps": []}
    allowed = {step["id"] for step in playbook["steps"]}
    if any(step_id not in allowed for step_id in values):
        raise ValueError("Sono presenti passaggi non previsti per questo servizio.")
    if any(not isinstance(value, bool) for value in values.values()):
        raise ValueError("Indica completato o da fare per ogni passaggio.")
    return dict(values)


def _record_playbook(item):
    definition = get_playbook(item["service_id"]) or {"steps": []}
    steps = [{"id": step["id"], "label": step["label"], "checked": item["steps"].get(step["id"]) is True}
             for step in definition["steps"]]
    pending = next((step for step in steps if not step["checked"]), None)
    return {"steps": steps, "completed": sum(step["checked"] for step in steps), "total": len(steps),
            "next_step": {"id": pending["id"], "label": pending["label"]} if pending else None}


def _effective_due(item):
    if item.get("due_date"):
        return item["due_date"], {"type": "record", "label": "Scadenza dell’attività"}
    definition = get_playbook(item["service_id"]) or {"due_fields": []}
    fields = {field["id"]: field for field in _service(item["service_id"])["fields"]}
    for field_id in definition["due_fields"]:
        value = item.get("details", {}).get(field_id)
        if value:
            return value, {"type": "field", "label": fields[field_id]["label"]}
    return None, None


def _metadata_projection():
    """Extract only scheduling/stock/financial metadata, never every document."""
    clauses = []
    for service in list_services():
        if service["kind"] != "business":
            continue
        playbook = get_playbook(service["id"]) or {"due_fields": []}
        due_fields = playbook["due_fields"]
        if due_fields:
            # Identifiers are fixed by the trusted catalog, never query input.
            parts = [f"NULLIF(json_extract(details, '$.{field_id}'),'')" for field_id in due_fields]
            expression = parts[0] if len(parts) == 1 else "COALESCE(" + ",".join(parts) + ")"
            clauses.append(f"WHEN '{service['id']}' THEN {expression}")
    derived = "CASE service_id " + " ".join(clauses) + " ELSE NULL END" if clauses else "NULL"
    return f"""id, status, priority, due_date, created_at, saved_minutes, service_id,
        COALESCE(NULLIF(due_date,''),{derived}) AS effective_due_date,
        CASE WHEN service_id='inventory' THEN details ELSE NULL END AS inventory_details,
        CASE WHEN service_id IN ('receivables','expenses') THEN json_extract(details,'$.amount') ELSE NULL END AS finance_amount"""


def _stock_reorder(item):
    if item["service_id"] != "inventory":
        return None
    details = item["details"]
    if "quantity" not in details or "reorder_level" not in details:
        return None
    quantity = Decimal(str(details["quantity"]))
    minimum = Decimal(str(details["reorder_level"]))
    return {"quantity": format(quantity, "f"), "minimum": format(minimum, "f"),
            "suggested_quantity": format(max(minimum - quantity, Decimal(0)), "f")}


def _stock_below_minimum(item):
    if item["service_id"] != "inventory":
        return False
    details = item.get("details")
    if details is None:
        details = json.loads(item.get("inventory_details") or "{}")
    if "quantity" not in details or "reorder_level" not in details:
        return False
    return Decimal(str(details["quantity"])) < Decimal(str(details["reorder_level"]))


def _attention_reason(item):
    if item["status"] not in OPEN_STATUSES:
        return None
    if _stock_below_minimum(item):
        return "stock_below_minimum"
    if item["priority"] == "urgent":
        return "urgent_priority"
    if item["priority"] == "high":
        return "high_priority"
    return None


def _document(item, service, company=None):
    labels = {"todo": "Da fare", "in_progress": "In corso", "waiting": "In attesa", "done": "Completata", "cancelled": "Annullata"}
    lines = [
        f'Filo — {service.get("output_title", service["name"])}',
        "Bozza locale preparata dai dati inseriti; da verificare prima dell’uso.", "",
        item["title"],
    ]
    if service.get("output_intro"):
        lines.extend([service["output_intro"], ""])
    if item["service_id"] in ("quotes", "invoices", "receivables") and company:
        # Current workspace identity is reused only in commercial internal drafts.
        # The helper omits demo identities and invalid legacy profile fields.
        identity = company.get("legal_name") or company.get("name")
        if identity:
            lines.append(f"Azienda emittente: {identity}")
        issuer_fields = (("vat_number", "Partita IVA"), ("tax_code", "Codice fiscale"),
                         ("address", "Indirizzo"), ("postal_code", "CAP"), ("city", "Comune"),
                         ("province", "Provincia"), ("email", "Email aziendale"),
                         ("phone", "Telefono aziendale"), ("pec", "PEC"), ("sdi_code", "Codice destinatario"))
        lines.extend(f'{label}: {company[key]}' for key, label in issuer_fields if company.get(key))
        if identity or any(company.get(key) for key, _ in issuer_fields):
            lines.append("")
    if item["contact"]:
        lines.append(f'Referente: {item["contact"]}')
    if item["due_date"]:
        lines.append(f'Scadenza inserita: {date.fromisoformat(item["due_date"]).strftime("%d/%m/%Y")}')
    elif item.get("effective_due_date"):
        lines.append(f'Scadenza da «{item["due_source"]["label"]}»: {date.fromisoformat(item["effective_due_date"]).strftime("%d/%m/%Y")}')
    lines.append(f'Stato: {labels[item["status"]]}')
    for field in service.get("fields", []):
        value = item["details"].get(field["id"])
        if value is None:
            continue
        if field["type"] == "money":
            value = _money_text(value)
        elif field["type"] == "date":
            value = date.fromisoformat(value).strftime("%d/%m/%Y")
        elif isinstance(value, bool):
            value = "Sì" if value else "No"
        lines.append(f'{field["label"]}: {value}')
    totals = item["totals"]
    if totals:
        lines.extend(["", f'Imponibile: {_money_text(totals["net_amount"])}', f'IVA {totals["vat_rate"]}%: {_money_text(totals["vat_amount"])}', f'Totale: {_money_text(totals["gross_amount"])}', "Aliquota indicata dall’utente; correttezza fiscale da verificare."])
    if item["service_id"] == "invoices":
        lines.extend(["", "BOZZA INTERNA — non è una fattura fiscale. Nessuna numerazione fiscale assegnata o trasmissione allo SDI."])
    if item.get("measurement"):
        measure = item["measurement"]
        lines.extend(["", f'Differenza per attività (prima meno dopo): {measure["difference_per_session"]} minuti.',
                      f'Differenza per {measure["session_count"]} attività: {measure["total_difference_minutes"]} minuti.',
                      "Un valore negativo indica più tempo impiegato nel periodo inserito.", measure["source"]])
    if item["service_id"] == "receivables":
        lines.extend(["", "Bozza di promemoria da verificare e inviare personalmente:",
                      f'Buongiorno{", " + (item["contact"] or item["details"].get("client", "")) if item["contact"] or item["details"].get("client") else ""},',
                      f'vi chiediamo cortesemente un aggiornamento sul pagamento relativo a «{item["details"].get("reference", item["title"])}».' ])
        if "amount" in item["details"]:
            lines.append(f'Importo indicato: {_money_text(item["details"]["amount"])}.')
        lines.append("Se il pagamento è già stato effettuato, vi chiediamo di segnalarcelo. Grazie.")
    if item["notes"]:
        lines.extend(["", "Note inserite:", item["notes"]])
    if item.get("links", {}).get("source"):
        origin = item["links"]["source"]
        lines.extend(["", f'Origine interna: {_service(origin["service_id"])["name"]} — {origin["title"]}',
                      "I dati sono stati copiati alla creazione e possono essere modificati separatamente."])
    if item.get("playbook", {}).get("steps"):
        lines.extend(["", "Passaggi operativi — stato segnato manualmente da te:"])
        lines.extend(f'{"[x]" if step["checked"] else "[ ]"} {step["label"]}' for step in item["playbook"]["steps"])
    if item.get("stock_reorder"):
        lines.extend(["", f'Quantità suggerita per raggiungere la soglia: {item["stock_reorder"]["suggested_quantity"]}.',
                      "Calcolo sulle quantità inserite; nessun movimento di magazzino o acquisto eseguito."])
    if item["attention_reason"] == "stock_below_minimum":
        lines.extend(["", "Prossimo passo:", item["next_action"]])
    if service.get("output_footer"):
        lines.extend(["", service["output_footer"]])
    lines.extend(["", "Nessun invio, pagamento o adempimento eseguito da Filo."])
    return "\n".join(lines).rstrip() + "\n"


def _item(row, *, company=None, links=None):
    item = dict(row)
    item["details"] = json.loads(item["details"])
    item["steps"] = json.loads(item.get("steps") or "{}")
    service = _service(item["service_id"])
    item["service_name"] = service["name"]
    item["totals"] = _totals(item)
    item["measurement"] = _measurement(item)
    item["playbook"] = _record_playbook(item)
    item["effective_due_date"], item["due_source"] = _effective_due(item)
    item["stock_reorder"] = _stock_reorder(item)
    item["next_action"] = service.get("next_action", "Verifica i dati e completa l’attività.")
    if item["status"] in OPEN_STATUSES:
        if item["playbook"]["next_step"]:
            item["next_action"] = item["playbook"]["next_step"]["label"]
        elif item["playbook"]["total"]:
            item["next_action"] = "Hai segnato tutti i passaggi. Se hai finito, completa l’attività."
    elif item["status"] == "done":
        item["next_action"] = "Attività completata. Puoi consultare il documento o ripartire con una nuova attività."
    elif item["status"] == "cancelled":
        item["next_action"] = "Attività annullata. Puoi consultarla o riaprirla."
    item["attention_reason"] = _attention_reason(item)
    item["attention"] = item["attention_reason"] is not None
    if item["attention_reason"] == "stock_below_minimum":
        details = item["details"]
        quantity = format(Decimal(str(details["quantity"])), "f").replace(".", ",")
        minimum = format(Decimal(str(details["reorder_level"])), "f").replace(".", ",")
        unit = " " + details["unit"] if details.get("unit") else ""
        item["next_action"] = f'Verifica il riordino di {details["item"]} ({details["sku"]}): quantità inserita {quantity}{unit}, sotto la soglia di {minimum}{unit}. Conferma il conteggio e le quantità prima di preparare l’ordine.'
    item["links"] = links or {"source": None, "targets": []}
    item["document"] = _document(item, service, company)
    return item


def _record_links(conn, record_ids):
    """Fetch a page's private relationships in one read snapshot."""
    ids = list(record_ids)
    result = {record_id: {"source": None, "targets": []} for record_id in ids}
    if not ids:
        return result
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(f"""
        SELECT l.source_record_id, l.target_record_id,
               s.title AS source_title, s.service_id AS source_service_id,
               t.title AS target_title, t.service_id AS target_service_id
        FROM business_links l
        JOIN business_records s ON s.id=l.source_record_id
        JOIN business_records t ON t.id=l.target_record_id
        WHERE l.source_record_id IN ({placeholders}) OR l.target_record_id IN ({placeholders})
        ORDER BY t.created_at,t.id
    """, [*ids, *ids]).fetchall()
    for row in rows:
        if row["source_record_id"] in result:
            result[row["source_record_id"]]["targets"].append({"id": row["target_record_id"], "title": row["target_title"], "service_id": row["target_service_id"]})
        if row["target_record_id"] in result:
            result[row["target_record_id"]]["source"] = {"id": row["source_record_id"], "title": row["source_title"], "service_id": row["source_service_id"]}
    repeat_rows = conn.execute(f"""
        SELECT l.source_record_id,l.target_record_id,
               s.title AS source_title,s.service_id AS source_service_id,
               t.title AS target_title,t.service_id AS target_service_id
        FROM business_repeats l
        JOIN business_records s ON s.id=l.source_record_id
        JOIN business_records t ON t.id=l.target_record_id
        WHERE l.source_record_id IN ({placeholders}) OR l.target_record_id IN ({placeholders})
        ORDER BY t.created_at,t.id
    """, [*ids, *ids]).fetchall()
    for row in repeat_rows:
        if row["source_record_id"] in result:
            result[row["source_record_id"]]["targets"].append({"id": row["target_record_id"], "title": row["target_title"], "service_id": row["target_service_id"], "kind": "repeat"})
        if row["target_record_id"] in result:
            result[row["target_record_id"]]["source"] = {"id": row["source_record_id"], "title": row["source_title"], "service_id": row["source_service_id"], "kind": "repeat"}
    return result


def _record_identity(row):
    return {key: row[key] for key in ("id", "title", "service_id")}


def _conversion_proposal(row, target_service_id):
    directions = {"quotes": "invoices", "invoices": "receivables"}
    if directions.get(row["service_id"]) != target_service_id:
        raise HTTPException(status_code=422, detail="Puoi creare una bozza fattura da un preventivo o un incasso da una bozza fattura.")
    details = json.loads(row["details"])
    if target_service_id == "invoices":
        mapped = {"client": details["client"], "reference": ("Bozza da " + row["title"])[:160],
                  "description": details["scope"], "net_amount": details["net_amount"], "vat_rate": details["vat_rate"]}
        if details.get("payment_terms"):
            mapped["payment_terms"] = details["payment_terms"]
        title = ("Bozza fattura — " + row["title"])[:160]
    else:
        totals = _totals({"service_id": row["service_id"], "details": details})
        mapped = {"client": details["client"], "reference": details["reference"], "amount": totals["gross_amount"]}
        if details.get("payment_terms"):
            mapped["payment_context"] = details["payment_terms"]
        title = ("Incasso — " + row["title"])[:160]
    return {"service_id": target_service_id, "title": title, "contact": row["contact"],
            "due_date": None, "status": "todo", "priority": row["priority"], "notes": row["notes"],
            "details": mapped, "saved_minutes": 0}


def _repetition_date(value):
    try:
        parsed = _calendar_date(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not parsed or parsed <= _today():
        raise HTTPException(status_code=422, detail="Scegli una data successiva a oggi per la prossima attività (fuso Europe/Rome).")
    return parsed


def _repetition_proposal(row, next_date):
    service = _service(row["service_id"], writable=True)
    details = json.loads(row["details"])
    for field in service["fields"]:
        if field["type"] == "date":
            details.pop(field["id"], None)
    due_fields = (get_playbook(row["service_id"]) or {"due_fields": []})["due_fields"]
    if due_fields:
        details[due_fields[0]] = next_date
    return {"service_id": row["service_id"], "title": row["title"], "contact": row["contact"],
            "due_date": next_date, "status": "todo", "priority": row["priority"], "notes": row["notes"],
            "details": details, "saved_minutes": 0, "steps": {}}


def _insert_record(conn, payload, *, source="manual"):
    """Validate and insert within the caller's write transaction."""
    try:
        service = _service(payload.service_id, writable=True)
        details = _validated_details(service, payload.details)
        steps = _validated_steps(payload.service_id, payload.steps)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0] >= MAX_RECORDS:
        raise HTTPException(status_code=409, detail="Hai raggiunto il limite di attività salvate. Elimina quelle non più necessarie.")
    values = payload.model_dump()
    values.update({"id": uuid4().hex, "details": json.dumps(details, ensure_ascii=False), "steps": json.dumps(steps, ensure_ascii=False), "source": source,
                   "created_at": datetime.now(UTC).isoformat(timespec="microseconds")})
    values["updated_at"] = values["created_at"]
    conn.execute("INSERT INTO business_records(id,service_id,title,contact,due_date,status,priority,notes,details,steps,saved_minutes,source,created_at,updated_at) VALUES (:id,:service_id,:title,:contact,:due_date,:status,:priority,:notes,:details,:steps,:saved_minutes,:source,:created_at,:updated_at)", values)
    return conn.execute("SELECT * FROM business_records WHERE id=?", (values["id"],)).fetchone()


def _today(now=None):
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now deve includere il fuso orario.")
    return now.astimezone(ROME).date().isoformat()


def _urgency(item, today):
    due = item.get("effective_due_date", item["due_date"])
    if due and due < today:
        return "overdue"
    if due == today:
        return "today"
    if _attention_reason(item):
        return "attention"
    return "upcoming" if due else "undated"


def _financial_summary(open_items, today):
    summary = {"currency": "EUR", "source": "Importi inseriti dall’utente; non collegati alla banca e non verificati automaticamente."}
    for service_id, group in (("receivables", "receivables"), ("expenses", "payables")):
        totals = {"open_amount": Decimal(0), "overdue_amount": Decimal(0), "today_amount": Decimal(0), "count": 0}
        for item in open_items:
            amount = item.get("finance_amount")
            if item["service_id"] != service_id or amount is None:
                continue
            value = Decimal(str(amount))
            totals["open_amount"] += value
            totals["count"] += 1
            due = item.get("effective_due_date")
            if due and due < today:
                totals["overdue_amount"] += value
            elif due == today:
                totals["today_amount"] += value
        summary[group] = {key: format(value, ".2f") if isinstance(value, Decimal) else value for key, value in totals.items()}
    return summary


def business_summary(db, now=None):
    """Prioritize only recorded work using the Rome calendar day."""
    today = _today(now)
    company = get_document_company(db)
    with db.connection() as conn:
        # One read snapshot keeps counts and documents consistent with concurrent edits.
        conn.execute("BEGIN")
        rows = conn.execute("SELECT " + _metadata_projection() + " FROM business_records").fetchall()
        items = [dict(row) for row in rows]
        open_items = [item for item in items if item["status"] in OPEN_STATUSES]
        for item in open_items:
            item["urgency"] = _urgency(item, today)
        ranks = {"overdue": 0, "today": 1, "attention": 2, "upcoming": 3, "undated": 4}
        open_items.sort(key=lambda item: (ranks[item["urgency"]], PRIORITY_VALUES.index(item["priority"]), item["effective_due_date"] or "9999-12-31", item["created_at"], item["id"]))
        next_actions = []
        links = _record_links(conn, [metadata["id"] for metadata in open_items[:12]])
        for metadata in open_items[:12]:
            item = _item(conn.execute("SELECT * FROM business_records WHERE id=?", (metadata["id"],)).fetchone(), company=company, links=links[metadata["id"]])
            item["urgency"] = metadata["urgency"]
            next_actions.append(item)
    counts = {status: sum(item["status"] == status for item in items) for status in STATUS_VALUES}
    return {
        "date": today, "timezone": "Europe/Rome", "source": SOURCE,
        "totals": {"total": len(items), "open": len(open_items), "overdue": sum(item["urgency"] == "overdue" for item in open_items), "today": sum(item["urgency"] == "today" for item in open_items), "done": counts["done"], "waiting": counts["waiting"]},
        "counts": counts, "next_actions": next_actions,
        "financial_summary": _financial_summary(open_items, today),
        "attention_count": sum(bool(_attention_reason(item)) for item in open_items),
        "declared_saved_minutes": sum(item["saved_minutes"] for item in items if item["status"] == "done"),
        "savings_source": "Minuti dichiarati dall’utente per attività completate; non misurati automaticamente.",
    }


def business_services(db, now=None):
    today = _today(now)
    with db.connection() as conn:
        rows = conn.execute("SELECT " + _metadata_projection() + " FROM business_records").fetchall()
    services = list_services()
    for service in services:
        records = [row for row in rows if row["service_id"] == service["id"]]
        counts = {status: sum(row["status"] == status for row in records) for status in STATUS_VALUES}
        service.update({"record_count": len(records), "open_count": sum(counts[status] for status in OPEN_STATUSES), "overdue_count": sum(row["status"] in OPEN_STATUSES and bool(row["effective_due_date"]) and row["effective_due_date"] < today for row in records), "counts": counts})
        if service["kind"] == "business":
            service["playbook"] = get_playbook(service["id"])
    return {"services": services, "source": SOURCE, "date": today, "timezone": "Europe/Rome"}


def create_business_router(db):
    _initialize(db)
    router = APIRouter(prefix="/api/business", tags=["business"])

    @router.get("/services")
    def services():
        return business_services(db)

    @router.get("/summary")
    def summary():
        return business_summary(db)

    @router.get("/records")
    def records(service_id: str | None = Query(default=None, max_length=64), status: StatusFilter | None = None, limit: int = Query(default=250, ge=1, le=250), offset: int = Query(default=0, ge=0, le=MAX_RECORDS), q: str | None = Query(default=None, max_length=120)):
        filters, values = [], []
        if service_id is not None:
            try:
                _service(service_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            filters.append("service_id=?")
            values.append(service_id)
        if status == "open":
            filters.append("status IN ('todo','in_progress','waiting')")
        elif status is not None:
            filters.append("status=?")
            values.append(status)
        if q is not None:
            try:
                q = _readable_text(q, maximum=120)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if q:
                literal = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                term = "%" + literal + "%"
                filters.append("(title LIKE ? ESCAPE '\\' OR contact LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM json_each(business_records.details) detail WHERE CAST(detail.value AS TEXT) LIKE ? ESCAPE '\\'))")
                values.extend([term] * 4)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        company = get_document_company(db)
        with db.connection() as conn:
            conn.execute("BEGIN")
            total = conn.execute("SELECT COUNT(*) FROM business_records" + where, values).fetchone()[0]
            rows = conn.execute("SELECT * FROM business_records" + where + " ORDER BY updated_at DESC,id LIMIT ? OFFSET ?", [*values, limit, offset]).fetchall()
            links = _record_links(conn, [row["id"] for row in rows])
            items = [_item(row, company=company, links=links[row["id"]]) for row in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset, "source": SOURCE}

    @router.post("/records", status_code=201)
    def add_record(payload: RecordInput):
        company = get_document_company(db)
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = _item(_insert_record(conn, payload), company=company)
        return {"item": item}

    @router.get("/records/{record_id}")
    def get_record(record_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
        company = get_document_company(db)
        with db.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM business_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Attività non trovata.")
            item = _item(row, company=company, links=_record_links(conn, [record_id])[record_id])
        return {"item": item}

    @router.patch("/records/{record_id}")
    def update_record(payload: RecordPatch, record_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(status_code=422, detail="Indica almeno una modifica.")
        for key, value in changes.items():
            if value is None and key not in ("contact", "notes", "due_date"):
                raise HTTPException(status_code=422, detail="Questo campo non può essere vuoto.")
        company = get_document_company(db)
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM business_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Attività non trovata.")
            item = dict(row)
            if "details" in changes:
                merged = {**json.loads(item["details"]), **changes["details"]}
                try:
                    normalized = _validated_details(_service(item["service_id"], writable=True), merged)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                changes["details"] = json.dumps(normalized, ensure_ascii=False)
            if "steps" in changes:
                merged_steps = {**json.loads(item.get("steps") or "{}"), **changes["steps"]}
                try:
                    normalized_steps = _validated_steps(item["service_id"], merged_steps)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                changes["steps"] = json.dumps(normalized_steps, ensure_ascii=False)
            for key in ("contact", "notes"):
                if key in changes and changes[key] is None:
                    changes[key] = ""
            item.update(changes)
            item["updated_at"] = datetime.now(UTC).isoformat(timespec="microseconds")
            conn.execute("UPDATE business_records SET title=:title,contact=:contact,due_date=:due_date,status=:status,priority=:priority,notes=:notes,details=:details,steps=:steps,saved_minutes=:saved_minutes,updated_at=:updated_at WHERE id=:id", item)
            updated = _item(conn.execute("SELECT * FROM business_records WHERE id=?", (record_id,)).fetchone(), company=company, links=_record_links(conn, [record_id])[record_id])
        return {"item": updated}

    @router.delete("/records/{record_id}")
    def delete_record(record_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
        with db.connection() as conn:
            deleted = conn.execute("DELETE FROM business_records WHERE id=?", (record_id,)).rowcount
        if not deleted:
            raise HTTPException(status_code=404, detail="Attività non trovata.")
        return {"deleted": True, "id": record_id}

    @router.get("/records/{record_id}/conversion")
    def conversion_proposal(target_service_id: Literal["invoices", "receivables"], record_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
        company = get_document_company(db)
        with db.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM business_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Attività non trovata.")
            proposal = _conversion_proposal(row, target_service_id)
            existing_row = conn.execute("SELECT r.* FROM business_links l JOIN business_records r ON r.id=l.target_record_id WHERE l.source_record_id=? AND l.target_service_id=?", (record_id, target_service_id)).fetchone()
            existing = _item(existing_row, company=company, links=_record_links(conn, [existing_row["id"]])[existing_row["id"]]) if existing_row else None
        return {"proposal": proposal, "existing": existing, "source": _record_identity(row)}

    @router.post("/records/{record_id}/convert")
    def convert_record(payload: ConversionInput, record_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
        company = get_document_company(db)
        with db.connection() as conn:
            # Serialize duplicate clicks and concurrent retries before inspecting the link.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM business_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Attività non trovata.")
            proposal = _conversion_proposal(row, payload.target_service_id)
            existing = conn.execute("SELECT r.* FROM business_links l JOIN business_records r ON r.id=l.target_record_id WHERE l.source_record_id=? AND l.target_service_id=?", (record_id, payload.target_service_id)).fetchone()
            if existing:
                item = _item(existing, company=company, links=_record_links(conn, [existing["id"]])[existing["id"]])
                return {"item": item, "created": False, "source": _record_identity(row)}
            changes = payload.model_dump(exclude_unset=True, exclude={"target_service_id"})
            for key, value in changes.items():
                if value is None and key not in ("contact", "notes", "due_date"):
                    raise HTTPException(status_code=422, detail="Questo campo non può essere vuoto.")
            if "details" in changes:
                changes["details"] = {**proposal["details"], **changes["details"]}
            for key in ("contact", "notes"):
                if key in changes and changes[key] is None:
                    changes[key] = ""
            proposal.update(changes)
            try:
                validated = RecordInput(**proposal)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail="Controlla i campi della bozza: alcuni valori non sono validi.") from exc
            target = _insert_record(conn, validated, source="conversion")
            conn.execute("INSERT INTO business_links(source_record_id,target_record_id,target_service_id) VALUES(?,?,?)", (record_id, target["id"], payload.target_service_id))
            item = _item(target, company=company, links=_record_links(conn, [target["id"]])[target["id"]])
        return {"item": item, "created": True, "source": _record_identity(row)}

    @router.get("/records/{record_id}/repetition")
    def repetition_proposal(next_date: str = Query(min_length=10, max_length=10), record_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
        next_date = _repetition_date(next_date)
        company = get_document_company(db)
        with db.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM business_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Attività non trovata.")
            proposal = _repetition_proposal(row, next_date)
            existing_row = conn.execute("SELECT r.* FROM business_repeats l JOIN business_records r ON r.id=l.target_record_id WHERE l.source_record_id=? AND l.next_date=?", (record_id, next_date)).fetchone()
            existing = _item(existing_row, company=company, links=_record_links(conn, [existing_row["id"]])[existing_row["id"]]) if existing_row else None
        return {"proposal": proposal, "existing": existing, "source": _record_identity(row)}

    @router.post("/records/{record_id}/repeat")
    def repeat_record(payload: RepetitionInput, record_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
        next_date = _repetition_date(payload.next_date)
        company = get_document_company(db)
        with db.connection() as conn:
            # A date has one successor; repeated clicks return the first reviewed copy.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM business_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Attività non trovata.")
            existing = conn.execute("SELECT r.* FROM business_repeats l JOIN business_records r ON r.id=l.target_record_id WHERE l.source_record_id=? AND l.next_date=?", (record_id, next_date)).fetchone()
            if existing:
                item = _item(existing, company=company, links=_record_links(conn, [existing["id"]])[existing["id"]])
                return {"item": item, "created": False, "source": _record_identity(row)}
            proposal = _repetition_proposal(row, next_date)
            changes = payload.model_dump(exclude_unset=True, exclude={"next_date"})
            for key, value in changes.items():
                if value is None and key not in ("contact", "notes"):
                    raise HTTPException(status_code=422, detail="Questo campo non può essere vuoto.")
            if "details" in changes:
                changes["details"] = {**proposal["details"], **changes["details"]}
            for key in ("contact", "notes"):
                if key in changes and changes[key] is None:
                    changes[key] = ""
            proposal.update(changes)
            try:
                validated = RecordInput(**proposal)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail="Controlla i campi della prossima attività: alcuni valori non sono validi.") from exc
            target = _insert_record(conn, validated, source="repeat")
            conn.execute("INSERT INTO business_repeats(source_record_id,target_record_id,next_date) VALUES(?,?,?)", (record_id, target["id"], next_date))
            item = _item(target, company=company, links=_record_links(conn, [target["id"]])[target["id"]])
        return {"item": item, "created": True, "source": _record_identity(row)}

    @router.get("/records/{record_id}/export")
    def export_record(record_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
        company = get_document_company(db)
        with db.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM business_records WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Attività non trovata.")
            item = _item(row, company=company, links=_record_links(conn, [record_id])[record_id])
        return Response(content=item["document"].encode("utf-8"), media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="filo-{item["service_id"]}-{record_id}.txt"', "X-Content-Type-Options": "nosniff"})

    return router
