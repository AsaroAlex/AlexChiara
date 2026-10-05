"""Route an explicit business request to a local workflow, without executing it."""
import re
import unicodedata

from .business_catalog import get_service

# A keyword starts at a word boundary. Entries ending in "*" are stems
# ("fattur*" matches fattura/fatture); the others must also end at a word
# boundary, where a hyphen counts as part of the word ("post" is not
# "post-vendita").
_END = r"(?![\w-])"

# Signals of intent, strongest first. A multi-word phrase decides before a
# domain stem, and a domain stem before a generic object such as "cliente":
# within the same strength the keyword that appears first in the message wins.
CANDIDATES = [
    ("time-measurement", ("misura i tempi", "misurare i tempi", "misura il risparmio", "tempo risparmiato", "efficienza")),
    ("expense-claims", ("nota spese", "note spese", "rimborso", "rimborsi", "trasferta", "trasferte")),
    ("receivables", ("incass*", "sollecit*", "crediti", "recupero crediti", "crediti scaduti", "fatture non pagate", "fattura non pagata",
                     "fatture insolute", "fattura insoluta", "insolut*", "non ha pagato", "non hanno pagato", "da incassare")),
    ("expenses", ("spese", "spesa", "costi", "uscite", "pagamenti", "pagamento fornitore", "pagamento ai fornitori", "fattura da pagare",
                  "fatture da pagare", "pagare la fattura", "pagare le fatture", "scontrin*", "spendiamo", "spendo", "speso")),
    ("invoices", ("fattur*",)),
    ("purchases", ("acquist*", "ordine di acquisto", "ordini di acquisto", "ordine al fornitore", "ordini ai fornitori", "richiesta d'acquisto", "approvvigion*")),
    ("suppliers", ("fornitor*",)),
    ("quotes", ("preventiv*", "offerta commerciale", "offerte commerciali")),
    ("projects", ("commess*", "progett*", "cantier*")),
    ("contracts", ("contratt*", "rinnovi", "rinnovo")),
    ("deadlines", ("scadenz*", "adempiment*", "f24", "tribut*", "versamenti fiscali")),
    ("inventory", ("magazzin*", "giacenz*", "scorte", "sottoscorta", "inventari*", "punto di riordino")),
    ("shipments", ("spedizion*", "corrier*", "consegne")),
    ("support", ("assistenza clienti", "richieste di assistenza", "richiesta di assistenza", "assistenza", "post-vendita", "ticket")),
    ("quality", ("non conform*", "qualita", "reclami", "reclamo", "manutenzion*")),
    ("documents", ("document*", "archiv*", "dossier")),
    ("newsletter", ("newsletter",)),
    ("content", ("contenut*", "social", "social media", "instagram", "facebook", "linkedin", "post")),
    ("procedures", ("procedur*", "checklist", "sop")),
    ("meetings", ("verbali", "verbale", "riunione", "riunioni", "ordine del giorno")),
    ("leave", ("ferie", "permess*", "assenze")),
    ("training", ("formazion*", "corsi", "onboarding")),
    ("clients", ("crm", "lead", "trattativ*", "anagrafica clienti", "opportunita commerciali", "opportunita", "nuovo cliente", "nuovi clienti")),
]
# Generic objects only decide when nothing more specific is asked.
WEAK = {"client*": "clients"}
# "Ticket restaurant" is a meal voucher, not a support request.
_EXCLUDED = re.compile(r"\bticket restaurant\b")

INTEGRATIONS = [
    ("sdi", (r"\b(?:invi\w*|trasmett\w*|manda\w*|spedisc\w*|carica\w*|collega\w*)\b[^.?!;]{0,40}\bsdi\b", r"\binvia (?:la )?fattura elettronica\b", r"\btrasmetti (?:la )?fattura\b")),
    ("bank", (r"\b(?:collega|sincronizza)\w* (?:la |il mio |la mia )?(?:banca|conto)\b", r"\bopen banking\b",
              r"\b(?:esegu\w*|effettu\w*|fai|fare|disponi|ordina\w*|invia\w*|manda\w*)\s+(?:(?:il|un|uno|i|dei|del|questo)\s+)?bonific\w*")),
    ("pec-send", (r"\b(?:invia\w*|sped\w*|manda\w*)\s+(?:una\s+|la\s+)?pec\b",)),
    ("sync-calendar", (r"\b(?:sincronizza|collega)\w* (?:il |un )?calendari\w*",)),
    ("phone", (r"\brispondi al telefono\b", r"\bcentralino\b", r"\btelefonia\b")),
    ("whatsapp", (r"\b(?:invia\w*|manda\w*) (?:un |dei )?(?:messaggi\w* )?whatsapp\b", r"\bwhatsapp business\b")),
]


def _keyword(phrase):
    if phrase.endswith("*"):
        return r"(?<!\w)" + re.escape(phrase[:-1])
    return r"(?<!\w)" + re.escape(phrase) + _END


def _negated(text, start):
    # "non inviarla allo SDI" or "senza fare il bonifico" describe what not to do.
    return re.search(r"\b(?:non|senza|mai)\s+(?:\w+\s+){0,1}$", text[:start]) is not None


def _match(text, patterns):
    for pattern in patterns:
        for found in re.finditer(pattern, text):
            if not _negated(text, found.start()):
                return found
    return None


def _proposal(service_id):
    service = get_service(service_id)
    if not service or service["kind"] != "business":
        return None
    return {"supported": True, "service_id": None, "business_service_id": service_id,
            "reply": f"Ti propongo {service['name']}: {service['description']} {service['next_action']} Apri il servizio per inserire i dati. La richiesta non avvia invii, pagamenti o modifiche esterne."}


def propose_business_service(message):
    text = "".join(char for char in unicodedata.normalize("NFKD", message.casefold())
                   if not unicodedata.combining(char))
    if re.search(r"\b(?:licenzi(?:a|o|are|amo|ate|ano|ando|amento|amenti|ato|ati|ata)|chi mandare via|dipendenti inutili)\b", text):
        return {"supported": False, "service_id": None,
                "reply": "Posso aiutarti a ridurre il lavoro ripetitivo e registrare i tempi delle attività. Le decisioni sul personale restano al titolare, sulla base di valutazioni umane."}
    paid_ads = re.search(r"\b(?:campagn\w*|pubblicitari\w*|advertising|ads|sponsorizz\w*|inserzion\w*)\b", text)
    if paid_ads and re.search(r"\b(?:spend\w*|spes[ae]|budget|investi\w*|euro)\b|€", text):
        return {"supported": False, "service_id": None,
                "reply": "La gestione della spesa pubblicitaria richiede un'integrazione dedicata. Puoi usare Contenuti e newsletter per preparare testi locali da rivedere: questa richiesta non pubblica né spende denaro."}
    for service_id, patterns in INTEGRATIONS:
        if _match(text, patterns):
            service = get_service(service_id)
            return {"supported": False, "service_id": None,
                    "reply": f"{service['name']} richiede ancora un'integrazione dedicata. {service['next_action']}"}
    # Following a mailbox belongs to the email assistant, even when the
    # message also mentions quotes or clients.
    if re.search(r"\b(?:email|e-mail|mail|posta|casella|gmail|outlook)\b", text) and re.search(
            r"\b(?:controll\w*|monitor\w*|segu\w*|tien\w* d'occhio|guard\w*|legg\w*|verific\w*|prioritar\w*|important\w*|urgent\w*)\b", text):
        return None
    if re.search(r"\bprioritar\w*\b", text) and re.search(r"\b(?:client\w*|contatt\w*)\b", text):
        return None
    scan = _EXCLUDED.sub(" ", text)
    best = None
    for order, (service_id, phrases) in enumerate(CANDIDATES):
        for phrase in phrases:
            for found in re.finditer(_keyword(phrase), scan):
                strength = 0 if " " in phrase.strip("*") else 1
                rank = (strength, found.start(), order)
                if best is None or rank < best[0]:
                    best = (rank, service_id)
                break
    if best is None:
        for stem, service_id in WEAK.items():
            if re.search(_keyword(stem), scan):
                best = ((2, 0, 0), service_id)
                break
    return _proposal(best[1]) if best else None
