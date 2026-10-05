# Spazelia · la tua giornata, con più spazio

Applicazione in italiano per PMI e piccoli studi. **Segreteria email** controlla i contatti prioritari e prepara bozze da rivedere. Il catalogo include **23 moduli aziendali locali** per clienti, preventivi, bozze fattura, incassi, spese, acquisti, commesse, documenti, scadenze, magazzino, assistenza e organizzazione del lavoro. La panoramica presenta le cose da fare, un riepilogo neutro a destra e l’agenda manuale. Le integrazioni esterne non ancora disponibili sono indicate nel catalogo.

## Avvio

Ambiente verificato: Linux, Python 3.12.14. Non serve Node per eseguire il prodotto; Node serve solo al controllo sintattico del frontend. Non occorrono chiavi per la demo.

```bash
# dalla cartella del repository
bash scripts/setup.sh
bash scripts/start.sh
```

Il server locale ascolta sulla porta 8000 dell'interfaccia locale. Apri l'interfaccia con il browser dell'ambiente o con un tunnel privato della tua piattaforma. La homepage è pubblica; `/login` e `/register` portano allo spazio personale `/app` e all'area `/account`. Per l'accesso online usa l'avvio Railway descritto sotto.

```bash
bash scripts/check.sh
```

`requirements.lock` blocca anche le dipendenze transitive. Le istruzioni di installazione non modificano codice, test o file delle dipendenze. L'ambiente cloud è già isolato: usa il checkout esistente, senza creare worktree.

## Servizi per la PMI

Per studi e imprese di servizi, il profilo aziendale riusa i dati nei documenti. Preventivo, bozza fattura e incasso si possono collegare con una conversione da rivedere, senza riscrivere gli importi o creare duplicati. La ricerca trova le attività nei diversi moduli; dalla panoramica si possono completare e annullare il completamento. La [ricerca sui concorrenti](docs/research/market-review-2026-10-05.html) documenta le fonti, i prezzi e le priorità di prodotto al 5 ottobre 2026.

Ogni modulo propone passaggi operativi specifici: **94 passaggi nei 23 moduli**, con progresso segnato dall’utente e prossimo passo visibile nella panoramica. Dieci moduli riusano la data operativa già compilata quando manca una scadenza esplicita. Incassi e spese mostrano gli importi aperti, scaduti e previsti per oggi; il magazzino calcola la quantità necessaria per raggiungere la soglia scelta. **Ripeti attività** prepara una nuova scheda per una data futura, da rivedere prima di salvarla, con passaggi e minuti azzerati.

Apri **Servizi**, cerca un modulo o scegli un’area e aggiungi una scheda con i dati della tua impresa. Spazelia prepara il documento locale, calcola i totali di preventivi/bozze fattura, ordina scadenze e priorità e segnala scorte sotto la soglia inserita. Puoi aggiornare, completare, riaprire o esportare ogni attività. La chat suggerisce il servizio da aprire, senza modificare dati o permessi da sola. I nuovi spazi partono vuoti.

[La guida dei servizi PMI](docs/PMI.md) descrive funzioni e limiti: bozze fiscali interne, riferimenti documentali e dati aggiunti dall’utente; SDI, banche, PEC, calendario esterno e telefonia richiedono integrazioni dedicate. I minuti risparmiati sono dichiarazioni dell’utente, senza una stima automatica del costo del personale.

## Account e abbonamento

L'accesso usa un modulo nella pagina, senza popup HTTP Basic. La sessione persistente usa un cookie HttpOnly, SameSite=Lax e Secure su HTTPS; l'uscita la revoca sul server. Le password degli account registrati sono salvate con scrypt e le mutazioni richiedono un token CSRF. La registrazione crea un nuovo spazio vuoto: azienda, posta, contatti, agenda e bozze sono separati dagli altri account.

L'account già esistente accede con il nome e la password configurati dal gestore in `FILO_ACCESS_USERNAME` e `FILO_ACCESS_PASSWORD`, mantenendo i dati precedenti. La creazione di un nuovo account non concede accesso a questo spazio.

**Password dimenticata.** Dalla pagina di accesso, *Hai dimenticato la password?* porta a `/forgot-password`: l'utente indica l'email e riceve un link valido 60 minuti verso `/reset-password`. La risposta è identica per indirizzi esistenti e inesistenti, l'email parte dopo la risposta, solo l'ultimo link richiesto funziona e ogni link vale una volta. Salvare la nuova password chiude le sessioni su tutti i dispositivi ed entra nello spazio. Le richieste sono limitate per indirizzo e per email. L'account del gestore non si recupera via email: la sua password resta `FILO_ACCESS_PASSWORD`.

**Cambio password ed eliminazione.** Nell'area `/account`, la sezione *Password* cambia la password dopo la verifica di quella attuale e disconnette gli altri dispositivi. *Elimina account* chiede la password e la parola `ELIMINA`; elimina in modo definitivo account, sessioni, link di recupero, l'intero spazio (attività, documenti, agenda, credenziali email cifrate, bozze e cronologia) e i dati di fatturazione locali. Se esiste un cliente Stripe, viene eliminato su Stripe, che disdice subito l'abbonamento senza rimborso del periodo in corso e conserva le fatture emesse. Se Stripe non risponde, nulla viene cancellato. L'account del gestore non si elimina da qui.

**Invio delle email di servizio.** Il recupero password richiede `FILO_MAIL_FROM` (per esempio `Spazelia <noreply@tuodominio.it>`, con dominio verificato presso il fornitore) e uno di questi canali:

- `FILO_RESEND_API_KEY`: [Resend](https://resend.com) via HTTPS, consigliato da Railway e l'unico utilizzabile sui piani Railway Free, Trial e Hobby, dove l'SMTP in uscita è disabilitato.
- `FILO_SMTP_HOST`, con `FILO_SMTP_PORT` (587 predefinita), `FILO_SMTP_USERNAME`, `FILO_SMTP_PASSWORD` e `FILO_SMTP_SECURITY` (`starttls` predefinito oppure `ssl`): disponibile su Railway dal piano Pro. Senza TLS l'invio non parte.
- `FILO_MAIL_OUTBOX_DIR`: solo per sviluppo e prove, salva i messaggi come file `.eml` in una cartella locale.

Senza configurazione la pagina di recupero indica che il servizio non è ancora attivo. I link usano `FILO_PUBLIC_URL` oppure il dominio pubblico Railway, mai l'header `Host` della richiesta.

L'area personale include il percorso di abbonamento Stripe Checkout e il portale di gestione. **Prezzo da definire: i pagamenti restano disattivati finché il gestore non configura Stripe.** Registrarsi non avvia un addebito; il server sceglie il prezzo e aggiorna lo stato solo tramite webhook firmati. Questa versione non applica un blocco delle funzioni in base all'abbonamento. Il collegamento e gli addebiti reali Stripe non sono stati collaudati. Vedi [configurazione dei pagamenti](docs/BILLING.md).

## Il tuo buongiorno, già in ordine

La panoramica usa superfici bianche, grigi neutri, testo carbone e accenti mandarino e mantiene «La tua giornata, con più spazio». Mostra le risposte da rivedere, il motivo della priorità, un estratto della richiesta e un prossimo passo concreto. Le urgenze esplicite precedono le richieste in attesa da almeno due giorni; le altre seguono l’ordine di arrivo. Le bozze sono leggibili, copiabili e segnabili come riviste nella stessa pagina. I controlli successivi non duplicano le conversazioni e non ripropongono una sorgente già rivista.

Il riepilogo usa solo i controlli salvati: apre il periodo dalle 18 della sera precedente oppure da venerdì sera durante il weekend e il lunedì, con orari italiani. Indica l’ultimo controllo e segnala quando va aggiornato; oltre un’ora gli ultimi arrivi non sono considerati verificati. I contatti senza messaggi sono indicati come tali soltanto quando la copertura lo prova. I connettori restituiscono una selezione limitata di messaggi recenti: un’assenza resta «da verificare».

Prima del primo controllo la pagina è vuota, senza richieste o appuntamenti inventati. Su Railway `FILO_REAL_DATA_ONLY=1` nasconde i dati dimostrativi conservati e blocca le esecuzioni demo. La pagina propone il passo utile: configurare la posta, ricollegarla, riprendere il servizio, aggiornare il riepilogo o verificare la prima risposta.

L’agenda contiene gli appuntamenti aggiunti dall’utente, persistenti e ordinati per orario in Europe/Rome. Puoi consultare un’altra giornata e scaricare un file `.txt` con appuntamenti e note. Il calendario esterno non è collegato e non vengono inventati impegni.

Gli avvisi “oggi/domani se risponde questo contatto dimmelo subito” si possono proporre in chat e salvare, oppure aggiungere direttamente nella panoramica. Richiedono un contatto prioritario già configurato e una casella collegata. Mentre il servizio autorizzato è attivo, un avviso in attesa per oggi abilita un controllo ogni cinque minuti; quando viene osservato un messaggio arrivato dopo la creazione dell’avviso, la segnalazione compare prima delle altre priorità. Nessuna notifica esterna viene inviata. Pausa e disattivazione fermano anche questi controlli.

## Collegamenti email

La sezione **Collegamenti** offre Gmail, Outlook/Microsoft 365, iCloud, Yahoo, Aruba, Libero e un server IMAP personalizzato. Gmail e Outlook utilizzano consenso OAuth nella stessa scheda; richiedono la configurazione iniziale delle app Google e Microsoft da parte del gestore. Gli altri provider usano IMAP con TLS sulla porta 993 e password per app quando richiesta. Una sola casella per account; una sostituzione riuscita ferma il mandato precedente. Le credenziali sono cifrate e separate per utente. Tutti i provider supportano i controlli quotidiani e gli avvisi odierni ogni cinque minuti con mandato attivo.

Consulta [la guida di configurazione dei provider](docs/EMAIL.md). Le prove sono simulate: nessuna casella reale è stata collegata durante lo sviluppo. Su Railway le app OAuth Google e Microsoft restano da configurare.

## Percorso dimostrativo locale

Questo percorso è disponibile solo con `FILO_REAL_DATA_ONLY` disabilitato, in un’istanza isolata.

1. Dal catalogo scegli **Segreteria email** o chiedi nella chat di seguire le email dei clienti importanti.
2. Collega la **casella dimostrativa** e compila un'azienda e contatti fittizi per la prova. Lo spazio originale locale può conservare Studio Riva; i nuovi account partono vuoti.
3. Imposta da uno a quattro clienti prioritari e l'orario quotidiano in Italia.
4. Guarda l'anteprima: messaggi di esempio, richieste e bozze locali.
5. Autorizza lettura e preparazione di bozze; l'applicazione non prevede l'invio di email.
6. Attiva, esegui un controllo e apri risultato, bozze e cronologia. Segnare una bozza come rivista registra una revisione locale; non la invia e non la scrive in Gmail.
7. Modifica preferenze, metti in pausa e riattiva. Il pannello degli imprevisti consente di provare un guasto temporaneo e un'autorizzazione scaduta, dichiaratamente simulati.

La demo legge dati fittizi e usa regole deterministiche dichiarate. Il risultato non è la prova della qualità di un modello linguistico su email reali. Il connettore Gmail effettua operazioni reali solo dopo la configurazione e il consenso Google; non è stato collegato a un account reale durante questo sviluppo.

L'adapter AI cloud è implementato e verificato con risposte simulate. Per provarlo su email fittizie il gestore può impostare in modo sicuro `FILO_AI_API_KEY` e `FILO_AI_ENABLED=1` nel server; `FILO_AI_MODEL` è facoltativo (predefinito `gpt-4o-mini`). Usa l'API Chat Completions su `api.openai.com`, con output strutturato e riferimenti ai soli messaggi forniti. Il modello deve essere disponibile nell'account e compatibile con lo schema; disponibilità, qualità e fatturazione reali non sono state verificate. Le esecuzioni AI utilizzano anche il profilo aziendale: usa dati fittizi in queste prove. Nessuna chiave è richiesta per la modalità predefinita. Gmail rimane sull'analisi locale anche se l'AI demo è abilitata.

## Stato persistente e disponibilità

SQLite e le credenziali locali risiedono nella cartella ignorata `.runtime/`; puoi scegliere un'altra cartella con `ALEXCHIARA_DATA_DIR`. Gli account e le sessioni sono in `accounts.sqlite3`, lo stato degli abbonamenti in `billing.sqlite3`. Lo spazio originale conserva `alexchiara.sqlite3`; gli account registrati hanno archivi separati in `workspaces/<id>/`. Il processo server controlla la pianificazione indipendentemente dalla pagina e dalla chat. L'applicazione usa un unico processo Uvicorn. All'avvio vengono aperti solo gli spazi con un servizio attivo o controlli in sospeso; gli altri si aprono alla prima richiesta del titolare. Uno spazio danneggiato risponde con un errore 503 senza fermare gli altri account.

Database, preferenze, mandati, cronologia e tentativi sopravvivono al riavvio se la cartella dati resta disponibile. I processi non sopravvivono alla pubblicazione di un ambiente cloud o allo spegnimento: riavvia il server. Il servizio riprende il lavoro pendente e recupera un controllo dovuto, senza inventare esecuzioni durante il fermo o produrre in massa tutti gli arretrati. Connessioni interrotte producono errori e tentativi limitati: il controllo quotidiano riprova per circa due ore, quelli manuali tre volte. Un consenso revocato richiede un nuovo collegamento. I controlli ripetuti degli avvisi che non hanno osservato nulla di nuovo vengono rimossi dopo un giorno, così l'archivio non cresce senza limite.

Prima dell'uso continuativo reale servono backup e ripristino verificato dell'intera cartella dati e delle chiavi locali. Il deploy Railway aggiunge HTTPS e supervisione del processo; l'applicazione separa i dati per account, senza condivisione o ruoli di squadra. La cifratura dei token non cifra l'intero database delle email.

## Identità e compatibilità

Spazelia è il nome pubblico del prodotto. Le variabili `FILO_*`, il nome tecnico del servizio Railway `filo`, il nome utente predefinito `filo`, i cookie, gli archivi SQLite e i percorsi API conservano i loro identificatori per mantenere configurazione, dati e sessioni esistenti. Il dominio pubblico e le callback OAuth restano quelli effettivamente configurati su Railway; il rebranding non registra un nuovo dominio.

## Pubblicazione Railway

`railway.toml` configura Railpack, le dipendenze bloccate, l'avvio su `0.0.0.0:$PORT`, un solo processo e il controllo `/api/health`. Il file `.python-version` seleziona Python 3.12 (la variabile `RAILPACK_PYTHON_VERSION=3.12`, se presente, indica la stessa versione) e collega un volume persistente a `/data` con `ALEXCHIARA_DATA_DIR=/data`. Mantieni una sola replica e disabilita la sospensione automatica: il processo gestisce i controlli programmati.

Prima del deploy imposta nelle variabili Railway `FILO_ACCESS_PASSWORD` con una password robusta; `FILO_ACCESS_USERNAME` è facoltativo e vale `filo` per impostazione predefinita. L'avvio cloud si interrompe se manca la password dell'account esistente. Homepage, login, registrazione, risorse statiche, `favicon.ico`, icona Apple e `robots.txt` sono pubblici; gli indirizzi inesistenti mostrano una pagina 404 in italiano. Lo spazio personale e le sue API richiedono una sessione autenticata. Il controllo di salute restituisce solo lo stato tecnico (anche con richieste HEAD) e rimane accessibile a Railway; `scheduler` vale `degraded` se un worker si è fermato. I limiti ai tentativi di accesso usano l'indirizzo che il proxy Railway scrive in `X-Real-IP` (`FILO_CLIENT_IP_HEADER`, impostata dallo script di avvio), non `X-Forwarded-For`, che il visitatore può falsificare. Se `FILO_ACCESS_USERNAME` coincide con l'email di un account registrato, l'accesso del gestore viene disattivato con un errore nel log invece di bloccare l'avvio. Cambiare la password del gestore e ridistribuire il servizio revoca le sue sessioni precedenti.

Genera il dominio HTTPS Railway prima del deploy. Lo script include automaticamente `RAILWAY_PUBLIC_DOMAIN`, `RAILWAY_PRIVATE_DOMAIN` e `healthcheck.railway.app` tra gli host consentiti; per un dominio personalizzato aggiungilo a `ALEXCHIARA_ALLOWED_HOSTS`. Un volume nuovo parte senza dati locali; un volume esistente conserva lo spazio precedente. La cartella locale `.runtime/`, le chiavi e le credenziali non vanno in Git. Gmail e AI rimangono facoltativi e richiedono le loro variabili dedicate. Il volume conserva lo stato fra i deploy; un redeploy con volume può causare una breve interruzione.

## Account e AI reali

Vedi [collegamento Gmail](docs/GMAIL.md) per i passaggi ufficiali e le variabili del gestore. I segreti restano sul server e fuori dal codice e dal browser. Non incollare segreti nella chat o in Git.

La progettazione del prodotto, il confronto dei moduli, le fonti e la proposta commerciale sono in [PRODOTTO.md](docs/PRODOTTO.md). Architettura, controlli e limiti sono descritti in [ARCHITETTURA.md](docs/ARCHITETTURA.md). Le prove eseguite sono raccolte in [VERIFICA.md](docs/VERIFICA.md).

Il prodotto adotta **Spazelia**, con la promessa «La tua giornata, con più spazio», un simbolo aperto e una palette bianco, carbone e arancio caldo. La [ricerca di nome e branding](docs/research/naming-branding-2026-10-05.html) conserva il confronto con Giorvia e Prontela, sei loghi/simboli SVG e i controlli documentati. Il cambio di identità non conferma la disponibilità dei domini, dei marchi o dei nomi social: queste verifiche restano separate prima di registrarli.

## Ambito

Questo è un MVP verificabile, disponibile anche su Railway con homepage pubblica e account separati. Include il percorso Stripe da configurare, ma non un prezzo attivo o un paywall. Non include invio di email ai clienti, sincronizzazione delle bozze Gmail o dei calendari esterni, telefonia, campagne, condivisione dello spazio fra colleghi o verifica dell'indirizzo email alla registrazione. Le sole email inviate sono quelle di servizio per il recupero password. Non impegna budget pubblicitario.

La prova browser facoltativa `node scripts/browser-smoke.cjs` richiede Playwright e Chromium, già presenti nell'ambiente cloud ma non necessari all'app. Usala contro un'istanza dimostrativa inizialmente inattiva con dati nuovi: modifica soltanto l'azienda fittizia e la sua configurazione. Per isolare la prova avvia il server con `ALEXCHIARA_DATA_DIR` in una cartella temporanea, `FILO_PORT=8001` e `FILO_ACCESS_PASSWORD=browser-test`; imposta anche `FILO_PORT=8001` per il comando del test. Il controllo accede tramite modulo e verifica configurazione guidata, risultati, revisione, preferenze, errori, recupero e chat. Una password di prova diversa si può indicare al test con `FILO_TEST_OWNER_PASSWORD`. Le prove API di `scripts/check.sh` isolano automaticamente i dati e includono il confine di autenticazione e l'isolamento fra account.

La prova `FILO_PORT=8002 node scripts/morning-smoke.cjs` usa una nuova istanza isolata sulla porta 8002, con `FILO_ACCESS_PASSWORD=morning-test`; imposta `ALEXCHIARA_DATA_DIR` a una cartella temporanea e avvia `app.main:create_app` con `.venv/bin/python`, oppure usa `scripts/start.sh` con la porta scelta. Verifica l’accesso con modulo, il riepilogo prima e dopo il controllo, la persistenza delle bozze aperte, la revisione senza duplicati, l’agenda con browser in un altro fuso, il download e il layout desktop/mobile. Non usarla sul servizio Railway o su dati reali.

La prova `FILO_PORT=8014 node scripts/real-mode-smoke.cjs` verifica un’istanza nuova con `FILO_REAL_DATA_ONLY=1` e `FILO_ACCESS_PASSWORD=real-mode-test`: accesso tramite sessione, pagina vuota senza dati dimostrativi, guida Google, callback corretta e layout mobile. Una password diversa si può indicare con `FILO_TEST_OWNER_PASSWORD`. La prova non modifica i dati dello spazio, non configura credenziali e non legge email.

La prova `node scripts/account-smoke.cjs` avvia autonomamente un server temporaneo isolato e verifica homepage, login, registrazione, persistenza della sessione dopo refresh, uscita, area personale e pagamenti non configurati. Le schermate sono controllate su desktop e telefono; nessun addebito o collegamento a provider reale viene effettuato.

La prova completa degli avvisi usa un server fixture riproducibile: in un terminale avvia `.venv/bin/python scripts/watch_smoke_server.py`, nell’altro `FILO_PORT=8016 node scripts/watch-smoke.cjs`. Il server ascolta solo su localhost, disabilita worker e chiamate ai provider e usa un database temporaneo eliminato all’arresto. Verifica form, proposta in chat, conferma, arrivo osservato in cima alle priorità, link alla casella corretta e chiusura persistente.

`node scripts/account-lifecycle-smoke.cjs` avvia un server isolato sulla porta 8046 con una cartella di posta locale e verifica recupero password dal link nell'email, link monouso rimosso dalla barra degli indirizzi, cambio password, eliminazione dell'account con conferma e note dell'account gestore, a 1440 e 390 pixel.

`node scripts/release-ui-smoke.cjs` avvia un server isolato sulla porta 8044 e verifica le correzioni di rilascio dell’interfaccia: messaggi in italiano, pagina 404, icone pubbliche, link della homepage per chi ha già effettuato l’accesso, collegamento “Vai al contenuto”, icone dei pulsanti durante il salvataggio, finestre che restano aperte selezionando il testo e messaggio dopo un collegamento OAuth non riuscito.

`node scripts/playbook-smoke.cjs` avvia un server isolato sulla porta 8042 e verifica passaggi suggeriti, scadenze dai campi, riepilogo economico, riordino, copia documento e revisione delle attività ripetute. Usa dati temporanei e controlla persistenza, errori e layout desktop/mobile, senza collegamenti esterni o scritture su Railway.
