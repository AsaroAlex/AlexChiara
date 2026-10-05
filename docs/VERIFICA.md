# Verifica dell'MVP e dell'ambiente

Prove eseguite il **4 ottobre 2026** in `/workspace/AlexChiara`, Linux x86_64, Python 3.12.14. Il repository iniziale era vuoto e senza commit, con checkout sul ramo locale `work`; il remoto GitHub era accessibile tramite autenticazione esistente ma non conteneva riferimenti, incluso `main`. Il codice è stato creato localmente in seguito alla richiesta di sviluppo dell'MVP. Il codice è stato successivamente pubblicato sul ramo remoto main e su Railway con accesso protetto e volume persistente. Non sono stati creati worktree o PR.

## Risultati

| Prova | Esito e significato |
| --- | --- |
| Installazione | `bash scripts/setup.sh` riuscito, anche ripetuto con le versioni del lockfile. `pip check` senza dipendenze incompatibili. TLS e verifica degli artefatti non sono stati disabilitati |
| Suite applicativa | `bash scripts/check.sh`: **40 test superati**, controllo sintattico JavaScript passato. Un avviso di deprecazione Starlette/AnyIO del runner, nessun test fallito o saltato |
| API e server reali | Avvio con `bash scripts/start.sh`; health `status=ok`, `scheduler=running`, `can_send=false`. Esecuzioni demo reali registrate nel database, con tre richieste e tre bozze locali nell'ultimo controllo |
| Browser completo | `FILO_PORT=8001 node scripts/browser-smoke.cjs` passato su istanza demo isolata: attivazione guidata, anteprima, consensi, prima esecuzione, revisione locale, preferenze, pausa/ripresa, guasto temporaneo con recupero, connessione scaduta/ripristino, profilo aziendale e chat entro catalogo. Nessun errore JavaScript |
| Desktop/mobile | Chromium a 1440 e 390 pixel: nessun overflow orizzontale; dialog e focus verificati. Una modifica del profilo non viene cancellata dal polling. Non è un audit completo di accessibilità con utenti assistiti |
| Riavvio del processo | Server fermato e riavviato con lo script salvato: azienda, servizio attivo, preferenze, risultati e revisione di una bozza conservati. Nuovo controllo riuscito sullo stesso archivio |
| Timer reale senza browser | Dopo il riavvio dell'istanza isolata, impostato il controllo alle 16:11 Europe/Rome. Con browser chiuso e senza richieste HTTP durante l'attesa, il worker ha registrato una sola esecuzione programmata riuscita con tre bozze. Nessuno slot duplicato |
| Fonte delle bozze | Estratto originale espandibile e persistente, massimo 4.000 caratteri; UI lo tratta come testo. I risultati precedenti alla migrazione conservano le bozze e hanno estratto vuoto, senza inventare fonti |

## Cosa verifica la suite

- L'attivazione richiede una casella collegata e il mandato di sola lettura e bozze locali; invio, impostazioni fuori schema, contatti duplicati e orari non validi vengono rifiutati.
- I tick dello scheduler generano un controllo quotidiano senza altra richiesta browser e senza duplicare lo slot. Pausa e disattivazione fermano il lavoro; un job interrotto recupera il medesimo identificativo dopo il riavvio.
- I guasti temporanei usano backoff e massimo tre tentativi; timeout e risultati tardivi non producono bozze duplicate. Una revoca richiede nuovo collegamento, non tentativi illimitati.
- Le mutazioni richiedono CSRF e origine appropriata; un corpo HTTP oltre 64 KiB viene fermato anche senza Content-Length.
- OAuth usa PKCE e stato monouso, scadente e legato alla sessione. I token sono cifrati; permessi più ampi di readonly vengono rifiutati. Cambiare o scollegare una casella invalida il mandato e impedisce che un refresh/callback concorrente ripristini credenziali rimosse.
- Rimuovere un cliente annulla il lavoro pendente per quei contatti e blocca risultati di un controllo già avviato. Email con istruzioni malevole non possono cambiare mandato, autorizzare invii o eseguire codice.
- Il percorso Gmail è provato con HTTPX MockTransport e strutture message/thread coerenti con lo schema ufficiale: lettura, ricostruzione, ultima risposta dello studio, refresh e revoca. Nessuna scrittura Gmail.
- L'adapter AI è provato con risposte simulate: valida schema e riferimenti alle fonti, non accetta messaggi inventati e non usa strumenti di azione. Anche con AI configurata, Gmail non invia dati a un modello esterno.
- La migrazione da database senza estratti conserva risultati e servizio. Estratti demo, Gmail simulato e AI simulata restano associati alla fonte e persistono dopo riavvio.

## Stato della configurazione cloud

Salvati nella bozza **install_script** e **start_skill**, con directory, installazione dal lockfile, avvio, ripartenza dei processi, verifiche e uso del checkout esistente. Aggiunti alla lista personalizzata i domini `accounts.google.com`, `oauth2.googleapis.com`, `gmail.googleapis.com` e `api.openai.com`, conservando il preset package manager. Non sono stati inseriti segreti, valori di credenziali o nuovi requisiti obbligatori per la demo.

Il salvataggio della bozza è confermato. La bozza non esegue script, non applica automaticamente la rete alla macchina e non pubblica l'ambiente. L'utente deve rivedere e salvare le modifiche nelle impostazioni e pubblicare tramite il prodotto. La ripartenza in un nuovo task da una versione pubblicata non è stata provata.

## Limiti delle prove

Tutti i contenuti sono fittizi. Nessun messaggio è stato inviato, nessun calendario modificato e nessuna spesa pubblicitaria impegnata. Gmail OAuth e AI con account reali non sono stati validati perché non ci sono credenziali configurate; non si dichiara qualità di un modello, approvazione Google o disponibilità di un SaaS in produzione.

La ricerca ufficiale su GitHub è documentata in PRODOTTO.md; alcuni siti dei fornitori e il testo GDPR erano bloccati dal proxy. Prezzi concorrenti, termini correnti e requisiti di verifica pubblica restano da controllare. Railway fornisce HTTPS e un processo supervisionato; l’app richiede la password condivisa. Prima di un pilota reale restano da verificare account individuali e isolamento, retention/cancellazione, backup e accordi di trattamento.

## Dashboard mattutina e agenda — 4 ottobre 2026

Il browser ha verificato accesso protetto anche per il download, esempio fittizio visibile senza attivazione, riepilogo dei controlli salvati, bozze che rimangono aperte durante il polling, revisione inline e mancata ricomparsa della stessa sorgente dopo un nuovo controllo. Agenda: orari italiani corretti anche con browser America/Los_Angeles, ordinamento, giorni futuri, persistenza dopo ricarica e ordine del giorno scaricato con titoli e note. Nessun errore JavaScript o overflow orizzontale a 1440 e 390 pixel. La palette e i simboli sono arancioni, senza verde o viola.

La suite completa passa con 103 test. Le nuove prove API coprono agenda, date e ora legale, esportazione, migrazioni additive, periodo notte/weekend, copertura incompleta, snapshot con zero bozze, priorità motivate, revisioni e deduplicazione. Le prove usano dati fittizi e istanze isolate, senza modificare il servizio Railway o calendari esterni.

Un test Gmail con MockTransport segue il connettore reale fino al briefing: una risposta dello studio elimina la priorità pendente, una nuova richiesta la ripropone, senza aumentare le chiamate al provider. Le bozze Gmail precedenti senza snapshot restano nello storico e chiedono un nuovo controllo: non vengono sostituite da un esempio fittizio o attribuite a una nuova casella.

## Priorità in primo piano e prove con Gmail — 4 ottobre 2026

La suite completa passa con **183 test**, con controllo sintattico JavaScript e dipendenze senza incompatibilità. I nuovi controlli verificano assenza di dati dimostrativi nella modalità reale, blocco di rotte e worker demo, profilo reale prima della lettura Gmail, conservazione dello storico e slot giornalieri distinti per casella/revisione.

Gli avvisi coprono giorno italiano e ora legale, contatti conosciuti, ambiguità della chat, conferma e chiusura, isolamento tra caselle, arrivi successivi alla creazione e assenza di false conclusioni sui messaggi non osservati. Lo scheduler è verificato senza browser: polling ogni cinque minuti soltanto con avviso odierno e mandato attivo, cooldown dopo controlli manuali/quotidiani, deduplicazione, pausa, revoca, rimozione del contatto prioritario e stop dopo un arrivo riscontrato. I provider di queste prove sono simulati.

Playwright verifica il layout a 1440, 1024 e 390 pixel: priorità sotto il titolo, report compatto grigio caldo in alto a destra e agenda accanto; sul telefono priorità, agenda e riepilogo. Verificati la pagina iniziale vuota in modalità reale e uno scenario visuale inserito esclusivamente nella risposta del browser, senza record inventati nel database. Nessun errore JavaScript o overflow.

`scripts/real-mode-smoke.cjs` verifica solo letture: nessun nome fittizio visibile, nessun pulsante demo, guida Google con callback production completa, collegamenti alle console, controllo di configurazione e profilo vuoto. `scripts/morning-smoke.cjs` verifica su un’altra istanza isolata accesso con password, primo stato vuoto, successivo percorso demo esplicito, priorità, revisione, agenda, download e persistenza.

`scripts/watch-smoke.cjs` passa con fixture localhost, worker e provider disabilitati: creazione dal form, proposta chat e conferma per domani, chiusura di un avviso in attesa, snapshot in ingresso successivo alla creazione, evidenza prima delle priorità urgenti, collegamento Gmail con account e thread corretti, chiusura persistente e pagina senza overflow desktop/mobile. Nessuna lettura o invio di email reali.

Il sito Railway usa `FILO_REAL_DATA_ONLY=1` e la callback `https://filo-production-65a1.up.railway.app/api/gmail/oauth/callback`. Il collegamento a Gmail reale resta da provare: il client OAuth e il segreto Google non sono ancora configurati e il consenso dell’account non è stato concesso. La guida nell’app e `docs/GMAIL.md` descrivono il passaggio concreto.

## Revisione UI/UX — 5 ottobre 2026

La review ha evidenziato una dominante beige che assegnava lo stesso peso a quasi ogni elemento, testi e controlli troppo piccoli, inviti ripetuti alla configurazione e richieste precedute da moduli e promemoria. Il nuovo foglio di stile definisce superfici bianche, argento e carbone, con mandarino riservato alle azioni principali e agli arrivi importanti; il riepilogo rimane neutro in alto a destra. La tipografia usa i caratteri di sistema, le intestazioni sono più dirette e le decorazioni non funzionali sono state rimosse.

Gli avvisi trovati restano prima delle email. Attese e modulo per creare avvisi vengono dopo le richieste da gestire. Il riepilogo mostra un trattino al posto del conteggio delle email prima del primo controllo; il conteggio dell'agenda conserva i dati effettivi. La configurazione nella scheda secondaria ha un pulsante neutro, mentre il prossimo passo in cima rimane evidente.

Misurati contrasti dei testi: carbone sul mandarino **6,85:1**, testo secondario su bianco **5,84:1**, testo del report **5,17:1** e testo delle urgenze **5,85:1**. Focus da tastiera visibile, icone decorative escluse dalla lettura e label associate ai campi. I pulsanti e i campi mobili principali misurano almeno 44 pixel in altezza; gli input a 16 pixel evitano lo zoom automatico su iOS. Queste verifiche non costituiscono un audit completo di accessibilità.

`bash scripts/check.sh` passa con **183 test** e controllo sintattico JavaScript. I browser smoke passano per accesso con password, configurazione e bozze, persistenza durante il polling, agenda e download, guida Google e avvisi da form/chat con conferma e chiusura. Il layout è stato verificato a **1440, 1024 e 390 pixel**, sia vuoto reale sia con risposte simulate soltanto nel browser. Nessun errore JavaScript, overflow orizzontale o chiamata a un account Gmail reale durante le prove. Servizi, profilo aziendale e guida OAuth sono stati ispezionati anche su telefono. Le prove restano isolate dai dati Railway.

## Homepage, account e abbonamenti — 5 ottobre 2026

La homepage è pubblica; login e registrazione sono pagine del sito. L'autenticazione non restituisce più la challenge HTTP Basic che attivava il popup del browser. La sessione persistente mantiene l'accesso dopo un refresh e viene revocata all'uscita. Le credenziali del gestore aprono lo spazio originale; gli account registrati ricevono database, profilo, contatti, agenda e credenziali Gmail separati e inizialmente vuoti.

`bash scripts/check.sh` passa con **247 test**, controllo delle dipendenze e sintassi dei due script frontend. I nuovi controlli verificano hash scrypt, rotazione e scadenza delle sessioni, limiti ai tentativi, CSRF anche prima del login, risposte senza password, isolamento tra account, conservazione dello spazio originale e persistenza dopo riavvio. I test dei pagamenti verificano prezzo e identità scelti dal server, checkout idempotente, portale associato al cliente e webhook firmati, senza richieste a Stripe reale. Il middleware completo è verificato anche con notifiche firmate sui byte originali, senza cookie o CSRF del browser.

`node scripts/account-smoke.cjs` passa avviando e rimuovendo un proprio server e archivio temporanei: homepage e login restano utilizzabili dopo refresh, registrazione e logout funzionano, due utenti non vedono né eliminano gli appuntamenti dell'altro, le credenziali iniziali continuano a funzionare. Sono verificati dashboard e account a **1440 e 390 pixel**, senza popup, challenge native, errori JavaScript, overflow o controlli visibili più bassi di 44 pixel. La homepage è stata ispezionata anche a 1024 e 320 pixel.

Le prove `morning-smoke.cjs` e `real-mode-smoke.cjs` passano dopo l'accesso tramite modulo o sessione: bozze e agenda restano operative; la guida Gmail del gestore e il profilo iniziale rimangono corretti. Una dipendenza obsoleta dal badge rimosso nella barra superiore è stata intercettata e corretta prima del deploy.

Il prezzo Stripe è da definire e le variabili Stripe non sono configurate su Railway: l'area account mostra esplicitamente che i pagamenti non sono disponibili. Registrarsi non attiva addebiti; il ritorno dal checkout non basta a segnare un abbonamento come pagato. Le prove locali non certificano un pagamento reale o il comportamento specifico del browser integrato in Codex; il login standard nella pagina elimina la challenge che causava il popup. Nessun dato Railway, casella Gmail reale o carta è stato modificato durante le verifiche.

## Collegamenti email multiprovider — 5 ottobre 2026

`bash scripts/check.sh` passa con **352 test**, dipendenze compatibili e sintassi JavaScript valida. I nuovi test coprono OAuth Microsoft PKCE, ambiti di sola lettura, rinnovo, scadenza, callback monouso e sostituzione concorrente; IMAP TLS993, rifiuto di DNS privati/misti e IPv6 locali, socket fissato al DNS validato, limite literal prima dell’allocazione, cartelle readonly, BODY.PEEK e limiti del campione. Sono verificati token cifrati, chiavi distinte e collegamenti separati attraverso le rotte dell’app pubblica con due utenti.

La regressione del completamento dello scheduler è riprodotta anche contro il vecchio comportamento: aggiornare l’ultimo controllo fuori dalla transazione poteva ripristinare un mandato rimosso durante un cambio casella. Il test passa con l’aggiornamento atomico. Anteprime e controlli scartano risultati di una connessione sostituita; errori scaduti non modificano una casella nuova. I controlli quotidiani e gli avvisi ogni cinque minuti supportano Gmail, Outlook e IMAP con mandato attivo.

`node scripts/mail-providers-smoke.cjs` passa su **1440, 390 e 320 pixel**: sette provider, guide del gestore e stato cliente, modulo IMAP, password cancellata dopo invio riuscito/fallito, chiusura ed Esc, assenza del segreto nello storage, conferma del cambio casella e scollegamento. Il consenso Gmail/Microsoft usa la stessa scheda senza popup; ritorno e contesto del wizard sono verificati con risposte simulate. Le schermate sono state ispezionate visivamente.

Passano anche `account-smoke.cjs`, `real-mode-smoke.cjs` e `morning-smoke.cjs`: login e sessione dopo refresh, account separati, guida del gestore, pagina iniziale vuota, priorità, bozze e agenda. I server e gli archivi di prova sono temporanei e isolati. Non sono state inviate richieste a caselle reali. Google e Microsoft richiedono ancora la creazione e configurazione delle app OAuth; la guida nell’app e `docs/EMAIL.md` descrivono i passaggi e le callback production. Stripe e il prezzo restano da configurare.

## Servizi operativi per PMI — 5 ottobre 2026

`bash scripts/check.sh` passa con **475 test**, dipendenze compatibili e sintassi JavaScript valida. Il catalogo aggiunge **23 moduli aziendali locali**, oltre a email e agenda, e distingue otto integrazioni ancora indisponibili. Le prove coprono CRUD persistente, campi specifici e limiti, filtri e paginazione, date italiane, IVA Decimal, bozze fattura interne e documenti TXT. Una quantità di magazzino inferiore alla soglia inserita propone un riordino; uguaglianza, quantità superiori e soglie assenti non generano l’avviso. Le priorità rispettano scadute, oggi e attenzione, senza modificare giacenze o inviare ordini.

Le rotte dell’app completa verificano login, CSRF, origine, isolamento fra due membri e gestore, esportazione privata e persistenza dopo riavvio. Il riepilogo legge uno snapshot SQLite per mantenere coerenti conteggi e schede durante modifiche concorrenti. La chat propone un modulo senza creare record o cambiare il mandato email; invii fiscali, bonifici e spesa pubblicitaria non vengono eseguiti.

`node scripts/business-smoke.cjs` passa su **1440, 390 e 320 pixel**: campi dei 23 moduli, creazione e modifica di un preventivo, IVA ed export, cancellazione di campi facoltativi, completamento e riapertura, eliminazione, priorità visibili nella panoramica, dettaglio e seconda pagina di 101 record. Verificati suggerimento chat senza scritture, focus dei dialoghi, assenza di errori JavaScript e overflow, controlli visibili di almeno 44 pixel. Le schermate catalogo e panoramica sono state ispezionate visivamente. Passano anche `account-smoke.cjs`, `mail-providers-smoke.cjs`, `real-mode-smoke.cjs` e `morning-smoke.cjs`.

Il confronto dei tempi usa durate medie per singola attività e numero di attività inseriti dall’utente; conserva anche differenze negative. Le stime facoltative di minuti dichiarati sono separate dal confronto e aggregate solo per attività completate. Non sono stati misurati risparmi reali, riduzioni del costo del personale o qualità di integrazioni esterne. Tutti gli archivi delle prove sono temporanei: nessuna pratica fittizia è stata inserita nello spazio Railway e nessun account esterno o pagamento reale è stato usato.
