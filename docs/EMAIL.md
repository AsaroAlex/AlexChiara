# Collegare la propria posta a Filo

Apri **Collegamenti** nell'area riservata. Ogni account Filo può avere una sola casella: Gmail, Outlook/Microsoft 365, iCloud, Yahoo, Aruba, Libero oppure un altro server IMAP. Cambiare casella ferma i controlli precedenti; attiva nuovamente il servizio per autorizzare la nuova connessione.

Le credenziali sono cifrate sul server, nello spazio privato dell'utente. I connettori non inviano email e non modificano i messaggi. Le bozze rimangono in Filo. Il controllo legge un campione limitato degli ultimi sette giorni, filtrato sui contatti prioritari: non dimostra che una persona non abbia scritto o risposto.

## Gmail: configurazione iniziale del gestore

Il pulsante Gmail apre il consenso Google nella stessa scheda. Prima occorre configurare una volta il client OAuth del prodotto; ogni cliente utilizzerà poi il pulsante del sito.

1. Apri [Google Cloud Console](https://console.cloud.google.com/) e crea o scegli il progetto Filo.
2. Abilita **Gmail API** in **API e servizi → Libreria**.
3. In **Google Auth Platform**, completa nome e contatti. Scegli **External** per Gmail personale; in modalità test aggiungi gli indirizzi delle caselle che proveranno Filo. **Internal** limita l'accesso alla propria organizzazione Google Workspace.
4. Aggiungi il solo ambito `https://www.googleapis.com/auth/gmail.readonly`.
5. Crea un client **Web application** e registra esattamente questa callback:

   ```text
   https://filo-production-65a1.up.railway.app/api/gmail/oauth/callback
   ```

6. In **filo → production → Variables** su [Railway](https://railway.com/project/23154853-8a90-4944-bac7-2315cd4f816f/service/9d3ce564-55cc-482e-ba8b-6e381cff9127?environmentId=16183f90-7643-4954-9f1e-6efdb2fb16d3), inserisci `FILO_GOOGLE_CLIENT_ID` e `FILO_GOOGLE_CLIENT_SECRET`. Imposta `FILO_GOOGLE_REDIRECT_URI` alla callback sopra e applica il deploy. Il segreto va nelle variabili del server, senza inserirlo in Git o in chat.
7. Torna a **Collegamenti → Gmail**, accedi alla casella autorizzata e concedi la lettura.

`redirect_uri_mismatch` indica una callback differente. In modalità test, verifica che la casella sia tra gli utenti di test. I refresh token di app External in test possono scadere dopo sette giorni. Per offrire il collegamento al pubblico, completa il percorso di produzione e la verifica richiesta da Google per l'ambito Gmail ristretto. Vedi [la guida Gmail dettagliata](GMAIL.md).

## Outlook e Microsoft 365: configurazione iniziale del gestore

1. Apri [Microsoft Entra](https://entra.microsoft.com/) → **App registrations → New registration**.
2. Scegli account in qualsiasi organizzazione e account Microsoft personali, per supportare Microsoft 365, Outlook.com e Hotmail. Le policy dell'organizzazione possono richiedere il consenso dell'amministratore.
3. Registra come piattaforma **Web** questa callback:

   ```text
   https://filo-production-65a1.up.railway.app/api/outlook/oauth/callback
   ```

4. Usa permessi **delegati** Microsoft Graph `Mail.Read` e `User.Read`, insieme a `offline_access` per il rinnovo. Non aggiungere permessi di invio o scrittura al consenso Filo.
5. In **Certificates & secrets**, crea un client secret e copia il suo **Value**, non il suo ID. Conserva la scadenza per rinnovarlo in tempo.
6. Nelle variabili Railway configura e applica il deploy:

   ```text
   FILO_MICROSOFT_CLIENT_ID=<Application (client) ID>
   FILO_MICROSOFT_CLIENT_SECRET=<secret Value>
   FILO_MICROSOFT_TENANT=common
   FILO_MICROSOFT_REDIRECT_URI=https://filo-production-65a1.up.railway.app/api/outlook/oauth/callback
   ```

7. Torna a **Collegamenti → Outlook e Microsoft 365** e completa il consenso nella stessa scheda.

`User.Read` identifica l'indirizzo della casella; `Mail.Read` legge la posta. Riferimenti: [registrare un'app](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app), [consenso e ambiti](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc), [elenco messaggi](https://learn.microsoft.com/en-us/graph/api/user-list-messages).

## iCloud, Yahoo, Aruba, Libero e altri server IMAP

Seleziona il provider, inserisci indirizzo email e password per app quando richiesta, poi collega. Non serve un'app OAuth Google o Microsoft. La connessione viene verificata prima di sostituire la casella precedente: un tentativo fallito conserva la connessione esistente.

| Provider | Server IMAP TLS, porta 993 | Preparazione |
| --- | --- | --- |
| iCloud | `imap.mail.me.com` | Genera una password per app dall'account Apple; può servire il nome utente iCloud senza dominio. |
| Yahoo | `imap.mail.yahoo.com` | Usa una password per app quando richiesta dalla sicurezza Yahoo. |
| Aruba | `imaps.aruba.it` | Verifica che IMAP sia attivo sul dominio. Per `@aruba.it`/`@technet.it` viene usato `imap.aruba.it`. |
| Libero | `imapmail.libero.it` | Usa la password per app quando richiesta dalle impostazioni di sicurezza. |
| Altro IMAP | Server indicato dal gestore | Server pubblico con TLS993; nome utente completo salvo istruzioni differenti. |

Gmail e Outlook utilizzano i propri pulsanti OAuth. Il preset Aruba riguarda la posta ordinaria; per una PEC usa il modulo personalizzato con il server corretto indicato dal fornitore. Eventuali costi dell'opzione IMAP dipendono dal proprio contratto.

Filo apre le cartelle in sola lettura e usa `BODY.PEEK`, senza segnare le email come lette. Controlla la posta inviata quando disponibile per riconoscere risposte già osservate; la copertura rimane incompleta. Scarica messaggi MIME limitati a 512 KiB, esclude gli allegati dall'analisi e salta messaggi troppo grandi. Server privati, locali o privi di TLS non vengono accettati.

Riferimenti: [iCloud IMAP](https://support.apple.com/102525), [password Apple per app](https://support.apple.com/102654), [Yahoo IMAP](https://help.yahoo.com/kb/SLN4075.html), [assistenza Aruba](https://guide.hosting.aruba.it/), [assistenza Libero](https://aiuto.libero.it/).

## Dopo il collegamento

Compila il profilo della tua attività, scegli da uno a quattro contatti prioritari e l'orario del controllo. L'attivazione autorizza lettura e preparazione di bozze. Con un avviso odierno in attesa, il servizio attivo verifica nuovi arrivi ogni cinque minuti anche senza browser aperto; pausa e scollegamento fermano i controlli.

**Scollega** elimina localmente le credenziali di tutti i provider e ferma il servizio. Per revocare anche presso il fornitore, rimuovi Filo dalle app collegate oppure revoca la password per app nelle impostazioni della casella.

Le prove di sviluppo usano risposte simulate e archivi temporanei. Il collegamento a caselle reali richiede credenziali e consenso del titolare; non è stato collaudato durante queste verifiche. I pagamenti Stripe restano nel proprio percorso separato, con prezzo da definire.
