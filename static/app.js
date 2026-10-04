'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { data: null, gmail: null, page: 'overview', companyDirty: false, companyInitialized: false, loading: false, wizard: null, currentRun: null, chatBusy: false, chatPreferences: null, briefingSignature: null, agendaSignature: null, agendaDate: null, agendaView: null };
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
  if (state.agendaDate && state.agendaDate !== data.agenda?.date) {
    const day = state.agendaDate;
    const agenda = await api(`/api/agenda?date=${encodeURIComponent(day)}`);
    if (state.agendaDate === day) state.agendaView = agenda;
  } else state.agendaView = null;
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
  const hour = Number(new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', hour: 'numeric', hourCycle: 'h23' }).format(new Date()));
  $('#greeting-label').textContent = `${hour < 13 ? 'BUONGIORNO' : hour < 19 ? 'BUON POMERIGGIO' : 'BUONASERA'}${company.name ? `, ${company.name.toLocaleUpperCase('it')}` : ', C’È FILO'}`;
  setPill('#overview-service-status', service.status);
  const expired = connection.status === 'expired';
  notice('#connection-alert', expired ? 'La connessione alla casella è scaduta. Filo conserva lo storico ma non può leggere nuove email. Ricollega la casella dal servizio; per la demo puoi usare “Ripristina demo” negli scenari di prova.' : '');
  if (connection.provider === 'gmail') {
    $('.demo-banner p').replaceChildren(el('strong', '', 'La tua casella Gmail è collegata. '), document.createTextNode('Filo mette in evidenza le richieste e prepara bozze da verificare. Nessuna email viene inviata.'));
    $('.demo-chip').lastChild.textContent = ' Prototipo con Gmail';
  } else {
    $('.demo-banner p').replaceChildren(el('strong', '', 'Un assaggio di Filo, con dati fittizi. '), document.createTextNode('Puoi provare priorità e bozze senza collegare la tua posta. Nessuna email viene inviata.'));
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
  renderMorning();
  renderAgenda();
}

function currentBriefing() {
  const briefing = state.data?.briefing;
  return briefing?.status === 'not_started' && state.data.briefing_example ? state.data.briefing_example : briefing;
}
function renderMorning() {
  const real = state.data.briefing;
  const briefing = currentBriefing();
  if (!briefing) return;
  const preview = Boolean(briefing.preview);
  const priorities = briefing.priorities || [];
  $('#briefing-title').textContent = preview ? 'Un esempio del tuo buongiorno.' : priorities.length ? 'Ecco da dove cominciare.' : 'Il tuo buongiorno, già in ordine.';
  $('#briefing-period').textContent = preview ? 'EMAIL FITTIZIE · ANTEPRIMA' : (briefing.period?.label || 'IL TUO RIEPILOGO').toLocaleUpperCase('it');
  $('#briefing-summary').textContent = briefing.summary || 'Il riepilogo arriverà dopo il primo controllo della posta.';
  const nextAppointment = state.data.agenda?.next_event;
  $('#briefing-next-appointment').hidden = !nextAppointment;
  $('#briefing-next-appointment').textContent = nextAppointment ? `Il prossimo appuntamento: ${nextAppointment.time_label} · ${nextAppointment.title}` : '';
  $('#briefing-freshness').textContent = preview ? 'Nessuna casella letta' : briefing.last_checked_at ? `Ultimo controllo: ${prettyDate(briefing.last_checked_at)}` : briefing.stale ? 'Da aggiornare' : 'In attesa del primo controllo';
  $('#briefing-freshness').classList.toggle('stale', Boolean(briefing.stale));
  const counts = briefing.counts || {};
  const stats = [[counts.pending ?? priorities.length, preview ? 'Risposte nell’esempio' : 'Risposte da rivedere'], [counts.high_priority ?? 0, preview ? 'Priorità nell’esempio' : 'Da guardare per prime'], [state.data.agenda?.items?.length || 0, 'Appuntamenti oggi']];
  replace('#briefing-stats', ...stats.map(([value, label]) => { const card = el('div', 'briefing-stat'); card.append(el('strong', '', value), el('small', '', label)); return card; }));
  const suggestion = preview ? { label: 'Prepara il tuo primo riepilogo', action: 'open-wizard', reason: 'Scegli i contatti che contano. Filo ti proporrà ogni giorno da dove iniziare.' } : real?.suggested_action;
  const suggestionNodes = [];
  if (suggestion) {
    const mark = el('span', 'icon-box peach'); mark.append(icon('spark'));
    const copy = el('div', 'suggestion-copy'); copy.append(el('strong', '', preview ? 'Il prossimo passo lo propone Filo.' : 'Ti propongo questo.'), el('p', '', suggestion.reason || suggestion.label));
    const action = suggestion.action === 'review-priority' ? 'review-priority' : ['open-wizard', 'run', 'resume', 'reconnect', 'edit-preferences'].includes(suggestion.action) ? suggestion.action : priorities.length ? 'review-priority' : 'run';
    const button = actionButton(suggestion.label || 'Comincia da qui', action, 'primary', 'arrow', { id: suggestion.draft_id || priorities[0]?.draft_id || '' });
    if (real?.status === 'checking') button.disabled = true;
    suggestionNodes.push(mark, copy);
    if (!['none', 'wait'].includes(suggestion.action)) suggestionNodes.push(button);
  }
  const suggestionSignature = JSON.stringify({ suggestion, checking: real?.status === 'checking' });
  if (state.suggestionSignature !== suggestionSignature) {
    replace('#suggestion-card', ...suggestionNodes);
    state.suggestionSignature = suggestionSignature;
  }
  $('#suggestion-card').hidden = !suggestionNodes.length;
  const signature = JSON.stringify({ preview, priorities: preview ? priorities.map(({ received_at, ...item }) => item) : priorities, contacts: preview ? briefing.contacts?.map(({ last_received_at, ...contact }) => contact) : briefing.contacts, summary: briefing.summary, status: briefing.status, last_checked_at: briefing.last_checked_at });
  if (signature === state.briefingSignature) return;
  state.briefingSignature = signature;
  if (!priorities.length) {
    const empty = el('div', 'briefing-empty'); empty.append(icon('check'), el('h3', '', real?.legacy_pending_count ? 'Le bozze precedenti restano nello storico.' : real?.status === 'not_started' ? 'Il primo passo è collegare la posta.' : real?.status === 'checking' ? 'Filo sta preparando il riepilogo.' : 'Nessuna risposta da rivedere qui.'), el('p', '', real?.legacy_pending_count ? 'Un nuovo controllo verificherà quali richieste aspettano ancora una risposta. Puoi consultare le bozze precedenti nelle attività.' : real?.status === 'not_started' ? 'Parti dalla casella demo o da Gmail. Poi troverai le richieste importanti direttamente in questa pagina.' : 'Il riepilogo riguarda i messaggi controllati: aggiornalo per verificare nuove richieste.'));
    if (real?.legacy_pending_count) { const link = el('a', 'text-link', 'Consulta le attività'); link.href = '#activity'; empty.append(link); }
    replace('#priority-list', empty);
  } else {
    replace('#priority-list', ...priorities.map((item, index) => {
      const row = el('article', 'priority-item'); row.dataset.draftId = item.draft_id || `example-${index}`;
      const rank = el('span', 'priority-rank', String(index + 1).padStart(2, '0'));
      const body = el('div', 'priority-main');
      const meta = el('div', 'priority-meta'); meta.append(el('strong', '', item.client), el('span', `priority-badge ${item.priority === 'high' ? 'high' : 'normal'}`, item.priority_label || (item.priority === 'high' ? 'Da guardare prima' : 'A seguire')));
      if (item.received_at) meta.append(el('span', 'small-text', `Ha scritto ${prettyDate(item.received_at)}`));
      body.append(meta, el('h3', 'priority-subject', item.subject));
      if (item.source_excerpt) { const context = el('p', 'priority-context', item.source_excerpt.length > 220 ? `${item.source_excerpt.slice(0, 220).trim()}…` : item.source_excerpt); body.append(context); }
      body.append(el('p', 'small-text', item.reason), el('p', 'priority-next-step', item.next_step || 'Rivedi la bozza e controlla i dettagli prima di rispondere.'));
      const draft = el('details', 'priority-inline-draft');
      draft.append(el('summary', '', preview ? 'La bozza di esempio è già pronta' : 'La bozza è già pronta'));
      draft.append(el('pre', 'draft-text', item.draft));
      if (!preview && item.draft_id) {
        const actions = el('div', 'card-actions'); actions.append(actionButton('Copia la bozza', 'copy-priority', 'secondary', undefined, { id: item.draft_id }), actionButton('Segna come rivista', 'approve-draft', 'primary', 'check', { id: item.draft_id })); draft.append(actions);
        draft.append(el('p', 'small-text', 'La revisione resta in Filo. L’email non viene inviata.'));
      }
      body.append(draft); row.append(rank, body); return row;
    }));
  }
  const checkedBeforePeriod = briefing.last_checked_at && briefing.period?.start && new Date(briefing.last_checked_at) < new Date(briefing.period.start);
  $('#contacts-period').textContent = preview ? 'Contatti fittizi dell’esempio.' : checkedBeforePeriod ? 'L’ultimo controllo precede questo periodo. Aggiorna per verificare i nuovi arrivi.' : `${briefing.period?.start ? `Da ${prettyDate(briefing.period.start)}` : 'Periodo del riepilogo'}${briefing.last_checked_at ? ` al controllo del ${prettyDate(briefing.last_checked_at)}` : ''}.`;
  replace('#contact-snapshot', ...(briefing.contacts || []).map(contact => {
    const card = el('div', 'contact-status'); const status = contact.status === 'wrote' ? 'wrote' : contact.status === 'no_messages' ? 'quiet' : 'unverified'; card.classList.add(status);
    card.append(el('strong', '', contact.name), el('span', '', status === 'wrote' ? 'Ha scritto' : status === 'quiet' ? 'Nessuna email nel periodo controllato' : 'Da verificare con un nuovo controllo'));
    if (contact.last_received_at && status === 'wrote') card.append(el('small', '', prettyDate(contact.last_received_at)));
    return card;
  }));
  if (briefing.coverage_note && !preview) $('#contact-snapshot').append(el('p', 'small-text', briefing.coverage_note));
}

function renderAgenda() {
  const agenda = state.agendaView || state.data.agenda;
  if (!agenda) return;
  $('#agenda-source').textContent = 'Appuntamenti aggiunti da te · orari italiani. Il calendario esterno non è ancora collegato.';
  $('#agenda-export').href = `/api/agenda/export?date=${encodeURIComponent(agenda.date)}`;
  if (document.activeElement !== $('#agenda-view-date')) $('#agenda-view-date').value = agenda.date;
  if (!$('#agenda-event-date').value) $('#agenda-event-date').value = agenda.date;
  const signature = JSON.stringify(agenda);
  if (signature === state.agendaSignature) return;
  state.agendaSignature = signature;
  if (!agenda.items.length) {
    const empty = el('div', 'agenda-empty'); empty.append(icon('calendar'), el('h3', '', 'La giornata aspetta i tuoi impegni.'), el('p', '', 'Aggiungi un incontro: lo troverai qui, in ordine di orario, e nel tuo ordine del giorno.'));
    replace('#agenda-timeline', empty);
  } else {
    replace('#agenda-timeline', ...agenda.items.map(item => {
      const card = el('article', 'agenda-event'); const copy = el('div'); copy.append(el('h3', '', item.title));
      if (agenda.next_event?.id === item.id) copy.append(el('span', 'status-pill warning', 'Il prossimo appuntamento'));
      if (item.notes) copy.append(el('p', 'agenda-note', item.notes));
      const remove = actionButton('Rimuovi', 'delete-agenda-event', 'ghost', undefined, { id: item.id }); remove.setAttribute('aria-label', `Rimuovi ${item.title}`);
      card.append(el('time', 'agenda-time', item.time_label), copy, remove); return card;
    }));
  }
}

function romeDateTimeISO(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) throw new Error('Scegli un giorno e un orario validi.');
  const [, year, month, day, hour, minute] = match.map(Number);
  const target = Date.UTC(year, month - 1, day, hour, minute);
  let result = target;
  const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const wallTime = timestamp => { const parts = Object.fromEntries(formatter.formatToParts(new Date(timestamp)).filter(part => part.type !== 'literal').map(part => [part.type, Number(part.value)])); return Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute); };
  for (let iteration = 0; iteration < 3; iteration++) result += target - wallTime(result);
  if (wallTime(result) !== target) throw new Error('Questo orario non esiste per il cambio dell’ora. Scegli un altro orario.');
  return new Date(result).toISOString();
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
  if (action === 'review-priority') {
    const row = $$('.priority-item').find(item => item.dataset.draftId === button.dataset.id) || $('.priority-item');
    if (row) { $('details', row).open = true; row.scrollIntoView({ behavior: 'smooth', block: 'center' }); $('summary', row).focus({ preventScroll: true }); }
    return;
  }
  if (action === 'copy-priority') {
    const item = currentBriefing()?.priorities?.find(item => item.draft_id === button.dataset.id);
    if (item?.draft) { await navigator.clipboard.writeText(item.draft); toast('Bozza copiata. Puoi rivederla nella tua casella prima di inviarla.'); }
    return;
  }
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
        toast({ run: 'Controllo avviato. Il riepilogo si aggiornerà qui tra poco.', pause: 'Segreteria in pausa. La programmazione è ferma.', resume: 'Segreteria ripresa. La programmazione è di nuovo attiva.', deactivate: 'Servizio disattivato. Lo storico resta disponibile.' }[kind]);
      }
      else if (action === 'open-run') await openRun(button.dataset.id);
      else if (action === 'retry-run') { await api(`/api/runs/${encodeURIComponent(button.dataset.id)}/retry`, { method: 'POST', body: {} }); await refresh(); await openRun(button.dataset.id); toast('Nuovo tentativo avviato.'); }
      else if (action === 'approve-draft') { await api(`/api/drafts/${encodeURIComponent(button.dataset.id)}/approve`, { method: 'POST', body: {} }); await refresh(); if ($('#detail-dialog').open && state.currentRun) await openRun(state.currentRun.id); toast('Revisione registrata in Filo. Nessun messaggio inviato.'); }
      else if (action === 'delete-agenda-event') { await api(`/api/agenda/${encodeURIComponent(button.dataset.id)}`, { method: 'DELETE' }); await refresh(); toast('Appuntamento rimosso dall’agenda.'); }
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
$('#agenda-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  notice('#agenda-error', '');
  await withBusy($('button[type=submit]', form), async () => {
    try {
      const fields = Object.fromEntries(new FormData(form).entries());
      await api('/api/agenda', { method: 'POST', body: { title: fields.title, starts_at: romeDateTimeISO(`${fields.date}T${fields.time}`), notes: fields.notes } });
      state.agendaDate = fields.date;
      form.reset(); $('#agenda-event-date').value = fields.date; $('#agenda-add').open = false;
      await refresh(); toast('Appuntamento aggiunto. Lo troverai nel giorno scelto e nell’ordine del giorno.');
    } catch (error) { notice('#agenda-error', error.message); }
  });
});
$('#agenda-view-date').addEventListener('change', async event => {
  const day = event.target.value;
  if (!day) return;
  state.agendaDate = day;
  try {
    const agenda = await api(`/api/agenda?date=${encodeURIComponent(day)}`);
    if (state.agendaDate === day) { state.agendaView = agenda; renderAgenda(); }
  } catch (error) { toast(error.message, true); }
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
