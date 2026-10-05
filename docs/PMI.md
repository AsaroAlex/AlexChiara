# Spazelia per una PMI italiana

La pagina **Servizi** offre 23 moduli aziendali locali, oltre alla segreteria email e all'agenda. Si possono cercare per nome, filtrare per area o mostrare soltanto quelli già utilizzati. Ogni nuovo account parte vuoto: nessuna pratica, scadenza, fattura o persona viene inventata.

## Le aree operative

| Area | Servizi |
| --- | --- |
| Clienti e vendite | Clienti e opportunità, preventivi, assistenza clienti |
| Amministrazione | Bozze fattura, incassi e solleciti, spese, scadenze, contratti e rinnovi, note spese |
| Fornitori e operazioni | Fornitori, acquisti e ordini, commesse, magazzino e riordini, spedizioni, qualità |
| Organizzazione | Documenti e checklist, procedure, verbali delle riunioni, misurazione dei tempi |
| Persone | Ferie e permessi, formazione |
| Comunicazione | Contenuti e social, newsletter |

I moduli usano schede con campi specifici. Puoi assegnare una scadenza, una priorità e uno stato, aggiornare i dati, completare o riaprire un'attività. Dalla scheda Spazelia prepara un documento locale da leggere, copiare o scaricare in `.txt`. La modifica dei dati aggiorna il documento.

Ogni modulo suggerisce da tre a cinque **Passaggi da seguire**, adatti al suo compito: per esempio verificare le condizioni del preventivo, raccogliere il giustificativo di una spesa o controllare il destinatario di una spedizione. Le caselle conservano ciò che hai segnato e il prossimo passaggio aperto compare anche nella panoramica. I passaggi sono conferme manuali: segnare un controllo non prova un’approvazione esterna e completare l’attività non spunta automaticamente le caselle.

La ricerca **Cerca attività** trova titoli, referenti, note e dati delle schede in tutti i moduli aziendali, senza distinguere maiuscole o lettere accentate; gli importi si cercano anche nel formato italiano (`1.250,50`). Non cerca nella casella email. Si può aprire anche con `Ctrl/Cmd + K`. Le attività della panoramica possono essere completate direttamente e riaperte con il comando per annullare.

La panoramica mostra subito le attività scadute, quelle di oggi e quelle che richiedono attenzione, con il prossimo passo. Per il magazzino una quantità inferiore alla soglia annotata propone un controllo e un riordino: non aggiorna la giacenza e non invia ordini. Le date vengono valutate nel fuso italiano `Europe/Rome`.

Se lasci vuota la scadenza generale, Spazelia usa la data già inserita nel modulo: validità del preventivo, incasso previsto, pagamento della spesa, consegna dell’acquisto, rinnovo del contratto, pubblicazione, riunione, inizio dell’assenza, formazione o consegna della spedizione. Il dettaglio indica da quale campo arriva. Una scadenza generale scelta da te ha precedenza. La data storica di una nota spese non diventa una scadenza di rimborso.

Il riepilogo economico somma soltanto gli importi delle schede aperte **Incassi** e **Spese**, distinguendo scaduti e oggi. Preventivi e bozze fattura non vengono sommati di nuovo. Sono importi inseriti da te, senza riscontro bancario. Il dettaglio magazzino mostra quanto manca per raggiungere la soglia; la quantità suggerita resta da verificare prima di un acquisto.

Con **Ripeti attività** scegli una data successiva a oggi e rivedi la scheda proposta prima di salvarla. Il titolo, i dati utili e le note sono copiati; i passaggi e i minuti ripartono da zero. Le vecchie date vengono rimosse e la nuova data operativa viene proposta quando il modulo la prevede; le altre date richieste, come la fine di un’assenza, vanno reinserite. Annullare non crea una scheda. Ripetere dalla stessa origine per la stessa data riapre la destinazione già creata e conserva le modifiche. Non è una pianificazione automatica e l’origine resta invariata.

La chat propone il modulo pertinente, ad esempio **Preventivi**, **Incassi** o **Verbali**, senza creare record o attivare permessi soltanto dal testo della richiesta. La segreteria email mantiene il proprio consenso e i limiti del collegamento Gmail/Outlook/IMAP.

## Documenti e amministrazione

**La tua azienda** raccoglie anche ragione sociale, partita IVA, codice fiscale, indirizzo e contatti. Questi campi sono facoltativi e vengono riutilizzati nell'intestazione dei preventivi, delle bozze fattura e degli incassi. I controlli verificano il formato, senza interrogare registri fiscali. Un export usa il profilo aziendale attualmente salvato: non rappresenta una copia storica immutabile di una fattura emessa.

Dal dettaglio di un preventivo puoi preparare una **bozza fattura** con cliente, descrizione, imponibile, IVA e condizioni già compilati. Il modulo si apre per la revisione: annullare non crea alcuna scheda. Da una bozza fattura puoi preparare una scheda **Incassi** con l'importo totale. Le scadenze restano da inserire, senza inventare termini di pagamento. Le schede conservano i collegamenti all'origine; ripetere la conversione apre la stessa destinazione, senza creare duplicati o sovrascrivere modifiche. La conversione non completa l'origine, non emette fatture fiscali e non verifica che un pagamento sia avvenuto.

Preventivi e bozze fattura calcolano imponibile, IVA e totale con aritmetica decimale. L'imponibile e l'aliquota sono inseriti dall'utente; Spazelia non determina il trattamento fiscale del caso concreto. La **bozza fattura** è un promemoria interno da verificare: non è un XML fiscale e non viene inviata allo SDI. Il modulo incassi prepara un testo di sollecito da rivedere e usare nella propria casella.

Le scadenze fiscali, contrattuali e della formazione sono quelle inserite dall'utente: il prodotto non inventa termini normativi o adempimenti. I documenti conservano riferimenti e annotazioni, senza importare automaticamente i file da archivi esterni. Ferie e permessi registrano la richiesta e il suo periodo; il servizio non calcola cedolini né assume decisioni sul personale.

## Integrazioni dedicate

Il catalogo distingue otto collegamenti ancora da realizzare: calendario esterno, fatturazione SDI, banca, invio PEC, gestionali/ERP, pubblicazione social, telefonia e WhatsApp Business. Ogni scheda indica i prerequisiti del provider. La loro presenza nel catalogo non le attiva e non prova che un fornitore sia già collegato.

Non vengono inviati messaggi, eseguiti bonifici, impegnati budget pubblicitari o depositati documenti fiscali dai moduli locali. Le API email restano in sola lettura. Il prezzo dell'abbonamento e Stripe mantengono la propria configurazione, separata dai nuovi moduli.

## Misurare il beneficio

Il modulo **Tempo operativo misurato** registra durata media per singola attività prima e dopo, e numero di attività nel periodo, inseriti dall'utente. Il confronto moltiplica la differenza per attività per questo numero, compreso un eventuale peggioramento. Non inserire nei campi prima/dopo i totali dell'intero periodo. I minuti facoltativi dichiarati per un'attività completata sono aggregati come stima dell'utente, separati dal confronto: non si sommano due volte le due fonti.

Un conteggio di schede completate non prova ore risparmiate né riduzione del costo del personale. Per valutare il canone, confronta tempi, qualità dei risultati, rilavorazioni e costo effettivo del servizio su attività ricorrenti. Questa versione non propone graduatorie dei dipendenti o decisioni di licenziamento.

## Dati e verifiche

Record e documenti sono conservati nello spazio privato dell'account. Il server sceglie il database tramite la sessione, senza accettare identificativi di azienda o account dal browser. CRUD ed esportazione richiedono login; le modifiche richiedono CSRF e origine consentita. I record persistono dopo ricarica e riavvio. Le pagine e gli export trattano i dati inseriti come testo, senza eseguire HTML o istruzioni.

Le prove usano archivi temporanei e dati di test; non creano pratiche fittizie nello spazio Railway. I collegamenti esterni richiedono il proprio collaudo prima di un uso operativo continuativo.
