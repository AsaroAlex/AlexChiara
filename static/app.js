'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { data: null, gmail: null, providers: [], mailSetup: null, mailChange: null, imapProvider: null, page: 'overview', companyDirty: false, companyInitialized: false, loading: false, wizard: null, currentRun: null, chatBusy: false, chatPreferences: null, watchSuggestion: null, watchSignature: null, briefingSignature: null, agendaSignature: null, agendaDate: null, agendaView: null, businessServices: [], businessSummary: null, businessCategory: 'all', businessSearch: '', businessScope: 'all', businessLimit: 6, businessService: null, businessRecords: [], businessFilter: 'open', businessOffset: 0, businessTotal: 0, businessPageSize: 100, businessRecord: null, businessEditing: null, businessDelete: null, businessRequest: 0, businessCatalogSignature: null, businessRecordsSignature: null, businessSummarySignature: null };
let toastTimer;
const dialogFocus = new WeakMap();
let businessSearchTimer;
let businessSearchController;
let businessSearchRequest = 0;
let businessConversion = null;
let businessRepetition = null;
let businessRepeatingSource = null;
let businessFormService = null;
let businessUndo = null;
const businessPending = new Set();
const companyFields = ['name', 'sector', 'description', 'signature', 'legal_name', 'vat_number', 'tax_code', 'address', 'postal_code', 'city', 'province', 'email', 'phone', 'pec', 'sdi_code'];

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
  if (Array.isArray(error)) return error.map(item => {
    const location = (item.loc || []).filter(x => x !== 'body');
    const companyLabels = { name: 'Nome dell’azienda', sector: 'Settore', description: 'Descrizione', signature: 'Firma', legal_name: 'Ragione sociale', vat_number: 'Partita IVA', tax_code: 'Codice fiscale', address: 'Indirizzo', postal_code: 'CAP', city: 'Comune', province: 'Provincia', email: 'Email aziendale', phone: 'Telefono', pec: 'PEC', sdi_code: 'Codice destinatario SDI' };
    const label = location.length === 1 ? companyLabels[location[0]] : null;
    const message = label ? String(item.msg || 'Valore non valido').replace(/^Value error, /, '') : item.msg || 'Valore non valido';
    return `${label || location.join(' · ')}${item.loc ? ': ' : ''}${message}`;
  }).join('; ');
  return error?.message || 'Non è stato possibile completare l’operazione. Riprova.';
}
function forgetPendingWizard() { try { sessionStorage.removeItem('filo-pending-wizard'); } catch (_) { /* Storage can be disabled. */ } }
async function api(path, options = {}) {
  const headers = { Accept: 'application/json', ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
  if (options.method && options.method !== 'GET' && state.data?.csrf_token) headers['X-CSRF-Token'] = state.data.csrf_token;
  let response;
  try { response = await fetch(path, { credentials: 'same-origin', ...options, headers, body: options.body !== undefined ? JSON.stringify(options.body) : undefined }); }
  catch (error) { if (error.name === 'AbortError') throw error; throw new Error('Il servizio non è raggiungibile. Controlla la connessione e riprova.'); }
  let result;
  try { result = await response.json(); } catch (_) { result = {}; }
  if (response.status === 401) {
    clearImapSecret();
    forgetPendingWizard();
    location.replace('/login?next=/app');
    throw new Error('Accedi a Spazelia per continuare.');
  }
  if (!response.ok) throw new Error(errorText(result.detail || result.error || result.message || `Operazione non riuscita (${response.status}).`));
  return result;
}
function notice(selector, message) { const target = $(selector); target.textContent = message || ''; target.hidden = !message; }
function toast(message, isError = false, undo = null) {
  clearTimeout(toastTimer);
  const target = $('#toast');
  target.replaceChildren(el('span', '', message)); target.classList.toggle('error', isError); target.hidden = false;
  businessUndo = undo;
  if (undo) target.append(actionButton('Annulla', 'business-undo', 'ghost'));
  toastTimer = setTimeout(() => { target.hidden = true; businessUndo = null; }, undo ? 12000 : 5500);
}
async function withBusy(button, operation) {
  if (button?.disabled) return;
  // Keep icons and inner structure: restore the original nodes, not just the text.
  const original = button ? Array.from(button.childNodes, node => node.cloneNode(true)) : [];
  const iconOnly = button && !button.textContent.trim();
  if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); if (!iconOnly) button.textContent = 'Un momento…'; }
  try { return await operation(); }
  finally { if (button?.isConnected) { button.disabled = false; button.removeAttribute('aria-busy'); button.replaceChildren(...original); } }
}
function prettyDate(value, withTime = true) {
  if (!value) return 'Non ancora prevista';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return 'Data non disponibile';
  return new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: 'numeric', month: 'short', ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}) }).format(date);
}
function clockTime(service = state.data?.service) { return service ? `${String(service.hour ?? 9).padStart(2, '0')}:${String(service.minute ?? 0).padStart(2, '0')}` : '09:00'; }
function orderedRuns() { return [...(state.data?.runs || [])].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)); }
function openDialog(selector) { const dialog = $(selector); if (!dialog.open) { dialogFocus.set(dialog, document.activeElement); dialog.showModal(); } if (selector === '#wizard-dialog') { const heading = $('#wizard-title'); if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); } } }
function closeDialog(selector) { if (selector === '#imap-dialog') clearImapSecret(); $(selector).close(); }

let refreshGeneration = 0;
async function refresh(forceCompany = false) {
  const generation = ++refreshGeneration;
  const data = await api('/api/bootstrap');
  // The 5-second poll, window focus and actions can overlap: keep the newest.
  if (generation !== refreshGeneration) return;
  state.data = data;
  await Promise.all([refreshProviders(), refreshBusiness()]);
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
const PAGE_TITLES = { overview: 'Panoramica', services: 'Servizi', activity: 'Attività', company: 'La tua azienda', connections: 'Collegamenti' };
function showPage(page) {
  // pushState changes the address without the hashchange scroll to the top.
  if (location.hash.slice(1) !== page) history.pushState(null, '', `#${page}`);
  setPage(page);
}
function setPage(page) {
  const titles = PAGE_TITLES;
  state.page = titles[page] ? page : 'overview';
  $$('.page-view').forEach(view => { view.hidden = view.id !== `page-${state.page}`; });
  $$('.nav-item').forEach(item => { const active = item.dataset.page === state.page; item.classList.toggle('active', active); if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current'); });
  $('#page-crumb').textContent = titles[state.page];
  document.title = `${titles[state.page]} · Spazelia`;
}
function render(forceCompany = false) {
  const { company = {}, service = {}, connection = {}, approvals = [] } = state.data;
  $('#sidebar-company').textContent = company.name || 'Il tuo studio';
  $('#company-avatar').textContent = (company.name || 'Il tuo studio').split(/\s+/).filter(Boolean).slice(0, 2).map(x => x[0]).join('').toUpperCase();
  const hour = Number(new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', hour: 'numeric', hourCycle: 'h23' }).format(new Date()));
  $('#greeting-label').textContent = `${hour < 13 ? 'Buongiorno' : hour < 19 ? 'Buon pomeriggio' : 'Buonasera'}${company.name ? `, ${company.name}` : ', c’è Spazelia'}`;
  setPill('#overview-service-status', service.status);
  $('#catalog-mail-status').textContent = connection.status === 'connected' ? 'Casella collegata' : connection.status === 'expired' ? 'Da ricollegare' : 'Collega la casella';
  const expired = connection.status === 'expired';
  notice('#connection-alert', expired ? 'La connessione alla casella è scaduta. Ricollega la casella per riprendere i controlli.' : '');
  $('.demo-banner').hidden = Boolean(state.data.real_data_only) || connection.provider !== 'demo';
  $('.demo-controls').hidden = Boolean(state.data.real_data_only);
  $('.form-intro').textContent = 'Nome e firma per iniziare. Gli altri dati sono facoltativi.';
  const reusableFields = companyFields.filter(field => !['name', 'sector', 'description', 'signature'].includes(field));
  const filled = reusableFields.filter(field => company[field]).length;
  $('#company-profile-summary').textContent = filled ? `${filled} informazioni facoltative salvate. Le utilizziamo dove servono, senza copiarle in ogni scheda.` : 'Aggiungi ragione sociale e contatti quando ti servono. Puoi lavorare anche con il solo nome.';
  const isActive = service.status === 'active';
  const isPaused = service.status === 'paused';
  $('#overview-service-description').textContent = isActive ? 'La tua segreteria è al lavoro: seleziona le email prioritarie e prepara bozze locali che potrai rivedere.' : isPaused ? 'Hai messo il servizio in pausa. Le esecuzioni programmate si fermano; attività e bozze restano a disposizione.' : 'Scegli chi seguire. Spazelia ordina le richieste e prepara le bozze.';
  const facts = [];
  if (isActive || isPaused) {
    for (const [symbol, text] of [['clock', `${clockTime()} · Europe/Rome`], ['mail', `${service.priority_contacts?.length || 0} contatti prioritari`], ['link', `Casella ${mailProviderLabel()}`]]) { const fact = el('span', 'service-fact', text); fact.prepend(icon(symbol)); facts.push(fact); }
  }
  replace('#overview-service-facts', ...facts);
  const overviewActions = [];
  const catalogActions = [];
  if (service.status === 'inactive') {
    overviewActions.push(actionButton('Configura il servizio', 'open-wizard', 'secondary', 'arrow'));
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
    for (const field of companyFields) $(`#company-${field}`).value = company[field] || '';
    state.companyInitialized = true; state.companyDirty = false;
  }
  const connectionNodes = [statusPill(connection.status || 'disconnected'), el('p', '', connection.label || (connection.provider === 'demo' ? 'Casella demo con email di esempio.' : 'Nessuna casella collegata. Scegli il tuo provider nei Collegamenti.'))];
  if (isRealMailbox()) connectionNodes.push(el('p', '', `${mailProviderLabel()} · Lettura della posta e bozze locali, senza invio.`));
  connectionNodes.push(actionButton(isRealMailbox() ? 'Gestisci la casella' : 'Collega la tua posta', 'open-connections', 'secondary', 'link'));
  replace('#company-connection', ...connectionNodes);
  renderMailConnections();
  $('#demo-scenario-status').textContent = expired ? 'Connessione demo scaduta: il servizio richiede un ripristino.' : state.data.demo_failure === 'expired' ? 'La prossima esecuzione demo simulerà una connessione scaduta.' : state.data.demo_failure === 'temporary' ? 'La prossima esecuzione demo simulerà un errore temporaneo.' : '';
  $$('[data-action^="simulate-"]').forEach(button => { button.disabled = connection.provider !== 'demo'; });
  renderMorning();
  renderWatches();
  renderAgenda();
  renderBusiness();
}

const businessStatusLabels = { todo: 'Da fare', in_progress: 'In corso', waiting: 'In attesa', done: 'Completata', cancelled: 'Annullata' };
const businessPriorityLabels = { low: 'Bassa', normal: 'Normale', high: 'Alta', urgent: 'Urgente' };
function businessService(id) { return state.businessServices.find(service => service.id === id); }
function businessOpen(record) { return !['done', 'cancelled'].includes(record.status); }
function businessDay() { return state.businessSummary?.date || new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()); }
function businessEffectiveDate(record) { return record.effective_due_date || record.due_date || null; }
function businessTomorrow() { const date = new Date(`${businessDay()}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + 1); return date.toISOString().slice(0, 10); }
function businessProgress(record) { return record.playbook?.total ? `${record.playbook.completed}/${record.playbook.total} passaggi segnati da te` : ''; }
function businessDue(record) {
  const active = businessOpen(record);
  const effectiveDate = businessEffectiveDate(record);
  const date = effectiveDate ? prettyDate(`${effectiveDate}T12:00:00+02:00`, false) : '';
  if (active && effectiveDate && effectiveDate < businessDay()) return { label: `Scaduta · ${date}`, style: 'error' };
  if (active && effectiveDate === businessDay()) return { label: 'Da fare oggi', style: 'warning' };
  if (active && (record.urgency === 'attention' || record.attention)) return { label: 'Da controllare', style: 'warning' };
  if (!effectiveDate) return { label: 'Senza scadenza', style: 'neutral' };
  return { label: active ? `Entro ${date}` : date, style: 'neutral' };
}

function businessRecordStatus(record) { return el('span', `status-pill ${record.status === 'done' ? 'success' : 'neutral'}`, businessStatusLabels[record.status] || 'Da fare'); }
async function refreshBusiness() {
  try {
    const [catalog, summary] = await Promise.all([api('/api/business/services'), state.data?.business ? Promise.resolve(state.data.business) : api('/api/business/summary')]);
    state.businessServices = catalog.services || [];
    state.businessSummary = summary;
    notice('#business-catalog-error', '');
    if (state.businessService) await loadBusinessRecords(state.businessService, false);
  } catch (error) { notice('#business-catalog-error', `I servizi non si sono aggiornati. ${error.message}`); }
}
function renderBusiness() {
  renderBusinessCatalog(); renderBusinessSummary();
  $('#business-catalog').hidden = Boolean(state.businessService);
  $('#business-workspace').hidden = !state.businessService;
  if (state.businessService) renderBusinessRecords();
}
function renderBusinessCatalog() {
  const services = state.businessServices.filter(service => service.kind === 'business' && service.availability === 'available');
  const categories = [...new Set(services.map(service => service.category))];
  const signature = JSON.stringify({ services: state.businessServices, category: state.businessCategory, search: state.businessSearch, scope: state.businessScope, limit: state.businessLimit });
  if (signature === state.businessCatalogSignature) return;
  state.businessCatalogSignature = signature;
  replace('#business-categories', ...['all', ...categories].map(category => {
    const button = actionButton(category === 'all' ? 'Tutte le aree' : category, 'business-category', 'secondary', undefined, { category });
    button.classList.add('business-category'); button.setAttribute('aria-pressed', String(state.businessCategory === category)); return button;
  }));
  const search = state.businessSearch.trim().toLocaleLowerCase('it');
  const filtered = services.filter(service => (state.businessCategory === 'all' || service.category === state.businessCategory) && (state.businessScope !== 'used' || service.record_count > 0) && (!search || `${service.name} ${service.description} ${service.category}`.toLocaleLowerCase('it').includes(search)));
  $('#business-catalog-count').textContent = `${filtered.length} ${filtered.length === 1 ? 'servizio disponibile' : 'servizi disponibili'}${state.businessCategory === 'all' ? '' : ` · ${state.businessCategory}`}`;
  if (!filtered.length) { const empty = el('div', 'card business-empty'); empty.append(icon('grid'), el('h3', '', 'Nessun servizio con questi filtri.'), el('p', '', 'Prova un’altra parola o torna a tutte le aree.'), actionButton('Azzera i filtri', 'business-clear-filters', 'secondary')); replace('#business-service-list', empty); }
  else replace('#business-service-list', ...filtered.slice(0, state.businessLimit).map(service => {
    const card = el('article', 'card business-service-card'); card.dataset.serviceId = service.id;
    const top = el('div', 'business-service-top'); const mark = el('span', 'icon-box neutral'); mark.append(icon($('#icon-' + service.icon) ? service.icon : 'grid'));
    top.append(mark, el('span', 'small-text', service.category));
    card.append(top, el('h3', '', service.name), el('p', '', service.description));
    const counts = el('div', 'business-service-counts');
    if (service.overdue_count) counts.append(el('span', 'status-pill error', `${service.overdue_count} ${service.overdue_count === 1 ? 'scaduta' : 'scadute'}`));
    else counts.append(el('span', 'small-text', service.open_count ? `${service.open_count} ${service.open_count === 1 ? 'attività aperta' : 'attività aperte'}` : 'Pronto per le tue attività'));
    card.append(counts, actionButton('Apri', 'open-business', 'secondary', 'arrow', { service: service.id })); return card;
  }));
  replace('#business-catalog-more', ...(filtered.length > state.businessLimit ? [actionButton(`Mostra altri servizi (${filtered.length - state.businessLimit})`, 'business-more', 'secondary', 'arrow')] : []));
  const integrations = state.businessServices.filter(service => service.kind === 'integration');
  $('#business-integration-panel').hidden = !integrations.length;
  replace('#business-integration-list', ...integrations.map(service => {
    const card = el('article', 'card business-service-card business-integration-card'); card.append(el('span', 'status-pill neutral', 'Integrazione da attivare'), el('h3', '', service.name), el('p', '', service.description));
    if (service.next_action) card.append(el('p', 'business-integration-next', service.next_action)); return card;
  }));
}
function renderBusinessSummary() {
  const summary = state.businessSummary;
  const signature = JSON.stringify(summary);
  if (signature === state.businessSummarySignature) return;
  state.businessSummarySignature = signature;
  const totals = summary?.totals || {};
  renderBusinessFinancialSummary(summary?.financial_summary);
  $('#business-priorities-summary').textContent = totals.overdue ? `${totals.overdue} ${totals.overdue === 1 ? 'attività scaduta' : 'attività scadute'}${totals.today ? ` · ${totals.today} per oggi` : ''}. Scadenze aggiunte da te.` : totals.today ? `${totals.today} ${totals.today === 1 ? 'attività per oggi' : 'attività per oggi'}. Scadenze aggiunte da te.` : 'Scadenze e attività aggiunte da te, in ordine.';
  const actions = summary?.next_actions || [];
  if (!actions.length) { const empty = el('div', 'business-summary-empty'); empty.append(el('h3', '', totals.total ? 'Le tue attività sono in ordine.' : 'Da quale attività vuoi partire?'), el('p', '', totals.total ? 'Le attività completate restano nei tuoi servizi.' : 'Aggiungi un incasso, un preventivo o una scadenza. Le priorità arriveranno qui.'), actionButton('Scegli un servizio', 'business-open-catalog', 'secondary', 'arrow')); replace('#business-next-actions', empty); return; }
  replace('#business-next-actions', ...actions.slice(0, 5).map(record => {
    const row = el('article', 'business-next-action'); row.dataset.recordId = record.id;
    const content = el('div'); const meta = el('div', 'business-record-meta'); const due = businessDue(record);
    meta.append(el('span', `status-pill ${due.style}`, due.label), el('span', 'small-text', businessService(record.service_id)?.name || record.service_name || 'Attività'));
    content.append(meta, el('h3', '', record.title));
    if (record.contact) content.append(el('p', 'small-text', record.contact));
    content.append(el('p', 'business-next-step', record.next_action || businessService(record.service_id)?.next_action || 'Rivedi le informazioni e completa il prossimo passo.'));
    if (businessProgress(record)) content.append(el('p', 'business-progress-caption', businessProgress(record)));
    const actions = el('div', 'business-next-action-buttons');
    actions.append(actionButton('Apri attività', 'business-open-record', 'secondary', 'arrow', { id: record.id, service: record.service_id }), actionButton('Completa', 'business-quick-done', 'ghost', 'check', { id: record.id }));
    if (businessPending.has(record.id)) $$('button', actions).forEach(button => { button.disabled = true; });
    row.append(content, actions); return row;
  }));
}
function renderBusinessFinancialSummary(financial) {
  const target = $('#business-financial-summary');
  target.replaceChildren(); target.hidden = !financial || !(financial.receivables?.count || financial.payables?.count);
  if (target.hidden) return;
  const money = value => new Intl.NumberFormat('it-IT', { style: 'currency', currency: financial.currency || 'EUR' }).format(Number(value || 0));
  for (const [key, label] of [['receivables', 'Incassi da seguire'], ['payables', 'Pagamenti da seguire']]) {
    const amounts = financial[key] || {};
    const box = el('div', 'business-financial-item'); box.dataset.financial = key;
    box.append(el('h3', '', label), el('strong', '', money(amounts.open_amount)), el('p', 'small-text', `Scaduti ${money(amounts.overdue_amount)} · Oggi ${money(amounts.today_amount)}`)); target.append(box);
  }
  target.append(el('p', 'business-financial-source', financial.source || 'Importi inseriti da te. Nessun collegamento a un conto bancario.'));
}
async function loadBusinessRecords(serviceId, renderNow = true) {
  const request = ++state.businessRequest;
  try {
    const result = await api(`/api/business/records?service_id=${encodeURIComponent(serviceId)}&limit=${state.businessPageSize}&offset=${state.businessOffset}${state.businessFilter === 'all' ? '' : `&status=${encodeURIComponent(state.businessFilter)}`}`);
    if (state.businessService !== serviceId || request !== state.businessRequest) return;
    state.businessRecords = result.items || []; state.businessTotal = result.total ?? state.businessRecords.length; notice('#business-records-error', '');
    if (renderNow) renderBusinessRecords();
  } catch (error) { if (state.businessService === serviceId && request === state.businessRequest) notice('#business-records-error', error.message); }
}
async function openBusinessModule(id) {
  const service = businessService(id);
  if (!service || service.kind !== 'business' || service.availability !== 'available') throw new Error('Questo collegamento non è ancora disponibile.');
  state.businessService = id; state.businessRecords = []; state.businessOffset = 0; state.businessTotal = 0; state.businessRecordsSignature = null;
  location.hash = 'services'; setPage('services'); renderBusiness();
  await loadBusinessRecords(id); $('#business-module-title').tabIndex = -1; $('#business-module-title').focus({ preventScroll: true }); $('#business-workspace').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function renderBusinessRecords() {
  const service = businessService(state.businessService);
  if (!service) return;
  $('#business-module-title').textContent = service.name; $('#business-module-category').textContent = service.category;
  $('#business-module-description').textContent = service.description;
  $('#business-module-boundary').textContent = service.output_footer || 'Informazioni e documenti preparati dai tuoi dati. Rivedi il risultato prima di usarlo o inviarlo.';
  const signature = JSON.stringify({ service: service.id, records: state.businessRecords, filter: state.businessFilter, total: state.businessTotal, offset: state.businessOffset, date: businessDay() });
  if (signature === state.businessRecordsSignature) return;
  state.businessRecordsSignature = signature;
  const records = state.businessRecords.filter(record => state.businessFilter === 'all' || (state.businessFilter === 'done' ? record.status === 'done' : businessOpen(record)));
  $('#business-records-count').textContent = state.businessTotal > state.businessPageSize ? `${state.businessOffset + 1}–${state.businessOffset + records.length} di ${state.businessTotal} attività · dati aggiunti da te` : `${state.businessTotal} attività · dati aggiunti da te`;
  const pages = [];
  if (state.businessOffset > 0) pages.push(actionButton('Attività precedenti', 'business-record-page', 'secondary', undefined, { offset: String(Math.max(0, state.businessOffset - state.businessPageSize)) }));
  if (state.businessOffset + state.businessPageSize < state.businessTotal) pages.push(actionButton('Attività successive', 'business-record-page', 'secondary', 'arrow', { offset: String(state.businessOffset + state.businessPageSize) }));
  replace('#business-record-pages', ...pages);
  if (!records.length) {
    const hasHistory = service.record_count > 0;
    const empty = el('div', 'card business-empty'); empty.append(icon('check'), el('h3', '', hasHistory ? 'Nessuna attività con questo filtro.' : `Inizia con ${service.name.toLocaleLowerCase('it')}.`), el('p', '', hasHistory ? 'Le attività salvate restano nello storico. Scegli tutte le attività per consultarle.' : 'Inserisci i dati della prima attività. Ti aiutiamo a seguirla, un passo alla volta.'));
    if (!hasHistory && service.playbook?.steps?.length) { const recipe = el('ol', 'business-empty-recipe'); for (const step of service.playbook.steps) recipe.append(el('li', '', step.label)); empty.append(recipe); }
    if (hasHistory && state.businessFilter !== 'all') empty.append(actionButton('Mostra tutte le attività', 'business-show-history', 'secondary', 'arrow'));
    empty.append(actionButton('Aggiungi attività', 'business-new', 'primary', 'arrow')); replace('#business-record-list', empty); return;
  }
  replace('#business-record-list', ...records.map(record => {
    const card = el('article', 'card business-record'); card.dataset.recordId = record.id;
    const content = el('div'); const meta = el('div', 'business-record-meta'); const due = businessDue(record);
    meta.append(businessRecordStatus(record), el('span', `status-pill ${due.style}`, due.label));
    if (['urgent', 'high'].includes(record.priority)) meta.append(el('span', 'status-pill warning', `Priorità ${businessPriorityLabels[record.priority].toLocaleLowerCase('it')}`));
    content.append(meta, el('h3', '', record.title)); if (record.contact) content.append(el('p', 'small-text', record.contact));
    if (record.next_action) content.append(el('p', 'business-next-step', record.next_action));
    if (businessProgress(record)) content.append(el('p', 'business-progress-caption', businessProgress(record)));
    const actions = el('div', 'card-actions'); actions.append(actionButton('Apri attività', 'business-open-record', 'secondary', 'arrow', { id: record.id, service: record.service_id }));
    if (businessOpen(record)) actions.append(actionButton('Segna completata', 'business-status', 'ghost', 'check', { id: record.id, status: 'done' }));
    card.append(content, actions); return card;
  }));
}
function businessFormField(field, value = '') {
  const container = el('div', `business-form-field ${field.type === 'textarea' ? 'business-form-wide' : ''}`);
  const id = `business-field-${field.id}`; const label = el('label', '', field.label); label.htmlFor = id;
  if (!field.required) label.append(el('span', 'muted', ' (facoltativo)'));
  const input = el(field.type === 'textarea' ? 'textarea' : field.type === 'select' ? 'select' : 'input'); input.id = id; input.name = field.id;
  if (field.type === 'textarea') input.rows = 3;
  else if (field.type === 'select') { input.append(new Option('Scegli…', '')); for (const option of field.options || []) input.append(new Option(typeof option === 'string' ? option : option.label, typeof option === 'string' ? option : option.value)); }
  else { input.type = ['date', 'number', 'money'].includes(field.type) ? (field.type === 'money' ? 'number' : field.type) : 'text'; if (field.type === 'money') input.step = field.step || '0.01'; else if (field.type === 'number') input.step = field.step || '1'; }
  if (field.type === 'date') { input.min = '1900-01-01'; input.max = '2200-12-31'; }
  input.required = Boolean(field.required); if (field.max_length) input.maxLength = field.max_length;
  if (field.min !== undefined) input.min = field.min; if (field.max !== undefined) input.max = field.max;
  input.value = value ?? ''; container.append(label, input); return container;
}
function openBusinessForm(record = null, conversion = null, repetition = null) {
  const service = businessService(record?.service_id || state.businessService);
  if (!service) return;
  businessConversion = conversion;
  businessRepetition = repetition;
  state.businessEditing = conversion || repetition ? null : record; businessFormService = service.id;
  if (!conversion && !repetition) state.businessService = service.id;
  $('#business-form-module').textContent = service.name; $('#business-form-title').textContent = repetition ? 'Rivedi la prossima attività' : conversion ? (service.id === 'invoices' ? 'Rivedi la bozza fattura' : 'Rivedi l’incasso') : record ? 'Modifica attività' : 'Aggiungi attività';
  $('#business-form-intro').textContent = repetition ? 'Abbiamo ripreso le informazioni utili e azzerato i passaggi. Controlla e modifica i dati prima di creare la prossima attività.' : conversion ? 'Abbiamo ripreso i dati dal documento precedente. Controlla importi, riferimenti e scadenza prima di salvare.' : 'Inserisci le informazioni che hai. Spazelia prepara un riepilogo e ordina le tue scadenze.';
  $('#business-conversion-note').hidden = !conversion && !repetition;
  $('#business-conversion-note').textContent = repetition ? `Da “${repetition.source.title}” · Prossima scadenza: ${prettyDate(`${repetition.next_date}T12:00:00+02:00`, false)}. L’attività originale resta invariata. Le altre date vanno verificate o reinserite.` : conversion ? `Da “${conversion.source.title}”. ${service.id === 'invoices' ? 'Questa è una bozza interna: non è una fattura elettronica e non viene inviata allo SDI.' : 'Il salvataggio prepara un promemoria: non registra un pagamento né invia un sollecito.'}` : '';
  $('#business-savings-fields').hidden = Boolean(conversion || repetition);
  $('.business-form-extra > summary').textContent = conversion || repetition ? 'Note facoltative' : 'Note e tempo risparmiato';
  $('#business-record-form button[type="submit"]').firstChild.textContent = repetition ? 'Crea la prossima attività ' : conversion ? (service.id === 'invoices' ? 'Salva bozza fattura ' : 'Salva incasso ') : 'Salva attività ';
  const fields = [
    { id: 'title', label: 'Titolo dell’attività', type: 'text', required: true, max_length: 160 },
    { id: 'contact', label: 'Cliente, referente o fornitore', type: 'text', max_length: 160 },
    { id: 'due_date', label: 'Scadenza', type: 'date' },
    { id: 'status', label: 'Stato', type: 'select', required: true, options: Object.entries(businessStatusLabels).map(([value, label]) => ({ value, label })) },
    { id: 'priority', label: 'Priorità', type: 'select', required: true, options: Object.entries(businessPriorityLabels).map(([value, label]) => ({ value, label })) },
  ];
  replace('#business-common-fields', ...fields.filter(field => !(conversion || repetition) || field.id !== 'status').map(field => businessFormField(field, record?.[field.id] ?? (field.id === 'status' ? 'todo' : field.id === 'priority' ? 'normal' : ''))));
  replace('#business-detail-fields', ...(service.fields || []).map(field => businessFormField(field, record?.details?.[field.id])));
  const dueField = $('#business-field-due_date');
  if (repetition) { dueField.readOnly = true; dueField.setAttribute('aria-describedby', 'business-conversion-note'); const primaryDueField = service.playbook?.due_fields?.[0]; const primary = primaryDueField ? $(`#business-field-${primaryDueField}`) : null; if (primary && primary.value === repetition.next_date) primary.readOnly = true; }
  else if (service.playbook?.due_fields?.length) {
    const labels = service.playbook.due_fields.map(id => service.fields.find(field => field.id === id)?.label).filter(Boolean);
    if (labels.length) { const hint = el('p', 'helper-text', `Se lasci vuota questa scadenza, usiamo ${labels.map(label => `“${label}”`).join(', ')} quando compilata.`); hint.id = 'business-due-helper'; dueField.setAttribute('aria-describedby', hint.id); dueField.parentElement.append(hint); }
  }
  $('#business-record-notes').value = record?.notes || ''; $('#business-record-saved-minutes').value = record?.saved_minutes || 0;
  notice('#business-form-error', ''); openDialog('#business-form-dialog'); $('#business-field-title').focus();
}
async function openBusinessRecord(id, serviceId) {
  const result = await api(`/api/business/records/${encodeURIComponent(id)}`); const record = result.item;
  if (!record) throw new Error('L’attività non è più disponibile. Aggiorna i tuoi servizi.');
  state.businessRecord = record; renderBusinessDetail(record); openDialog('#business-detail-dialog');
}
function renderBusinessDetail(record) {
  const service = businessService(record.service_id); const body = $('#business-detail-body'); body.replaceChildren();
  $('#business-detail-module').textContent = service?.name || 'Attività';
  const title = el('h2', '', record.title); title.id = 'business-detail-title'; const meta = el('div', 'business-record-meta'); const due = businessDue(record);
  meta.append(businessRecordStatus(record), el('span', `status-pill ${due.style}`, due.label));
  body.append(title, meta); if (record.contact) body.append(el('p', 'business-detail-contact', record.contact));
  if (record.due_source?.type === 'field') body.append(el('p', 'business-due-source', `Scadenza ripresa da “${record.due_source.label}”.`));
  const actions = el('div', 'card-actions');
  actions.append(actionButton(businessOpen(record) ? 'Segna completata' : 'Riapri attività', 'business-status', 'primary', 'check', { id: record.id, status: businessOpen(record) ? 'done' : 'todo' }), actionButton('Modifica', 'business-edit', 'secondary', undefined, { id: record.id }));
  const download = el('a', 'button secondary', 'Scarica il documento'); download.href = `/api/business/records/${encodeURIComponent(record.id)}/export`; download.setAttribute('download', ''); download.append(icon('arrow')); actions.append(actionButton('Copia il documento', 'business-copy-document', 'secondary', undefined, { id: record.id }), download, actionButton('Ripeti attività', 'business-repeat', 'secondary', undefined, { id: record.id }), actionButton('Elimina', 'business-delete', 'ghost', undefined, { id: record.id })); body.append(actions);
  if (record.playbook?.steps?.length) {
    const section = el('section', 'business-playbook'); section.setAttribute('aria-labelledby', 'business-playbook-title');
    const heading = el('div', 'business-playbook-heading'); const title = el('h3', '', 'Passaggi da seguire'); title.id = 'business-playbook-title';
    const progress = el('span', 'business-progress-caption', `${record.playbook.completed}/${record.playbook.total} segnati da te`); progress.id = 'business-playbook-progress'; progress.setAttribute('role', 'status'); heading.append(title, progress); section.append(heading, el('p', 'helper-text', 'Segna tu ciò che hai fatto. Spazelia conserva il progresso; i passaggi non vengono verificati automaticamente.'));
    for (const step of record.playbook.steps) {
      const label = el('label', 'business-playbook-step'); const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.id = `business-step-${step.id}`; checkbox.dataset.businessStep = step.id; checkbox.dataset.recordId = record.id; checkbox.checked = Boolean(step.checked); label.htmlFor = checkbox.id; label.append(checkbox, el('span', '', step.label)); section.append(label);
    }
    const error = el('div', 'notice error'); error.id = 'business-step-error'; error.setAttribute('role', 'alert'); error.hidden = true; section.append(error); body.append(section);
  }
  if (record.stock_reorder) {
    const stock = el('section', 'business-stock-reorder'); const number = value => new Intl.NumberFormat('it-IT', { maximumFractionDigits: 6 }).format(Number(value));
    stock.append(el('h3', '', 'Riordino da valutare'), el('p', '', `${number(record.stock_reorder.suggested_quantity)} ${record.details?.unit || 'unità'} per raggiungere la soglia indicata.`), el('p', 'helper-text', `Disponibili ${number(record.stock_reorder.quantity)} · Soglia ${number(record.stock_reorder.minimum)}. Calcolo dai tuoi dati: nessun ordine viene inviato.`)); body.append(stock);
  }
  if (['quotes', 'invoices'].includes(record.service_id) && record.status !== 'cancelled') {
    const target = record.service_id === 'quotes' ? 'invoices' : 'receivables';
    const workflow = el('div', 'business-commercial-step');
    workflow.append(el('h3', '', 'Il prossimo passo'), el('p', 'small-text', target === 'invoices' ? 'Riprendi cliente e importi in una bozza interna, da rivedere. Nessun invio allo SDI.' : 'Prepara il promemoria dell’incasso con importo e riferimento. Scegli tu la scadenza.'), actionButton(target === 'invoices' ? 'Prepara bozza fattura' : 'Prepara incasso', 'business-convert', 'secondary', 'arrow', { id: record.id, target })); body.append(workflow);
  }
  const links = record.links || { source: null, targets: [] };
  if (links.source || links.targets?.length) {
    const connections = el('section', 'business-document-links'); connections.append(el('h3', '', 'Attività collegate'));
    for (const link of [...(links.source ? [{ ...links.source, role: links.source.kind === 'repeat' ? 'Attività precedente' : 'Origine' }] : []), ...(links.targets || []).map(item => ({ ...item, role: item.kind === 'repeat' ? 'Prossima attività' : 'Passo successivo' }))]) {
      const button = actionButton('', 'business-linked-record', 'ghost', 'arrow', { id: link.id, service: link.service_id });
      const description = el('span'); description.append(el('small', '', `${link.role} · ${businessService(link.service_id)?.name || 'Attività'}`), el('strong', '', link.title)); button.prepend(description); connections.append(button);
    }
    body.append(connections);
  }
  body.append(el('h3', 'business-document-heading', 'Il tuo documento'), el('p', 'small-text', 'Preparato dalle informazioni inserite. Rivedilo prima di utilizzarlo.'));
  const document = el('pre', 'business-document', record.document || 'Il documento non è ancora disponibile.'); body.append(document);
  if (record.saved_minutes) body.append(el('p', 'helper-text', `${record.saved_minutes} minuti risparmiati: tua stima dichiarata.`));
}
async function saveBusinessRecord(event) {
  event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return;
  const service = businessService(businessFormService || state.businessService); if (!service) return;
  const values = new FormData(form); const details = {};
  for (const field of service.fields || []) { const value = String(values.get(field.id) || '').trim(); if (value) details[field.id] = ['number', 'money'].includes(field.type) ? Number(value) : value; else if ((state.businessEditing || businessConversion || businessRepetition) && !field.required) details[field.id] = null; }
  const body = { title: String(values.get('title') || '').trim(), contact: String(values.get('contact') || '').trim(), due_date: values.get('due_date') || null, status: values.get('status'), priority: values.get('priority'), notes: String(values.get('notes') || '').trim(), saved_minutes: Number(values.get('saved_minutes') || 0), details };
  const conversion = businessConversion;
  const repetition = businessRepetition;
  if (repetition) { delete body.status; delete body.saved_minutes; delete body.due_date; body.next_date = repetition.next_date; }
  else if (conversion) { delete body.status; delete body.saved_minutes; body.target_service_id = service.id; }
  else if (!state.businessEditing) body.service_id = service.id;
  notice('#business-form-error', '');
  await withBusy($('button[type="submit"]', form), async () => {
    try {
      const editing = state.businessEditing; const path = repetition ? `/api/business/records/${encodeURIComponent(repetition.source.id)}/repeat` : conversion ? `/api/business/records/${encodeURIComponent(conversion.source.id)}/convert` : editing ? `/api/business/records/${encodeURIComponent(editing.id)}` : '/api/business/records';
      const result = await api(path, { method: editing ? 'PATCH' : 'POST', body }); closeDialog('#business-form-dialog'); state.businessEditing = null; businessConversion = null; businessRepetition = null;
      await refresh();
      if ((conversion || repetition) && result.item) { if ($('#business-detail-dialog').open) closeDialog('#business-detail-dialog'); await openBusinessModule(result.item.service_id); await openBusinessRecord(result.item.id, result.item.service_id); }
      else if (result.item && $('#business-detail-dialog').open) { state.businessRecord = result.item; renderBusinessDetail(result.item); }
      toast(repetition ? result.created ? 'Prossima attività creata. I passaggi ripartono da zero.' : 'Questa prossima attività esiste già. Abbiamo mantenuto i dati salvati.' : conversion ? result.created ? 'Passo successivo salvato e collegato al documento di origine.' : 'Il documento collegato è già presente. Lo abbiamo aperto.' : editing ? 'Attività aggiornata. Il documento è pronto da rivedere.' : 'Attività salvata. Il documento e le scadenze sono pronti.');
    }
    catch (error) { notice('#business-form-error', error.message); }
  });
}
async function handleBusinessAction(button) {
  const action = button.dataset.action;
  if (action === 'business-search-open') { openBusinessSearch(); return; }
  if (action === 'business-search-close') { closeDialog('#business-search-dialog'); return; }
  if (action === 'business-search-result' || action === 'business-linked-record') { await withBusy(button, async () => { if ($('#business-search-dialog').open) closeDialog('#business-search-dialog'); if ($('#business-detail-dialog').open) closeDialog('#business-detail-dialog'); await openBusinessModule(button.dataset.service); await openBusinessRecord(button.dataset.id, button.dataset.service); }); return; }
  if (action === 'business-convert') {
    await withBusy(button, async () => {
      const result = await api(`/api/business/records/${encodeURIComponent(button.dataset.id)}/conversion?target_service_id=${encodeURIComponent(button.dataset.target)}`);
      if (result.existing) { closeDialog('#business-detail-dialog'); await openBusinessModule(result.existing.service_id); await openBusinessRecord(result.existing.id, result.existing.service_id); }
      else openBusinessForm(result.proposal, { source: result.source, target: button.dataset.target });
    }); return;
  }
  if (action === 'business-repeat') {
    businessRepeatingSource = { id: button.dataset.id, title: state.businessRecord?.title || 'Attività' }; $('#business-repeat-description').textContent = `Riparti da “${businessRepeatingSource.title}” senza cambiare l’attività originale.`; $('#business-repeat-date').min = businessTomorrow(); $('#business-repeat-date').value = ''; notice('#business-repeat-error', ''); openDialog('#business-repeat-dialog'); $('#business-repeat-date').focus(); return;
  }
  if (action === 'business-close-repeat') { closeDialog('#business-repeat-dialog'); return; }
  if (action === 'business-copy-document') {
    const record = state.businessRecord; if (record?.id !== button.dataset.id || !record.document) throw new Error('Apri di nuovo l’attività per copiare il documento aggiornato.');
    await withBusy(button, async () => { try { if (!navigator.clipboard?.writeText) throw new Error('Clipboard non disponibile'); await navigator.clipboard.writeText(record.document); toast('Documento copiato. Rivedilo prima di utilizzarlo.'); } catch (_) { toast('Il browser non ha permesso di copiare il documento. Puoi scaricarlo dal pulsante accanto.', true); } }); return;
  }
  if (action === 'business-quick-done') {
    const id = button.dataset.id; if (businessPending.has(id)) return; businessPending.add(id);
    try { await withBusy(button, async () => { const original = (await api(`/api/business/records/${encodeURIComponent(id)}`)).item; if (!businessOpen(original)) { await refresh(); return; } await api(`/api/business/records/${encodeURIComponent(id)}`, { method: 'PATCH', body: { status: 'done' } }); await refresh(); toast('Attività completata. Puoi annullare questa modifica.', false, { id, status: original.status }); }); }
    finally { businessPending.delete(id); state.businessSummarySignature = null; renderBusinessSummary(); } return;
  }
  if (action === 'business-undo') {
    const undo = businessUndo; if (!undo || businessPending.has(undo.id)) return; businessPending.add(undo.id); clearTimeout(toastTimer);
    try { await withBusy(button, async () => { const record = (await api(`/api/business/records/${encodeURIComponent(undo.id)}`)).item; if (record.status !== 'done') { toast('Lo stato è già cambiato. Abbiamo mantenuto l’ultima modifica.'); return; } await api(`/api/business/records/${encodeURIComponent(undo.id)}`, { method: 'PATCH', body: { status: undo.status } }); await refresh(); toast('Completamento annullato. L’attività è tornata tra le priorità.'); }); }
    finally { businessPending.delete(undo.id); state.businessSummarySignature = null; renderBusinessSummary(); } return;
  }
  if (action === 'open-business') { await openBusinessModule(button.dataset.service || button.dataset.id); return; }
  if (action === 'business-open-catalog' || action === 'business-back') { state.businessService = null; state.businessRequest++; location.hash = 'services'; setPage('services'); renderBusiness(); $('#services-title').tabIndex = -1; $('#services-title').focus(); return; }
  if (action === 'open-business-agenda') { showPage('overview'); $('#agenda-title').scrollIntoView({ behavior: 'smooth', block: 'start' }); $('#agenda-view-date').focus({ preventScroll: true }); return; }
  if (action === 'business-category') { state.businessCategory = button.dataset.category; state.businessLimit = 6; renderBusinessCatalog(); return; }
  if (action === 'business-clear-filters') { state.businessCategory = 'all'; state.businessSearch = ''; state.businessScope = 'all'; state.businessLimit = 6; $('#business-service-search').value = ''; $('#business-service-scope').value = 'all'; renderBusinessCatalog(); return; }
  if (action === 'business-more') { state.businessLimit += 6; renderBusinessCatalog(); return; }
  if (action === 'business-record-page') { state.businessOffset = Number(button.dataset.offset); await withBusy(button, () => loadBusinessRecords(state.businessService)); return; }
  if (action === 'business-show-history') { state.businessFilter = 'all'; state.businessOffset = 0; $('#business-record-filter').value = 'all'; await withBusy(button, () => loadBusinessRecords(state.businessService)); return; }
  if (action === 'business-new') { openBusinessForm(); return; }
  if (action === 'business-close-form') { closeDialog('#business-form-dialog'); return; }
  if (action === 'business-close-detail') { closeDialog('#business-detail-dialog'); return; }
  if (action === 'business-close-delete') { state.businessDelete = null; closeDialog('#business-delete-dialog'); return; }
  if (action === 'business-open-record') { await withBusy(button, () => openBusinessRecord(button.dataset.id, button.dataset.service)); return; }
  if (action === 'business-edit') { const record = state.businessRecord; if (record?.id === button.dataset.id) { closeDialog('#business-detail-dialog'); await openBusinessModule(record.service_id); openBusinessForm(record); } return; }
  if (action === 'business-delete') { state.businessDelete = button.dataset.id; $('#business-delete-description').textContent = `“${state.businessRecord?.title || 'Questa attività'}” e il suo documento verranno rimossi dai tuoi servizi.`; openDialog('#business-delete-dialog'); return; }
  if (action === 'business-confirm-delete') { await withBusy(button, async () => { const id = state.businessDelete; if (!id) return; await api(`/api/business/records/${encodeURIComponent(id)}`, { method: 'DELETE' }); closeDialog('#business-delete-dialog'); if ($('#business-detail-dialog').open) closeDialog('#business-detail-dialog'); state.businessDelete = null; state.businessRecord = null; await refresh(); toast('Attività eliminata.'); }); return; }
  if (action === 'business-status') { await withBusy(button, async () => { const result = await api(`/api/business/records/${encodeURIComponent(button.dataset.id)}`, { method: 'PATCH', body: { status: button.dataset.status } }); await refresh(); if (result.item && $('#business-detail-dialog').open && state.businessRecord?.id === result.item.id) { state.businessRecord = result.item; renderBusinessDetail(result.item); } toast(button.dataset.status === 'done' ? 'Attività completata. Resta disponibile nello storico.' : 'Attività riaperta.'); }); }
}

function stopBusinessSearch() { clearTimeout(businessSearchTimer); businessSearchController?.abort(); businessSearchController = null; businessSearchRequest++; }
function openBusinessSearch() {
  stopBusinessSearch(); $('#business-global-search').value = ''; replace('#business-search-results'); notice('#business-search-error', ''); $('#business-search-status').textContent = 'Scrivi una parola per trovare un’attività, un cliente o un documento.';
  openDialog('#business-search-dialog'); $('#business-global-search').focus();
}
async function searchBusinessRecords(query, request) {
  if (request !== businessSearchRequest || !$('#business-search-dialog').open) return;
  businessSearchController = new AbortController(); $('#business-search-status').textContent = 'Cerchiamo nelle tue attività…'; notice('#business-search-error', '');
  try {
    const result = await api(`/api/business/records?q=${encodeURIComponent(query)}&limit=20`, { signal: businessSearchController.signal });
    if (request !== businessSearchRequest || !$('#business-search-dialog').open) return;
    const items = result.items || []; $('#business-search-status').textContent = items.length ? `${result.total} ${result.total === 1 ? 'attività trovata' : 'attività trovate'}${result.total > items.length ? ' · primi 20 risultati' : ''}` : 'Nessuna attività trovata. Prova un titolo, un cliente o una parola nelle note.';
    replace('#business-search-results', ...items.map(record => {
      const button = actionButton('', 'business-search-result', 'ghost', 'arrow', { id: record.id, service: record.service_id }); button.classList.add('business-search-result');
      const content = el('span'); content.append(el('small', '', `${businessService(record.service_id)?.name || record.service_name || 'Attività'} · ${businessStatusLabels[record.status] || 'Da fare'}`), el('strong', '', record.title));
      if (record.contact) content.append(el('span', 'small-text', record.contact)); button.prepend(content); return button;
    }));
  } catch (error) { if (error.name !== 'AbortError' && request === businessSearchRequest && $('#business-search-dialog').open) { replace('#business-search-results'); $('#business-search-status').textContent = ''; notice('#business-search-error', error.message); } }
}

function currentBriefing() {
  return state.data?.briefing;
}
function renderMorning() {
  const real = state.data.briefing;
  const briefing = currentBriefing();
  if (!briefing) return;
  const priorities = briefing.priorities || [];
  $('#briefing-title').textContent = 'Il riepilogo';
  $('#briefing-period').textContent = (briefing.period?.label || 'IL TUO RIEPILOGO').toLocaleUpperCase('it');
  $('#briefing-summary').textContent = briefing.summary || 'Il riepilogo arriverà dopo il primo controllo della posta.';
  const nextAppointment = state.data.agenda?.next_event;
  $('#briefing-next-appointment').hidden = !nextAppointment;
  $('#briefing-next-appointment').textContent = nextAppointment ? `Il prossimo appuntamento: ${nextAppointment.time_label} · ${nextAppointment.title}` : '';
  $('#briefing-freshness').textContent = briefing.last_checked_at ? `Ultimo controllo: ${prettyDate(briefing.last_checked_at)}` : briefing.stale ? 'Da aggiornare' : 'In attesa del primo controllo';
  $('#briefing-freshness').classList.toggle('stale', Boolean(briefing.stale));
  const counts = briefing.counts || {};
  const checked = Boolean(briefing.last_checked_at);
  const stats = [[checked ? counts.pending ?? priorities.length : '—', 'Risposte da rivedere'], [checked ? counts.high_priority ?? 0 : '—', 'Da guardare per prime'], [state.data.agenda?.items?.length || 0, 'Appuntamenti oggi']];
  replace('#briefing-stats', ...stats.map(([value, label]) => { const card = el('div', 'briefing-stat'); card.append(el('strong', '', value), el('small', '', label)); return card; }));
  const suggestion = real?.suggested_action;
  const suggestionNodes = [];
  if (suggestion) {
    const mark = el('span', 'icon-box peach'); mark.append(icon('spark'));
    const copy = el('div', 'suggestion-copy'); copy.append(el('strong', '', 'Il prossimo passo'), el('p', '', suggestion.reason || suggestion.label));
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
  const signature = JSON.stringify({ priorities, contacts: briefing.contacts, summary: briefing.summary, status: briefing.status, last_checked_at: briefing.last_checked_at });
  if (signature === state.briefingSignature) return;
  state.briefingSignature = signature;
  if (!priorities.length) {
    const empty = el('div', 'briefing-empty'); empty.append(icon(real?.status === 'not_started' ? 'mail' : real?.status === 'checking' ? 'clock' : 'check'), el('h3', '', real?.legacy_pending_count ? 'Le bozze precedenti restano nello storico.' : real?.status === 'not_started' ? 'Le tue priorità arriveranno qui.' : real?.status === 'checking' ? 'Spazelia sta preparando il riepilogo.' : 'Nessuna risposta da rivedere qui.'), el('p', '', real?.legacy_pending_count ? 'Un nuovo controllo verificherà quali richieste aspettano ancora una risposta. Puoi consultare le bozze precedenti nelle attività.' : real?.status === 'not_started' ? 'Richieste, contatti e bozze da rivedere, dopo il primo controllo.' : 'Il riepilogo riguarda i messaggi controllati: aggiornalo per verificare nuove richieste.'));
    if (real?.legacy_pending_count) { const link = el('a', 'text-link', 'Consulta le attività'); link.href = '#activity'; empty.append(link); }
    replace('#priority-list', empty);
  } else {
    replace('#priority-list', ...priorities.map((item, index) => {
      const row = el('article', 'priority-item'); row.dataset.draftId = item.draft_id || `priority-${index}`;
      const rank = el('span', 'priority-rank', String(index + 1).padStart(2, '0'));
      const body = el('div', 'priority-main');
      const meta = el('div', 'priority-meta'); meta.append(el('strong', '', item.client), el('span', `priority-badge ${item.priority === 'high' ? 'high' : 'normal'}`, item.priority_label || (item.priority === 'high' ? 'Da guardare prima' : 'A seguire')));
      if (item.received_at) meta.append(el('span', 'small-text', `Ha scritto ${prettyDate(item.received_at)}`));
      body.append(meta, el('h3', 'priority-subject', item.subject));
      if (item.source_excerpt) { const context = el('p', 'priority-context', item.source_excerpt.length > 220 ? `${item.source_excerpt.slice(0, 220).trim()}…` : item.source_excerpt); body.append(context); }
      body.append(el('p', 'small-text', item.reason), el('p', 'priority-next-step', item.next_step || 'Rivedi la bozza e controlla i dettagli prima di rispondere.'));
      const draft = el('details', 'priority-inline-draft');
      draft.append(el('summary', '', 'La bozza è già pronta'));
      draft.append(el('pre', 'draft-text', item.draft));
      if (item.draft_id) {
        const actions = el('div', 'card-actions'); actions.append(actionButton('Copia la bozza', 'copy-priority', 'secondary', undefined, { id: item.draft_id }), actionButton('Segna come rivista', 'approve-draft', 'primary', 'check', { id: item.draft_id })); draft.append(actions);
        draft.append(el('p', 'small-text', 'La revisione resta in Spazelia. L’email non viene inviata.'));
      }
      body.append(draft); row.append(rank, body); return row;
    }));
  }
  $('.contacts-section').hidden = !(briefing.contacts || []).length;
  const checkedBeforePeriod = briefing.last_checked_at && briefing.period?.start && new Date(briefing.last_checked_at) < new Date(briefing.period.start);
  $('#contacts-period').textContent = checkedBeforePeriod ? 'L’ultimo controllo precede questo periodo. Aggiorna per verificare i nuovi arrivi.' : `${briefing.period?.start ? `Da ${prettyDate(briefing.period.start)}` : 'Periodo del riepilogo'}${briefing.last_checked_at ? ` al controllo del ${prettyDate(briefing.last_checked_at)}` : ''}.`;
  replace('#contact-snapshot', ...(briefing.contacts || []).map(contact => {
    const card = el('div', 'contact-status'); const status = contact.status === 'wrote' ? 'wrote' : contact.status === 'no_messages' ? 'quiet' : 'unverified'; card.classList.add(status);
    card.append(el('strong', '', contact.name), el('span', '', status === 'wrote' ? 'Ha scritto' : status === 'quiet' ? 'Nessuna email nel periodo controllato' : 'Da verificare con un nuovo controllo'));
    if (contact.last_received_at && status === 'wrote') card.append(el('small', '', prettyDate(contact.last_received_at)));
    return card;
  }));
  if (briefing.coverage_note) $('#contact-snapshot').append(el('p', 'small-text', briefing.coverage_note));
}

function renderWatches() {
  const summary = state.data.watches || { items: [] };
  const contacts = state.data.service.priority_contacts || [];
  const available = isRealMailbox() && state.data.connection.status === 'connected' && contacts.length;
  $('#reply-watch-form-panel').hidden = !available;
  const contactSelect = $('#watch-contact');
  const selected = contactSelect.value;
  if (JSON.stringify(contacts) !== state.watchContactsSignature) {
    contactSelect.replaceChildren(...contacts.map(contact => { const option = el('option', '', contact.name); option.value = contact.email; return option; }));
    if (contacts.some(contact => contact.email === selected)) contactSelect.value = selected;
    state.watchContactsSignature = JSON.stringify(contacts);
  }
  const today = state.data.agenda.date;
  $('#watch-day').min = today;
  $('#watch-day').max = new Date(new Date(`${today}T12:00:00Z`).valueOf() + 30 * 86400000).toISOString().slice(0, 10);
  if (!$('#watch-day').value) $('#watch-day').value = today;
  const signature = JSON.stringify(summary);
  if (signature === state.watchSignature) return;
  state.watchSignature = signature;
  const watchCard = item => {
    const found = item.status === 'matched';
    const card = el('article', `reply-watch${found ? ' reply-watch--matched' : ''}`);
    card.dataset.watchId = item.id;
    const text = el('div');
    text.append(el('strong', '', found ? `${item.name} ha scritto · da guardare subito` : `${item.name} · ${prettyDate(`${item.day}T12:00:00Z`, false)}`));
    const detail = found ? `È arrivata un’email ${prettyDate(item.match.received_at)}. Aprila per verificare la risposta che aspettavi.` : item.status === 'expired' ? 'Il giorno è concluso. Non abbiamo un riscontro verificato: il controllo copre una selezione di messaggi.' : item.contact_active === false ? 'Il contatto non è più seguito. Riaggiungilo nelle preferenze o chiudi l’avviso.' : !summary.monitoring_active ? 'Avviso salvato. Attiva o riprendi la segreteria per controllare la casella.' : summary.next_check_at ? `Risposta da seguire. Prossimo controllo: ${prettyDate(summary.next_check_at)}.` : 'Risposta da seguire. Controllo in corso.';
    text.append(el('p', 'small-text', detail));
    const actions = el('div', 'watch-actions');
    if (found) {
      const href = mailboxMessageURL(item.match);
      if (href) { const link = el('a', 'button secondary', `Apri ${mailProviderLabel()}`); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link); }
    }
    actions.append(actionButton(found ? 'Segna come vista' : 'Chiudi avviso', 'dismiss-watch', 'ghost', undefined, { id: item.id }));
    card.append(text, actions); return card;
  };
  replace('#reply-watch-list', ...(summary.items || []).filter(item => item.status === 'matched').map(watchCard));
  replace('#reply-watch-waiting-list', ...(summary.items || []).filter(item => item.status !== 'matched').map(watchCard));
}

const MAIL_PRESETS = [
  { id: 'gmail', label: 'Gmail', kind: 'oauth', symbol: 'G', configured: false },
  { id: 'outlook', label: 'Outlook / Microsoft 365', kind: 'oauth', symbol: 'O', configured: false },
  { id: 'icloud', label: 'iCloud Mail', kind: 'imap', symbol: 'i', configured: true, host: 'imap.mail.me.com', setup_note: 'Crea una password specifica per app nel tuo Apple Account, poi inseriscila qui.', help: 'https://support.apple.com/it-it/102654' },
  { id: 'yahoo', label: 'Yahoo Mail', kind: 'imap', symbol: 'Y', configured: true, host: 'imap.mail.yahoo.com', setup_note: 'Usa una password per app Yahoo, creata nelle impostazioni di sicurezza del tuo account.', help: 'https://help.yahoo.com/kb/SLN15241.html' },
  { id: 'aruba', label: 'Aruba', kind: 'imap', symbol: 'A', configured: true, host: 'imaps.aruba.it', setup_note: 'Collega una casella email Aruba standard. Le caselle PEC e i servizi Exchange usano impostazioni diverse.' },
  { id: 'libero', label: 'Libero Mail', kind: 'imap', symbol: 'L', configured: true, host: 'imapmail.libero.it', setup_note: 'Inserisci le credenziali della tua casella Libero. Se usi la verifica in due passaggi, crea una password per app.' },
  { id: 'imap', label: 'Altra casella IMAP', kind: 'imap', symbol: '@', configured: true, setup_note: 'Chiedi al tuo provider il server IMAP e verifica che l’accesso IMAP sia abilitato. La connessione usa TLS sulla porta 993.' }
];
function activeMailProvider() { const connection = state.data?.connection || {}; return connection.provider === 'imap' ? connection.mail_provider || 'imap' : connection.provider; }
function mailProvider(id) { const preset = MAIL_PRESETS.find(item => item.id === id); const current = state.providers.find(item => item.id === id); return { ...preset, ...current }; }
function mailProviderLabel() { const id = activeMailProvider(); return id === 'demo' ? 'demo' : mailProvider(id).label || 'email'; }
function isRealMailbox() { return ['gmail', 'outlook', 'imap'].includes(state.data?.connection?.provider); }
async function refreshProviders() {
  try { const result = await api('/api/mail/providers'); state.providers = Array.isArray(result.providers) ? result.providers.map(item => ({ ...MAIL_PRESETS.find(preset => preset.id === item.id), ...item })) : []; }
  catch (_) { if (!state.providers.length) state.providers = MAIL_PRESETS.map(item => ({ ...item, configured: item.kind === 'imap' || item.id === 'gmail' && Boolean(state.gmail?.configured) })); }
}
function mailboxMessageURL(match) {
  const id = activeMailProvider();
  if (id === 'gmail') { const url = new URL('https://mail.google.com/mail/'); const email = state.data.connection.label || ''; if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) url.searchParams.set('authuser', email); url.hash = `all/${encodeURIComponent(match.thread_id || match.source_id || '')}`; return url.href; }
  const links = { outlook: 'https://outlook.office.com/mail/', icloud: 'https://www.icloud.com/mail/', yahoo: 'https://mail.yahoo.com/', aruba: 'https://webmail.aruba.it/', libero: 'https://mail.libero.it/' };
  return links[id] || null;
}
function providerChooser() {
  const grid = el('div', 'provider-grid mail-provider-grid');
  const items = state.providers.length ? state.providers : MAIL_PRESETS;
  for (const provider of items) {
    const connected = activeMailProvider() === provider.id && state.data.connection.status === 'connected';
    const card = el('article', `provider-card${connected ? ' selected' : ''}`); const symbol = el('span', 'provider-symbol', provider.symbol || provider.label?.[0] || '@'); symbol.setAttribute('aria-hidden', 'true'); card.append(symbol, el('h3', '', provider.label));
    const description = provider.kind === 'oauth' ? provider.id === 'gmail' ? 'Autorizza con il tuo account Google.' : 'Per account personali e aziendali Microsoft.' : provider.id === 'imap' ? 'Per la posta del tuo studio e altri provider.' : provider.id === 'icloud' || provider.id === 'yahoo' ? 'Con una password dedicata alle app.' : 'Con le credenziali della tua casella.';
    card.append(el('p', '', description));
    if (connected) card.append(statusPill('connected'));
    else if (!provider.configured) card.append(el('span', 'status-pill neutral', 'Configurazione necessaria'));
    const label = connected ? $('#wizard-dialog').open ? `Usa ${provider.label}` : 'Casella collegata · Preferenze' : !provider.configured ? state.data.user?.role === 'owner' ? `Prepara il collegamento ${provider.id === 'outlook' ? 'Microsoft' : provider.label}` : 'Scopri come collegarla' : `Collega ${provider.id === 'outlook' ? 'Outlook' : provider.id === 'imap' ? 'la casella' : provider.label}`;
    card.append(actionButton(label, connected ? 'use-mail-provider' : !provider.configured ? 'show-mail-setup' : 'connect-mail-provider', 'secondary', 'link', { provider: provider.id })); grid.append(card);
  }
  if (!state.data.real_data_only) { const card = el('article', `provider-card${activeMailProvider() === 'demo' ? ' selected' : ''}`); const symbol = el('span', 'provider-symbol', 'D'); symbol.setAttribute('aria-hidden', 'true'); card.append(symbol, el('h3', '', 'Casella demo'), el('p', '', 'Email di esempio, senza collegare la tua posta.'), actionButton('Usa la demo', 'connect-demo', 'secondary', 'link')); grid.append(card); }
  return grid;
}
function renderMailConnections() {
  const connection = state.data.connection; const connected = isRealMailbox(); const copy = el('div', 'mailbox-copy');
  copy.append(statusPill(connection.status || 'disconnected'), el('p', 'mailbox-address', connected ? connection.label || mailProviderLabel() : connection.provider === 'demo' ? 'Casella demo' : 'Nessuna casella collegata'), el('p', 'small-text', connected ? `${mailProviderLabel()} · I controlli seguono i contatti che scegli.` : 'Scegli il provider qui sotto. Puoi organizzare l’agenda anche prima di collegare la posta.'));
  const actions = el('div', 'card-actions');
  if (connected) { actions.append(actionButton(connection.status === 'expired' ? 'Ricollega' : state.data.service.status === 'inactive' ? 'Attiva la segreteria' : 'Preferenze del servizio', connection.status === 'expired' ? 'reconnect' : state.data.service.status === 'inactive' ? 'open-wizard' : 'edit-preferences', 'primary', 'arrow'), actionButton('Scollega la casella', 'disconnect-mail', 'ghost')); }
  copy.append(actions); replace('#mailbox-connection', copy);
  const signature = JSON.stringify({ providers: state.providers, connection, role: state.data.user?.role, wizard: $('#wizard-dialog').open });
  if (signature !== state.providerSignature) { replace('#mail-provider-list', ...providerChooser().children); state.providerSignature = signature; }
  $('#mail-provider-setup').hidden = !state.mailSetup; const setupSignature = JSON.stringify([state.mailSetup, state.providers, state.data.user?.role]); if (state.mailSetup && setupSignature !== state.mailSetupSignature) { replace('#mail-provider-setup', mailSetupGuide(state.mailSetup)); state.mailSetupSignature = setupSignature; }
}
function mailSetupGuide(id) {
  const provider = mailProvider(id); const guide = el('div', 'boundary-note mail-setup-guide');
  if (state.data.user && state.data.user.role !== 'owner') { guide.append(el('h3', '', `Il collegamento ${provider.label} è in preparazione.`), el('p', '', 'L’amministratore deve completare l’autorizzazione del sito. Intanto puoi scegliere una casella IMAP, organizzare l’agenda e completare il profilo della tua attività.'), actionButton('Verifica disponibilità', 'check-mail-setup', 'secondary', 'refresh', { provider: id })); return guide; }
  const google = id === 'gmail'; guide.append(el('h3', '', `Prepara ${provider.label}. Una sola volta.`)); const steps = el('ol');
  const first = el('li', '', google ? 'In Google Cloud abilita Gmail API e configura il consenso con il solo permesso https://www.googleapis.com/auth/gmail.readonly. Aggiungi la tua email tra gli utenti di test e crea un client OAuth di tipo Applicazione web.' : 'In Microsoft Entra registra un’app con account aziendali e personali Microsoft. Aggiungi i permessi delegati Microsoft Graph Mail.Read e User.Read, con offline_access per mantenere l’accesso, poi crea un segreto client.');
  const link = el('a', 'text-link', google ? 'Apri Google Cloud' : 'Apri Microsoft Entra'); link.href = google ? 'https://console.cloud.google.com/apis/credentials' : 'https://entra.microsoft.com/'; link.target = '_blank'; link.rel = 'noopener noreferrer'; first.append(document.createTextNode(' '), link);
  const second = el('li', '', 'Aggiungi questo URI di reindirizzamento come applicazione web:'); const callback = el('code', '', provider.redirect_uri || `${location.origin}/api/${google ? 'gmail' : 'outlook'}/oauth/callback`); second.append(el('br'), callback);
  const third = el('li', '', google ? 'Nelle variabili Railway salva FILO_GOOGLE_CLIENT_ID e FILO_GOOGLE_CLIENT_SECRET. Imposta FILO_GOOGLE_REDIRECT_URI con l’URI qui sopra, poi distribuisci la modifica.' : 'Nelle variabili Railway salva FILO_MICROSOFT_CLIENT_ID e FILO_MICROSOFT_CLIENT_SECRET. Imposta FILO_MICROSOFT_REDIRECT_URI con l’URI qui sopra, poi distribuisci la modifica.');
  const railway = el('a', 'text-link', 'Apri le variabili Railway'); railway.href = 'https://railway.com/dashboard'; railway.target = '_blank'; railway.rel = 'noopener noreferrer'; third.append(document.createTextNode(' '), railway);
  steps.append(first, second, third); guide.append(steps, el('p', 'small-text', 'Il collegamento diventa disponibile dopo la configurazione. Nessuna casella risulta collegata fino alla tua autorizzazione.'), actionButton('Verifica configurazione', 'check-mail-setup', 'secondary', 'refresh', { provider: id })); return guide;
}
function clearImapSecret() { const input = $('#imap-password'); if (input) input.value = ''; }
function openImapConnection(id) {
  const provider = mailProvider(id); state.imapProvider = id; $('#imap-form').reset(); clearImapSecret(); notice('#imap-error', ''); $('#imap-title').textContent = `Collega ${provider.label}.`; $('#imap-guidance').textContent = provider.setup_note || MAIL_PRESETS.find(item => item.id === id)?.setup_note || 'Inserisci le credenziali della casella.'; $('#imap-host-field').hidden = id !== 'imap'; $('#imap-host').required = id === 'imap'; $('#imap-host').value = provider.host || ''; $('#imap-email').value = isRealMailbox() ? state.data.connection.label || '' : ''; $('#imap-advanced').open = false;
  const help = provider.help || MAIL_PRESETS.find(item => item.id === id)?.help; $('#imap-help-link').hidden = !help; if (help) $('#imap-help-link').href = help;
  openDialog('#imap-dialog'); $('#imap-email').focus();
}
function confirmMailChange(provider = null) {
  state.mailChange = { provider }; $('#mail-confirm-title').textContent = provider ? 'Cambiare casella?' : 'Scollegare la casella?'; $('#mail-confirm-description').textContent = provider ? `Stai passando da ${mailProviderLabel()} a ${mailProvider(provider).label}. Dopo il collegamento, i controlli della vecchia casella si fermano. Dovrai rivedere i contatti e autorizzare di nuovo la segreteria. Lo storico resta consultabile.` : 'I controlli automatici si fermano e le credenziali vengono rimosse da Spazelia. Lo storico resta consultabile. Potrai collegare di nuovo la casella e autorizzare il servizio.'; $('#mail-confirm-button').textContent = provider ? 'Continua' : 'Scollega la casella'; openDialog('#mail-confirm-dialog');
}
async function connectMailProvider(id) {
  const provider = mailProvider(id);
  if (provider.kind === 'imap') { openImapConnection(id); return; }
  if (!provider.configured) { state.mailSetup = id; renderMailConnections(); if (state.wizard?.step === 2) { state.wizard.mailSetup = id; renderWizard(); } return; }
  const result = await api(`/api/${id}/oauth/start`, { method: 'POST', body: {} }); if (!result.authorization_url) throw new Error(`Il collegamento ${provider.label} non è ancora disponibile.`); const target = new URL(result.authorization_url, location.origin); const expectedHost = id === 'gmail' ? 'accounts.google.com' : 'login.microsoftonline.com'; if (target.protocol !== 'https:' || target.hostname !== expectedHost) throw new Error('Il collegamento ricevuto non è valido.');
  try { sessionStorage.setItem('filo-pending-wizard', JSON.stringify(state.wizard && $('#wizard-dialog').open ? { preferences: state.wizard.preferences, edit: state.wizard.edit, createdAt: Date.now() } : { reconnect: true, createdAt: Date.now() })); } catch (_) { /* The return still works; only the wizard step is not restored. */ } location.assign(target.href);
}

function renderAgenda() {
  const agenda = state.agendaView || state.data.agenda;
  if (!agenda) return;
  $('#agenda-source').textContent = 'Orari italiani · appuntamenti aggiunti da te.';
  $('#agenda-export').href = `/api/agenda/export?date=${encodeURIComponent(agenda.date)}`;
  if (document.activeElement !== $('#agenda-view-date')) $('#agenda-view-date').value = agenda.date;
  if (!$('#agenda-event-date').value) $('#agenda-event-date').value = agenda.date;
  const signature = JSON.stringify(agenda);
  if (signature === state.agendaSignature) return;
  state.agendaSignature = signature;
  if (!agenda.items.length) {
    const empty = el('div', 'agenda-empty'); empty.append(icon('calendar'), el('h3', '', 'Nessun appuntamento aggiunto.'), el('p', '', 'Gli impegni che aggiungi compaiono qui, in ordine di orario.'));
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
  if (suggestedPreferences) {
    const suggested = (suggestedPreferences.priority_contacts || []).map(contact => ({ name: contact.name, email: contact.email }));
    state.wizard.preferences = { priority_contacts: suggested.length ? suggested : [{ name: '', email: '' }], hour: suggestedPreferences.hour ?? 9, minute: suggestedPreferences.minute ?? 0, timezone: 'Europe/Rome' };
  }
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
  replace('#wizard-progress', ...['Service', 'Casella', 'Preferenze', 'Anteprima', 'Consenso'].map((label, index) => { const step = index + 1; const item = el('li', step === wizard.step ? 'current' : step < wizard.step ? 'complete' : ''); item.append(el('span', 'wizard-step-number', step < wizard.step ? '✓' : step), el('span', 'wizard-step-label', label === 'Service' ? 'Servizio' : label)); if (step === wizard.step) item.setAttribute('aria-current', 'step'); return item; }));
  const body = $('#wizard-body'); body.replaceChildren();
  const footer = $('#wizard-footer'); footer.replaceChildren();
  const back = actionButton(wizard.step === 1 || wizard.edit && wizard.step === 3 ? 'Annulla' : 'Indietro', wizard.step === 1 || wizard.edit && wizard.step === 3 ? 'close-wizard' : 'wizard-back', 'ghost'); footer.append(back);
  const title = (text, description) => { const heading = el('h2', '', text); heading.id = 'wizard-title'; body.append(heading, el('p', 'step-intro', description)); };
  if (wizard.step === 1) {
    title('Una segreteria, alle tue condizioni.', 'Un compito preciso: leggere le email dei contatti prioritari e preparare bozze locali. Tu mantieni l’ultima parola.');
    const choice = el('div', 'step-choice'); const mark = el('span', 'icon-box teal'); mark.append(icon('mail')); const copy = el('div'); copy.append(el('h3', '', 'Segreteria email'), el('p', '', 'Priorità, risposte abbozzate e un giro ogni giorno.')); choice.append(mark, copy, icon('check')); body.append(choice);
    body.append(el('p', 'step-disclosure', 'Le bozze restano in Spazelia. Questo servizio non invia messaggi e non crea bozze nella casella del provider.'));
    if (state.data.ai?.enabled && state.data.ai?.configured) body.append(el('p', 'step-disclosure', 'Questo ambiente usa un servizio AI esterno solo sulle email dimostrative, durante le esecuzioni attivate. L’anteprima e le email reali usano regole locali.'));
    footer.append(actionButton('Iniziamo', 'wizard-next', 'primary', 'arrow'));
  } else if (wizard.step === 2) {
    title('Collega la tua posta.', 'Scegli il provider. Una casella, i contatti che contano per te.');
    body.append(providerChooser(), el('p', 'step-disclosure', 'Spazelia legge la posta e prepara bozze locali. I controlli automatici partono dopo il tuo consenso.'));
    if (wizard.mailSetup) body.append(mailSetupGuide(wizard.mailSetup));
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
    title('Ecco cosa preparerebbe Spazelia.', isRealMailbox() ? 'Un’anteprima sulle email reali dei contatti scelti. Le bozze restano nella piattaforma: nessuna email viene inviata.' : 'Un’anteprima con email di esempio. È una simulazione deterministica senza AI reale; l’attivazione parte solo con il tuo consenso.');
    body.append(el('div', 'preview-intro', typeof wizard.preview?.summary === 'string' ? wizard.preview.summary : `${wizard.preview?.items?.length || 0} email selezionate per i contatti scelti.`));
    const items = wizard.preview?.items || [];
    if (items.length) items.forEach(item => body.append(renderDraft(item, false)));
    else body.append(el('p', 'helper-text', 'Nessuna email corrisponde ai contatti prioritari in questa anteprima. Puoi aggiornare i contatti o mantenere la configurazione per le prossime email.'));
    body.append(el('p', 'step-disclosure', 'L’anteprima non attiva il servizio. Le risposte mostrate sono suggerimenti da controllare e correggere prima di usarli.'));
    footer.append(actionButton(wizard.edit ? 'Salva le preferenze' : 'Rivedi e autorizza', wizard.edit ? 'wizard-save-preferences' : 'wizard-next', 'primary', 'arrow'));
  } else if (wizard.step === 5) {
    title('Il via libera è tuo.', 'Il servizio partirà soltanto dopo questi consensi espliciti. Potrai metterlo in pausa o disattivarlo quando vuoi.');
    body.append(mandateSummary());
    const read = el('label', 'checkbox-row'); const readInput = el('input'); readInput.type = 'checkbox'; readInput.id = 'authorize-read'; readInput.checked = wizard.authorizedRead; read.append(readInput, el('span', '', isRealMailbox() ? 'Autorizzo Spazelia a leggere le email dei contatti prioritari nella casella collegata. Il servizio limita le ricerche ai contatti scelti.' : 'Autorizzo Spazelia a leggere le email dei contatti prioritari nella casella demo.'));
    const draft = el('label', 'checkbox-row'); const draftInput = el('input'); draftInput.type = 'checkbox'; draftInput.id = 'authorize-draft'; draftInput.checked = wizard.authorizedDraft; draft.append(draftInput, el('span', '', 'Autorizzo la preparazione di bozze locali in Spazelia, che dovrò rivedere prima di usare.'));
    body.append(read, draft);
    if (state.data.connection.provider === 'demo' && state.data.ai?.enabled && state.data.ai?.configured) body.append(el('p', 'step-disclosure', 'Nelle esecuzioni demo autorizzate, il servizio AI esterno configurato può elaborare le email fittizie. Nessun contenuto delle caselle reali viene condiviso.'));
    const noSend = el('div', 'no-send'); noSend.append(icon('shield'), document.createTextNode('Nessuna autorizzazione all’invio. Spazelia non invia email.')); body.append(noSend);
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
  const values = [['Servizio', 'Segreteria email'], ['Casella', isRealMailbox() ? `${mailProviderLabel()} · email reali` : 'Demo · dati fittizi'], ['Ogni giorno', `${String(preferences.hour).padStart(2, '0')}:${String(preferences.minute).padStart(2, '0')} · Europe/Rome`], ['Priorità', `${preferences.priority_contacts.length} contatti scelti`], ['Permessi', 'Lettura e bozze locali · nessun invio']];
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
    const bar = el('div', 'approval-bar'); const copy = el('p', '', 'La revisione viene registrata solo in Spazelia. Nessun invio alla casella.'); bar.append(copy);
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

async function handleAction(button, clickCount = 1) {
  const action = button.dataset.action;
  if (action === 'open-business' || action === 'open-business-agenda' || action.startsWith('business-')) { await handleBusinessAction(button); return; }
  if (action === 'review-priority') {
    const row = $$('.priority-item').find(item => item.dataset.draftId === button.dataset.id) || $('.priority-item');
    if (row) { $('details', row).open = true; row.scrollIntoView({ behavior: 'smooth', block: 'center' }); $('summary', row).focus({ preventScroll: true }); }
    return;
  }
  if (action === 'copy-priority') {
    const item = currentBriefing()?.priorities?.find(item => item.draft_id === button.dataset.id);
    if (item?.draft) {
      try { await navigator.clipboard.writeText(item.draft); toast('Bozza copiata. Puoi rivederla nella tua casella prima di inviarla.'); }
      catch (_) { toast('Il browser non ha permesso la copia. Apri la bozza e copia il testo selezionandolo.', true); }
    }
    return;
  }
  if (action === 'open-connections') { location.hash = 'connections'; return; }
  if (action === 'show-mail-setup' || action === 'show-gmail-setup') { const id = button.dataset.provider || 'gmail'; state.mailSetup = id; renderMailConnections(); if (state.wizard?.step === 2 && $('#wizard-dialog').open) { state.wizard.mailSetup = id; renderWizard(); } else $('#mail-provider-setup').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); return; }
  if (action === 'close-imap') { closeDialog('#imap-dialog'); return; }
  if (action === 'close-mail-confirm') { state.mailChange = null; closeDialog('#mail-confirm-dialog'); return; }
  if (action === 'use-mail-provider') { if ($('#wizard-dialog').open && state.wizard) { state.wizard.step = 3; renderWizard(); } else startWizard(state.data.service.status !== 'inactive'); return; }
  if (action === 'disconnect-mail' || action === 'disconnect-gmail') { confirmMailChange(); return; }
  if (action === 'connect-mail-provider' || action === 'connect-gmail') { const id = button.dataset.provider || 'gmail'; if (isRealMailbox() && activeMailProvider() !== id) { confirmMailChange(id); return; } }
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
  if (action === 'wizard-company') { closeDialog('#wizard-dialog'); showPage('company'); window.scrollTo({ top: 0, behavior: 'auto' }); $('#company-name').focus(); return; }
  if (action === 'wizard-finish' || action === 'wizard-finish-reconnect') { closeDialog('#wizard-dialog'); location.hash = 'overview'; return; }
  if (action === 'add-contact') { if (state.wizard.preferences.priority_contacts.length < 4) { state.wizard.preferences.priority_contacts.push({ name: '', email: '' }); renderContactRows(); const input = $('#wizard-contacts .contact-row:last-child input'); input?.focus(); } return; }
  if (action === 'remove-contact') { if (state.wizard.preferences.priority_contacts.length > 1) { state.wizard.preferences.priority_contacts.splice(Number(button.dataset.index), 1); renderContactRows(); } return; }
  if (action === 'wizard-back' || action === 'wizard-next') {
    // The footer is re-rendered under the pointer: the second click of a double click must not skip a step.
    if (clickCount > 1) return;
    if (action === 'wizard-back') { if (state.wizard.step > 1) state.wizard.step--; }
    else { if (state.wizard.step === 2 && state.data.connection.status !== 'connected') return; state.wizard.step++; }
    renderWizard(); return;
  }
  const inWizard = $('#wizard-dialog').open;
  if (inWizard) notice('#wizard-error', '');
  await withBusy(button, async () => {
    try {
      if (action === 'check-mail-setup' || action === 'check-gmail-setup') { const id = button.dataset.provider || 'gmail'; await refreshProviders(); renderMailConnections(); if (state.wizard?.step === 2 && inWizard) renderWizard(); toast(mailProvider(id).configured ? `Configurazione pronta. Ora puoi collegare ${mailProvider(id).label}.` : `Il collegamento ${mailProvider(id).label} richiede ancora la configurazione del sito.`); }
      else if (action === 'confirm-mail-change') { const change = state.mailChange; state.mailChange = null; closeDialog('#mail-confirm-dialog'); if (change?.provider) await connectMailProvider(change.provider); else if (change) { await api('/api/mail/disconnect', { method: 'POST', body: {} }); await refresh(); toast('Casella scollegata. I controlli automatici sono fermi.'); } }
      else if (action === 'connect-mail-provider' || action === 'connect-gmail') await connectMailProvider(button.dataset.provider || 'gmail');
      else if (action === 'confirm-watch') { if (!state.watchSuggestion) return; await api('/api/watches', { method: 'POST', body: state.watchSuggestion }); state.watchSuggestion = null; $('#chat-suggestion').hidden = true; await refresh(); toast('Avviso salvato. Lo troverai tra le cose da fare.'); }
      else if (action === 'dismiss-watch') { await api(`/api/watches/${encodeURIComponent(button.dataset.id)}`, { method: 'DELETE' }); await refresh(); toast('Promemoria chiuso.'); }
      else if (action === 'connect-demo') { await api('/api/connection/demo', { method: 'POST', body: {} }); await refresh(); renderWizard(); }
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
      else if (action === 'approve-draft') { await api(`/api/drafts/${encodeURIComponent(button.dataset.id)}/approve`, { method: 'POST', body: {} }); await refresh(); if ($('#detail-dialog').open && state.currentRun) await openRun(state.currentRun.id); toast('Revisione registrata in Spazelia. Nessun messaggio inviato.'); }
      else if (action === 'delete-agenda-event') { await api(`/api/agenda/${encodeURIComponent(button.dataset.id)}`, { method: 'DELETE' }); await refresh(); toast('Appuntamento rimosso dall’agenda.'); }
      else if (action.startsWith('simulate-')) { const kind = action.replace('simulate-', ''); await api('/api/demo/failure', { method: 'POST', body: { kind } }); await refresh(); toast({ temporary: 'Imprevisto temporaneo preparato per la prossima esecuzione demo.', expired: 'Simulazione pronta: la connessione demo scadrà alla prossima esecuzione.', clear: 'Connessione demo ripristinata.' }[kind]); }
    } catch (error) { if (inWizard) notice('#wizard-error', error.message); else toast(error.message, true); }
  });
}

document.addEventListener('click', event => { const button = event.target.closest('[data-action]'); if (button && !button.disabled) handleAction(button, event.detail).catch(error => toast(error.message, true)); });
$('#business-search-button kbd').textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : 'Ctrl K';
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && $('#business-search-dialog').open) { event.preventDefault(); closeDialog('#business-search-dialog'); return; }
  if (typeof event.key !== 'string' || event.key.toLowerCase() !== 'k' || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.repeat) return;
  if (event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])') || $('dialog[open]') || !state.data) return;
  event.preventDefault(); openBusinessSearch();
});
$$('dialog').forEach(dialog => dialog.addEventListener('close', () => {
  if (dialog.id === 'business-search-dialog') stopBusinessSearch();
  if (dialog.id === 'business-form-dialog') {
    businessConversion = null; businessRepetition = null; businessFormService = null;
  }
  if (dialog.id === 'business-repeat-dialog') businessRepeatingSource = null;
  const previous = dialogFocus.get(dialog); const current = $('dialog[open]');
  if (previous?.isConnected && (!current || current.contains(previous)) && previous.getClientRects().length) previous.focus({ preventScroll: true });
  else if (!current) $('#main-content').focus({ preventScroll: true });
}));
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
$('#business-record-form').addEventListener('submit', saveBusinessRecord);
$('#business-repeat-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity() || !businessRepeatingSource) return;
  const source = businessRepeatingSource; const nextDate = $('#business-repeat-date').value; notice('#business-repeat-error', '');
  await withBusy($('button[type="submit"]', form), async () => {
    try {
      const result = await api(`/api/business/records/${encodeURIComponent(source.id)}/repetition?next_date=${encodeURIComponent(nextDate)}`);
      closeDialog('#business-repeat-dialog');
      if (result.existing) { if ($('#business-detail-dialog').open) closeDialog('#business-detail-dialog'); await openBusinessModule(result.existing.service_id); await openBusinessRecord(result.existing.id, result.existing.service_id); toast('Questa prossima attività esiste già. Abbiamo aperto quella salvata.'); }
      else openBusinessForm(result.proposal, null, { source: result.source, next_date: nextDate });
    } catch (error) { notice('#business-repeat-error', error.message); }
  });
});
document.addEventListener('change', async event => {
  const checkbox = event.target.closest('[data-business-step]'); if (!checkbox) return;
  const recordId = checkbox.dataset.recordId; const stepId = checkbox.dataset.businessStep; const checked = checkbox.checked;
  if (businessPending.has(recordId)) { checkbox.checked = !checked; return; }
  const setBusy = busy => $$('[data-business-step],[data-action="business-status"],[data-action="business-edit"],[data-action="business-convert"],[data-action="business-delete"],[data-action="business-repeat"]', $('#business-detail-body')).forEach(control => { control.disabled = busy; });
  businessPending.add(recordId); setBusy(true); notice('#business-step-error', '');
  try {
    const result = await api(`/api/business/records/${encodeURIComponent(recordId)}`, { method: 'PATCH', body: { steps: { [stepId]: checked } } });
    if ($('#business-detail-dialog').open && state.businessRecord?.id === recordId) { state.businessRecord = result.item; renderBusinessDetail(result.item); setBusy(true); }
    try { await refresh(); }
    catch (error) { if ($('#business-detail-dialog').open && state.businessRecord?.id === recordId) notice('#business-step-error', `Il passaggio è salvato. La panoramica non si è aggiornata: ${error.message}`); else toast('Il passaggio è salvato. La panoramica si aggiornerà al prossimo controllo.', true); }
  } catch (error) { if ($('#business-detail-dialog').open && state.businessRecord?.id === recordId) { checkbox.checked = !checked; notice('#business-step-error', `Il passaggio non è stato salvato. ${error.message}`); } else toast(error.message, true); }
  finally { businessPending.delete(recordId); if ($('#business-detail-dialog').open && state.businessRecord?.id === recordId) { setBusy(false); $(`#business-step-${stepId}`)?.focus({ preventScroll: true }); } state.businessSummarySignature = null; renderBusinessSummary(); }
});
$('#business-global-search').addEventListener('input', event => {
  stopBusinessSearch(); const request = businessSearchRequest; const query = event.target.value.trim(); replace('#business-search-results'); notice('#business-search-error', '');
  if (!query) { $('#business-search-status').textContent = 'Scrivi una parola per trovare un’attività, un cliente o un documento.'; return; }
  $('#business-search-status').textContent = 'Cerchiamo nelle tue attività…'; businessSearchTimer = setTimeout(() => searchBusinessRecords(query, request), 220);
});
$('#business-service-search').addEventListener('input', event => { state.businessSearch = event.target.value; state.businessLimit = 6; renderBusinessCatalog(); });
$('#business-service-scope').addEventListener('change', event => { state.businessScope = event.target.value; state.businessLimit = 6; renderBusinessCatalog(); });
$('#business-record-filter').addEventListener('change', async event => { state.businessFilter = event.target.value; state.businessOffset = 0; if (state.businessService) await loadBusinessRecords(state.businessService); });
$('#imap-dialog').addEventListener('close', clearImapSecret);
$('#imap-dialog').addEventListener('cancel', clearImapSecret);
$('#imap-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return;
  const provider = state.imapProvider; const fields = new FormData(form); const body = { provider, email: String(fields.get('email') || '').trim(), password: String(fields.get('password') || '') };
  if (provider === 'imap') body.host = String(fields.get('host') || '').trim();
  const username = String(fields.get('username') || '').trim(); if (username) body.username = username;
  clearImapSecret(); notice('#imap-error', '');
  await withBusy($('button[type=submit]', form), async () => {
    try { await api('/api/mail/imap/connect', { method: 'POST', body }); closeDialog('#imap-dialog'); await refresh(); if ($('#wizard-dialog').open && state.wizard) { state.wizard.step = 3; renderWizard(); } toast('Casella collegata. Rivedi i contatti e autorizza la segreteria per avviare i controlli.'); }
    catch (error) { notice('#imap-error', error.message); $('#imap-password').focus(); }
    finally { body.password = ''; fields.delete('password'); clearImapSecret(); }
  });
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
$('#reply-watch-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget;
  if (!form.reportValidity()) return;
  notice('#reply-watch-error', '');
  await withBusy($('button[type=submit]', form), async () => {
    try {
      const contact = state.data.service.priority_contacts.find(item => item.email === $('#watch-contact').value);
      await api('/api/watches', { method: 'POST', body: { name: contact.name, email: contact.email, day: $('#watch-day').value } });
      $('#reply-watch-form-panel').open = false; await refresh(); toast('Avviso salvato tra le cose da fare.');
    } catch (error) { notice('#reply-watch-error', error.message); }
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
    try { const result = await api('/api/chat', { method: 'POST', body: { message } }); addChatMessage(result.reply || 'Posso aiutarti a configurare la segreteria email.'); state.chatPreferences = result.preferences || null; state.watchSuggestion = result.watch_suggestion || null; const suggestion = $('#chat-suggestion'); suggestion.replaceChildren(); suggestion.hidden = !result.supported; if (state.watchSuggestion) suggestion.append(actionButton('Segui questa risposta', 'confirm-watch', 'secondary', 'check')); else if (result.business_service_id) suggestion.append(actionButton(`Apri ${businessService(result.business_service_id)?.name || 'il servizio'}`, 'open-business', 'secondary', 'arrow', { service: result.business_service_id })); else if (result.supported) suggestion.append(actionButton(state.data.service.status === 'inactive' ? 'Configura la segreteria email' : 'Rivedi le preferenze', state.data.service.status === 'inactive' ? 'open-chat-wizard' : 'edit-chat-preferences', 'secondary', 'arrow')); }
    catch (error) { addChatMessage(error.message, 'assistant error'); }
    finally { state.chatBusy = false; }
  });
});
window.addEventListener('hashchange', () => {
  // In-page anchors such as the "Vai al contenuto" skip link are not pages.
  if (!PAGE_TITLES[location.hash.slice(1)]) return;
  setPage(location.hash.slice(1)); window.scrollTo({ top: 0, behavior: 'auto' });
});
// A text selection that starts in a field and ends outside the dialog also
// produces a click on the dialog: close only when the press began on the backdrop.
function outsideDialog(dialog, event) { const bounds = dialog.getBoundingClientRect(); return event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom; }
$$('dialog').forEach(dialog => {
  let pressedOutside = false;
  dialog.addEventListener('pointerdown', event => { pressedOutside = event.target === dialog && outsideDialog(dialog, event); });
  dialog.addEventListener('click', event => { if (pressedOutside && event.target === dialog && outsideDialog(dialog, event)) closeDialog(`#${dialog.id}`); pressedOutside = false; });
});
window.addEventListener('focus', async () => {
  if (!state.data) return;
  try { await refresh(); if (state.wizard?.step === 2 && $('#wizard-dialog').open && !$('#imap-dialog').open) renderWizard(); } catch (_) {}
});
async function initialize() {
  $('#today-label').textContent = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', weekday: 'short', day: 'numeric', month: 'long' }).format(new Date());
  setPage(location.hash.slice(1));
  try {
    await refresh();
    const params = new URLSearchParams(location.search);
    const returned = params.get('gmail') === 'connected' || params.get('outlook') === 'connected' || params.get('mail') === 'connected' || params.get('connected') === '1' && ['gmail', 'outlook'].includes(params.get('provider'));
    if (returned && isRealMailbox() && state.data.connection.status === 'connected') {
      toast(`${mailProviderLabel()} collegata. Rivedi le preferenze e autorizza il servizio.`);
      let pending; try { pending = JSON.parse(sessionStorage.getItem('filo-pending-wizard')); } catch (_) {}
      forgetPendingWizard();
      if (pending && Date.now() - pending.createdAt < 3600000 && Array.isArray(pending.preferences?.priority_contacts)) { startWizard(Boolean(pending.edit), false, pending.preferences); state.wizard.step = 3; renderWizard(); }
      else if (pending?.reconnect) { location.hash = 'connections'; }
      history.replaceState(null, '', `${location.pathname}${location.hash}`);
    }
    const failedProvider = params.get('mail_error');
    if (failedProvider) {
      const failure = state.data.mail_oauth_error;
      const label = failedProvider === 'outlook' ? 'Outlook' : 'Gmail';
      toast(failure?.provider === failedProvider && failure.message ? failure.message : `Il collegamento ${label} non è stato completato. Nessun accesso è stato salvato.`, true);
      history.replaceState(null, '', `${location.pathname}${location.hash || '#connections'}`);
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
