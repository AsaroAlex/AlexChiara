> Documento storico del primo MVP, ora presentato con il marchio Spazelia. Il rebranding non aggiorna la data delle fonti o rende attuali le ipotesi iniziali. Per le funzionalità attuali e il catalogo PMI vedi [README](../README.md) e [Servizi PMI](PMI.md). I prezzi e i budget qui sotto sono ipotesi iniziali, non il prezzo configurato nell’app.

# Servizi AI per piccoli studi: scelta dell'MVP e piano di validazione

Data della ricerca e delle ipotesi: **4 ottobre 2026**. Primo segmento: **studi italiani di consulenza B2B, 2–10 persone, già su Google Workspace**. Primo servizio: **Segreteria email**, con lettura dei messaggi dei quattro clienti prioritari, riepiloghi e bozze conservate nell'applicazione. Nessun invio e nessuna modifica della casella.

Il prodotto vende servizi operativi già definiti, configurabili entro limiti precisi. Il cliente sceglie clienti, orario e firma aziendale; non progetta agenti, workflow, prompt o modelli. La chat riconduce le richieste alle capacità del catalogo e spiega i limiti. Agenda, comunicazioni, analisi social, ADV e telefono rimangono servizi previsti, senza attivazione simulata.

## Come leggere questa valutazione

- **Fatto verificato (V)**: contenuto effettivamente letto in una fonte ufficiale raggiungibile, citata in fondo. Una specifica API prova la capacità documentata; non prova che il nostro account abbia accesso o che l'integrazione sia stata provata.
- **Stima (S)**: durata, costo o obiettivo numerico ipotizzato, da misurare. Non è un listino del fornitore, una statistica di mercato o un risultato del prototipo.
- **Giudizio progettuale (G)**: priorità e scelta del team, motivata dai vincoli di budget, dimensione e competenze dei clienti. Non deriva da un'indagine rappresentativa.

La ricerca diretta sui siti Google Workspace/Gmail, Microsoft, Calendly, Mailchimp, Meta e Twilio è stata tentata via HTTPS con verifica TLS attiva. Il proxy ha rifiutato la connessione con **403 Forbidden** ai domini di documentazione/commerciali; anche EUR-Lex e le policy OAuth Google erano bloccati. Sono state invece lette le fonti ufficiali pubblicate dai fornitori su GitHub/raw. Non sono stati verificati listini correnti, accordi commerciali, disponibilità per piano o processo di approvazione di applicazioni esterne. Le fonti sono lette alla data sopra, non necessariamente aggiornate dai rispettivi autori quel giorno.

## Confronto dei sei candidati

| Candidato e caso d'uso preciso | Problema e beneficio atteso (G) | Primo risultato utile (S) | Dati/accessi e integrazione (V dove citato; resto G) |
| --- | --- | --- | --- |
| **A. Segreteria email**: ogni mattina evidenziare richieste aperte di quattro clienti, con contesto e testo di risposta da revisionare | Problema quotidiano; beneficio leggibile nell'elenco delle risposte da preparare. Frequenza e tempo risparmiato vanno misurati sul segmento | 5–15 minuti dal collegamento riuscito della casella; le autorizzazioni del provider possono richiedere interventi ulteriori | Un account Gmail, indirizzi dei clienti, orario, tono. Gmail documenta lettura messaggi/thread e ricerca con `gmail.readonly` [1]. Non serve il permesso di invio |
| **B. Agenda**: trovare uno slot per una consulenza da 30 minuti su un solo calendario, confermare, spostare e ricordare | Frequente per chi vende appuntamenti; risultato chiarissimo, ma bisogna modellare durata, festività e disponibilità | 15–30 minuti, dopo che calendario e regole sono corretti | Calendario, fusi orari, durata, disponibilità e contatti. Google Calendar documenta `freeBusy`, inserimento e aggiornamento eventi con permessi distinti [3]. Concorrenza sugli slot e notifiche aumentano la complessità |
| **C. Comunicazioni**: newsletter mensile a una lista già consentita, con revisione, invio approvato e disiscrizioni | Beneficio evidente a chi ha una lista affidabile; frequenza meno alta e preparazione iniziale sostanziale | 30–60 minuti se destinatari, basi giuridiche e dominio sono già pronti; altrimenti più giorni | Lista, consensi/base giuridica, contenuto, identità del mittente, esclusioni. Il SDK ufficiale Mailchimp documenta OAuth, campagne, invio e pianificazione [6]; la corretta gestione di destinatari e deliverability rimane responsabilità operativa |
| **D. Analisi social**: rapporto settimanale su un account, tre metriche definite e due proposte motivate | Utile solo con obiettivi e dati sufficienti; il valore è meno immediatamente verificabile del lavoro email | 1–3 giorni per collegamento, definizioni e primo rapporto; la prima tendenza richiede uno storico | Account/ruoli, obiettivi, storico e definizioni metriche. Il codice ufficiale Meta espone oggetti account e insights [7]; copertura di ogni canale, permessi correnti e review non sono verificati |
| **E. ADV**: proporre una campagna lead B2B su un solo canale, con approvazione esplicita di materiali, periodo e tetto di spesa | Potenziale beneficio economico, difficile da attribuire con pochi dati; costo degli errori alto | 2–5 giorni per materiali e configurazione; settimane per valutare risultati | Account pubblicitario, pagina, budget, offerta, asset, misurazione. Meta documenta oggetti campagne e creazione tramite SDK [7]; questo non garantisce accesso al nostro account né approvazione degli annunci |
| **F. Telefono**: rispondere a richieste di orari/prezzi approvati e trasferire le eccezioni a una persona | Beneficio chiaro quando si perdono chiamate; frequenza da verificare nello studio. Un errore interrompe una conversazione reale | 1–3 settimane per numero, routing, voce, gestione degli errori e prove telefoniche | Numero, provider, istruzioni, eventuale calendario e reperibilità umana. Twilio documenta chiamate, TwiML e callback di stato [8]. La voce aggiunge ASR/TTS, latenza, rumore, accenti, interruzioni, cadute di linea e continuità del servizio |

| Candidato | Funzionamento/assistenza (G) | Conseguenze di errore e verifica (G) | Ripetibilità tra aziende e alternative |
| --- | --- | --- | --- |
| A | Inferenza testo contenuta, pochi accessi; assistenza OAuth e qualità delle bozze | Una richiesta può sfuggire o una bozza risultare errata. Nessun messaggio parte. Il cliente può confrontare ogni risultato con il thread | Stesse regole per studi simili; differiscono indirizzi e tono. Copilot in Outlook già riepiloga thread e produce bozze [4,5]. Gemini Workspace è un confronto commerciale obbligatorio, ma funzionalità/piani correnti non sono stati letti |
| B | Sincronizzazione, prenotazioni concorrenti, cancellazioni e notifiche | Doppia prenotazione o promemoria errato; verificabile sul calendario, con possibile danno al cliente | Buona ripetibilità solo con pochi tipi di appuntamento. Confrontare Calendly e prenotazioni native; documentazione Calendly non raggiungibile, quindi nessun endpoint o piano assunto |
| C | Reputazione del dominio, bounce, liste e assistenza contenuti | Invio al destinatario sbagliato, spam o mancata disiscrizione. Gli esiti tecnici non provano consenso o interesse | Riutilizzabile, ma ogni lista richiede controllo. Mailchimp dispone già delle operazioni di campagna [6]; non abbiamo verificato costi e funzioni AI correnti |
| D | Cambi API, token/ruoli, normalizzazione e definizioni metriche | Consigli non supportati o metriche confuse; verifica meno facile per il cliente non tecnico | Ripetibile su un solo canale/settore, meno su canali eterogenei. Insights nativi e codice Meta dimostrano dati disponibili [7], non superiorità del nostro rapporto |
| E | Creatività, policy, tracciamento e monitoraggio della spesa | Spesa reale, danno reputazionale e conclusioni causali scorrette. Richiede controllo prima della pubblicazione | Molte eccezioni per settore/offerta. Strumenti nativi ADV già gestiscono campagne; accesso/policy/prezzi correnti ancora da verificare |
| F | Costi per minuto, voce/modelli, disponibilità e trasferimento umano | Risposte pronunciate immediatamente, appuntamenti errati, dati sensibili ascoltati; il recupero è difficile durante la chiamata | FAQ semplici sono riutilizzabili, ma escalation, numeri e voci richiedono manutenzione. Twilio è infrastruttura verificata [8], non un concorrente completo validato |

### Punteggi motivati

Scala **1–5, maggiore = più favorevole**. Per accessi, integrazioni, costi ed errori, un punteggio alto significa minore difficoltà/rischio. Per alternative, alto significa più spazio competitivo ipotizzato. I pesi privilegiano frequenza e integrazioni; sono **giudizi progettuali**, senza dati di interviste. La media pesata è `Σ(punteggio × peso) / 100`.

| Servizio | Frequenza 15% | Beneficio 10% | Primo risultato 10% | Accessi 5% | Integrazioni 15% | Costi 10% | Errori 10% | Verifica 10% | Ripetibilità 10% | Alternative 5% | Media /5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | 5 | 5 | 4 | 3 | 4 | 4 | 4 | 4 | 4 | 2 | **4,10** |
| B | 4 | 5 | 4 | 3 | 3 | 3 | 2 | 5 | 5 | 1 | 3,65 |
| C | 3 | 4 | 4 | 2 | 3 | 3 | 2 | 4 | 4 | 2 | 3,20 |
| D | 3 | 3 | 2 | 2 | 2 | 3 | 4 | 2 | 3 | 2 | 2,65 |
| E | 3 | 4 | 2 | 2 | 1 | 2 | 1 | 2 | 2 | 2 | 2,10 |
| F | 4 | 4 | 1 | 2 | 1 | 1 | 1 | 3 | 3 | 2 | 2,25 |

A è la scelta iniziale perché offre un risultato quotidiano controllabile e limita le conseguenze attraverso la sola lettura. B è il secondo candidato, ma scrive in un sistema esterno e compete con percorsi di prenotazione già consolidati. C richiede liste e invii affidabili; D richiede prima una base di misurazione; E e F introducono spesa o interazioni immediate. L'ordine va rivisto dopo il pilota, senza moltiplicare ora i moduli.

Il vantaggio da verificare è **un controllo ricorrente dei quattro clienti più importanti, attivato con assistenza italiana e problemi spiegati chiaramente**, con risultati consegnati nell'app senza inventare mandati. Non rivendichiamo novità della sintesi email o superiorità dei modelli. Nel pilota confrontiamo il percorso con la posta attuale e con gli assistenti già disponibili al cliente, inclusi Gemini Workspace e Copilot quando presenti. Non sosteniamo che questi ultimi non possano svolgere attività ricorrenti: la loro copertura esatta non è stata verificata.

## Contratto del servizio Segreteria email

| Aspetto | Definizione dell'MVP |
| --- | --- |
| Scopo e completamento | Individuare conversazioni dei clienti prioritari, mostrare fonte, richieste da verificare, riepilogo e bozza locale. Completamento registrato anche se non ci sono nuove conversazioni; assenza di messaggi non significa assenza di richieste nell'intera casella |
| Integrazione | Gmail API v1, un account e una sola azienda. Demo con dati fittizi distinti dai risultati Gmail. OAuth reale predisposto, non validato su un account reale in questa consegna |
| Dati necessari | Nome/attività dell'azienda, indirizzi email di massimo quattro clienti, orario quotidiano e firma. Email lette solo per ricostruire il contesto pertinente; niente allegati da analizzare |
| Preferenze | Clienti, orario in `Europe/Rome`, firma e dati aziendali. Non si possono ampliare le capacità usando la chat; un controllo del tono è futuro |
| Avvio | Orario quotidiano e comando manuale previsto. Il lavoro è eseguito dal processo server/scheduler, non dal browser; dopo arresto nessuna elaborazione avviene finché il server non riparte |
| Limiti di lettura | Ultimi sette giorni, massimo 20 thread/25 messaggi nella selezione iniziale, con ricostruzione dei thread selezionati. Questi limiti possono lasciare fuori conversazioni e devono essere visibili; il limite della selezione iniziale non è una promessa di copertura completa né un tetto a ogni messaggio del thread |
| Azioni automatiche | Leggere, filtrare indirizzi, analizzare, produrre riepiloghi/bozze nell'app e aggiornare stato/cronologia secondo il mandato ricorrente |
| Mandato | Autorizzazione di lettura ricorrente e trattamento nel perimetro spiegato. Il servizio continua senza chiedere ogni mattina la stessa conferma |
| Nuova approvazione | Cambi di clienti/preferenze vengono salvati esplicitamente. In futuro un invio o una scrittura esterna richiederebbe nuova capacità, permesso e mandato: nessuna approvazione dell'interfaccia può abilitare queste operazioni nell'MVP |
| Arresto/intervento | Revoca/errore OAuth, dati mancanti, provider non disponibile oltre i tentativi, richiesta oltre catalogo o analisi non verificabile. Mostrare passaggio fallito, stato già salvato e azione utile |
| Verifica | Risultato associato a esecuzione e fonte; cronologia e stato persistenti; riesecuzione della stessa operazione non duplica risultati. Il cliente revisiona il testo prima di copiarlo |

Il consenso OAuth `gmail.readonly` permette **lettura della casella**, non soltanto delle email dei quattro clienti: il filtro più stretto è applicato dalla nostra logica, non dal permesso Google. La documentazione [1] distingue chiaramente lettura e invio. Anche **creare una bozza dentro Gmail** richiede permessi più ampi; le nostre bozze rimangono nel database dell'app. La revisione locale non costituisce un invio.

Email, oggetti, firme e thread sono dati esterni. Nessun testo letto può cambiare account, clienti, orario, permessi, destinazioni o capacità. Filtri, limiti e decisioni operative devono essere applicati nel codice. L'AI può proporre sintesi e testi, con verifica dello schema di output, citazioni alla fonte e revisione umana. Le istruzioni ricevute da una email non diventano comandi.

## Attivazione e continuità

Percorso: catalogo o richiesta in chat → Segreteria email → scelta demo/Gmail → dati aziendali e account → clienti/orario/firma → anteprima dichiarata → riepilogo del mandato → attivazione. In mancanza di credenziali il percorso Gmail deve spiegare il requisito e consentire la demo; non può mostrarsi collegato. I dati aziendali salvati sono riutilizzati. Nessuna configurazione tecnica è delegata al cliente finale: il team prepara prima progetto Google, client OAuth, callback, chiave di cifratura e politiche.

Dopo l'attivazione si vedono stato, ultimo risultato, prossima esecuzione, errori, account e cronologia, con pausa, ripresa e disattivazione. Le interruzioni del processo interrompono il lavoro: SQLite conserva preferenze e risultati; il server riavviato ripristina la pianificazione e il recupero previsto dall'implementazione. La consegna cloud dell'ambiente di sviluppo non equivale a disponibilità 24/7: un servizio commerciale richiede processo supervisionato, volume persistente, backup, monitoraggio e procedure di ripristino verificati.

## Dove gira il prodotto

| Dimensione | Locale | Cloud | Ibrido |
| --- | --- | --- | --- |
| Modello e qualità | Sul computer del cliente; qualità/velocità da misurare sul modello e sull'hardware concreto | Sul servizio del fornitore AI, oppure su infrastruttura gestita; qualità comunque da valutare su email italiane | Parte locale e parte cloud: la regola di instradamento deve essere esplicita, non nascosta |
| Dati | Database e contenuti sul computer; Gmail e OAuth restano servizi internet | Database nel servizio gestito; parti del testo possono essere inviate all'AI secondo contratto e mandato | Metadati/stato nel cloud e contenuto eventualmente locale; separazione e sincronizzazione richiedono lavoro |
| Internet | Necessario per Gmail, OAuth e aggiornamenti; il modello locale non rende offline la casella | Necessario per Gmail/OAuth/API modello e accesso del cliente | Necessario per provider e coordinamento; una macchina locale spenta resta un problema |
| Ricorrenza | Funziona solo mentre macchina e processo sono attivi | Processo gestito sempre acceso, con ripartenza e volume persistente | Dipende dai componenti coinvolti e può fermarsi se uno manca |
| Hardware/manutenzione | Cliente mantiene computer, spazio e disponibilità; noi aggiornamenti e supporto installazione | Noi manteniamo servizio, dati, account tecnici e monitoraggio | Noi manteniamo due superfici operative; assistenza più onerosa |
| Costi nel tempo | Hardware, elettricità, assistenza, aggiornamenti, eventuali API esterne | Hosting, storage/backup, AI, supporto, sicurezza e verifiche del provider | Entrambe le categorie, più sincronizzazione |

**Scelta iniziale (G): architettura server/cloud gestita**, con database persistente e scheduler lato server. L'MVP consegnato è **una singola azienda, in ambiente cloud privato, accessibile su localhost e senza autenticazione multiutente**: dimostra la logica del servizio, non è un SaaS pubblico. Non va esposto a internet né usato come installazione condivisa fra clienti. Per tre aziende serviranno istanze isolate e accesso autenticato oppure isolamento multi-tenant verificato.

La modalità demo predefinita usa un **analizzatore deterministico dichiarato**, con dati fittizi: non prova le prestazioni di un modello AI sui clienti reali. È implementato anche un adapter AI cloud opzionale, disabilitato senza credenziali: lavora solo sulle email dimostrative, valida lo schema dei risultati e i riferimenti ai messaggi e non dispone di strumenti operativi. È verificato con risposte simulate, non con un account AI reale. Il connettore Gmail è distinto dalla logica di analisi e rimane locale/deterministico anche quando l'AI demo è abilitata. Il modello impiegato per sviluppare questa applicazione non viene distribuito ai clienti e non ne stabilisce la licenza commerciale.

La possibilità locale rimane futura. La documentazione ufficiale Qwen3 dichiara modelli open-weight con licenza Apache 2.0 e varianti 4B/8B [9], con esecuzione locale CPU/GPU e quantizzazione documentate. Questa è una dichiarazione letta, **non un audit della licenza di un artefatto specifico**: prima di distribuire bisogna fissare modello/versione/pesi, leggere la relativa licenza, preservare attribuzioni/NOTICE quando richiesti e verificare licenze di runtime e quantizzazioni. **Stima hardware** per una prova con 4B–8B quantizzato: macchina con 16 GB RAM e spazio aggiuntivo, GPU opzionale; non è un requisito verificato né una garanzia di latenza. Vanno misurati memoria reale, contesto, concorrenza e accuratezza in italiano. Non proponiamo oggi una vendita locale.

## Licenza e proposta commerciale

L'architettura e il contratto commerciale sono decisioni separate.

| Formula | Cosa copre | Come restano coperti i costi ricorrenti | Valutazione (G) |
| --- | --- | --- | --- |
| Licenza una tantum | Diritto d'uso di una versione definita | Hosting/AI/assistenza richiedono canone separato o costi a carico del cliente; una vendita perpetua non finanzia automaticamente questi costi | Poco coerente con il servizio gestito iniziale |
| Abbonamento | Esecuzione, manutenzione del modulo, hosting e limiti dichiarati | Canone ricorrente e quota d'uso esplicita; fermare lavoro o chiedere aggiornamento del piano oltre quota, senza addebiti nascosti | Scelta iniziale |
| Licenza + assistenza/aggiornamenti | Versione installata e contratto separato di interventi/upgrade | Assistenza e aggiornamenti a pagamento; API esterne da attribuire contrattualmente | Possibile futuro per versione locale; più complesso per il cliente |

**Ipotesi commerciale da testare (S): €59/mese + IVA per azienda**, una casella, quattro clienti prioritari e controllo quotidiano nei limiti sopra; **€99 + IVA una tantum** per attivazione assistita. Nessun addebito è implementato. Per il primo pilota: due settimane gratuite, poi chiedere acquisto effettivo del mese successivo alla tariffa dichiarata. Questi importi sono ipotesi nostre, non prezzi osservati di Copilot, Gemini, Calendly o altri fornitori. Non includono Google Workspace, acquistato già dal cliente, né servizi non ancora sviluppati.

**Budget di lavoro stimato per azienda/mese**, prima di preventivi e misure:

- Hosting, database e backup: **€10–20** come allocazione di budget, da preventivare; non listino verificato.
- Inferenza testo: **€2–8** come tetto iniziale da validare, non tariffa di un modello scelto. La demo deterministica non sostiene questo costo e non misura i token di produzione.
- Supporto ricorrente: **€6–15**, ipotizzando 12–30 minuti a un costo interno di €30/ora. Onboarding e incidenti straordinari sono separati.
- Totale di queste tre voci: **€18–43**, lasciando **€16–41** sul canone di €59 prima di sviluppo, amministrazione, imposte, sicurezza, acquisizione clienti, verifiche OAuth ed eventuali costi aggiuntivi. Non è un margine netto dimostrato.

Costo AI misurabile: `token_input/1.000.000 × prezzo_input + token_output/1.000.000 × prezzo_output`, più eventuali chiamate/tool/storage. Occorre registrare consumi senza loggare contenuti email, scegliere un modello con termini commerciali e trattamento dati accettabili, consultare il relativo listino effettivo e aggiornare il calcolo. Prima di promettere il canone si verificano anche requisiti/costi di verifica e valutazione di sicurezza Google eventualmente applicabili. Se assistenza o conformità rendono i costi superiori al budget, si cambia prezzo o perimetro prima della vendita. Per qualsiasi futuro modulo ADV, **canone software e spesa pubblicitaria saranno separati** e il tetto della seconda approvato esplicitamente.

## Dati personali e condizioni per un pilota reale

Il prototipo usa dati dimostrativi. Quanto segue è il piano operativo necessario per clienti veri, non una dichiarazione di conformità già ottenuta. Il testo GDPR ufficiale [10] non è stato consultabile da questo ambiente e la verifica legale del caso concreto rimane da completare.

1. **Ruoli e finalità:** lo studio determina perché tratta la corrispondenza; il fornitore del prodotto tratta per suo conto secondo istruzioni documentate. Preparare accordo di nomina/responsabilità del trattamento, elenco subfornitori, finalità precise e informativa appropriata. Verificare la base giuridica effettiva del cliente; il consenso OAuth è un permesso tecnico, non sostituisce la base giuridica GDPR.
2. **Minimizzazione:** indirizzi prioritari, ricerca ristretta, limite temporale, niente allegati o importazione integrale della casella. Il permesso Gmail consente comunque lettura ampia: spiegarlo prima del collegamento. Evitare piloti con dati sanitari, categorie particolari o segreti professionali non valutati. Il contesto di un thread può includere persone non prioritarie.
3. **Destinazioni:** scegliere hosting e AI con contratti verificati, regioni e accessi documentati, divieto/limiti di training appropriati. Un server europeo non prova che l'inferenza e i subfornitori restino in Europa. Se ci sono trasferimenti extra SEE, verificarne strumenti e condizioni applicabili. Non assumere che i termini della chat di sviluppo valgano per una futura API commerciale.
4. **Sicurezza:** l'MVP cifra i token a riposo con chiave separata dal repository. Questo non equivale a cifrare database, risultati o backup. Prima di un pilota remoto servono autenticazione, controllo accessi, HTTPS, gestione/rotazione della chiave, isolamento delle aziende, backup cifrati e test di ripristino; vietare la pubblicazione di token o contenuti email nei log.
5. **Conservazione e diritti:** proposta da concordare: testi temporanei solo durante l'analisi; riepiloghi/bozze ed estratti di fonte per 30 giorni; audit tecnico privo di corpi per 90 giorni; eliminazione dell'azienda entro 30 giorni dalla cessazione, con scadenza documentata dei backup. **L'MVP SQLite conserva attualmente bozze ed estratti originali fino a 4.000 caratteri senza scadenza automatica: retention, export e cancellazione verificata sono lavoro necessario prima del pilota reale.** Revocare OAuth non cancella automaticamente copie locali.
6. **Gestione incidenti:** contatto responsabile, registro incidenti, valutazione del rischio e procedura di notifica con i termini applicabili. Registrare chi può accedere, come ripristinare e come sospendere il servizio. Valutare se ricorrono condizioni per una DPIA; non assumere che ogni piccola azienda ne sia esente o obbligata.
7. **Google:** verificare policy User Data/Limited Use, classificazione dello scope richiesto, pubblicazione/consent screen, eventuale verifica dell'app/security assessment e restrizioni dell'amministratore Workspace prima di collegare aziende esterne. Le relative pagine ufficiali [11] erano bloccate: non presentiamo il connettore predisposto come approvazione Google o integrazione reale già collaudata.

Per C, E e F serviranno valutazioni ulteriori prima dell'uso: provenienza/consenso delle liste e opposizioni marketing, policy pubblicitarie e spesa autorizzata, informazione agli interlocutori telefonici, eventuale registrazione e trasferimento umano. Non vengono eseguiti ora invii, chiamate, appuntamenti o acquisti reali.

## Pilota essenziale con tre aziende

**Selezione:** tre studi di consulenza B2B italiani con 2–10 persone e Google Workspace, senza categorie particolari di dati nel perimetro. Una persona referente per studio; una casella e quattro clienti ciascuno. I piloti reali iniziano dopo connettore verificato, configurazione provider, accesso protetto, accordi privacy e cancellazione/retention completati. Prima si può svolgere una prova assistita soltanto con i dati fittizi forniti.

**Durata (S):** una settimana di osservazione del lavoro corrente, due settimane di utilizzo, valutazione e proposta d'acquisto. Nell'osservazione la persona annota tempo speso e richieste rilevanti. Nessun invio automatico viene aggiunto nel pilota.

| Metrica e misura | Obiettivo iniziale da verificare (S) |
| --- | --- |
| Attivazione | Almeno 2 su 3 attivano senza assistenza tecnica individuale, mediana ≤15 minuti dal percorso fino al primo risultato; misurare separatamente attesa/ostacoli OAuth. Registrare step abbandonato e richieste d'aiuto |
| Utilità dei risultati | Il referente valuta almeno 30 thread per studio, confrontando email originali e richieste evidenziate. ≥80% dei risultati segnalati utili; misurare anche richieste rilevanti perse sul campione e copertura esclusa dai limiti |
| Qualità delle bozze | ≥70% delle bozze utilizzabili con sole correzioni brevi; riportare errori fattuali, impegni inventati e testo non supportato separatamente. È una valutazione umana, non il conteggio delle bozze generate |
| Tempo risparmiato | ≥15 minuti/giorno per almeno 2 studi, confrontando baseline e tempo reale di controllo/revisione, senza stimare il risparmio solo dal numero di output |
| Continuità | ≥95% delle esecuzioni programmate nel periodo previsto; registrare provider non disponibile, revoche e arresti separatamente. Il pilota deve usare un processo supervisionato |
| Interventi manuali | Dopo l'onboarding ≤1 intervento di supporto per studio/settimana; distinguere revisioni normali delle bozze da interventi tecnici necessari a far funzionare il servizio |
| Disponibilità a pagare | Almeno 2 su 3 accettano e pagano il mese successivo a €59 + IVA; interesse dichiarato o richiesta di sconto non contano come vendita. Raccogliere ragioni di rifiuto e alternativa già usata |

Con tre aziende questi indicatori guidano la decisione, non stimano statisticamente il mercato. Decisione: proseguire sul segmento se beneficio, continuità e pagamento superano le soglie senza supporto eccessivo; altrimenti correggere attivazione/perimetro o rivalutare il segmento. Non sviluppare un secondo modulo per compensare un beneficio non dimostrato del primo.

## Fonti ufficiali effettivamente lette

Tutte consultate il **4 ottobre 2026**, via HTTPS con verifica TLS. I riferimenti `main`/`public` sono mutabili; le revisioni delle discovery sotto identificano lo schema letto.

1. **Google Gmail API, discovery ufficiale**, revisione `20260727`: scope, `messages.list/get`, `threads.get`, `drafts.create/send`, metodi HTTP e permessi. <https://raw.githubusercontent.com/googleapis/google-api-python-client/main/googleapiclient/discovery_cache/documents/gmail.v1.json>
2. **Google Workspace, quickstart Python ufficiale:** `gmail.readonly`, autorizzazione e refresh token; è un esempio per app installata, non un tutorial che sostituisce l'OAuth web della nostra app. <https://raw.githubusercontent.com/googleworkspace/python-samples/main/gmail/quickstart/quickstart.py>
3. **Google Calendar API, discovery ufficiale**, revisione `20260708`: free/busy, creazione/modifica eventi e scope. <https://raw.githubusercontent.com/googleapis/google-api-python-client/main/googleapiclient/discovery_cache/documents/calendar.v3.json>
4. **Microsoft Docs, Microsoft 365 Copilot overview:** integrazione con Outlook, riepilogo thread, dati Microsoft Graph, agenti e distinzione di licenza con Copilot Chat; nessun prezzo letto. <https://raw.githubusercontent.com/MicrosoftDocs/microsoft-365-docs/public/copilot/microsoft-365-copilot-overview.md>
5. **Microsoft Docs, Data, Privacy, and Security for Microsoft 365 Copilot:** permessi sui dati, conservazione delle interazioni, bozze/riepiloghi da revisionare e affermazioni del fornitore sulle sue protezioni. Non costituisce verifica indipendente né copre la nostra applicazione. <https://raw.githubusercontent.com/MicrosoftDocs/microsoft-365-docs/public/copilot/microsoft-365-copilot-privacy.md>
6. **Mailchimp, SDK marketing Python ufficiale:** Basic Auth/OAuth2 e operazioni campagne/lista. <https://raw.githubusercontent.com/mailchimp/mailchimp-marketing-python/master/README.md>
7. **Meta, Business SDK Python ufficiale:** oggetti account, campagne e insights. Il README contiene anche esempi/nomi di permesso da non assumere correnti senza documentazione di prodotto; non vengono prescritti nell'MVP. <https://raw.githubusercontent.com/facebook/facebook-python-business-sdk/main/README.md> e <https://raw.githubusercontent.com/facebook/facebook-python-business-sdk/main/facebook_business/adobjects/adaccount.py>
8. **Twilio, specifica OpenAPI ufficiale:** creazione chiamate, TwiML, callback, timeout e registrazione opzionale. La licenza Apache della specifica non è una licenza gratuita del servizio telefonico. <https://raw.githubusercontent.com/twilio/twilio-oai/main/spec/json/twilio_api_v2010.json>
9. **Qwen, README Qwen3 ufficiale:** modelli, esecuzione CPU/GPU, quantizzazione e dichiarazione licenza Apache 2.0. <https://raw.githubusercontent.com/QwenLM/Qwen3/main/README.md>

## Fonti tentate ma non consultabili: verifica ancora necessaria

Questi URL sono riferimenti ufficiali per il lavoro successivo, **non prove lette in questa sessione**. Ogni richiesta ha ricevuto dal proxy `Tunnel connection failed: 403 Forbidden`.

- Gmail scope: <https://developers.google.com/workspace/gmail/api/auth/scopes>
- Gemini Workspace, confronto funzionalità/piani: <https://workspace.google.com/solutions/ai/>
- Microsoft Copilot, confronto commerciale: <https://www.microsoft.com/en-us/microsoft-365-copilot/business>
- Calendly, capacità/permessi effettivi: <https://developer.calendly.com/>
- Mailchimp: <https://mailchimp.com/developer/marketing/api/>
- Meta Marketing API: <https://developers.facebook.com/docs/marketing-apis/>
- Twilio Voice: <https://www.twilio.com/docs/voice>
- **[10] GDPR, testo italiano EUR-Lex:** <https://eur-lex.europa.eu/eli/reg/2016/679/oj/ita>
- **[11] Google, verifica restricted scopes e User Data Policy:** <https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification> e <https://developers.google.com/terms/api-services-user-data-policy>

Le ricerche di un ipotetico mirror ufficiale Calendly su GitHub e di documentazione Workspace generica hanno ricevuto 404: non sono fonti e non provano assenza di documentazione o di funzionalità. Per completare la ricerca commerciale servono accesso ai domini sopra e controllo dei listini/termini ufficiali prima di qualsiasi offerta reale.
