# Verifica dell'MVP e dell'ambiente

Prove eseguite il **4 ottobre 2026** in `/workspace/AlexChiara`, Linux x86_64, Python 3.12.14. Il repository iniziale era vuoto e senza commit, con checkout sul ramo locale `work`; il remoto GitHub era accessibile tramite autenticazione esistente ma non conteneva riferimenti, incluso `main`. Il codice è stato creato localmente in seguito alla richiesta di sviluppo dell'MVP. Non sono stati creati worktree, PR o commit remoti.

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

La ricerca ufficiale su GitHub è documentata in PRODOTTO.md; alcuni siti dei fornitori e il testo GDPR erano bloccati dal proxy. Prezzi concorrenti, termini correnti e requisiti di verifica pubblica restano da controllare. Prima di un pilota reale servono autenticazione, isolamento, retention/cancellazione, backup, processo supervisionato e accordi di trattamento.
