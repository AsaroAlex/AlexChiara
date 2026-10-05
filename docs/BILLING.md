# Abbonamenti Spazelia con Stripe

La registrazione crea un account gratuito e non richiede una carta. Il prezzo
dell'abbonamento deve essere deciso dal titolare: questa implementazione non
crea prodotti, prezzi, clienti o addebiti durante il deploy e non inventa un
importo. Solo il pulsante di acquisto di un utente autenticato apre Stripe
Checkout; Stripe mostra il prezzo e raccoglie l'accettazione del pagamento.

Finché manca la configurazione, l'area personale mostra che i pagamenti non
sono ancora attivi. La segreteria esistente resta utilizzabile; non sono state
introdotte restrizioni legate al piano.

Il piano usa Spazelia come nome pubblico. Le variabili `FILO_*`, il dominio Railway e l’endpoint webhook rimangono compatibili con la configurazione esistente. Il cambio di nome non crea prodotti Stripe, non cambia prezzi e non attiva addebiti.

## Configurazione sul server

1. Nel dashboard Stripe creare un prodotto e un prezzo **ricorrente** dopo
   aver approvato importo, valuta, periodicità e condizioni. Usare prima la
   modalità di test.
2. Aggiungere al servizio Railway queste variabili server:
   - `STRIPE_SECRET_KEY`: chiave segreta dell'ambiente Stripe scelto.
   - `STRIPE_PRICE_ID`: identificativo `price_...` del prezzo ricorrente.
   - `STRIPE_WEBHOOK_SECRET`: segreto `whsec_...` dell'endpoint di notifica.
   - `FILO_PUBLIC_URL`: origine pubblica, per esempio
     `https://filo-production-65a1.up.railway.app`, senza percorso. Se omessa,
     viene usata l'origine della richiesta, già controllata dal middleware.
   - `FILO_PLAN_LABEL` facoltativa: nome del piano; il valore predefinito è Spazelia.
   - `FILO_PLAN_PRICE_LABEL` facoltativa: testo del listino approvato, coerente
     con il prezzo Stripe. Se omessa, nessun prezzo è presentato sul sito.
3. Registrare in Stripe l'endpoint pubblico
   `https://filo-production-65a1.up.railway.app/api/billing/webhook`, per gli
   eventi `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
   `checkout.session.expired`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`,
   `invoice.payment_failed`, `invoice.paid` e `invoice.payment_succeeded`.
4. Abilitare Stripe Customer Portal nel dashboard Stripe per consentire la
   gestione del metodo di pagamento e dell'abbonamento. I ritorni da Checkout
   e dal portale conducono a `/account`.
5. Verificare un acquisto con carta di test, la consegna delle notifiche e
   l'annullamento dal portale. Attivare le chiavi live e il relativo prezzo e
   webhook solo dopo la verifica del listino e delle condizioni. Tenere gli
   ambienti test e produzione e i relativi dati separati.
6. Impostare la versione API dell'endpoint webhook su `2024-06-20`, la stessa
   usata dal server, così le notifiche riportano anche la fine del periodo.

Il passaggio dalle chiavi di test (`sk_test_…`) a quelle live (`sk_live_…`)
sullo stesso volume è previsto: clienti e abbonamenti sono marcati con la
modalità che li ha creati, gli abbonamenti di prova non contano in produzione
e al primo acquisto live viene creato un nuovo cliente Stripe. Se un cliente
viene eliminato dal dashboard Stripe, il successivo Checkout ne crea uno nuovo
una sola volta; il portale indica che non c'è ancora un abbonamento da gestire.

Le chiavi segrete non vanno nel JavaScript, nei file statici, nei link, nei log
o nel repository. Il codice usa l'API Stripe versione `2024-06-20` tramite
`httpx`; non richiede una nuova dipendenza. Il database locale
`billing.sqlite3` risiede nello stesso volume persistente degli account e
contiene solo associazioni utente/cliente, stato degli abbonamenti, tentativi
di Checkout e identificativi delle notifiche già elaborate. Non conserva
dati delle carte.

## Contratto e verifiche

- `GET /api/billing/status`: utente autenticato, stato locale dell'abbonamento,
  configurazione disponibile e informazioni di piano. Non chiama Stripe e
  non produce alcun addebito.
- `POST /api/billing/checkout`: utente autenticato e CSRF; cliente e prezzo
  vengono scelti dal server. La sessione in corso è riutilizzata e le richieste
  a Stripe hanno chiavi di idempotenza persistenti.
- `POST /api/billing/portal`: utente autenticato e CSRF; apre soltanto il
  cliente Stripe associato all'account corrente.
- `POST /api/billing/webhook`: accesso pubblico senza cookie/CSRF, ma con
  firma HMAC SHA-256 obbligatoria sul corpo originale e timestamp entro cinque
  minuti. La dimensione massima nel router è 512 KiB; vale anche l'eventuale
  limite più basso del middleware. Gli eventi sono deduplicati in modo
  transazionale e quelli più vecchi non sovrascrivono uno stato più recente.
  Se due eventi dello stesso abbonamento hanno lo stesso secondo di creazione
  e stati diversi, il server legge lo stato attuale da Stripe invece di fidarsi
  dell'ordine di consegna.

Il parametro `billing=success` non significa che il pagamento sia riuscito.
Solo le notifiche firmate aggiornano lo stato. Checkout completato e fatture
comportano la lettura server dell'abbonamento effettivo: una fattura fallita
non viene trasformata automaticamente in un abbonamento annullato. Le
notifiche relative a clienti sconosciuti o con metadata incoerenti non vengono
associate a un account. Le indisponibilità di Stripe restituiscono un messaggio
italiano senza riportare risposte interne o segreti; una notifica che richiede
una lettura Stripe fallita non viene riconosciuta, permettendo il retry.

I test di `tests/test_billing.py` usano soltanto risposte Stripe simulate:
assenza di configurazione, isolamento tra utenti, prezzi imposti dal server,
riuso di Checkout, firme errate o scadute, duplicati, notifiche fuori ordine
o nello stesso secondo, annullamento, mancati pagamenti, persistenza,
passaggio test/live, cliente eliminato, scadenza dei Checkout ritentati e
indisponibilità del provider.
