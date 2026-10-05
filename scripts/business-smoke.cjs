/* Isolated browser verification of local business services. Never contacts real providers. */
'use strict';
const { chromium, request } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const port = Number(process.env.FILO_PORT || 8036);
const origin = `http://127.0.0.1:${port}`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const password = 'business-browser-secret-12';
async function api(page, endpoint, method = 'GET', body) {
  return page.evaluate(async ({ endpoint, method, body }) => {
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') { const session = await (await fetch('/api/auth/session')).json(); headers['X-CSRF-Token'] = session.csrf_token; headers['Content-Type'] = 'application/json'; }
    const response = await fetch(endpoint, { method, headers, credentials: 'same-origin', ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    return { status: response.status, body: await response.json() };
  }, { endpoint, method, body });
}
async function inspect(page, label) {
  const dimensions = await page.evaluate(() => ({ focusInDialog: !document.querySelector('dialog[open]') || document.querySelector('dialog[open]').contains(document.activeElement), active: document.activeElement?.id || document.activeElement?.tagName, viewport: innerWidth, document: document.documentElement.scrollWidth, dialogs: [...document.querySelectorAll('dialog[open]')].map(dialog => ({ width: dialog.clientWidth, content: dialog.scrollWidth })), small: [...document.querySelectorAll('button,input:not([type="checkbox"]):not([type="hidden"]),select,a.button,summary')].filter(item => { const rect = item.getBoundingClientRect(); return rect.height && rect.width; }).filter(item => item.getBoundingClientRect().height < 43.5).map(item => item.id || item.textContent.trim().slice(0, 30)) }));
  assert.equal(dimensions.focusInDialog, true, `${label} focus escaped dialog: ${dimensions.active}`);
  assert.ok(dimensions.document <= dimensions.viewport + 1, `${label} page overflow: ${JSON.stringify(dimensions)}`);
  for (const dialog of dimensions.dialogs) assert.ok(dialog.content <= dialog.width + 1, `${label} dialog overflow`);
  assert.deepEqual(dimensions.small, [], `${label} small controls`);
  await page.screenshot({ path: path.join(root, '.runtime', `business-${label}-${dimensions.viewport}.png`), fullPage: true });
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
    await page.locator('[data-page="services"]').click();
    await page.waitForSelector('.business-service-card[data-service-id]');
    assert.equal(await page.locator('.business-service-card[data-service-id]').count(), 6);
    await inspect(page, 'catalog');
    await page.locator('#business-service-search').fill('preventivi');
    await page.locator('[data-service-id="quotes"] [data-action="open-business"]').click();
    await page.waitForSelector('#business-workspace:not([hidden])');
    await page.locator('#business-workspace [data-action="business-new"]').first().click();
    await page.waitForSelector('#business-form-dialog[open]');
    await page.locator('#business-field-title').fill('Proposta reale di prova');
    await page.locator('#business-field-contact').fill('Referente prova');
    await page.locator('#business-field-due_date').fill('2026-01-01');
    await page.locator('#business-field-priority').selectOption('urgent');
    await page.locator('#business-field-client').fill('Cliente prova');
    await page.locator('#business-field-scope').fill('Una consulenza da rivedere');
    await page.locator('#business-field-net_amount').fill('100.01');
    await page.locator('#business-field-vat_rate').selectOption('22');
    await page.locator('#business-field-payment_terms').fill('Bonifico a 30 giorni');
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'form'); }
    await page.locator('#business-record-form button[type="submit"]').click();
    await page.waitForSelector('#business-form-dialog:not([open])', { state: 'attached' });
    await page.waitForSelector('.business-record');
    let records = (await api(page, '/api/business/records?service_id=quotes')).body.items;
    assert.equal(records.length, 1); const id = records[0].id;
    assert.equal(records[0].totals.gross_amount, '122.01');
    await page.locator('.business-record [data-action="business-open-record"]').click();
    await page.waitForSelector('#business-detail-dialog[open]');
    assert.match(await page.locator('.business-document').innerText(), /122,01 €/);
    assert.match(await page.locator('.business-document').innerText(), /non viene trasmessa allo SDI/);
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'detail'); }
    const exported = await page.request.get(`${origin}/api/business/records/${id}/export`);
    assert.equal(exported.status(), 200); assert.match(await exported.text(), /Proposta reale di prova/);
    await page.locator('#business-detail-dialog [data-action="business-edit"]').click();
    await page.waitForSelector('#business-form-dialog[open]');
    await page.locator('#business-field-payment_terms').fill('');
    await page.locator('#business-record-form button[type="submit"]').click();
    await page.waitForSelector('#business-form-dialog:not([open])', { state: 'attached' });
    records = (await api(page, '/api/business/records?service_id=quotes')).body.items;
    assert.equal(records[0].details.payment_terms, undefined, 'Cleared fields must leave the generated document.');
    await page.locator('.business-record [data-action="business-open-record"]').click(); await page.waitForSelector('#business-detail-dialog[open]');
    await page.locator('#business-detail-dialog [data-action="business-status"]').click();
    await page.waitForFunction(() => document.querySelector('#business-detail-body').textContent.includes('Riapri attività'));
    assert.equal((await api(page, `/api/business/records/${id}`)).body.item.status, 'done');
    await page.locator('#business-detail-dialog [data-action="business-status"]').click();
    await page.waitForFunction(() => document.querySelector('#business-detail-body').textContent.includes('Segna completata'));
    assert.equal((await api(page, `/api/business/records/${id}`)).body.item.status, 'todo');
    await page.locator('[data-action="business-close-detail"]').click();
    await page.locator('[data-page="overview"]').click();
    await page.waitForSelector(`.business-next-action[data-record-id="${id}"]`);
    assert.match(await page.locator('#business-priorities-summary').innerText(), /scaduta/);
    assert.match(await page.locator(`.business-next-action[data-record-id="${id}"]`).innerText(), /Scaduta/, 'Due dates precede attention for urgent records.');
    await inspect(page, 'overview');
    await page.locator(`.business-next-action[data-record-id="${id}"] [data-action="business-open-record"]`).click();
    await page.waitForSelector('#business-detail-dialog[open]');
    await page.locator('#business-detail-dialog [data-action="business-delete"]').click(); await page.waitForSelector('#business-delete-dialog[open]');
    await page.locator('[data-action="business-confirm-delete"]').click(); await page.waitForSelector('#business-delete-dialog:not([open])', { state: 'attached' });
    assert.equal((await api(page, '/api/business/records')).body.total, 0);
    await page.locator('[data-page="services"]').click(); await page.locator('[data-action="business-back"]').click();
    await page.locator('[data-action="business-clear-filters"]').count();
    await page.locator('#business-service-search').fill('');
    await page.locator('#business-integration-panel').evaluate(element => { element.open = true; });
    assert.equal(await page.locator('#business-integration-list .business-service-card button').count(), 0);
    const catalog = (await api(page, '/api/business/services')).body.services;
    assert.equal(catalog.filter(service => service.kind === 'business').length, 23);
    for (const service of catalog.filter(item => item.kind === 'business')) {
      await page.locator('#business-service-search').fill(service.name);
      await page.locator(`[data-service-id="${service.id}"] [data-action="open-business"]`).click();
      await page.locator('#business-workspace [data-action="business-new"]').first().click();
      await page.waitForSelector('#business-form-dialog[open]');
      assert.equal(await page.locator('#business-detail-fields .business-form-field').count(), service.fields.length);
      for (const field of service.fields) { const control = page.locator(`#business-field-${field.id}`); assert.equal(await control.count(), 1); assert.equal(await control.evaluate(element => element.required), Boolean(field.required)); }
      await page.locator('[data-action="business-close-form"]').first().click(); await page.locator('[data-action="business-back"]').click();
    }
    await page.locator('#business-service-search').fill('');
    for (const width of [1440, 390, 320]) { await page.setViewportSize({ width, height: 1000 }); await inspect(page, 'catalog'); }
    // Exercise record pagination using temporary local fixtures and actual APIs.
    const fixtures = await page.evaluate(async () => {
      const session = await (await fetch('/api/auth/session')).json(); const ids = [];
      for (let start = 0; start < 101; start += 10) {
        const results = await Promise.all(Array.from({ length: Math.min(10, 101 - start) }, async (_, index) => {
          const response = await fetch('/api/business/records', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token }, body: JSON.stringify({ service_id: 'clients', title: `Attività temporanea ${start + index}`, details: { company: 'Azienda locale di prova', request: 'Prova paginazione' } }) });
          return { status: response.status, body: await response.json() };
        }));
        for (const result of results) { if (result.status !== 201) throw new Error(JSON.stringify(result)); ids.push(result.body.item.id); }
      }
      return ids;
    });
    assert.equal(fixtures.length, 101);
    await page.reload(); await page.waitForSelector('#app-content:not([hidden])');
    await page.locator('#business-service-search').fill('Clienti e opportunità');
    await page.locator('[data-service-id="clients"] [data-action="open-business"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.business-record').length === 100);
    await page.locator('[data-action="business-record-page"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.business-record').length === 1);
    await page.locator('.business-record [data-action="business-open-record"]').click(); await page.waitForSelector('#business-detail-dialog[open]');
    await page.locator('[data-action="business-close-detail"]').click(); await page.locator('[data-action="business-back"]').click();
    // The chat proposes a real local module, without creating a record.
    await page.locator('#chat-input').fill('Vorrei preparare un preventivo'); await page.locator('#chat-form button[type="submit"]').click();
    await page.waitForSelector('#chat-suggestion [data-action="open-business"]');
    assert.equal(await page.locator('#chat-suggestion [data-action="open-business"]').getAttribute('data-service'), 'quotes');
    await page.locator('#chat-suggestion [data-action="open-business"]').click();
    await page.waitForFunction(() => document.querySelector('#business-module-title').textContent === 'Preventivi');
    assert.equal((await api(page, '/api/business/records')).body.total, 101, 'Chat suggestions never create business records.');
    assert.deepEqual(errors, [], 'No JavaScript errors.'); assert.deepEqual(remoteRequests, [], 'Only isolated localhost requests.');
    console.log('Business browser PASS: 23 guided modules, catalog filters, private blank workspace, quote with VAT, export, edit and erase fields, complete/reopen/delete, proactive overdue dashboard, pending integrations, 101-record pagination, chat suggestions without writes, dialog focus, 1440/390/320 responsive controls.');
  } finally {
    await probe.dispose(); if (browser) await browser.close();
    if (server.exitCode === null) { server.kill('SIGTERM'); await Promise.race([new Promise(resolve => server.once('exit', resolve)), pause(2000)]); if (server.exitCode === null) server.kill('SIGKILL'); }
    await fs.rm(runtime, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
