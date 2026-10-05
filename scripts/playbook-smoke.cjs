/* Isolated browser verification of the operational playbooks and reviewable repetitions. Never contacts real providers. */
'use strict';
const { chromium, request } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const port = Number(process.env.FILO_PORT || 8042);
const origin = `http://127.0.0.1:${port}`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const password = 'playbook-browser-secret-12';
async function api(page, endpoint, method = 'GET', body) {
  return page.evaluate(async ({ endpoint, method, body }) => {
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') { const session = await (await fetch('/api/auth/session')).json(); headers['X-CSRF-Token'] = session.csrf_token; headers['Content-Type'] = 'application/json'; }
    const response = await fetch(endpoint, { method, headers, credentials: 'same-origin', ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    return { status: response.status, body: await response.json() };
  }, { endpoint, method, body });
}
async function inspect(page, label) {
  const dimensions = await page.evaluate(() => ({ focusInDialog: !document.querySelector('dialog[open]') || [...document.querySelectorAll('dialog[open]')].some(dialog => dialog.contains(document.activeElement)), active: document.activeElement?.id || document.activeElement?.tagName, viewport: innerWidth, document: document.documentElement.scrollWidth, dialogs: [...document.querySelectorAll('dialog[open]')].map(dialog => ({ width: dialog.clientWidth, content: dialog.scrollWidth })), small: [...document.querySelectorAll('button,input:not([type="checkbox"]):not([type="hidden"]),select,a.button,summary')].filter(item => { const rect = item.getBoundingClientRect(); return rect.height && rect.width; }).filter(item => item.getBoundingClientRect().height < 43.5).map(item => item.id || item.textContent.trim().slice(0, 30)) }));
  assert.equal(dimensions.focusInDialog, true, `${label} focus escaped dialog: ${dimensions.active}`);
  assert.ok(dimensions.document <= dimensions.viewport + 1, `${label} page overflow: ${JSON.stringify(dimensions)}`);
  for (const dialog of dimensions.dialogs) assert.ok(dialog.content <= dialog.width + 1, `${label} dialog overflow`);
  assert.deepEqual(dimensions.small, [], `${label} small controls`);
  await page.screenshot({ path: path.join(root, '.runtime', `playbook-${label}-${dimensions.viewport}.png`), fullPage: true });
}
(async () => {
  const runtime = await fs.mkdtemp(path.join(os.tmpdir(), 'filo-business-smoke-'));
  const env = { ...process.env };
  for (const key of Object.keys(env)) if (/^(STRIPE_|FILO_GOOGLE_|FILO_MICROSOFT_|FILO_AI_|OPENAI_|FILO_PLAN_|FILO_PUBLIC_URL$)/.test(key)) delete env[key];
  Object.assign(env, { ALEXCHIARA_DATA_DIR: runtime, ALEXCHIARA_ALLOWED_HOSTS: '127.0.0.1,localhost', FILO_ACCESS_USERNAME: 'filo', FILO_ACCESS_PASSWORD: password, FILO_REAL_DATA_ONLY: '1', FILO_AI_ENABLED: '0' });
  const server = spawn(path.join(root, '.venv', 'bin', 'python'), ['-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port)], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = ''; let browser; const errors = []; const remoteRequests = [];
  server.on('error', error => { logs += error.message; });
  for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => { logs = (logs + chunk).slice(-6000); });
  const probe = await request.newContext({ baseURL: origin });
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) { if (server.exitCode !== null) throw new Error(logs); if (!logs.includes('Uvicorn running on')) { await pause(100); continue; } try { ready = (await probe.get('/api/health', { timeout: 1000 })).ok(); } catch (_) {} if (ready) break; await pause(100); }
    assert.ok(ready, logs);
    await fs.mkdir(path.join(root, '.runtime'), { recursive: true });
    browser = await chromium.launch({ executablePath: process.env.FILO_CHROMIUM_PATH || (require('node:fs').existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined), headless: true, args: ['--no-sandbox'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'it-IT', timezoneId: 'America/Los_Angeles' });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => { const url = new URL(route.request().url()); if (url.origin !== origin && !['data:', 'blob:'].includes(url.protocol)) { remoteRequests.push(url.origin); return route.abort(); } return route.continue(); });
    await page.goto(`${origin}/register`); await page.waitForSelector('#auth-form:not([hidden])');
    await page.locator('#auth-name').fill('Azienda browser di prova'); await page.locator('#auth-email').fill('browser@business.invalid'); await page.locator('#auth-password').fill(password); await page.locator('#auth-submit').click();
    await page.waitForURL(`${origin}/app`); await page.waitForSelector('#app-content:not([hidden])');
    assert.equal((await api(page, '/api/business/records')).body.total, 0);

    const services = (await api(page, '/api/business/services')).body.services.filter(service => service.kind === 'business');
    assert.equal(services.length, 23);
    for (const service of services) {
      assert.ok(service.playbook.steps.length >= 3 && service.playbook.steps.length <= 5, `${service.id} has practical operating steps.`);
      await page.evaluate(id => openBusinessModule(id), service.id);
      assert.equal(await page.locator('.business-empty-recipe li').count(), service.playbook.steps.length, `${service.id} guided first use`);
      assert.ok((await page.locator('.business-empty-recipe').innerText()).includes(service.playbook.steps[0].label));
    }
    const day = (await api(page, '/api/business/summary')).body.date;
    const addDays = count => { const date = new Date(`${day}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + count); return date.toISOString().slice(0, 10); };
    const future = addDays(7); const override = addDays(60);
    const create = async body => { const response = await api(page, '/api/business/records', 'POST', body); assert.equal(response.status, 201, JSON.stringify(response.body)); return response.body.item; };
    const quote = await create({ service_id: 'quotes', title: 'Consulenza ricorrente di prova', contact: 'Cliente prova', status: 'waiting', saved_minutes: 23, details: { client: 'Cliente prova', scope: 'Consulenza mensile da rivedere', net_amount: '100.01', vat_rate: '22', valid_until: '2020-01-01', payment_terms: 'Pagamento a 30 giorni' } });
    const income = await create({ service_id: 'receivables', title: 'Incasso con data prevista', details: { client: 'Cliente prova', reference: 'R-1', amount: '123.45', expected_date: '2020-01-01' } });
    await create({ service_id: 'receivables', title: 'Incasso con scadenza scelta', due_date: override, details: { client: 'Cliente prova', reference: 'R-2', amount: '200.00', expected_date: '2020-01-01' } });
    await create({ service_id: 'receivables', title: 'Incasso completato escluso', status: 'done', details: { client: 'Cliente prova', reference: 'R-3', amount: '900.00', expected_date: '2020-01-01' } });
    await create({ service_id: 'expenses', title: 'Pagamento per oggi', details: { supplier: 'Fornitore prova', amount: '54.32', payment_date: day } });
    await create({ service_id: 'expenses', title: 'Pagamento scaduto', details: { supplier: 'Fornitore prova', amount: '11.11', payment_date: '2020-01-01' } });
    const stock = await create({ service_id: 'inventory', title: 'Materiale sotto soglia', details: { sku: 'P-1', item: 'Cartelline prova', quantity: '2.5', reorder_level: '10', unit: 'pezzi' } });
    const leave = await create({ service_id: 'leave', title: 'Copertura concordata', details: { person: 'Referente prova', starts_on: '2020-01-01', ends_on: '2020-01-03', covering_person: 'Referente copertura' } });
    await page.reload(); await page.waitForSelector('#app-content:not([hidden])');
    await page.locator('[data-page="overview"]').click();
    await page.waitForSelector('#business-financial-summary:not([hidden])');
    assert.match(await page.locator('[data-financial="receivables"]').innerText(), /323,45/);
    assert.match(await page.locator('[data-financial="receivables"]').innerText(), /Scaduti 123,45/);
    assert.match(await page.locator('[data-financial="payables"]').innerText(), /65,43/);
    assert.match(await page.locator('[data-financial="payables"]').innerText(), /Oggi 54,32/);
    assert.match(await page.locator('.business-financial-source').innerText(), /non collegati alla banca/);
    assert.equal(income.effective_due_date, '2020-01-01'); assert.equal(income.due_source.type, 'field');
    const summary = (await api(page, '/api/business/summary')).body;
    assert.equal(summary.financial_summary.receivables.overdue_amount, '123.45');
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'overview'); }
    await page.evaluate(async id => { await openBusinessModule('quotes'); await openBusinessRecord(id, 'quotes'); }, quote.id);
    await page.waitForSelector('#business-detail-dialog[open]');
    assert.match(await page.locator('.business-due-source').innerText(), /Validità della proposta/);
    assert.equal(await page.locator('[data-business-step]').count(), 4);
    assert.match(await page.locator('#business-playbook-progress').innerText(), /0\/4/);
    assert.equal(await page.locator('.business-playbook img').count(), 0);
    await page.evaluate(() => { window.playbookCopiedText = null; Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => { window.playbookCopiedText = text; } } }); });
    await page.locator('[data-action="business-copy-document"]').click();
    assert.equal(await page.evaluate(() => window.playbookCopiedText), quote.document, 'Copy uses the exact local document, including its manual source.');
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('Documento copiato'));
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('Clipboard denied for test'); } } }));
    await page.locator('[data-action="business-copy-document"]').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('Il browser non ha permesso'));
    assert.equal(await page.locator('#toast').evaluate(node => node.classList.contains('error')), true);
    const first = quote.playbook.steps[0]; const second = quote.playbook.steps[1];
    // A failed save preserves the prior checkbox and remains actionable.
    const patchRoute = `**/api/business/records/${quote.id}`;
    const failSave = async route => { if (route.request().method() === 'PATCH') await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Errore temporaneo di prova' }) }); else await route.continue(); };
    await page.route(patchRoute, failSave);
    await page.locator(`#business-step-${first.id}`).check();
    await page.waitForSelector('#business-step-error:not([hidden])');
    assert.equal(await page.locator(`#business-step-${first.id}`).isChecked(), false);
    assert.equal(await page.locator(`#business-step-${first.id}`).isDisabled(), false);
    assert.equal((await api(page, `/api/business/records/${quote.id}`)).body.item.playbook.completed, 0);
    await page.unroute(patchRoute, failSave);
    await page.locator(`#business-step-${first.id}`).focus(); await page.keyboard.press('Space');
    await page.waitForFunction(() => document.querySelector('#business-playbook-progress').textContent.startsWith('1/'));
    await page.waitForFunction(id => document.activeElement?.id === id && !document.activeElement.disabled, `business-step-${first.id}`);
    const bootstrapRoute = '**/api/bootstrap';
    const failRefresh = route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Aggiornamento temporaneamente non disponibile' }) });
    await page.route(bootstrapRoute, failRefresh);
    await page.locator(`#business-step-${second.id}`).check();
    await page.waitForFunction(() => document.querySelector('#business-playbook-progress').textContent.startsWith('2/'));
    await page.waitForSelector('#business-step-error:not([hidden])');
    assert.match(await page.locator('#business-step-error').innerText(), /Il passaggio è salvato/);
    assert.equal(await page.locator(`#business-step-${second.id}`).isChecked(), true);
    await page.waitForFunction(id => document.activeElement?.id === id && !document.activeElement.disabled, `business-step-${second.id}`);
    await page.unroute(bootstrapRoute, failRefresh);
    await page.evaluate(() => refresh());
    let updated = (await api(page, `/api/business/records/${quote.id}`)).body.item;
    assert.equal(updated.playbook.completed, 2); assert.equal(updated.status, 'waiting');
    assert.equal(updated.next_action, updated.playbook.steps[2].label);
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(await page.locator('.business-playbook-step').evaluateAll(labels => labels.every(label => label.getBoundingClientRect().height >= 44)), true);
      await inspect(page, 'steps');
    }
    await page.locator('[data-action="business-close-detail"]').click(); await page.locator('[data-page="overview"]').click();
    await page.waitForSelector(`.business-next-action[data-record-id="${quote.id}"]`);
    assert.match(await page.locator(`.business-next-action[data-record-id="${quote.id}"]`).innerText(), /2\/4 passaggi segnati da te/);
    assert.equal(await page.locator(`.business-next-action[data-record-id="${quote.id}"] .business-next-step`).innerText(), updated.next_action);
    assert.equal(await page.locator('#business-next-actions input[type="checkbox"]').count(), 0, 'Overview stays concise.');
    await page.reload(); await page.waitForSelector('#app-content:not([hidden])');
    await page.evaluate(async id => { await openBusinessModule('quotes'); await openBusinessRecord(id, 'quotes'); }, quote.id);
    assert.equal(await page.locator(`#business-step-${first.id}`).isChecked(), true);
    assert.equal(await page.locator(`#business-step-${second.id}`).isChecked(), true);
    await page.locator('#business-detail-dialog [data-action="business-status"]').click();
    await page.waitForFunction(() => document.querySelector('#business-detail-body').textContent.includes('Riapri attività'));
    assert.equal((await api(page, `/api/business/records/${quote.id}`)).body.item.playbook.completed, 2, 'Completion does not silently check steps.');
    await page.locator('[data-action="business-repeat"]').click(); await page.waitForSelector('#business-repeat-dialog[open]');
    assert.equal(await page.locator('#business-repeat-date').getAttribute('min'), addDays(1));
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'repeat-date'); }
    await page.locator('#business-repeat-date').fill(future); await page.locator('#business-repeat-form button[type="submit"]').click();
    await page.waitForSelector('#business-form-dialog[open]');
    assert.equal(await page.locator('#business-field-due_date').inputValue(), future);
    assert.equal(await page.locator('#business-field-due_date').evaluate(node => node.readOnly), true);
    assert.equal(await page.locator('#business-field-valid_until').inputValue(), future);
    assert.equal(await page.locator('#business-field-valid_until').evaluate(node => node.readOnly), true);
    assert.equal(await page.locator('#business-field-status').count(), 0);
    assert.equal(await page.locator('#business-savings-fields').isVisible(), false);
    assert.equal((await api(page, '/api/business/records')).body.total, 8, 'Proposal has no writes.');
    await page.locator('#business-field-title').fill('Modifica annullata');
    await page.locator('#business-form-dialog [data-action="business-close-form"]').first().click();
    await page.waitForSelector('#business-form-dialog:not([open])', { state: 'attached' });
    assert.equal((await api(page, '/api/business/records')).body.total, 8, 'Cancelling review does not create a record.');
    await page.locator('[data-action="business-repeat"]').click(); await page.locator('#business-repeat-date').fill(future); await page.locator('#business-repeat-form button[type="submit"]').click();
    await page.waitForSelector('#business-form-dialog[open]');
    await page.locator('#business-field-title').fill('Consulenza mensile revisionata'); await page.locator('#business-field-net_amount').fill('150.01'); await page.locator('#business-field-payment_terms').fill('');
    await pause(5500);
    assert.equal(await page.locator('#business-field-title').inputValue(), 'Consulenza mensile revisionata', 'Polling must preserve the pending review.');
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'repeat-review'); }
    await page.locator('#business-record-form button[type="submit"]').evaluate(button => { button.click(); button.click(); });
    await page.waitForFunction(() => !document.querySelector('#business-form-dialog').open && document.querySelector('#business-detail-title')?.textContent === 'Consulenza mensile revisionata');
    const repeated = (await api(page, '/api/business/records?service_id=quotes')).body.items.find(item => item.id !== quote.id);
    assert.ok(repeated); assert.equal(repeated.status, 'todo'); assert.equal(repeated.saved_minutes, 0); assert.equal(repeated.playbook.completed, 0); assert.equal(repeated.effective_due_date, future); assert.equal(repeated.details.valid_until, future); assert.equal(repeated.details.payment_terms, undefined); assert.equal(repeated.totals.gross_amount, '183.01');
    assert.equal(repeated.links.source.id, quote.id); assert.equal(repeated.links.source.kind, 'repeat');
    const original = (await api(page, `/api/business/records/${quote.id}`)).body.item;
    assert.equal(original.status, 'done'); assert.equal(original.saved_minutes, 23); assert.equal(original.playbook.completed, 2); assert.equal(original.details.valid_until, '2020-01-01'); assert.equal(original.totals.gross_amount, '122.01');
    await page.locator('.business-document-links [data-action="business-linked-record"]').click();
    await page.waitForFunction(() => document.querySelector('#business-detail-title')?.textContent === 'Consulenza ricorrente di prova');
    await page.locator('[data-action="business-repeat"]').click(); await page.locator('#business-repeat-date').fill(future); await page.locator('#business-repeat-form button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#business-detail-title')?.textContent === 'Consulenza mensile revisionata');
    assert.equal(await page.locator('#business-form-dialog').evaluate(node => node.open), false); assert.equal((await api(page, '/api/business/records?service_id=quotes')).body.total, 2);
    await page.locator('[data-action="business-close-detail"]').click();
    await page.evaluate(async id => { await openBusinessModule('inventory'); await openBusinessRecord(id, 'inventory'); }, stock.id);
    assert.match(await page.locator('.business-stock-reorder').innerText(), /7,5 pezzi/); assert.match(await page.locator('.business-stock-reorder').innerText(), /nessun ordine viene inviato/);
    await page.locator('[data-action="business-close-detail"]').click();
    await page.evaluate(async id => { await openBusinessModule('leave'); await openBusinessRecord(id, 'leave'); }, leave.id);
    await page.locator('[data-action="business-repeat"]').click(); await page.locator('#business-repeat-date').fill(future); await page.locator('#business-repeat-form button[type="submit"]').click();
    await page.waitForSelector('#business-form-dialog[open]');
    assert.equal(await page.locator('#business-field-starts_on').inputValue(), future); assert.equal(await page.locator('#business-field-ends_on').inputValue(), ''); assert.equal(await page.locator('#business-field-ends_on').evaluate(node => node.required), true);
    await page.locator('#business-record-form button[type="submit"]').click();
    assert.equal(await page.locator('#business-form-dialog').evaluate(node => node.open), true);
    assert.equal((await api(page, '/api/business/records?service_id=leave')).body.total, 1, 'A repeated period requires its new end date.');
    await page.locator('#business-field-ends_on').fill(addDays(9)); await page.locator('#business-record-form button[type="submit"]').click();
    await page.waitForSelector('#business-form-dialog:not([open])', { state: 'attached' });
    assert.equal((await api(page, '/api/business/records?service_id=leave')).body.total, 2);
    await page.locator('[data-action="business-close-detail"]').click();
    const support = await create({ service_id: 'support', title: 'Richiesta conclusa da conservare', details: { customer: 'Cliente prova', request: 'Richiesta reale di prova' } });
    await page.evaluate(() => refresh());
    await page.evaluate(async id => { await openBusinessModule('support'); await openBusinessRecord(id, 'support'); }, support.id);
    await page.locator('#business-detail-dialog [data-action="business-status"]').click();
    await page.waitForFunction(() => document.querySelector('#business-detail-body').textContent.includes('Riapri attività'));
    await page.locator('[data-action="business-close-detail"]').click();
    assert.match(await page.locator('.business-empty').innerText(), /attività salvate restano nello storico/);
    assert.equal(await page.locator('.business-empty-recipe').count(), 0, 'Completion must not show first-use recipes again.');
    await page.locator('[data-action="business-show-history"]').click();
    await page.waitForSelector(`.business-record[data-record-id="${support.id}"]`);
    assert.match(await page.locator(`.business-record[data-record-id="${support.id}"]`).innerText(), /Completata/);
    assert.deepEqual(errors, [], 'No JavaScript errors.'); assert.deepEqual(remoteRequests, [], 'Only isolated localhost requests.');
    console.log('Playbook browser PASS: all 23 guided empty modules, derived due dates and explicit override, exact manual document copy and truthful clipboard failure, finance from entered open records, stock reorder quantity, manual keyboard checkbox progress persistence and PATCH error and follow-up refresh failure recovery, completed records in history, next step on overview, status completion preserves checks, reviewed repetition with cancellation/no writes/edit/reset/duplicate prevention/source preservation, new required leave dates, polling preserves drafts, 1440/390/320 focus/layout/44px controls.');
  } finally {
    await probe.dispose(); if (browser) await browser.close();
    if (server.exitCode === null) { server.kill('SIGTERM'); await Promise.race([new Promise(resolve => server.once('exit', resolve)), pause(2000)]); if (server.exitCode === null) server.kill('SIGKILL'); }
    await fs.rm(runtime, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
