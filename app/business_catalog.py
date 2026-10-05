"""The service catalog for a private Italian SME workspace.

Business services produce records and documents from information entered by
the account holder. They do not imply a connection to an external provider.
Fields describe only service-specific details; the record API owns the common
title, contact, notes, due date, priority and progress fields.
"""
from copy import deepcopy


def _text(field_id, label, *, required=False, max_length=160):
    return {"id": field_id, "label": label, "type": "text", "required": required,
            "max_length": max_length}


def _area(field_id, label, *, required=False, max_length=2000):
    return {"id": field_id, "label": label, "type": "textarea", "required": required,
            "max_length": max_length}


def _date(field_id, label, *, required=False):
    return {"id": field_id, "label": label, "type": "date", "required": required}


def _select(field_id, label, options, *, required=False):
    return {"id": field_id, "label": label, "type": "select", "required": required,
            "options": list(options)}


def _money(field_id, label, *, required=False, maximum=1_000_000_000):
    return {"id": field_id, "label": label, "type": "money", "required": required,
            "min": 0, "max": maximum, "step": "0.01"}


def _number(field_id, label, *, required=False, minimum=0, maximum=1_000_000, step="0.01"):
    return {"id": field_id, "label": label, "type": "number", "required": required,
            "min": minimum, "max": maximum, "step": step}


def _business(service_id, name, category, description, icon, fields, *,
              next_action, output_title, output_intro, output_footer="",
              action_label="Apri il servizio"):
    return {
        "id": service_id, "name": name, "category": category,
        "description": description, "icon": icon, "kind": "business",
        "available": True, "status": "available", "availability": "available",
        "action_label": action_label, "fields": fields,
        "next_action": next_action, "output_title": output_title,
        "output_intro": output_intro, "output_footer": output_footer,
    }


def _integration(service_id, name, description, next_action):
    return {
        "id": service_id, "name": name, "category": "Collegamenti",
        "description": description, "icon": "link", "kind": "integration",
        "available": False, "status": "coming_soon",
        "availability": "requires_connection", "action_label": "Da integrare",
        "fields": [], "next_action": next_action,
        "output_title": "", "output_intro": "", "output_footer": "",
    }


SERVICE_CATALOG = (
    {
        "id": "priority-email", "name": "Segreteria email",
        "category": "Organizzazione", "icon": "mail", "kind": "existing",
        "description": "Controlli sulla casella collegata, priorità e bozze da rivedere.",
        "available": True, "status": "available", "availability": "available",
        "action_label": "Gestisci la posta", "fields": [],
        "next_action": "Collega una casella e scegli i contatti da controllare per primi.",
        "connection_note": "Gmail e Outlook richiedono la configurazione OAuth del sito; gli altri provider usano IMAP.",
        "output_title": "", "output_intro": "", "output_footer": "",
        "destination": "connections",
    },
    {
        "id": "agenda", "name": "Agenda e ordine del giorno",
        "category": "Organizzazione", "icon": "calendar", "kind": "existing",
        "description": "Appuntamenti aggiunti da te e ordine del giorno da scaricare.",
        "available": True, "status": "available", "availability": "available",
        "action_label": "Apri l’agenda", "fields": [],
        "next_action": "Aggiungi gli appuntamenti e le note utili per la giornata.",
        "connection_note": "Gli eventi vengono inseriti manualmente; non è attiva la sincronizzazione con calendari esterni.",
        "output_title": "", "output_intro": "", "output_footer": "",
        "destination": "overview",
    },
    _business(
        "clients", "Clienti e opportunità", "Clienti e vendite",
        "Raccogli richieste, contatti e prossimi passi commerciali in schede consultabili.",
        "building", [
            _text("company", "Azienda o cliente", required=True),
            _text("contact_email", "Email del referente", max_length=254),
            _text("contact_phone", "Telefono del referente", max_length=40),
            _area("request", "Esigenza o richiesta del cliente", required=True),
            _area("follow_up", "Prossimo passo concordato", max_length=1000),
        ],
        next_action="Rileggi la richiesta e concorda il prossimo contatto con il cliente.",
        output_title="Scheda cliente e prossimo contatto",
        output_intro="Riepilogo commerciale compilato con i dati inseriti nella scheda.",
    ),
    _business(
        "quotes", "Preventivi", "Clienti e vendite",
        "Prepara una proposta con lavoro previsto, importi e condizioni da rivedere.",
        "mail", [
            _text("client", "Destinatario del preventivo", required=True),
            _area("scope", "Lavoro, prodotti e quantità inclusi", required=True),
            _money("net_amount", "Imponibile in euro", required=True),
            _select("vat_rate", "Aliquota IVA (%) — inserita da te", ["0", "4", "5", "10", "22"], required=True),
            _date("valid_until", "Validità della proposta"),
            _area("payment_terms", "Pagamento e condizioni", max_length=1000),
        ],
        next_action="Controlla contenuto, importi e aliquota indicata prima di condividere la proposta.",
        output_title="Bozza di preventivo",
        output_intro="Proposta preparata dai dati inseriti; importi e aliquota sono indicati dall’utente.",
        output_footer="Bozza commerciale da verificare. Non è una fattura fiscale e non viene trasmessa allo SDI.",
    ),
    _business(
        "invoices", "Bozze fattura", "Amministrazione",
        "Prepara un riepilogo interno con riferimento, prestazioni, importi e condizioni di pagamento.",
        "mail", [
            _text("client", "Cliente o destinatario", required=True),
            _text("reference", "Riferimento della bozza", required=True),
            _area("description", "Prestazioni, prodotti e quantità", required=True),
            _money("net_amount", "Imponibile in euro", required=True),
            _select("vat_rate", "Aliquota IVA (%) — inserita da te", ["0", "4", "5", "10", "22"], required=True),
            _area("payment_terms", "Pagamento e condizioni", max_length=1000),
        ],
        next_action="Verifica dati, importi e aliquota con il referente prima di emettere la fattura nel gestionale fiscale.",
        output_title="Bozza interna di fattura",
        output_intro="Riepilogo interno preparato dai dati inseriti; importi e aliquota sono indicati dall’utente.",
        output_footer="Bozza non fiscale. Non è una fattura elettronica, non assegna numerazione fiscale e non viene trasmessa allo SDI.",
    ),
    _business(
        "receivables", "Incassi e solleciti", "Amministrazione",
        "Tieni visibili le somme da ricevere e prepara il testo di un promemoria.",
        "clock", [
            _text("client", "Cliente o debitore", required=True),
            _text("reference", "Riferimento fattura o accordo", required=True),
            _money("amount", "Importo da incassare in euro", required=True, maximum=1_220_000_000),
            _date("expected_date", "Data di incasso prevista"),
            _area("payment_context", "Accordi e ultimo contatto", max_length=1500),
        ],
        next_action="Verifica che l’importo sia ancora da incassare e rivedi il promemoria prima dell’invio.",
        output_title="Promemoria di incasso",
        output_intro="Riepilogo della somma e dei riferimenti inseriti per organizzare il prossimo contatto.",
        output_footer="I movimenti bancari non vengono verificati automaticamente e il promemoria non viene inviato.",
    ),
    _business(
        "expenses", "Spese e pagamenti", "Amministrazione",
        "Organizza uscite, giustificativi e pagamenti da verificare prima della scadenza.",
        "building", [
            _text("supplier", "Fornitore o beneficiario", required=True),
            _text("reference", "Riferimento del documento"),
            _money("amount", "Importo da pagare in euro", required=True),
            _date("payment_date", "Data di pagamento prevista"),
            _area("evidence", "Dove si trova il giustificativo", max_length=1000),
        ],
        next_action="Controlla documento, beneficiario e approvazione del pagamento con il referente.",
        output_title="Scheda spesa da verificare",
        output_intro="Riepilogo dei dati inseriti per il controllo amministrativo della spesa.",
        output_footer="La scheda non è una registrazione contabile e non dispone pagamenti.",
    ),
    _business(
        "suppliers", "Fornitori", "Fornitori e operazioni",
        "Raccogli contatti, condizioni concordate e punti da verificare con ogni fornitore.",
        "building", [
            _text("supplier", "Ragione sociale o fornitore", required=True),
            _text("contact_email", "Email di riferimento", max_length=254),
            _text("contact_phone", "Telefono di riferimento", max_length=40),
            _area("supply", "Prodotti o servizi forniti", required=True),
            _area("terms", "Condizioni e accordi da ricordare", max_length=1500),
        ],
        next_action="Conferma con il fornitore contatto, condizioni e tempi dell’ordine successivo.",
        output_title="Scheda fornitore",
        output_intro="Informazioni e condizioni annotate per la gestione del rapporto con il fornitore.",
    ),
    _business(
        "purchases", "Acquisti e ordini", "Fornitori e operazioni",
        "Prepara richieste di acquisto e controlla approvazione e consegna previste.",
        "mail", [
            _text("supplier", "Fornitore previsto", required=True),
            _area("items", "Prodotti, servizi e quantità", required=True),
            _money("amount", "Importo indicato in euro"),
            _date("delivery_date", "Consegna richiesta"),
            _text("approver", "Referente che deve approvare"),
        ],
        next_action="Chiedi al referente la conferma di quantità, budget e fornitore prima di inviare l’ordine.",
        output_title="Bozza di richiesta di acquisto",
        output_intro="Richiesta organizzata a partire dai prodotti, dagli importi e dalle date inserite.",
        output_footer="L’ordine deve essere verificato e inviato da te; non viene acquistato alcun prodotto.",
    ),
    _business(
        "projects", "Commesse e lavori", "Fornitori e operazioni",
        "Segui il lavoro, il referente e i punti ancora aperti di una commessa.",
        "grid", [
            _text("client", "Cliente della commessa", required=True),
            _area("scope", "Risultato da consegnare", required=True),
            _text("owner", "Referente operativo"),
            _money("budget", "Budget annotato in euro"),
            _area("milestones", "Passaggi, consegne e impedimenti", max_length=2000),
        ],
        next_action="Controlla il prossimo passaggio e assegna al referente un’azione concreta con una data.",
        output_title="Riepilogo di commessa",
        output_intro="Punti operativi e consegne annotati nella scheda della commessa.",
    ),
    _business(
        "deadlines", "Scadenze amministrative", "Amministrazione",
        "Raccogli adempimenti comunicati dai tuoi consulenti e documenti da preparare.",
        "calendar", [
            _text("obligation", "Adempimento o documento richiesto", required=True),
            _text("advisor", "Referente o consulente che lo ha indicato"),
            _area("documents", "Documenti da raccogliere", required=True),
            _area("instructions", "Istruzioni ricevute e fonte", max_length=1500),
        ],
        next_action="Conferma data e istruzioni con il consulente e raccogli i documenti mancanti.",
        output_title="Promemoria di scadenza amministrativa",
        output_intro="Scadenza e istruzioni riportate dai dati che hai inserito.",
        output_footer="Le scadenze non sono ricavate automaticamente dalle norme; non vengono presentate dichiarazioni o pratiche.",
    ),
    _business(
        "documents", "Documenti e checklist", "Organizzazione",
        "Crea un indice dei documenti e una lista di ciò che manca per una pratica.",
        "grid", [
            _text("purpose", "Pratica o attività", required=True),
            _area("required_documents", "Documenti richiesti", required=True),
            _area("received_documents", "Documenti già ricevuti", max_length=2000),
            _text("location", "Cartella o riferimento all’archivio", max_length=500),
        ],
        next_action="Confronta l’elenco richiesto con quello ricevuto e chiedi i documenti ancora mancanti.",
        output_title="Checklist documentale",
        output_intro="Indice e lista di controllo compilati con i riferimenti inseriti.",
        output_footer="La scheda contiene riferimenti: non importa file da Drive e non certifica la completezza di una pratica.",
    ),
    _business(
        "contracts", "Contratti e rinnovi", "Amministrazione",
        "Ricorda riferimenti, rinnovi e decisioni da prendere sui contratti esistenti.",
        "shield", [
            _text("counterparty", "Controparte", required=True),
            _text("reference", "Riferimento del contratto", required=True),
            _date("renewal_date", "Data di rinnovo annotata"),
            _money("value", "Importo periodico annotato in euro"),
            _area("terms", "Condizioni e preavviso da verificare", max_length=2000),
        ],
        next_action="Rileggi il contratto e conferma condizioni e preavviso prima di decidere sul rinnovo.",
        output_title="Promemoria di contratto e rinnovo",
        output_intro="Riferimenti e condizioni annotati per preparare il controllo del contratto.",
        output_footer="I termini sono inseriti da te. Il riepilogo non interpreta clausole e non invia disdette.",
    ),
    _business(
        "inventory", "Magazzino e riordini", "Fornitori e operazioni",
        "Annota quantità contate e soglie decise da te per preparare il prossimo riordino.",
        "grid", [
            _text("sku", "Codice articolo", required=True),
            _text("item", "Articolo o materiale", required=True),
            _number("quantity", "Quantità rilevata", required=True),
            _text("unit", "Unità di misura", max_length=40),
            _number("reorder_level", "Soglia di riordino decisa da te"),
            _money("unit_cost", "Costo unitario annotato in euro"),
        ],
        next_action="Verifica la quantità fisica e la soglia annotata prima di preparare un ordine.",
        output_title="Scheda articolo e controllo scorta",
        output_intro="Situazione della scorta basata sulle quantità inserite nella scheda.",
        output_footer="Le quantità non sono aggiornate da vendite o movimenti esterni e non parte alcun ordine automatico.",
    ),
    _business(
        "support", "Assistenza clienti", "Clienti e vendite",
        "Raccogli richieste, aggiornamenti e risposte da verificare con il referente.",
        "chat", [
            _text("customer", "Cliente che ha scritto", required=True),
            _area("request", "Richiesta o problema", required=True),
            _text("owner", "Referente che può risolvere"),
            _area("reply", "Risposta da rivedere", max_length=2000),
        ],
        next_action="Conferma la soluzione con il referente e rivedi la risposta prima di comunicarla al cliente.",
        output_title="Scheda richiesta di assistenza",
        output_intro="Richiesta, referente e risposta annotati per seguire il caso.",
        output_footer="Le richieste vengono inserite da te; la risposta non viene inviata automaticamente.",
    ),
    _business(
        "content", "Contenuti e social", "Comunicazione",
        "Organizza messaggio, pubblico e bozza di un contenuto prima della pubblicazione.",
        "spark", [
            _select("channel", "Canale", ["Sito", "LinkedIn", "Instagram", "Facebook", "Altro"], required=True),
            _text("audience", "A chi è rivolto", required=True),
            _area("message", "Messaggio e informazioni da comunicare", required=True),
            _area("draft", "Testo da rivedere", max_length=4000),
            _date("publication_date", "Pubblicazione prevista"),
        ],
        next_action="Controlla fatti, tono e materiali del contenuto prima di pubblicarlo sul canale scelto.",
        output_title="Scheda contenuto e bozza",
        output_intro="Messaggio e testo forniti da te, organizzati per la revisione del contenuto.",
        output_footer="La scheda prepara una bozza locale; i profili social non sono collegati e il contenuto non viene pubblicato.",
    ),
    _business(
        "newsletter", "Newsletter e comunicazioni", "Comunicazione",
        "Prepara oggetto, pubblico e testo di una comunicazione da rivedere.",
        "mail", [
            _text("audience", "Gruppo di destinatari", required=True),
            _text("subject", "Oggetto della comunicazione", required=True),
            _area("message", "Testo della comunicazione", required=True, max_length=4000),
            _area("consent_source", "Riferimento ai consensi e all’elenco destinatari", max_length=1000),
        ],
        next_action="Verifica pubblico, consensi e testo prima di usare il tuo strumento di invio.",
        output_title="Bozza di newsletter o comunicazione",
        output_intro="Oggetto e testo inseriti nella scheda, pronti per la tua revisione.",
        output_footer="Nessuna mailing list viene importata e nessuna email viene inviata da questa scheda.",
    ),
    _business(
        "procedures", "Procedure e attività ricorrenti", "Organizzazione",
        "Scrivi passaggi chiari per un’attività e una checklist riutilizzabile.",
        "check", [
            _text("process", "Attività o processo", required=True),
            _text("owner", "Referente della procedura"),
            _area("steps", "Passaggi da eseguire", required=True, max_length=4000),
            _area("checks", "Controlli finali", max_length=1500),
            _text("frequency", "Quando ripeterla"),
        ],
        next_action="Fai verificare i passaggi al referente e prova la checklist durante una vera attività.",
        output_title="Procedura operativa e checklist",
        output_intro="Passaggi e controlli forniti da te per rendere ripetibile l’attività.",
        output_footer="La procedura viene conservata come documento locale; i passaggi non vengono eseguiti automaticamente.",
    ),
    _business(
        "meetings", "Verbali e azioni", "Organizzazione",
        "Organizza appunti di riunione, decisioni e attività da seguire.",
        "chat", [
            _date("meeting_date", "Data della riunione", required=True),
            _text("participants", "Partecipanti", max_length=500),
            _area("discussion", "Appunti e punti discussi", required=True, max_length=4000),
            _area("decisions", "Decisioni concordate", max_length=2000),
            _area("actions", "Azioni, referenti e date concordate", max_length=2000),
        ],
        next_action="Conferma decisioni e referenti con i partecipanti e registra le azioni da seguire.",
        output_title="Bozza di verbale e azioni concordate",
        output_intro="Riepilogo degli appunti di riunione inseriti nella scheda.",
        output_footer="Il testo deriva dai tuoi appunti: non viene registrato o trascritto alcun incontro.",
    ),
    _business(
        "leave", "Ferie e copertura operativa", "Persone",
        "Annota periodi richiesti o concordati e la copertura del lavoro durante l’assenza.",
        "calendar", [
            _text("person", "Persona o referente", required=True),
            _date("starts_on", "Inizio del periodo", required=True),
            _date("ends_on", "Fine del periodo", required=True),
            _text("covering_person", "Referente per la copertura"),
            _area("handover", "Consegne operative e accordi confermati", max_length=1500),
        ],
        next_action="Concorda il periodo con la persona e verifica insieme copertura e consegne.",
        output_title="Promemoria del periodo e delle consegne",
        output_intro="Periodo e copertura operativa annotati nella scheda.",
        output_footer="La scheda non approva ferie, non calcola saldi o retribuzioni e non richiede informazioni sanitarie.",
    ),
    _business(
        "training", "Formazione e onboarding", "Persone",
        "Segui materiali, incontri e passaggi concordati per formazione e inserimento.",
        "building", [
            _text("person", "Persona o gruppo", required=True),
            _text("topic", "Argomento o percorso", required=True),
            _text("trainer", "Referente della formazione"),
            _area("materials", "Materiali e passaggi da completare", max_length=2000),
            _date("session_date", "Incontro o verifica concordata"),
        ],
        next_action="Conferma materiali e prossimo incontro con il referente della formazione.",
        output_title="Scheda di formazione e inserimento",
        output_intro="Percorso, materiali e appuntamenti annotati per organizzare la formazione.",
        output_footer="Il completamento è registrato manualmente; la scheda non certifica idoneità o obblighi formativi.",
    ),
    _business(
        "expense-claims", "Note spese", "Amministrazione",
        "Raccogli importo, motivo e riferimento al giustificativo per la verifica del rimborso.",
        "building", [
            _text("person", "Richiedente", required=True),
            _date("expense_date", "Data della spesa", required=True),
            _money("amount", "Importo richiesto in euro", required=True),
            _area("reason", "Motivo della spesa", required=True, max_length=1000),
            _text("evidence", "Riferimento al giustificativo", max_length=500),
        ],
        next_action="Verifica giustificativo e regole aziendali con il referente prima di autorizzare il rimborso.",
        output_title="Nota spese da verificare",
        output_intro="Spesa e riferimenti inseriti per preparare il controllo del rimborso.",
        output_footer="La scheda non determina deducibilità, non approva rimborsi e non dispone pagamenti.",
    ),
    _business(
        "shipments", "Spedizioni e consegne", "Fornitori e operazioni",
        "Raccogli destinatario, materiale e riferimenti per controllare una consegna.",
        "grid", [
            _text("recipient", "Destinatario", required=True),
            _area("items", "Materiale da spedire o consegnare", required=True),
            _text("courier", "Corriere o referente della consegna"),
            _text("tracking", "Riferimento di spedizione", max_length=240),
            _date("delivery_date", "Data prevista"),
            _money("amount", "Costo annotato in euro"),
        ],
        next_action="Verifica indirizzo, materiale e data con il referente e controlla il tracking presso il corriere.",
        output_title="Promemoria di spedizione o consegna",
        output_intro="Dati e riferimenti annotati per seguire la consegna.",
        output_footer="Il tracking non viene consultato automaticamente e non vengono acquistate etichette di spedizione.",
    ),
    _business(
        "quality", "Controlli e manutenzioni", "Fornitori e operazioni",
        "Organizza verifiche concordate, esiti osservati e attività correttive da seguire.",
        "shield", [
            _text("asset", "Attrezzatura, area o processo", required=True),
            _text("owner", "Referente del controllo"),
            _area("checks", "Controlli previsti", required=True),
            _area("findings", "Esiti rilevati e punti aperti", max_length=2000),
            _area("follow_up", "Interventi concordati", max_length=1500),
        ],
        next_action="Fai verificare gli esiti al referente e concorda l’intervento e la data successiva.",
        output_title="Scheda di controllo e interventi",
        output_intro="Controlli ed esiti osservati inseriti nella scheda.",
        output_footer="La scheda organizza le annotazioni e non certifica conformità o sicurezza delle attrezzature.",
    ),
    _business(
        "time-measurement", "Tempo operativo misurato", "Organizzazione",
        "Annota tempi realmente osservati prima e dopo un cambiamento per confrontare un’attività.",
        "clock", [
            _text("activity", "Attività confrontata", required=True),
            _text("period", "Periodo di osservazione", required=True),
            _number("minutes_before", "Minuti medi per attività — prima", required=True),
            _number("minutes_after", "Minuti medi per attività — dopo", required=True),
            _number("session_count", "Numero di attività nel periodo", required=True, minimum=1, step="1"),
            _area("method", "Come sono stati misurati i tempi", max_length=1500),
        ],
        next_action="Confronta attività equivalenti e verifica la misurazione prima di trarre conclusioni.",
        output_title="Confronto di tempi osservati",
        output_intro="Durate medie per singola attività dichiarate da te; la differenza viene moltiplicata per il numero di attività nel periodo indicato.",
        output_footer="La scheda non misura automaticamente l’utilizzo, non stima risparmi futuri e non valuta i dipendenti.",
    ),
    _integration(
        "sync-calendar", "Calendari Google e Microsoft",
        "La sincronizzazione con calendari esterni richiede un connettore dedicato; oggi l’agenda è manuale.",
        "Usa l’agenda locale. La sincronizzazione richiederà un’integrazione con le autorizzazioni del provider.",
    ),
    _integration(
        "sdi", "Fatturazione elettronica e SDI",
        "Emissione, ricezione e conservazione richiedono un provider fiscale autorizzato e un’integrazione dedicata.",
        "Continua a usare il tuo gestionale fiscale; puoi annotare in Spazelia riferimenti e scadenze degli incassi.",
    ),
    _integration(
        "bank", "Banche e riconciliazione",
        "Lettura dei movimenti e riconciliazione richiedono un servizio bancario autorizzato e il tuo consenso.",
        "Verifica i movimenti con la banca e aggiorna manualmente le schede di incasso e pagamento.",
    ),
    _integration(
        "pec-send", "PEC e ricevute",
        "Invio di PEC e acquisizione delle ricevute richiedono il collegamento con un provider PEC.",
        "Usa il tuo servizio PEC per l’invio e annota i riferimenti dei messaggi nelle schede documentali.",
    ),
    _integration(
        "erp-sync", "Gestionali e contabilità",
        "Scambio con il gestionale, registrazioni contabili e dati fiscali richiedono connettori dedicati.",
        "Mantieni le registrazioni nel gestionale. Spazelia organizza le attività annotate senza sostituire la contabilità.",
    ),
    _integration(
        "social-publish", "Pubblicazione sui social",
        "La pubblicazione automatica richiede l’integrazione con ogni piattaforma e le autorizzazioni dei profili.",
        "Prepara il testo nel servizio Contenuti e social e pubblicalo dal tuo profilo dopo la revisione.",
    ),
    _integration(
        "phone", "Telefonia e segreteria vocale",
        "Ricezione di chiamate e gestione telefonica richiedono un provider voce e un connettore dedicato.",
        "Annota richieste e richiami nelle schede clienti o assistenza; Spazelia non riceve chiamate.",
    ),
    _integration(
        "whatsapp", "WhatsApp Business",
        "Conversazioni e invio di messaggi richiedono il collegamento con la piattaforma WhatsApp Business.",
        "Gestisci i messaggi nell’app del provider e riporta le attività da seguire nella scheda del cliente.",
    ),
)

# Historical callers may import CATALOG directly; helpers prevent mutations of
# an API response from changing the shared definition for other workspaces.
CATALOG = SERVICE_CATALOG


def list_services():
    """Return a mutable response copy without altering the global catalog."""
    return deepcopy(list(SERVICE_CATALOG))


def get_service(service_id):
    """Return one response copy, or None for an unknown service identifier."""
    for service in SERVICE_CATALOG:
        if service["id"] == service_id:
            return deepcopy(service)
    return None
