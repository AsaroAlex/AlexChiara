/* Isolated browser verification of the optimized commercial workflow. Never contacts real providers. */
'use strict';
const { chromium, request } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const port = Number(process.env.FILO_PORT || 8040);
const origin = `http://127.0.0.1:${port}`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const password = 'optimized-browser-secret-12';
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
  await page.screenshot({ path: path.join(root, '.runtime', `optimized-${label}-${dimensions.viewport}.png`), fullPage: true });
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
    await page.locator('[data-page="company"]').click();
    assert.equal(await page.locator('#company-vat_number').evaluate(node => node.required), false);
    await page.locator('#company-fiscal-details > summary').click(); await page.locator('#company-contact-details > summary').click();
    const company = { name: 'Studio Workflow', legal_name: 'Studio Workflow S.r.l.', vat_number: 'IT01234567890', tax_code: '01234567890', address: 'Via del Lavoro 12', postal_code: '20100', city: 'Milano', province: 'mi', email: 'studio@workflow.invalid', phone: '+39 02 123456', pec: 'studio@pec.invalid', sdi_code: 'abc1234', signature: 'Segreteria Studio Workflow' };
    for (const [field, value] of Object.entries(company)) await page.locator(`#company-${field}`).fill(value);
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'company'); }
    await page.locator('#company-form button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#company-save-status').textContent === 'Informazioni salvate');
    const profile = (await api(page, '/api/bootstrap')).body.company;
    assert.equal(profile.vat_number, '01234567890'); assert.equal(profile.province, 'MI'); assert.equal(profile.sdi_code, 'ABC1234');
    await page.locator('#company-name').focus(); await page.keyboard.press('Control+k'); assert.equal(await page.locator('#business-search-dialog').evaluate(node => node.open), false, 'Do not intercept text editing.');
    const quote = (await api(page, '/api/business/records', 'POST', { service_id: 'quotes', title: 'Consulenza 50% <img src=x onerror=alert(1)>', contact: 'Cliente Workflow', due_date: '2020-01-01', status: 'waiting', priority: 'urgent', saved_minutes: 42, details: { client: 'Cliente Workflow', scope: 'Consulenza da rivedere', net_amount: '100.01', vat_rate: '22', payment_terms: 'Bonifico a 30 giorni' } })).body.item;
    assert.ok(quote.id); assert.match(quote.document, /Studio Workflow S.r.l./); assert.match(quote.document, /01234567890/);
    await page.reload(); await page.waitForSelector('#app-content:not([hidden])');
    // Search searches real records across modules and treats markup and % as text.
    await page.locator('#business-search-button').click(); await page.waitForSelector('#business-search-dialog[open]');
    await page.locator('#business-global-search').fill('%');
    await page.waitForSelector('.business-search-result');
    assert.equal(await page.locator('.business-search-result').count(), 1);
    assert.match(await page.locator('.business-search-result').innerText(), /<img src=x onerror=alert\(1\)>/);
    assert.equal(await page.locator('#business-search-results img').count(), 0);
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'search'); }
    await page.keyboard.press('Escape'); await page.waitForSelector('#business-search-dialog:not([open])', { state: 'attached' });
    assert.equal(await page.evaluate(() => document.activeElement.id), 'business-search-button');
    await page.locator('#company-title').evaluate(node => { node.tabIndex = -1; node.focus(); });
    await page.keyboard.press('Control+k'); await page.waitForSelector('#business-search-dialog[open]');
    await page.locator('#business-global-search').fill('Cliente Workflow'); await page.waitForSelector('.business-search-result');
    await page.locator('.business-search-result').click(); await page.waitForSelector('#business-detail-dialog[open]');
    assert.equal(await page.locator('#business-detail-dialog .business-document img').count(), 0);
    // A conversion first opens a proposal and does not copy the old deadline.
    await page.locator('[data-action="business-convert"]').click(); await page.waitForSelector('#business-form-dialog[open]');
    assert.equal(await page.locator('#business-field-due_date').inputValue(), '');
    assert.equal(await page.locator('#business-field-net_amount').inputValue(), '100.01');
    assert.equal(await page.locator('#business-field-status').count(), 0);
    assert.equal(await page.locator('#business-savings-fields').isVisible(), false);
    assert.match(await page.locator('#business-conversion-note').innerText(), /non viene inviata allo SDI/);
    assert.equal((await api(page, '/api/business/records?service_id=invoices')).body.total, 0);
    await page.locator('#business-form-dialog [data-action="business-close-form"]').first().click();
    await page.waitForSelector('#business-form-dialog:not([open])', { state: 'attached' });
    assert.equal((await api(page, '/api/business/records')).body.total, 1, 'Cancellation must not create a record.');
    assert.equal(await page.locator('#business-detail-dialog').evaluate(node => node.open), true);
    await page.locator('[data-action="business-convert"]').click(); await page.waitForSelector('#business-form-dialog[open]');
    await page.locator('#business-field-title').fill('Bozza fattura revisionata'); await page.locator('#business-field-net_amount').fill('120.01'); await page.locator('#business-field-due_date').fill('2027-01-15'); await page.locator('#business-field-payment_terms').fill('');
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'conversion'); }
    await page.locator('#business-record-form button[type="submit"]').evaluate(button => { button.click(); button.click(); });
    await page.waitForFunction(() => !document.querySelector('#business-form-dialog').open && document.querySelector('#business-detail-title')?.textContent === 'Bozza fattura revisionata');
    const invoices = (await api(page, '/api/business/records?service_id=invoices')).body.items;
    assert.equal(invoices.length, 1); const invoice = invoices[0];
    assert.equal(invoice.totals.gross_amount, '146.41'); assert.equal(invoice.due_date, '2027-01-15'); assert.equal(invoice.saved_minutes, 0); assert.equal(invoice.status, 'todo');
    assert.equal(invoice.details.payment_terms, undefined, 'Clearing an optional copied field must remove it from the draft.');
    assert.equal(invoice.links.source.id, quote.id);
    assert.match(invoice.document, /Studio Workflow S.r.l./);
    const source = (await api(page, `/api/business/records/${quote.id}`)).body.item;
    assert.equal(source.status, 'waiting'); assert.equal(source.saved_minutes, 42); assert.equal(source.totals.gross_amount, '122.01');
    // Reopening the previous step returns the existing draft, preserving edits.
    await page.locator('.business-document-links [data-action="business-linked-record"]').click();
    await page.waitForFunction(() => document.querySelector('#business-detail-title')?.textContent.includes('Consulenza 50%'));
    await page.locator('[data-action="business-convert"]').click();
    await page.waitForFunction(() => document.querySelector('#business-detail-title')?.textContent === 'Bozza fattura revisionata');
    assert.equal(await page.locator('#business-form-dialog').evaluate(node => node.open), false);
    assert.equal((await api(page, '/api/business/records?service_id=invoices')).body.total, 1);
    await page.locator('[data-action="business-convert"]').click(); await page.waitForSelector('#business-form-dialog[open]');
    assert.equal(await page.locator('#business-field-amount').inputValue(), '146.41'); assert.equal(await page.locator('#business-field-due_date').inputValue(), '');
    await page.locator('#business-field-title').fill('Incasso da verificare'); await page.locator('#business-field-due_date').fill('2027-02-01');
    await page.locator('#business-record-form button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#business-detail-title')?.textContent === 'Incasso da verificare');
    const receivable = (await api(page, '/api/business/records?service_id=receivables')).body.items[0];
    assert.equal(receivable.details.amount, '146.41'); assert.equal(receivable.links.source.id, invoice.id); assert.equal(receivable.due_date, '2027-02-01');
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'linked-documents'); }
    const exported = await page.request.get(`${origin}/api/business/records/${receivable.id}/export`); assert.equal(exported.status(), 200); assert.match(await exported.text(), /Studio Workflow S.r.l./);
    await page.locator('[data-action="business-close-detail"]').click(); await page.locator('[data-page="company"]').click();
    await page.locator('#company-fiscal-details').evaluate(node => { node.open = true; }); await page.locator('#company-legal_name').fill('Studio Workflow aggiornato S.r.l.'); await page.locator('#company-form button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#company-save-status').textContent === 'Informazioni salvate');
    assert.match((await api(page, `/api/business/records/${invoice.id}`)).body.item.document, /Studio Workflow aggiornato S.r.l./);
    // One action completes only this record. Undo restores its previous waiting state.
    await page.locator('[data-page="overview"]').click(); await page.waitForSelector(`.business-next-action[data-record-id="${quote.id}"]`);
    await page.locator(`.business-next-action[data-record-id="${quote.id}"] [data-action="business-quick-done"]`).click();
    await page.waitForSelector('#toast [data-action="business-undo"]');
    assert.equal((await api(page, `/api/business/records/${quote.id}`)).body.item.status, 'done');
    await page.locator('#toast [data-action="business-undo"]').click();
    await page.waitForSelector(`.business-next-action[data-record-id="${quote.id}"]`);
    assert.equal((await api(page, `/api/business/records/${quote.id}`)).body.item.status, 'waiting');
    assert.equal((await api(page, `/api/business/records/${invoice.id}`)).body.item.status, 'todo');
    assert.equal((await api(page, `/api/business/records/${receivable.id}`)).body.item.status, 'todo');
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'quick-actions'); }
    // Completed records remain discoverable; newer requests cannot show stale results.
    await api(page, `/api/business/records/${receivable.id}`, 'PATCH', { status: 'done' });
    await page.locator('#business-search-button').click(); await page.locator('#business-global-search').fill('Incasso da verificare'); await page.waitForSelector('.business-search-result'); assert.match(await page.locator('.business-search-result').innerText(), /Completata/);
    await page.locator('#business-global-search').fill('inesistente'); await pause(250); await page.locator('#business-global-search').fill('%');
    await page.waitForFunction(() => document.querySelector('#business-search-status').textContent === '3 attività trovate');
    assert.equal(await page.locator('.business-search-result').count(), 3);
    assert.match(await page.locator('.business-search-result').filter({ hasText: 'Consulenza 50%' }).innerText(), /Consulenza 50%/);
    assert.equal(await page.locator('#business-search-results img').count(), 0);
    assert.deepEqual(errors, [], 'No JavaScript errors.'); assert.deepEqual(remoteRequests, [], 'Only isolated localhost requests.');
    console.log('Optimized workflow browser PASS: optional reusable company profile, current document/export identity, editable quote→invoice→receivable proposal with cancellation/no writes, independent deadlines, reviewed VAT amounts, linked records and duplicate prevention, source status/savings preserved, literal global search including completed records and safe markup, keyboard/focus, quick completion/undo to original state, 1440/390/320 layouts and 44px controls.');
  } finally {
    await probe.dispose(); if (browser) await browser.close();
    if (server.exitCode === null) { server.kill('SIGTERM'); await Promise.race([new Promise(resolve => server.once('exit', resolve)), pause(2000)]); if (server.exitCode === null) server.kill('SIGKILL'); }
    await fs.rm(runtime, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
