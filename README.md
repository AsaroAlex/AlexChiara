# Filo · servizi già pronti per il lavoro di ogni giorno

MVP in italiano per piccoli studi di consulenza B2B. Il primo servizio è **Segreteria email**: controlla i clienti prioritari, segnala richieste aperte e prepara bozze da rivedere. La panoramica presenta subito le cose da fare, un riepilogo neutro a destra e un’agenda manuale con ordine del giorno scaricabile. La sincronizzazione del calendario e i social sono servizi futuri.

## Avvio

Ambiente verificato: Linux, Python 3.12.14. Non serve Node per eseguire il prodotto; Node serve solo al controllo sintattico del frontend. Non occorrono chiavi per la demo.

```bash
cd /workspace/AlexChiara
bash scripts/setup.sh
bash scripts/start.sh
```

Il server locale ascolta sulla porta 8000 dell'interfaccia locale. Apri l'interfaccia con il browser dell'ambiente o con un tunnel privato della tua piattaforma. La homepage è pubblica; `/login` e `/register` portano allo spazio personale `/app` e all'area `/account`. Per l'accesso online usa l'avvio Railway descritto sotto.

```bash
bash scripts/check.sh
```

`requirements.lock` blocca anche le dipendenze transitive. Le istruzioni di installazione non modificano codice, test o file delle dipendenze. L'ambiente cloud è già isolato: usa il checkout esistente, senza creare worktree.

## Account e abbonamento

L'accesso usa un modulo nella pagina, senza popup HTTP Basic. La sessione persistente usa un cookie HttpOnly, SameSite=Lax e Secure su HTTPS; l'uscita la revoca sul server. Le password degli account registrati sono salvate con scrypt e le mutazioni richiedono un token CSRF. La registrazione crea un nuovo spazio vuoto: azienda, posta, contatti, agenda e bozze sono separati dagli altri account.

L'account già esistente accede con il nome e la password configurati dal gestore in `FILO_ACCESS_USERNAME` e `FILO_ACCESS_PASSWORD`, mantenendo i dati precedenti. La creazione di un nuovo account non concede accesso a questo spazio.

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

SQLite e le credenziali locali risiedono nella cartella ignorata `.runtime/`; puoi scegliere un'altra cartella con `ALEXCHIARA_DATA_DIR`. Gli account e le sessioni sono in `accounts.sqlite3`, lo stato degli abbonamenti in `billing.sqlite3`. Lo spazio originale conserva `alexchiara.sqlite3`; gli account registrati hanno archivi separati in `workspaces/<id>/`. Il processo server controlla la pianificazione indipendentemente dalla pagina e dalla chat. L'applicazione usa un unico processo Uvicorn.

Database, preferenze, mandati, cronologia e tentativi sopravvivono al riavvio se la cartella dati resta disponibile. I processi non sopravvivono alla pubblicazione di un ambiente cloud o allo spegnimento: riavvia il server. Il servizio riprende il lavoro pendente e recupera un controllo dovuto, senza inventare esecuzioni durante il fermo o produrre in massa tutti gli arretrati. Connessioni interrotte producono errori e tentativi limitati; un consenso revocato richiede un nuovo collegamento.

Prima dell'uso continuativo reale servono backup e ripristino verificato dell'intera cartella dati e delle chiavi locali. Il deploy Railway aggiunge HTTPS e supervisione del processo; l'applicazione separa i dati per account, senza condivisione o ruoli di squadra. La cifratura dei token non cifra l'intero database delle email.

## Pubblicazione Railway

`railway.toml` configura Railpack, le dipendenze bloccate, l'avvio su `0.0.0.0:$PORT`, un solo processo e il controllo `/api/health`. Seleziona Python 3.12 tramite `RAILPACK_PYTHON_VERSION=3.12` e collega un volume persistente a `/data` con `ALEXCHIARA_DATA_DIR=/data`. Mantieni una sola replica e disabilita la sospensione automatica: il processo gestisce i controlli programmati.

Prima del deploy imposta nelle variabili Railway `FILO_ACCESS_PASSWORD` con una password robusta; `FILO_ACCESS_USERNAME` è facoltativo e vale `filo` per impostazione predefinita. L'avvio cloud si interrompe se manca la password dell'account esistente. Homepage, login, registrazione e risorse statiche sono pubblici; lo spazio personale e le sue API richiedono una sessione autenticata. Il controllo di salute restituisce solo lo stato tecnico e rimane accessibile a Railway. Cambiare la password del gestore e ridistribuire il servizio revoca le sue sessioni precedenti.

Genera il dominio HTTPS Railway prima del deploy. Lo script include automaticamente `RAILWAY_PUBLIC_DOMAIN`, `RAILWAY_PRIVATE_DOMAIN` e `healthcheck.railway.app` tra gli host consentiti; per un dominio personalizzato aggiungilo a `ALEXCHIARA_ALLOWED_HOSTS`. Un volume nuovo parte senza dati locali; un volume esistente conserva lo spazio precedente. La cartella locale `.runtime/`, le chiavi e le credenziali non vanno in Git. Gmail e AI rimangono facoltativi e richiedono le loro variabili dedicate. Il volume conserva lo stato fra i deploy; un redeploy con volume può causare una breve interruzione.

## Account e AI reali

Vedi [collegamento Gmail](docs/GMAIL.md) per i passaggi ufficiali e le variabili del gestore. I segreti restano sul server e fuori dal codice e dal browser. Non incollare segreti nella chat o in Git.

La progettazione del prodotto, il confronto dei moduli, le fonti e la proposta commerciale sono in [PRODOTTO.md](docs/PRODOTTO.md). Architettura, controlli e limiti sono descritti in [ARCHITETTURA.md](docs/ARCHITETTURA.md). Le prove eseguite sono raccolte in [VERIFICA.md](docs/VERIFICA.md).

## Ambito

Questo è un MVP verificabile, disponibile anche su Railway con homepage pubblica e account separati. Include il percorso Stripe da configurare, ma non un prezzo attivo o un paywall. Non include invio email, sincronizzazione delle bozze Gmail o dei calendari esterni, telefonia, campagne, condivisione dello spazio fra colleghi o recupero password tramite email. Non impegna budget pubblicitario.

La prova browser facoltativa `node scripts/browser-smoke.cjs` richiede Playwright e Chromium, già presenti nell'ambiente cloud ma non necessari all'app. Usala contro un'istanza dimostrativa inizialmente inattiva con dati nuovi: modifica soltanto l'azienda fittizia e la sua configurazione. Per isolare la prova avvia il server con `ALEXCHIARA_DATA_DIR` in una cartella temporanea, `FILO_PORT=8001` e `FILO_ACCESS_PASSWORD=browser-test`; imposta anche `FILO_PORT=8001` per il comando del test. Il controllo accede tramite modulo e verifica configurazione guidata, risultati, revisione, preferenze, errori, recupero e chat. Una password di prova diversa si può indicare al test con `FILO_TEST_OWNER_PASSWORD`. Le prove API di `scripts/check.sh` isolano automaticamente i dati e includono il confine di autenticazione e l'isolamento fra account.

La prova `FILO_PORT=8002 node scripts/morning-smoke.cjs` usa una nuova istanza isolata sulla porta 8002, con `FILO_ACCESS_PASSWORD=morning-test`; imposta `ALEXCHIARA_DATA_DIR` a una cartella temporanea e avvia `app.main:create_app` con `.venv/bin/python`, oppure usa `scripts/start.sh` con la porta scelta. Verifica l’accesso con modulo, il riepilogo prima e dopo il controllo, la persistenza delle bozze aperte, la revisione senza duplicati, l’agenda con browser in un altro fuso, il download e il layout desktop/mobile. Non usarla sul servizio Railway o su dati reali.

La prova `FILO_PORT=8014 node scripts/real-mode-smoke.cjs` verifica un’istanza nuova con `FILO_REAL_DATA_ONLY=1` e `FILO_ACCESS_PASSWORD=real-mode-test`: accesso tramite sessione, pagina vuota senza dati dimostrativi, guida Google, callback corretta e layout mobile. Una password diversa si può indicare con `FILO_TEST_OWNER_PASSWORD`. La prova non modifica i dati dello spazio, non configura credenziali e non legge email.

La prova `node scripts/account-smoke.cjs` avvia autonomamente un server temporaneo isolato e verifica homepage, login, registrazione, persistenza della sessione dopo refresh, uscita, area personale e pagamenti non configurati. Le schermate sono controllate su desktop e telefono; nessun addebito o collegamento a provider reale viene effettuato.

La prova completa degli avvisi usa un server fixture riproducibile: in un terminale avvia `.venv/bin/python scripts/watch_smoke_server.py`, nell’altro `FILO_PORT=8016 node scripts/watch-smoke.cjs`. Il server ascolta solo su localhost, disabilita worker e chiamate ai provider e usa un database temporaneo eliminato all’arresto. Verifica form, proposta in chat, conferma, arrivo osservato in cima alle priorità, link alla casella corretta e chiusura persistente.
