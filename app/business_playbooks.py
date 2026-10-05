"""Manual, service-specific operating steps for local business records.

These playbooks guide the account holder. A checked step records their choice;
it does not prove an approval, a payment, an external action or compliance.
Date fields are explicit dates entered in the catalog, never inferred legal
deadlines. The record's common due_date remains a separate user choice.
"""
from copy import deepcopy
import re

from .business_catalog import SERVICE_CATALOG


def _playbook(*steps, due_fields=()):
    return {
        "steps": [{"id": step_id, "label": label} for step_id, label in steps],
        "due_fields": list(due_fields),
    }


PLAYBOOKS = {
    "clients": _playbook(
        ("request", "Chiarisci la richiesta e il risultato atteso dal cliente."),
        ("contact", "Verifica i recapiti del referente da ricontattare."),
        ("follow_up", "Concorda il prossimo passo commerciale e annotalo."),
        ("next_date", "Scegli la data del prossimo contatto con il cliente."),
    ),
    "quotes": _playbook(
        ("scope", "Controlla prestazioni, quantità e cosa è incluso nella proposta."),
        ("amounts", "Verifica imponibile, aliquota IVA inserita e totale."),
        ("terms", "Conferma validità della proposta e condizioni di pagamento."),
        ("recipient", "Controlla il destinatario e fai rileggere la bozza al referente prima di condividerla."),
        due_fields=("valid_until",),
    ),
    "invoices": _playbook(
        ("customer", "Verifica i dati del cliente e il riferimento della bozza."),
        ("work", "Confronta le prestazioni con il lavoro o la consegna concordati."),
        ("tax", "Controlla imponibile, aliquota IVA inserita e totale con il referente."),
        ("payment", "Conferma le condizioni di pagamento da riportare."),
        ("fiscal_review", "Rivedi la bozza prima di emettere la fattura nel gestionale fiscale."),
    ),
    "receivables": _playbook(
        ("balance", "Verifica sul documento e in banca quanto resta da incassare."),
        ("date", "Controlla la data prevista e gli accordi con il cliente."),
        ("reminder", "Rileggi importo, riferimento e tono del promemoria."),
        ("outcome", "Annota l’esito del contatto e il prossimo passo per l’incasso."),
        due_fields=("expected_date",),
    ),
    "expenses": _playbook(
        ("evidence", "Recupera il giustificativo e controlla il riferimento della spesa."),
        ("beneficiary", "Verifica beneficiario e importo sul documento originale."),
        ("payment_date", "Conferma la data prevista per il pagamento."),
        ("authorization", "Chiedi al referente la conferma prima di disporre il pagamento."),
        due_fields=("payment_date",),
    ),
    "suppliers": _playbook(
        ("contact", "Controlla i recapiti del fornitore e il referente operativo."),
        ("supply", "Conferma quali prodotti o servizi copre la fornitura."),
        ("terms", "Rileggi prezzi, tempi e condizioni concordate."),
        ("next_order", "Annota cosa verificare con il fornitore per il prossimo ordine."),
    ),
    "purchases": _playbook(
        ("items", "Verifica prodotti, servizi e quantità da acquistare."),
        ("supplier", "Confronta fornitore, importo e condizioni della richiesta."),
        ("approval", "Chiedi al referente indicato la conferma del budget e dell’acquisto."),
        ("request", "Rileggi la richiesta e concorda la data prima di inviare l’ordine."),
        ("delivery", "Verifica con il fornitore quantità e consegna effettivamente ricevute."),
        due_fields=("delivery_date",),
    ),
    "projects": _playbook(
        ("scope", "Conferma con il cliente il risultato da consegnare."),
        ("owner", "Individua il referente del prossimo passaggio operativo."),
        ("plan", "Rileggi budget, passaggi e consegne della commessa."),
        ("blockers", "Verifica gli impedimenti e annota come affrontarli."),
        ("next_delivery", "Concorda una data per il prossimo risultato da consegnare."),
    ),
    "deadlines": _playbook(
        ("source", "Rileggi l’adempimento e le istruzioni ricevute dal consulente."),
        ("date", "Conferma la scadenza con il referente che l’ha comunicata."),
        ("documents", "Raccogli i documenti richiesti e individua quelli mancanti."),
        ("handover", "Prepara il materiale per il consulente e annota il prossimo controllo."),
    ),
    "documents": _playbook(
        ("requirements", "Verifica quali documenti servono per la pratica."),
        ("received", "Confronta i documenti ricevuti con quelli richiesti."),
        ("missing", "Chiedi al referente i documenti ancora mancanti."),
        ("archive", "Verifica versioni e riferimenti all’archivio con il referente prima di condividere l’indice."),
    ),
    "contracts": _playbook(
        ("original", "Recupera la versione del contratto in uso e verifica controparte e riferimento."),
        ("terms", "Rileggi importi, condizioni e preavviso nel contratto."),
        ("renewal", "Conferma la data di rinnovo annotata con il referente."),
        ("decision", "Annota la decisione da prendere e quando ricontrollare il contratto."),
        due_fields=("renewal_date",),
    ),
    "inventory": _playbook(
        ("count", "Verifica la quantità fisica dell’articolo e l’unità di misura."),
        ("minimum", "Confronta la quantità contata con la soglia di riordino scelta."),
        ("reorder", "Conferma con il referente quantità e fornitore prima di preparare il riordino."),
    ),
    "support": _playbook(
        ("request", "Chiarisci il problema del cliente e il risultato richiesto."),
        ("owner", "Concorda con il referente la soluzione o le informazioni da chiedere."),
        ("reply", "Rileggi la risposta e i passaggi indicati al cliente."),
        ("resolution", "Annota il riscontro del cliente prima di considerare risolta la richiesta."),
    ),
    "content": _playbook(
        ("audience", "Conferma pubblico, canale e obiettivo del contenuto."),
        ("facts", "Verifica fatti, materiali e informazioni presenti nel messaggio."),
        ("draft", "Rileggi tono, testo e collegamenti della bozza."),
        ("publication", "Controlla la data prevista prima di pubblicare dal tuo profilo."),
        due_fields=("publication_date",),
    ),
    "newsletter": _playbook(
        ("audience", "Controlla il gruppo di destinatari e le eventuali esclusioni."),
        ("consent", "Verifica i riferimenti ai consensi nell’elenco che userai per l’invio."),
        ("message", "Rileggi oggetto, testo e collegamenti della comunicazione."),
        ("test", "Fai una prova nel tuo strumento di invio prima di mandare la comunicazione."),
    ),
    "procedures": _playbook(
        ("scope", "Chiarisci quando usare la procedura e chi ne è il referente."),
        ("sequence", "Controlla ordine, materiali e istruzioni dei passaggi."),
        ("trial", "Prova la checklist durante una vera esecuzione dell’attività."),
        ("review", "Annota correzioni, controlli finali e quando ripetere l’attività."),
    ),
    "meetings": _playbook(
        ("context", "Verifica data, partecipanti e punti discussi negli appunti."),
        ("decisions", "Conferma le decisioni riportate con i partecipanti."),
        ("actions", "Assegna a ogni azione annotata un referente e una data concordati."),
        ("review", "Rileggi il verbale prima di condividerlo con i partecipanti."),
        due_fields=("meeting_date",),
    ),
    "leave": _playbook(
        ("period", "Conferma il periodo con la persona e il referente."),
        ("coverage", "Verifica chi seguirà le attività durante l’assenza."),
        ("handover", "Concorda le consegne e annota i riferimenti utili."),
        ("return", "Controlla la data di rientro e i passaggi da riprendere."),
        due_fields=("starts_on",),
    ),
    "training": _playbook(
        ("goal", "Concorda argomento e risultato atteso della formazione."),
        ("materials", "Raccogli i materiali e verifica i passaggi del percorso."),
        ("session", "Conferma referente, partecipanti e data del prossimo incontro."),
        ("progress", "Annota i passaggi svolti e cosa resta da approfondire."),
        due_fields=("session_date",),
    ),
    "expense-claims": _playbook(
        ("evidence", "Recupera il giustificativo e verifica richiedente e data della spesa."),
        ("reason", "Confronta il motivo della spesa con le regole aziendali."),
        ("amount", "Controlla l’importo richiesto sul giustificativo."),
        ("authorization", "Sottoponi la nota al referente prima di autorizzare il rimborso."),
    ),
    "shipments": _playbook(
        ("recipient", "Verifica destinatario e indirizzo con il referente della consegna."),
        ("items", "Controlla materiale e quantità da spedire."),
        ("courier", "Conferma corriere, costo annotato e data prevista."),
        ("tracking", "Consulta il tracking del corriere e annota l’esito reale della consegna."),
        due_fields=("delivery_date",),
    ),
    "quality": _playbook(
        ("checks", "Conferma attrezzatura o processo e controlli previsti con il referente."),
        ("findings", "Raccogli gli esiti osservati e i punti ancora aperti."),
        ("actions", "Concorda interventi correttivi e chi li deve seguire."),
        ("next_check", "Annota la data del prossimo controllo e verifica gli interventi svolti."),
    ),
    "time-measurement": _playbook(
        ("comparable", "Verifica che prima e dopo riguardino la stessa attività e condizioni comparabili."),
        ("method", "Annota come hai osservato i tempi e scegli un campione rappresentativo."),
        ("period", "Controlla periodo, numero di attività e durate medie osservate."),
        ("comparison", "Rileggi la differenza calcolata e le condizioni della misurazione."),
    ),
}


def get_playbook(service_id):
    """Return independent metadata, or None for a service without a playbook."""
    playbook = PLAYBOOKS.get(service_id)
    return deepcopy(playbook) if playbook is not None else None


def list_playbooks():
    """Return a copy of all playbooks indexed by service identifier."""
    return deepcopy(PLAYBOOKS)


def _validate_playbooks():
    """Reject catalog drift that could silently remove or misdate a workflow."""
    business_services = {
        service["id"]: service
        for service in SERVICE_CATALOG
        if service["kind"] == "business" and service["available"]
    }
    if PLAYBOOKS.keys() != business_services.keys():
        raise ValueError("Business playbooks must cover every available business service.")
    for service_id, playbook in PLAYBOOKS.items():
        steps = playbook["steps"]
        step_ids = [step["id"] for step in steps]
        if not 3 <= len(steps) <= 5 or len(set(step_ids)) != len(step_ids):
            raise ValueError(f"Invalid operating steps for {service_id}.")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", step_id) for step_id in step_ids):
            raise ValueError(f"Unstable step identifier for {service_id}.")
        if any(not step["label"].strip() for step in steps):
            raise ValueError(f"Missing step label for {service_id}.")
        date_fields = {
            field["id"] for field in business_services[service_id]["fields"]
            if field["type"] == "date"
        }
        if any(field_id not in date_fields for field_id in playbook["due_fields"]):
            raise ValueError(f"Deadline field missing from catalog for {service_id}.")
        if len(set(playbook["due_fields"])) != len(playbook["due_fields"]):
            raise ValueError(f"Duplicate deadline field for {service_id}.")


_validate_playbooks()
