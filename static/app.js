'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { data: null, gmail: null, page: 'overview', companyDirty: false, companyInitialized: false, loading: false, wizard: null, currentRun: null, chatBusy: false, chatPreferences: null };
let toastTimer;
let lastFocused;

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#icon-${name}`);
  svg.append(use);
  return svg;
}
function actionButton(label, action, style = 'primary', symbol, dataset = {}) {
  const button = el('button', `button ${style}`, label);
  button.type = 'button';
  button.dataset.action = action;
  Object.assign(button.dataset, dataset);
  if (symbol) button.append(icon(symbol));
  return button;
}
function replace(selector, ...children) { $(selector).replaceChildren(...children); }
function statusPill(status) {
  const labels = { inactive: ['Da attivare', 'neutral'], active: ['Attivo', 'success'], paused: ['In pausa', 'warning'], succeeded: ['Completata', 'success'], queued: ['In coda', 'neutral'], running: ['In corso', 'neutral'], retry_wait: ['Nuovo tentativo previsto', 'warning'], failed: ['Da verificare', 'error'], connected: ['Collegata', 'success'], disconnected: ['Da collegare', 'neutral'], expired: ['Da ricollegare', 'warning'], approved: ['Revisione registrata', 'success'], pending: ['Da rivedere', 'neutral'] };
  const [label, style] = labels[status] || ['Da verificare', 'neutral'];
  return el('span', `status-pill ${style}`, label);
}
function setPill(selector, status) { const node = statusPill(status); const target = $(selector); target.className = node.className; target.textContent = node.textContent; }
function errorText(error) {
  if (typeof error === 'string') return error;
  if (Array.isArray(error)) return error.map(item => `${(item.loc || []).filter(x => x !== 'body').join(' · ')}${item.loc ? ': ' : ''}${item.msg || 'Valore non valido'}`).join('; ');
  return error?.message || 'Non è stato possibile completare l’operazione. Riprova.';
}
async function api(path, options = {}) {
  const headers = { Accept: 'application/json', ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
  if (options.method && options.method !== 'GET' && state.data?.csrf_token) headers['X-CSRF-Token'] = state.data.csrf_token;
  let response;
  try { response = await fetch(path, { credentials: 'same-origin', ...options, headers, body: options.body !== undefined ? JSON.stringify(options.body) : undefined }); }
  catch (_) { throw new Error('Il servizio non è raggiungibile. Controlla la connessione e riprova.'); }
  let result;
  try { result = await response.json(); } catch (_) { result = {}; }
  if (!response.ok) throw new Error(errorText(result.detail || result.error || result.message || `Operazione non riuscita (${response.status}).`));
  return result;
}
function notice(selector, message) { const target = $(selector); target.textContent = message || ''; target.hidden = !message; }
function toast(message, isError = false) {
  clearTimeout(toastTimer);
  const target = $('#toast');
  target.textContent = message; target.classList.toggle('error', isError); target.hidden = false;
  toastTimer = setTimeout(() => { target.hidden = true; }, 5500);
}
async function withBusy(button, operation) {
  if (button?.disabled) return;
  const original = button?.textContent;
  if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); button.textContent = 'Un momento…'; }
  try { return await operation(); }
  finally { if (button?.isConnected) { button.disabled = false; button.removeAttribute('aria-busy'); button.textContent = original; } }
}
function prettyDate(value, withTime = true) {
  if (!value) return 'Non ancora prevista';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return 'Data non disponibile';
  return new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: 'numeric', month: 'short', ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}) }).format(date);
}
function clockTime(service = state.data?.service) { return service ? `${String(service.hour ?? 9).padStart(2, '0')}:${String(service.minute ?? 0).padStart(2, '0')}` : '09:00'; }
function orderedRuns() { return [...(state.data?.runs || [])].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)); }
function openDialog(selector) { lastFocused = document.activeElement; const dialog = $(selector); if (!dialog.open) dialog.showModal(); if (selector === '#wizard-dialog') { const heading = $('#wizard-title'); if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); } } }
function closeDialog(selector) { $(selector).close(); if (lastFocused?.isConnected) lastFocused.focus(); }

async function refresh(forceCompany = false) {
  const data = await api('/api/bootstrap');
  state.data = data;
  render(forceCompany);
  $('#loading').hidden = true;
  $('#load-error').hidden = true;
  $('#app-content').hidden = false;
}
function setPage(page) {
  const titles = { overview: 'Panoramica', services: 'Servizi', activity: 'Attività', company: 'La tua azienda' };
  state.page = titles[page] ? page : 'overview';
  $$('.page-view').forEach(view => { view.hidden = view.id !== `page-${state.page}`; });
  $$('.nav-item').forEach(item => { const active = item.dataset.page === state.page; item.classList.toggle('active', active); if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current'); });
  $('#page-crumb').textContent = titles[state.page];
  document.title = `${titles[state.page]} · Filo`;
}
function render(forceCompany = false) {
  const { company = {}, service = {}, connection = {}, approvals = [] } = state.data;
  $('#sidebar-company').textContent = company.name || 'Il tuo studio';
  $('#company-avatar').textContent = (company.name || 'Il tuo studio').split(/\s+/).filter(Boolean).slice(0, 2).map(x => x[0]).join('').toUpperCase();
  $('#greeting-label').textContent = company.name ? `BENVENUTO, ${company.name.toLocaleUpperCase('it')}` : 'IL TUO PUNTO DI PARTENZA';
  setPill('#overview-service-status', service.status);
  const expired = connection.status === 'expired';
  notice('#connection-alert', expired ? 'La connessione alla casella è scaduta. Filo conserva lo storico ma non può leggere nuove email. Ricollega la casella dal servizio; per la demo puoi usare “Ripristina demo” negli scenari di prova.' : '');
  if (connection.provider === 'gmail') {
    $('.demo-banner p').replaceChildren(el('strong', '', 'Casella Gmail collegata, elaborazione guidata. '), document.createTextNode('Le email sono reali. Selezione e bozze usano regole deterministiche, senza AI reale. Le bozze restano in Filo e nessun messaggio viene inviato.'));
    $('.demo-chip').lastChild.textContent = ' Prototipo con Gmail';
  } else {
    $('.demo-banner p').replaceChildren(el('strong', '', 'Dati demo, spazio per sperimentare. '), document.createTextNode('Le email di esempio e le bozze sono una simulazione deterministica, senza AI reale. Nessun messaggio viene inviato.'));
    $('.demo-chip').lastChild.textContent = ' Ambiente di prova';
  }
  if (connection.provider !== 'gmail' && state.data.ai?.enabled && state.data.ai?.configured) {
    $('.demo-banner p').replaceChildren(el('strong', '', 'Email di esempio, servizio in prova. '), document.createTextNode('L’anteprima usa una simulazione deterministica. Le esecuzioni possono usare l’AI esterna configurata solo sui dati demo. Nessun messaggio viene inviato.'));
  }
  $('.form-intro').textContent = company.demo ? 'Studio Riva è un esempio fittizio. Personalizza questi dati prima di usare il servizio per il tuo studio.' : 'Mantieni aggiornati il profilo e la firma usati nelle bozze. Non inserire password o dati sensibili.';
  const isActive = service.status === 'active';
  const isPaused = service.status === 'paused';
  $('#overview-service-description').textContent = isActive ? 'La tua segreteria è al lavoro: seleziona le email prioritarie e prepara bozze locali che potrai rivedere.' : isPaused ? 'Hai messo il servizio in pausa. Le esecuzioni programmate si fermano; attività e bozze restano a disposizione.' : 'Le email importanti, in evidenza. Le risposte, già abbozzate. Sempre sotto il tuo controllo.';
  const facts = [];
  if (isActive || isPaused) {
    for (const [symbol, text] of [['clock', `${clockTime()} · Europe/Rome`], ['mail', `${service.priority_contacts?.length || 0} contatti prioritari`], ['link', connection.provider === 'gmail' ? 'Casella Gmail' : 'Casella demo']]) { const fact = el('span', 'service-fact', text); fact.prepend(icon(symbol)); facts.push(fact); }
  }
  replace('#overview-service-facts', ...facts);
  const overviewActions = [];
  const catalogActions = [];
  if (service.status === 'inactive') {
    overviewActions.push(actionButton('Configura il servizio', 'open-wizard', 'primary', 'arrow'));
    catalogActions.push(actionButton('Configura il servizio', 'open-wizard', 'primary', 'arrow'));
  } else {
    overviewActions.push(actionButton(isPaused ? 'Riprendi il servizio' : 'Esegui ora', isPaused ? 'resume' : 'run', 'primary', isPaused ? 'play' : 'refresh'));
    overviewActions.push(actionButton('Preferenze', 'edit-preferences', 'secondary'));
    overviewActions.push(actionButton(isPaused ? 'Disattiva' : 'Metti in pausa', isPaused ? 'deactivate' : 'pause', 'ghost', isPaused ? undefined : 'pause'));
    catalogActions.push(actionButton('Modifica le preferenze', 'edit-preferences', 'primary', 'arrow'));
    catalogActions.push(actionButton('Disattiva il servizio', 'deactivate', 'ghost'));
    if (expired) overviewActions.unshift(actionButton('Ricollega la casella', 'reconnect', 'secondary', 'link'));
  }
  replace('#overview-service-actions', ...overviewActions);
  replace('#catalog-service-actions', ...catalogActions);
  $('#schedule-time').textContent = (isActive || isPaused) ? clockTime() : '— : —';
  $('#schedule-detail').textContent = isActive ? `Ogni giorno · Europe/Rome. ${service.next_run_at ? `Prossima esecuzione: ${prettyDate(service.next_run_at)}.` : 'Prepariamo la prossima esecuzione.'}` : isPaused ? 'La programmazione è in pausa. Riprendi il servizio quando vuoi.' : 'Scegli l’orario durante l’attivazione.';
  $('#schedule-footer').textContent = service.last_run_at ? `Ultimo giro: ${prettyDate(service.last_run_at)}` : isActive ? 'Lettura e bozze · nessun invio' : 'Tutto comincia dalle tue priorità';
  renderRuns('#recent-runs', orderedRuns().slice(0, 3));
  renderRuns('#all-runs', orderedRuns());
  const count = $('#activity-count'); count.textContent = String(approvals.length); count.hidden = !approvals.length;
  const metrics = [[orderedRuns().filter(run => run.status === 'succeeded').length, 'Attività completate'], [approvals.length, 'Bozze da rivedere'], [orderedRuns().filter(run => run.status === 'failed' || run.status === 'retry_wait').length, 'Attività da verificare']].map(([value, label]) => { const card = el('div', 'metric-card'); card.append(el('strong', '', value), el('p', '', label)); return card; });
  replace('#activity-summary', ...metrics);
  $('#activity-run-button').hidden = !isActive;
  if (!state.companyInitialized || forceCompany) {
    for (const field of ['name', 'sector', 'description', 'signature']) $(`#company-${field}`).value = company[field] || '';
    state.companyInitialized = true; state.companyDirty = false;
  }
  const connectionNodes = [statusPill(connection.status || 'disconnected'), el('p', '', connection.label || (connection.provider === 'demo' ? 'Casella demo con email di esempio.' : 'Nessuna casella collegata. Configurala durante l’attivazione.'))];
  if (connection.provider === 'gmail') {
    connectionNodes.push(el('p', '', 'Google autorizza la lettura della casella; Filo controlla solo i clienti scelti. Non può inviare email.'));
    connectionNodes.push(el('p', '', 'Controlla gli ultimi 7 giorni: fino a 20 conversazioni e una selezione di 25 messaggi. Gli allegati non vengono letti.'));
    connectionNodes.push(actionButton('Scollega Gmail', 'disconnect-gmail', 'secondary'));
  }
  replace('#company-connection', ...connectionNodes);
  $('#demo-scenario-status').textContent = expired ? 'Connessione demo scaduta: il servizio richiede un ripristino.' : state.data.demo_failure === 'expired' ? 'La prossima esecuzione demo simulerà una connessione scaduta.' : state.data.demo_failure === 'temporary' ? 'La prossima esecuzione demo simulerà un errore temporaneo.' : '';
  $$('[data-action^="simulate-"]').forEach(button => { button.disabled = connection.provider !== 'demo'; });
}
function renderRuns(selector, runs) {
  if (!runs.length) {
    const box = el('div', 'empty-state'); const mark = el('span', 'icon-box neutral'); mark.append(icon('mail'));
    const copy = el('div'); copy.append(el('h3', '', 'Le prime attività arriveranno qui.'), el('p', '', 'Attiva la segreteria email per vedere le priorità selezionate e le bozze da rivedere. Ogni esecuzione sarà sempre consultabile.'));
    box.append(mark, copy); replace(selector, box); return;
  }
  replace(selector, ...runs.map(run => {
    const row = el('button', 'run-row'); row.type = 'button'; row.dataset.action = 'open-run'; row.dataset.id = run.id;
    const mark = el('span', `icon-box ${run.status === 'failed' ? 'peach' : 'teal'}`); mark.append(icon(run.status === 'succeeded' ? 'check' : run.status === 'failed' ? 'info' : 'clock'));
    const description = el('div', 'run-description'); description.append(el('strong', '', 'Segreteria email'), el('p', '', run.status === 'failed' ? (run.error || 'Un imprevisto ha interrotto l’attività.') : typeof run.summary === 'string' && run.summary ? run.summary : run.status === 'succeeded' ? `${run.items?.length || 0} email selezionate · bozze locali da rivedere` : run.status === 'retry_wait' ? 'Un nuovo tentativo è programmato. I dati restano al sicuro.' : 'Elaborazione delle email prioritarie.'));
    row.append(mark, description, el('span', 'run-date', prettyDate(run.created_at)), statusPill(run.status), icon('arrow'));
    return row;
  }));
}
function addChatMessage(text, role = 'assistant') {
  const item = el('div', `chat-message ${role}`, text); $('#chat-messages').append(item); $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight;
}

function startWizard(edit = false, reconnect = false, suggestedPreferences = null) {
  const service = state.data.service;
  const contacts = service.priority_contacts?.length ? service.priority_contacts.map(x => ({ name: x.name, email: x.email })) : [{ name: '', email: '' }];
  state.wizard = { step: reconnect ? 2 : edit ? 3 : 1, edit, reconnect, preferences: { priority_contacts: contacts, hour: service.hour ?? 9, minute: service.minute ?? 0, timezone: 'Europe/Rome' }, preview: null, authorizedRead: false, authorizedDraft: false };
  if (suggestedPreferences) state.wizard.preferences = { priority_contacts: suggestedPreferences.priority_contacts.map(contact => ({ name: contact.name, email: contact.email })), hour: suggestedPreferences.hour, minute: suggestedPreferences.minute, timezone: 'Europe/Rome' };
  renderWizard(); openDialog('#wizard-dialog');
}
function field(labelText, type, id, value, props = {}) {
  const wrapper = el('div'); const label = el('label', '', labelText); label.htmlFor = id;
  const input = el('input'); input.type = type; input.id = id; input.value = value ?? ''; Object.assign(input, props); wrapper.append(label, input); return wrapper;
}
function renderWizard() {
  const wizard = state.wizard;
  if (!wizard) return;
  notice('#wizard-error', '');
  replace('#wizard-progress', ...['Service', 'Casella', 'Preferenze', 'Anteprima', 'Consenso'].map((label, index) => { const step = index + 1; const item = el('li', step === wizard.step ? 'current' : step < wizard.step ? 'complete' : ''); item.append(el('span', '', step < wizard.step ? '✓' : step), document.createTextNode(label === 'Service' ? 'Servizio' : label)); if (step === wizard.step) item.setAttribute('aria-current', 'step'); return item; }));
  const body = $('#wizard-body'); body.replaceChildren();
  const footer = $('#wizard-footer'); footer.replaceChildren();
  const back = actionButton(wizard.step === 1 || wizard.edit && wizard.step === 3 ? 'Annulla' : 'Indietro', wizard.step === 1 || wizard.edit && wizard.step === 3 ? 'close-wizard' : 'wizard-back', 'ghost'); footer.append(back);
  const title = (text, description) => { const heading = el('h2', '', text); heading.id = 'wizard-title'; body.append(heading, el('p', 'step-intro', description)); };
  if (wizard.step === 1) {
    title('Una segreteria, alle tue condizioni.', 'Un compito preciso: leggere le email dei contatti prioritari e preparare bozze locali. Tu mantieni l’ultima parola.');
    const choice = el('div', 'step-choice'); const mark = el('span', 'icon-box teal'); mark.append(icon('mail')); const copy = el('div'); copy.append(el('h3', '', 'Segreteria email'), el('p', '', 'Priorità, risposte abbozzate e un giro ogni giorno.')); choice.append(mark, copy, icon('check')); body.append(choice);
    body.append(el('p', 'step-disclosure', 'Le bozze restano in Filo. Questo servizio non invia messaggi e non crea bozze nella casella del provider.'));
    if (state.data.ai?.enabled && state.data.ai?.configured) body.append(el('p', 'step-disclosure', 'Questo ambiente usa un servizio AI esterno solo sulle email dimostrative, durante le esecuzioni attivate. L’anteprima e le email Gmail usano regole locali.'));
    footer.append(actionButton('Iniziamo', 'wizard-next', 'primary', 'arrow'));
  } else if (wizard.step === 2) {
    title('Collega una casella.', 'Puoi provare il percorso completo con le email di esempio oppure collegare Gmail, quando configurato.');
    const grid = el('div', 'provider-grid');
    const demo = el('div', `provider-card ${state.data.connection.provider === 'demo' && state.data.connection.status === 'connected' ? 'selected' : ''}`);
    const demoMark = el('span', 'icon-box teal'); demoMark.append(icon('mail')); demo.append(demoMark, el('h3', '', 'Casella demo'), el('p', '', 'Email fittizie per esplorare priorità, bozze e storico. Nessun accesso alla tua casella reale.'));
    if (state.data.connection.provider === 'demo' && state.data.connection.status === 'connected') demo.append(statusPill('connected'));
    demo.append(actionButton(state.data.connection.provider === 'demo' && state.data.connection.status === 'connected' ? 'Usa la casella demo' : 'Collega la demo', 'connect-demo', 'secondary', 'link'));
    const gmail = el('div', `provider-card ${state.data.connection.provider === 'gmail' && state.data.connection.status === 'connected' ? 'selected' : ''}`);
    gmail.append(el('span', 'provider-symbol', 'G'), el('h3', '', 'Gmail'), el('p', '', 'Google autorizza la lettura della casella; Filo controlla solo i clienti scelti. Non può inviare email.'));
    gmail.append(el('p', 'helper-text', 'Ultimi 7 giorni · fino a 20 conversazioni · selezione di 25 messaggi. Allegati esclusi.'));
    const gmailReady = Boolean(state.gmail?.configured);
    const gmailConnected = state.data.connection.provider === 'gmail' && state.data.connection.status === 'connected';
    if (gmailConnected) gmail.append(statusPill('connected'));
    else if (!gmailReady) gmail.append(el('span', 'status-pill neutral', 'Configurazione necessaria'));
    const gmailButton = actionButton(gmailConnected ? 'Usa Gmail' : 'Collega Gmail', 'connect-gmail', 'secondary', 'link'); gmailButton.disabled = !gmailReady && !gmailConnected; gmail.append(gmailButton); grid.append(demo, gmail); body.append(grid);
    body.append(el('p', 'step-disclosure', gmailReady ? 'L’anteprima demo usa una simulazione deterministica senza AI reale. Con Gmail le email sono reali, le bozze sono locali e generate con regole, senza AI reale.' : 'Gmail non è ancora configurato in questo ambiente. La casella demo permette di provare tutte le funzioni senza credenziali.'));
    if (wizard.reconnect) { const next = actionButton('Torna alla panoramica', 'wizard-finish-reconnect', 'primary', 'arrow'); next.disabled = state.data.connection.status !== 'connected'; footer.append(next); }
    else { const next = actionButton('Continua', 'wizard-next', 'primary', 'arrow'); next.disabled = state.data.connection.status !== 'connected'; footer.append(next); }
  } else if (wizard.step === 3) {
    title(wizard.edit ? 'Il tuo ritmo può cambiare.' : 'Decidi cosa conta.', 'Scegli fino a quattro contatti prioritari e l’orario del giro quotidiano. Le bozze useranno il profilo del tuo studio.');
    const context = el('div', 'boundary-note'); context.append(icon('building'), document.createTextNode(`${state.data.company.name || 'Profilo da completare'} · ${state.data.company.sector || 'Settore da indicare'}`)); body.append(context);
    const contextLink = actionButton('Modifica il profilo aziendale', 'wizard-company', 'ghost'); body.append(contextLink);
    const contactHeader = el('div', 'contact-header'); const label = el('label', '', 'Contatti prioritari'); const helper = el('span', '', 'DA 1 A 4 CONTATTI'); contactHeader.append(label, helper); body.append(contactHeader);
    const contacts = el('div', 'contact-list'); contacts.id = 'wizard-contacts'; body.append(contacts); renderContactRows();
    const add = actionButton('+ Aggiungi un contatto', 'add-contact', 'ghost'); add.className = 'add-contact'; add.id = 'add-contact'; add.disabled = wizard.preferences.priority_contacts.length >= 4; body.append(add);
    const schedule = el('div', 'schedule-fields'); const time = field('Orario quotidiano', 'time', 'wizard-time', `${String(wizard.preferences.hour).padStart(2, '0')}:${String(wizard.preferences.minute).padStart(2, '0')}`, { required: true }); const zone = field('Fuso orario', 'text', 'wizard-timezone', 'Europe/Rome', { readOnly: true }); schedule.append(time, zone); body.append(schedule, el('p', 'helper-text', 'Ogni giorno, anche nel fine settimana. L’orario segue automaticamente l’ora legale italiana. Puoi mettere il servizio in pausa in qualsiasi momento.'));
    footer.append(actionButton('Guarda l’anteprima', 'wizard-preview', 'primary', 'arrow'));
  } else if (wizard.step === 4) {
    title('Ecco cosa preparerebbe Filo.', state.data.connection.provider === 'gmail' ? 'Un’anteprima sulle email reali dei contatti scelti. Le bozze restano nella piattaforma: nessuna email viene inviata.' : 'Un’anteprima con email di esempio. È una simulazione deterministica senza AI reale; l’attivazione parte solo con il tuo consenso.');
    body.append(el('div', 'preview-intro', typeof wizard.preview?.summary === 'string' ? wizard.preview.summary : `${wizard.preview?.items?.length || 0} email selezionate per i contatti scelti.`));
    const items = wizard.preview?.items || [];
    if (items.length) items.forEach(item => body.append(renderDraft(item, false)));
    else body.append(el('p', 'helper-text', 'Nessuna email corrisponde ai contatti prioritari in questa anteprima. Puoi aggiornare i contatti o mantenere la configurazione per le prossime email.'));
    body.append(el('p', 'step-disclosure', 'L’anteprima non attiva il servizio. Le risposte mostrate sono suggerimenti da controllare e correggere prima di usarli.'));
    footer.append(actionButton(wizard.edit ? 'Salva le preferenze' : 'Rivedi e autorizza', wizard.edit ? 'wizard-save-preferences' : 'wizard-next', 'primary', 'arrow'));
  } else if (wizard.step === 5) {
    title('Il via libera è tuo.', 'Il servizio partirà soltanto dopo questi consensi espliciti. Potrai metterlo in pausa o disattivarlo quando vuoi.');
    body.append(mandateSummary());
    const read = el('label', 'checkbox-row'); const readInput = el('input'); readInput.type = 'checkbox'; readInput.id = 'authorize-read'; readInput.checked = wizard.authorizedRead; read.append(readInput, el('span', '', state.data.connection.provider === 'gmail' ? 'Autorizzo Filo a leggere le email dei contatti prioritari. Google concede accesso alla lettura della casella; il servizio limita le ricerche ai contatti scelti.' : 'Autorizzo Filo a leggere le email dei contatti prioritari nella casella demo.'));
    const draft = el('label', 'checkbox-row'); const draftInput = el('input'); draftInput.type = 'checkbox'; draftInput.id = 'authorize-draft'; draftInput.checked = wizard.authorizedDraft; draft.append(draftInput, el('span', '', 'Autorizzo la preparazione di bozze locali in Filo, che dovrò rivedere prima di usare.'));
    body.append(read, draft);
    if (state.data.connection.provider === 'demo' && state.data.ai?.enabled && state.data.ai?.configured) body.append(el('p', 'step-disclosure', 'Nelle esecuzioni demo autorizzate, il servizio AI esterno configurato può elaborare le email fittizie. Nessun contenuto Gmail viene condiviso.'));
    const noSend = el('div', 'no-send'); noSend.append(icon('shield'), document.createTextNode('Nessuna autorizzazione all’invio. Filo non invia email.')); body.append(noSend);
    const activate = actionButton('Autorizza e attiva il servizio', 'wizard-activate', 'primary', 'check'); activate.id = 'activate-button'; activate.disabled = !wizard.authorizedRead || !wizard.authorizedDraft; footer.append(activate);
  } else if (wizard.step === 6) {
    $('#wizard-progress').hidden = true;
    const success = el('div', 'wizard-success'); const mark = el('span', 'icon-box teal'); mark.append(icon('check')); const heading = el('h2', '', wizard.edit ? 'Preferenze aggiornate.' : 'La segreteria è attiva.'); heading.id = 'wizard-title'; success.append(mark, heading, el('p', '', wizard.edit ? 'Le prossime esecuzioni seguiranno i contatti e l’orario appena salvati.' : 'Hai autorizzato lettura e bozze locali. Puoi seguire ogni attività dalla panoramica e rivedere i risultati con calma.'), mandateSummary()); body.append(success);
    footer.replaceChildren(el('span'), actionButton('Vai alla panoramica', 'wizard-finish', 'primary', 'arrow'));
  }
  if (wizard.step !== 6) $('#wizard-progress').hidden = false;
  $('#wizard-dialog').scrollTop = 0;
  const heading = $('#wizard-title');
  if (heading) { heading.tabIndex = -1; if ($('#wizard-dialog').open) heading.focus({ preventScroll: true }); }
}
function renderContactRows() {
  const contacts = state.wizard.preferences.priority_contacts;
  const container = $('#wizard-contacts');
  container.replaceChildren(...contacts.map((contact, index) => {
    const row = el('div', 'contact-row');
    const name = el('input'); name.type = 'text'; name.value = contact.name; name.required = true; name.maxLength = 100; name.placeholder = 'Nome del contatto'; name.setAttribute('aria-label', `Nome del contatto ${index + 1}`); name.dataset.contact = index; name.dataset.field = 'name';
    const email = el('input'); email.type = 'email'; email.value = contact.email; email.required = true; email.maxLength = 254; email.placeholder = 'nome@azienda.it'; email.setAttribute('aria-label', `Email del contatto ${index + 1}`); email.dataset.contact = index; email.dataset.field = 'email';
    const remove = el('button', 'icon-button'); remove.type = 'button'; remove.dataset.action = 'remove-contact'; remove.dataset.index = index; remove.setAttribute('aria-label', `Rimuovi contatto ${index + 1}`); remove.append(icon('close')); remove.disabled = contacts.length <= 1;
    row.append(name, email, remove); return row;
  }));
  if ($('#add-contact')) $('#add-contact').disabled = contacts.length >= 4;
}
function capturePreferences() {
  const inputs = $$('input', $('#wizard-body')).filter(input => !input.readOnly);
  for (const input of inputs) if (!input.reportValidity()) return false;
  const contacts = state.wizard.preferences.priority_contacts.map(contact => ({ name: contact.name.trim(), email: contact.email.trim() }));
  if (!contacts.length || contacts.some(contact => !contact.name || !contact.email)) { notice('#wizard-error', 'Inserisci almeno un contatto con nome e indirizzo email.'); return false; }
  if (new Set(contacts.map(contact => contact.email.toLowerCase())).size !== contacts.length) { notice('#wizard-error', 'Ogni contatto deve avere un indirizzo email diverso.'); return false; }
  const time = $('#wizard-time').value.split(':').map(Number);
  state.wizard.preferences = { priority_contacts: contacts, hour: time[0], minute: time[1], timezone: 'Europe/Rome' };
  return true;
}
function mandateSummary() {
  const summary = el('div', 'mandate-summary');
  const preferences = state.wizard.preferences;
  const values = [['Servizio', 'Segreteria email'], ['Casella', state.data.connection.provider === 'gmail' ? 'Gmail · email reali' : 'Demo · dati fittizi'], ['Ogni giorno', `${String(preferences.hour).padStart(2, '0')}:${String(preferences.minute).padStart(2, '0')} · Europe/Rome`], ['Priorità', `${preferences.priority_contacts.length} contatti scelti`], ['Permessi', 'Lettura e bozze locali · nessun invio']];
  values.forEach(([key, value]) => { const row = el('div', 'mandate-summary-row'); row.append(el('span', '', key), el('strong', '', value)); summary.append(row); });
  return summary;
}
function renderDraft(item, withApproval = false) {
  const article = el('article', 'preview-item'); article.append(el('span', 'client-label', item.client || 'Contatto prioritario'), el('h3', '', item.subject || 'Email prioritaria'), el('p', 'reason', item.reason || 'Contatto incluso nelle tue priorità.'));
  if (typeof item.source_excerpt === 'string' && item.source_excerpt.trim()) {
    const source = el('details', 'source-message');
    source.append(el('summary', '', 'Leggi il messaggio di origine'), el('p', 'source-caption', 'Estratto usato per preparare la bozza. Potrebbe non includere tutta la conversazione.'), el('p', 'source-excerpt', item.source_excerpt.slice(0, 4000)));
    article.append(source);
  }
  article.append(el('div', 'draft-box-label', 'BOZZA LOCALE · DA CONTROLLARE'), el('div', 'draft-box', typeof item.draft === 'string' ? item.draft : item.draft?.body || 'Bozza non disponibile.'));
  if (withApproval) {
    const bar = el('div', 'approval-bar'); const copy = el('p', '', 'La revisione viene registrata solo in Filo. Nessun invio alla casella.'); bar.append(copy);
    const approved = item.status === 'approved' || state.data.approvals?.some(approval => approval.id === item.id && approval.status === 'approved');
    if (approved) bar.append(statusPill('approved'));
    else if (item.id) bar.append(actionButton('Registra la revisione', 'approve-draft', 'secondary', 'check', { id: item.id }));
    article.append(bar);
  }
  return article;
}
async function openRun(id) {
  const run = await api(`/api/runs/${encodeURIComponent(id)}`);
  state.currentRun = run;
  renderRunDetail(run);
  openDialog('#detail-dialog');
}
function renderRunDetail(run) {
  const body = $('#detail-body'); body.replaceChildren();
  const title = el('h2', '', 'Segreteria email'); title.id = 'detail-title'; body.append(title);
  const meta = el('div', 'detail-meta'); meta.append(statusPill(run.status), el('span', '', prettyDate(run.created_at)), el('span', '', run.demo ? 'Email demo' : 'Email reali')); body.append(meta);
  body.append(el('p', 'detail-summary', typeof run.summary === 'string' ? run.summary : 'Selezione delle email dei contatti prioritari e preparazione delle bozze locali.'));
  if (run.error) {
    const error = el('div', 'notice error', run.error); body.append(error);
    if (run.error_step) body.append(el('p', 'helper-text', `Passaggio interrotto: ${run.error_step}. Tentativi effettuati: ${run.attempts ?? 1}.`));
  }
  if (run.status === 'retry_wait') body.append(el('div', 'notice warning', 'Un nuovo tentativo è previsto automaticamente. Puoi seguirne l’esito nelle attività.'));
  if (run.status === 'failed' && run.retryable) body.append(actionButton('Riprova questa attività', 'retry-run', 'primary', 'refresh', { id: run.id }));
  if (run.items?.length) { body.append(el('h3', 'detail-subheading', `${run.items.length} email selezionate`)); run.items.forEach(item => body.append(renderDraft(item, true))); }
  else if (run.status === 'succeeded') body.append(el('p', 'helper-text', 'Nessuna email corrispondeva ai contatti prioritari in questa esecuzione.'));
  body.append(el('p', 'step-disclosure', 'Le bozze restano nella piattaforma. Registrare una revisione non invia email e non modifica la casella collegata.'));
}

async function handleAction(button) {
  const action = button.dataset.action;
  if (action === 'open-wizard') { startWizard(); return; }
  if (action === 'open-chat-wizard') { startWizard(false, false, state.chatPreferences); return; }
  if (action === 'edit-chat-preferences') { startWizard(true, false, state.chatPreferences); return; }
  if (action === 'edit-preferences') { startWizard(true); return; }
  if (action === 'reconnect') { startWizard(false, true); return; }
  if (action === 'close-wizard') { closeDialog('#wizard-dialog'); return; }
  if (action === 'close-detail') { closeDialog('#detail-dialog'); return; }
  if (action === 'close-confirm') { closeDialog('#confirm-dialog'); return; }
  if (action === 'close-info') { closeDialog('#info-dialog'); return; }
  if (action === 'open-demo-info') { openDialog('#info-dialog'); return; }
  if (action === 'open-chat') { location.hash = 'services'; setTimeout(() => { $('#chat-input').focus(); $('.chat-card').scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 0); return; }
  if (action === 'deactivate') { openDialog('#confirm-dialog'); return; }
  if (action === 'wizard-company') { closeDialog('#wizard-dialog'); location.hash = 'company'; $('#company-name').focus(); return; }
  if (action === 'wizard-finish' || action === 'wizard-finish-reconnect') { closeDialog('#wizard-dialog'); location.hash = 'overview'; return; }
  if (action === 'add-contact') { if (state.wizard.preferences.priority_contacts.length < 4) { state.wizard.preferences.priority_contacts.push({ name: '', email: '' }); renderContactRows(); const input = $('#wizard-contacts .contact-row:last-child input'); input?.focus(); } return; }
  if (action === 'remove-contact') { if (state.wizard.preferences.priority_contacts.length > 1) { state.wizard.preferences.priority_contacts.splice(Number(button.dataset.index), 1); renderContactRows(); } return; }
  if (action === 'wizard-back') { state.wizard.step--; renderWizard(); return; }
  if (action === 'wizard-next') { if (state.wizard.step === 2 && state.data.connection.status !== 'connected') return; state.wizard.step++; renderWizard(); return; }
  const inWizard = $('#wizard-dialog').open;
  if (inWizard) notice('#wizard-error', '');
  await withBusy(button, async () => {
    try {
      if (action === 'connect-demo') { await api('/api/connection/demo', { method: 'POST', body: {} }); await refresh(); renderWizard(); }
      else if (action === 'connect-gmail') {
        if (state.data.connection.provider === 'gmail' && state.data.connection.status === 'connected') { state.wizard.step = 3; renderWizard(); }
        else { const result = await api('/api/gmail/oauth/start', { method: 'POST', body: {} }); if (!result.authorization_url) throw new Error('Il collegamento Gmail non è ancora disponibile.'); const target = new URL(result.authorization_url, location.origin); if (target.protocol !== 'https:') throw new Error('Il collegamento ricevuto non è valido.'); sessionStorage.setItem('filo-pending-wizard', JSON.stringify({ preferences: state.wizard.preferences, edit: state.wizard.edit, createdAt: Date.now() })); location.assign(target.href); }
      }
      else if (action === 'wizard-preview') {
        if (!capturePreferences()) return;
        state.wizard.preview = await api('/api/service/preview', { method: 'POST', body: state.wizard.preferences }); state.wizard.step = 4; renderWizard();
      }
      else if (action === 'wizard-activate') {
        if (!state.wizard.authorizedRead || !state.wizard.authorizedDraft) { notice('#wizard-error', 'Per attivare il servizio servono entrambi i consensi.'); return; }
        await api('/api/service/activate', { method: 'POST', body: { ...state.wizard.preferences, authorization: { read: true, draft: true, send: false } } }); await refresh(); state.wizard.step = 6; renderWizard();
      }
      else if (action === 'wizard-save-preferences') { await api('/api/service/preferences', { method: 'PUT', body: state.wizard.preferences }); await refresh(); state.wizard.step = 6; renderWizard(); }
      else if (['run', 'pause', 'resume', 'confirm-deactivate'].includes(action)) {
        const kind = action === 'confirm-deactivate' ? 'deactivate' : action;
        await api('/api/service/action', { method: 'POST', body: { action: kind } }); await refresh();
        if (kind === 'deactivate') closeDialog('#confirm-dialog');
        toast({ run: 'Esecuzione avviata. Segui il risultato nelle attività.', pause: 'Segreteria in pausa. La programmazione è ferma.', resume: 'Segreteria ripresa. La programmazione è di nuovo attiva.', deactivate: 'Servizio disattivato. Lo storico resta disponibile.' }[kind]);
      }
      else if (action === 'open-run') await openRun(button.dataset.id);
      else if (action === 'retry-run') { await api(`/api/runs/${encodeURIComponent(button.dataset.id)}/retry`, { method: 'POST', body: {} }); await refresh(); await openRun(button.dataset.id); toast('Nuovo tentativo avviato.'); }
      else if (action === 'approve-draft') { await api(`/api/drafts/${encodeURIComponent(button.dataset.id)}/approve`, { method: 'POST', body: {} }); await refresh(); if (state.currentRun) await openRun(state.currentRun.id); toast('Revisione registrata in Filo. Nessun messaggio inviato.'); }
      else if (action.startsWith('simulate-')) { const kind = action.replace('simulate-', ''); await api('/api/demo/failure', { method: 'POST', body: { kind } }); await refresh(); toast({ temporary: 'Imprevisto temporaneo preparato per la prossima esecuzione demo.', expired: 'Simulazione pronta: la connessione demo scadrà alla prossima esecuzione.', clear: 'Connessione demo ripristinata.' }[kind]); }
      else if (action === 'disconnect-gmail') { await api('/api/gmail/disconnect', { method: 'POST', body: {} }); await refresh(); state.gmail = await api('/api/gmail/status'); toast('Gmail scollegato. Non verranno lette nuove email.'); }
    } catch (error) { if (inWizard) notice('#wizard-error', error.message); else toast(error.message, true); }
  });
}

document.addEventListener('click', event => { const button = event.target.closest('[data-action]'); if (button && !button.disabled) handleAction(button).catch(error => toast(error.message, true)); });
document.addEventListener('input', event => {
  const target = event.target;
  if (target.closest('#company-form')) { state.companyDirty = true; $('#company-save-status').textContent = 'Modifiche da salvare'; }
  if (target.dataset.contact !== undefined && state.wizard) state.wizard.preferences.priority_contacts[Number(target.dataset.contact)][target.dataset.field] = target.value;
});
document.addEventListener('change', event => {
  if (!state.wizard) return;
  if (event.target.id === 'authorize-read') state.wizard.authorizedRead = event.target.checked;
  if (event.target.id === 'authorize-draft') state.wizard.authorizedDraft = event.target.checked;
  if ($('#activate-button')) $('#activate-button').disabled = !state.wizard.authorizedRead || !state.wizard.authorizedDraft;
});
$('#company-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const body = Object.fromEntries(new FormData(form).entries());
  await withBusy($('button[type=submit]', form), async () => {
    try { await api('/api/company', { method: 'PUT', body }); await refresh(true); $('#company-save-status').textContent = 'Informazioni salvate'; toast('Profilo aziendale aggiornato.'); }
    catch (error) { toast(error.message, true); }
  });
});
$('#chat-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.chatBusy) return;
  const input = $('#chat-input'); const message = input.value.trim(); if (!message) return;
  addChatMessage(message, 'user'); input.value = ''; state.chatBusy = true;
  await withBusy($('button', event.currentTarget), async () => {
    try { const result = await api('/api/chat', { method: 'POST', body: { message } }); addChatMessage(result.reply || 'Posso aiutarti a configurare la segreteria email.'); state.chatPreferences = result.preferences || null; const suggestion = $('#chat-suggestion'); suggestion.replaceChildren(); suggestion.hidden = !result.supported; if (result.supported) suggestion.append(actionButton(state.data.service.status === 'inactive' ? 'Configura la segreteria email' : 'Rivedi le preferenze', state.data.service.status === 'inactive' ? 'open-chat-wizard' : 'edit-chat-preferences', 'secondary', 'arrow')); }
    catch (error) { addChatMessage(error.message, 'assistant error'); }
    finally { state.chatBusy = false; }
  });
});
window.addEventListener('hashchange', () => { setPage(location.hash.slice(1)); window.scrollTo({ top: 0, behavior: 'auto' }); });
$$('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeDialog(`#${dialog.id}`); } }));
window.addEventListener('focus', async () => {
  if (!state.data) return;
  try { await refresh(); if (state.wizard?.step === 2) { state.gmail = await api('/api/gmail/status'); renderWizard(); } } catch (_) {}
});
async function initialize() {
  $('#today-label').textContent = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', weekday: 'short', day: 'numeric', month: 'long' }).format(new Date());
  setPage(location.hash.slice(1));
  try {
    await refresh();
    try { state.gmail = await api('/api/gmail/status'); } catch (_) { state.gmail = { configured: false }; }
    if (new URLSearchParams(location.search).get('gmail') === 'connected' && state.data.connection.provider === 'gmail' && state.data.connection.status === 'connected') {
      toast('Gmail collegato. Rivedi le preferenze e autorizza il servizio.');
      let pending;
      try { pending = JSON.parse(sessionStorage.getItem('filo-pending-wizard')); } catch (_) {}
      sessionStorage.removeItem('filo-pending-wizard');
      if (pending && Date.now() - pending.createdAt < 3600000 && Array.isArray(pending.preferences?.priority_contacts)) { startWizard(Boolean(pending.edit), false, pending.preferences); state.wizard.step = 3; renderWizard(); }
      history.replaceState(null, '', `${location.pathname}${location.hash}`);
    }
  }
  catch (error) { $('#loading').hidden = true; notice('#load-error', error.message); const retry = actionButton('Riprova', 'reload', 'secondary'); retry.addEventListener('click', initialize); $('#load-error').append(document.createTextNode(' '), retry); }
}
initialize();
setInterval(async () => {
  if (!state.data || document.hidden || state.loading) return;
  state.loading = true;
  try {
    await refresh();
    if ($('#detail-dialog').open && state.currentRun && ['queued', 'running', 'retry_wait'].includes(state.currentRun.status)) { const run = await api(`/api/runs/${encodeURIComponent(state.currentRun.id)}`); state.currentRun = run; renderRunDetail(run); }
  } catch (_) { /* The next poll retries; user actions report failures immediately. */ }
  finally { state.loading = false; }
}, 5000);
