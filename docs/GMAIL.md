# Collegare Gmail con permessi di sola lettura

La demo funziona senza credenziali. Queste operazioni sono a carico del gestore del prodotto, non del cliente finale. Il connettore è implementato ma la sua integrazione reale non è stata verificata su un account durante lo sviluppo.

## Configurazione del gestore

1. Crea o seleziona un progetto Google Cloud e abilita **Gmail API**. Configura il consenso OAuth in **Google Auth Platform** secondo le istruzioni ufficiali; se il progetto è in test, configura gli utenti di test ammessi.
2. Crea un client OAuth di tipo applicazione web. Registra l'URI di ritorno esatto che il server userà. Per questa macchina privata l'URI predefinito è `http://127.0.0.1:8000/api/gmail/oauth/callback`; per una distribuzione reale serve un dominio HTTPS sotto il tuo controllo. Un browser esterno deve raggiungere lo stesso indirizzo registrato tramite un percorso supportato dalla tua piattaforma.
3. Inserisci in modo sicuro nell'ambiente del processo `FILO_GOOGLE_CLIENT_ID`, `FILO_GOOGLE_CLIENT_SECRET` e, se diverso, `FILO_GOOGLE_REDIRECT_URI`. Non scrivere valori in file versionati. Il segreto OAuth è letto dal processo server: una sostituzione del proxy su una destinazione HTTPS non deve essere scambiata per un segreto raw disponibile localmente.
4. Consenti, conservando le regole di rete esistenti, `accounts.google.com`, `oauth2.googleapis.com` e `gmail.googleapis.com`. Il preset package manager non comprende necessariamente questi domini. La configurazione cloud salvata elenca le aggiunte; salvarla non applica da solo l'accesso al processo corrente.
5. Riavvia il server e controlla lo stato Gmail nella schermata di collegamento.

Il cliente seleziona **Collega Gmail**, legge la spiegazione dell'accesso, concede il consenso nella pagina Google e torna all'app. Il codice usa `state` monouso con scadenza e PKCE, scambia il codice solo sul server e conserva i token cifrati con una chiave locale a permessi `0600`.

## Cosa legge il servizio

Un solo ambito: `https://www.googleapis.com/auth/gmail.readonly`. È un ambito ristretto: per un'app pubblica potrebbero essere necessarie verifica OAuth, requisiti della Google API Services User Data Policy e valutazione di sicurezza secondo architettura e uso dei dati. Verifica le regole vigenti prima di promettere un collegamento pubblico ai clienti.

L'accesso del provider è più ampio del filtro operativo: Google autorizza lettura della casella; il servizio limita le sue interrogazioni ai mittenti prioritari negli ultimi sette giorni. Legge al massimo 25 riferimenti iniziali e 20 conversazioni, con contesto testuale limitato alle ultime 12 email di ciascun thread. Non legge gli allegati, non invia messaggi e non crea bozze nella casella.

Le bozze sono locali, da verificare. L'analisi deterministica è un limite dichiarato del prototipo, non una garanzia che tutti gli impegni o le richieste siano individuati. Non automatizzare decisioni o risposte reali basandoti sulla demo.

Il tasto di scollegamento elimina il token locale. Per revocare anche il consenso lato provider, usa le impostazioni dell'account Google relative alle connessioni con app di terze parti. Una perdita della chiave locale richiede ripristino o nuovo collegamento.

## Riferimenti ufficiali

- [Gmail API quickstart Python](https://developers.google.com/workspace/gmail/api/quickstart/python)
- [Ambiti OAuth Gmail](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [OAuth 2.0 per applicazioni web](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Verifica degli ambiti ristretti](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
- [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)
- [Discovery Gmail ufficiale, verificato durante lo sviluppo](https://raw.githubusercontent.com/googleapis/google-api-python-client/main/googleapiclient/discovery_cache/documents/gmail.v1.json)

I siti `developers.google.com` non erano accessibili dalla rete del task; lo schema API e il quickstart ufficiale su GitHub sono stati consultati. Le indicazioni sulla verifica e sulle interfacce Google devono essere ricontrollate prima del collegamento pubblico. Non sono stati verificati prezzi o tempi di approvazione correnti.
