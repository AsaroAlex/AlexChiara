"""Route an explicit business request to a local workflow, without executing it."""
import re
import unicodedata

from .business_catalog import get_service


def propose_business_service(message):
    text = "".join(char for char in unicodedata.normalize("NFKD", message.casefold())
                   if not unicodedata.combining(char))
    if re.search(r"\b(?:licenzia\w*|chi licenziare|dipendenti inutili)\b", text):
        return {"supported": False, "service_id": None,
                "reply": "Posso aiutarti a ridurre il lavoro ripetitivo e registrare i tempi delle attività. Le decisioni sul personale restano al titolare, sulla base di valutazioni umane."}
    if re.search(r"\b(?:spendi\w*|budget|pubblicitaria|advertising|ads)\b", text) and re.search(r"\b(?:campagn\w*|pubblic\w*|spendi\w*)\b", text):
        return {"supported": False, "service_id": None,
                "reply": "La gestione della spesa pubblicitaria richiede un'integrazione dedicata. Puoi usare Contenuti e newsletter per preparare testi locali da rivedere: questa richiesta non pubblica né spende denaro."}
    integrations = [
        ("sdi", ("sdi", "invia fattura elettronica", "trasmetti fattura")),
        ("bank", ("collega banca", "sincronizza banca", "bonifico", "open banking")),
        ("pec-send", ("invia pec", "spedire pec", "manda pec")),
        ("sync-calendar", ("sincronizza calendario", "collega calendario")),
        ("phone", ("rispondi al telefono", "centralino", "telefonia")),
        ("whatsapp", ("invia whatsapp", "manda whatsapp", "whatsapp business")),
    ]
    for service_id, phrases in integrations:
        if any(re.search(r"(?<!\w)" + re.escape(phrase) + (r"(?!\w)" if phrase == "sdi" else ""), text) for phrase in phrases) or (service_id == "pec-send" and re.search(r"\b(?:invia\w*|sped\w*|manda)\s+(?:una\s+)?pec\b", text)):
            service = get_service(service_id)
            return {"supported": False, "service_id": None,
                    "reply": f"{service['name']} richiede ancora un'integrazione dedicata. {service['next_action']}"}
    if re.search(r"\b(?:email|e-mail|posta|gmail|outlook)\b", text) and re.search(r"\b(?:controll\w*|monitor\w*|segui)\b", text):
        return None
    candidates = [
        ("time-measurement", ("misura i tempi", "misurare i tempi", "misura il risparmio", "tempo risparmiato", "efficienza")),
        ("expense-claims", ("nota spese", "note spese", "rimborso", "rimborsi")),
        ("receivables", ("incass", "sollecit", "crediti scaduti", "fatture non pagate", "fatture insolute")),
        ("expenses", ("spese", "costi", "fatture da pagare", "uscite")),
        ("invoices", ("fattur",)),
        ("purchases", ("acquist", "ordine di acquisto", "richiesta d'acquisto", "approvvigion")),
        ("suppliers", ("fornitor",)),
        ("quotes", ("preventiv", "offerta commerciale")),
        ("projects", ("commess", "progett",)),
        ("contracts", ("contratt", "rinnovi", "rinnovo")),
        ("deadlines", ("scadenz", "adempiment", "f24", "tribut", "versamenti fiscali")),
        ("inventory", ("magazzin", "giacenz", "scorte", "riordin")),
        ("shipments", ("spedizion", "corrier", "consegne")),
        ("support", ("assistenza clienti", "ticket", "richieste di assistenza")),
        ("quality", ("non conform", "qualita", "reclami")),
        ("documents", ("document", "archiv", "dossier")),
        ("newsletter", ("newsletter",)),
        ("content", ("contenut", "social", "instagram", "facebook", "post")),
        ("procedures", ("procedur", "checklist", "sop",)),
        ("meetings", ("verbali", "verbale", "riunione", "riunioni")),
        ("leave", ("ferie", "permess", "assenze")),
        ("training", ("formazion", "corsi")),
        ("clients", ("crm", "lead", "trattativ", "anagrafica clienti", "opportunita commerciali")),
    ]
    for service_id, phrases in candidates:
        if any(re.search(r"(?<!\w)" + re.escape(phrase) + (r"(?!\w)" if phrase in {"post", "sop", "crm", "lead"} else ""), text) for phrase in phrases):
            service = get_service(service_id)
            if service and service["kind"] == "business":
                return {"supported": True, "service_id": None, "business_service_id": service_id,
                        "reply": f"Ti propongo {service['name']}: {service['description']} {service['next_action']} Apri il servizio per inserire i dati. La richiesta non avvia invii, pagamenti o modifiche esterne."}
    return None
