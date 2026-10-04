# Collegare Gmail con permessi di sola lettura

Il deploy [Filo su Railway](https://filo-production-65a1.up.railway.app) usa soltanto dati reali (`FILO_REAL_DATA_ONLY=1`). Il collegamento Gmail richiede un client OAuth Google; non basta l'accesso a Railway. Queste operazioni sono a carico del gestore del prodotto. L'integrazione è verificata con risposte simulate, senza collegamento a un account reale durante lo sviluppo.

## Configurazione del gestore

1. In [Google Cloud Console](https://console.cloud.google.com/), crea o seleziona il progetto e abilita **Gmail API** da **API e servizi → Libreria**.
2. In **Google Auth Platform**, completa il nome dell'app e le email di contatto. In **Audience**, scegli **External** per un account Gmail personale e aggiungi come **utente di test** l'indirizzo della casella che collegherai. Per un'organizzazione Google Workspace, **Internal** è utilizzabile solo dagli utenti di quella organizzazione.
3. In **Data Access**, aggiungi il solo ambito `https://www.googleapis.com/auth/gmail.readonly`.
4. In **Clients**, crea un client OAuth di tipo **Web application**. Aggiungi agli **Authorized redirect URIs** esattamente questo indirizzo, senza slash finale:

   ```text
   https://filo-production-65a1.up.railway.app/api/gmail/oauth/callback
   ```

5. Nel servizio Filo dell'ambiente **production** su Railway, apri **Variables** e inserisci `FILO_GOOGLE_CLIENT_ID` e `FILO_GOOGLE_CLIENT_SECRET` con i valori del client appena creato. `FILO_GOOGLE_REDIRECT_URI` è già impostata all'indirizzo del punto 4: mantieni lo stesso valore. Lascia `FILO_REAL_DATA_ONLY=1`. Salva le variabili e applica il nuovo deploy. Non inserire il segreto nella chat, nel codice o in Git.
6. Apri Filo, seleziona **Collega Gmail** e accedi con l'account aggiunto tra gli utenti di test. Concedi la lettura e attendi il ritorno all'app. Compila il profilo della tua azienda, scegli i contatti prioritari e autorizza lettura e preparazione di bozze per attivare i controlli automatici.

Se Google mostra `redirect_uri_mismatch`, confronta l'URI registrato con quello del punto 4. Se l'accesso è negato in modalità test, verifica che la casella sia tra gli utenti di test del progetto. Un'app **External** in stato **Testing** può ottenere refresh token con scadenza di sette giorni: per un uso continuativo serve completare il passaggio alla produzione previsto da Google o ricollegare la casella quando richiesto.

Il server deve poter raggiungere `accounts.google.com`, `oauth2.googleapis.com` e `gmail.googleapis.com`. Per mantenere SQLite e la chiave dei token al riavvio, conserva il volume Railway già associato a `ALEXCHIARA_DATA_DIR`.

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
