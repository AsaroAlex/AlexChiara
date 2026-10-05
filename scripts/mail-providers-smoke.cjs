/* Isolated multi-provider UI smoke. Mail routes are mocked; no real mailbox is used.
 * Run: node scripts/mail-providers-smoke.cjs
 */
'use strict';

const { chromium, request } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const port = Number(process.env.FILO_MAIL_SMOKE_PORT || 8027);
assert.ok(Number.isInteger(port) && port >= 1024 && port <= 65535);
const origin = `http://127.0.0.1:${port}`;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const presets = [
  { id: 'gmail', label: 'Gmail', kind: 'oauth', configured: false },
  { id: 'outlook', label: 'Outlook / Microsoft 365', kind: 'oauth', configured: false },
  { id: 'icloud', label: 'iCloud Mail', kind: 'imap', configured: true, host: 'imap.mail.me.com' },
  { id: 'yahoo', label: 'Yahoo Mail', kind: 'imap', configured: true, host: 'imap.mail.yahoo.com' },
  { id: 'aruba', label: 'Aruba', kind: 'imap', configured: true, host: 'imaps.aruba.it' },
  { id: 'libero', label: 'Libero Mail', kind: 'imap', configured: true, host: 'imapmail.libero.it' },
  { id: 'imap', label: 'Altra casella IMAP', kind: 'imap', configured: true },
];

(async () => {
  const runtime = await fs.mkdtemp(path.join(os.tmpdir(), 'filo-mail-ui-'));
  const env = { ...process.env };
  for (const key of Object.keys(env)) if (/^(STRIPE_|FILO_GOOGLE_|FILO_MICROSOFT_|FILO_AI_|OPENAI_|FILO_PLAN_|FILO_PUBLIC_URL$)/.test(key)) delete env[key];
  Object.assign(env, { ALEXCHIARA_DATA_DIR: runtime, ALEXCHIARA_ALLOWED_HOSTS: '127.0.0.1,localhost', FILO_ACCESS_USERNAME: 'filo', FILO_ACCESS_PASSWORD: 'mail-ui-owner-password', FILO_REAL_DATA_ONLY: '1', FILO_AI_ENABLED: '0' });
  const server = spawn(path.join(root, '.venv', 'bin', 'python'), ['-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port)], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = '', spawnError, browser;
  server.on('error', error => { spawnError = error; });
  for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => { logs = (logs + chunk).slice(-5000); });
  const probe = await request.newContext({ baseURL: origin });
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      if (spawnError) throw spawnError;
      if (server.exitCode !== null) throw new Error(`Server exited: ${logs}`);
      if (logs.includes('Uvicorn running on')) try { ready = (await probe.get('/api/health', { timeout: 1000 })).ok(); } catch (_) {}
      if (ready) break;
      await wait(100);
    }
    assert.ok(ready, `Server not ready: ${logs}`);
    browser = await chromium.launch({ executablePath: process.env.FILO_CHROMIUM_PATH || (require('node:fs').existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined), headless: true, args: ['--no-sandbox'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, locale: 'it-IT' });
    const page = await context.newPage();
    let connection = { provider: null, status: 'disconnected', label: null };
    let imapFailure = true, member = false;
    const errors = [], externalRequests = [], mailMutations = [], popups = [], oauthNavigations = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('popup', popup => { popups.push(popup.url()); popup.close(); });
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.origin !== origin) {
        if (['accounts.google.com', 'login.microsoftonline.com'].includes(url.hostname) && req.isNavigationRequest()) { oauthNavigations.push(url.hostname); return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Autorizzazione di test</title><p>Provider simulato, nessun accesso reale.</p>' }); }
        externalRequests.push(url.href); return route.abort();
      }
      if (url.pathname === '/api/mail/providers') return route.fulfill({ json: { providers: presets.map(item => ({ ...item, redirect_uri: `${origin}/api/${item.id}/oauth/callback` })), connection } });
      if (url.pathname === '/api/bootstrap') { const response = await route.fetch(); const body = await response.json(); body.connection = connection; if (member && body.user) body.user.role = 'member'; return route.fulfill({ response, json: body }); }
      if (url.pathname === '/api/mail/imap/connect') {
        mailMutations.push(req.method() + ' ' + url.pathname);
        const body = req.postDataJSON(); assert.equal(body.password, 'synthetic-mail-app-password'); assert.equal(body.provider, 'icloud'); assert.equal(body.email, 'mail-ui@example.com');
        if (imapFailure) return route.fulfill({ status: 400, json: { detail: 'Le credenziali non sono valide. Controlla la password per app e riprova.' } });
        connection = { provider: 'imap', mail_provider: 'icloud', status: 'connected', label: body.email };
        return route.fulfill({ json: { connection } });
      }
      if (url.pathname === '/api/mail/disconnect') { mailMutations.push(req.method() + ' ' + url.pathname); connection = { provider: null, status: 'disconnected', label: null }; return route.fulfill({ json: { connection } }); }
      if (['/api/gmail/oauth/start', '/api/outlook/oauth/start'].includes(url.pathname)) {
        const provider = url.pathname.includes('gmail') ? 'gmail' : 'outlook';
        assert.equal(req.method(), 'POST'); assert.ok(req.headers()['x-csrf-token']);
        mailMutations.push(req.method() + ' ' + url.pathname);
        return route.fulfill({ json: { authorization_url: `https://${provider === 'gmail' ? 'accounts.google.com' : 'login.microsoftonline.com'}/mock-authorize` } });
      }
      return route.continue();
    });
    await page.goto(`${origin}/login`);
    await page.waitForSelector('#auth-form:not([hidden])');
    await page.locator('#auth-email').fill('filo'); await page.locator('#auth-password').fill('mail-ui-owner-password'); await page.locator('#auth-submit').click();
    await page.waitForSelector('#app-content:not([hidden])');
    await page.locator('[data-page="connections"]').click();
    assert.equal(await page.locator('#mail-provider-list .provider-card').count(), 7);
    assert.equal(await page.locator('#mail-provider-list button:disabled').count(), 0);

    await page.getByRole('button', { name: 'Prepara il collegamento Gmail', exact: true }).click();
    assert.ok(await page.locator('#mail-provider-setup').getByRole('link', { name: 'Apri Google Cloud' }).isVisible());
    assert.equal(await page.locator('#mail-provider-setup code').innerText(), `${origin}/api/gmail/oauth/callback`);
    await page.getByRole('button', { name: 'Prepara il collegamento Microsoft', exact: true }).click();
    assert.ok(await page.locator('#mail-provider-setup').getByRole('link', { name: 'Apri Microsoft Entra' }).isVisible());
    assert.match(await page.locator('#mail-provider-setup').innerText(), /FILO_MICROSOFT_CLIENT_ID/);

    await fs.mkdir(path.join(root, '.runtime'), { recursive: true });
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 1050 });
      const dimensions = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: innerWidth }));
      assert.ok(dimensions.page <= dimensions.viewport + 1, `Provider page overflow at ${width}: ${JSON.stringify(dimensions)}`);
      const sizes = await page.locator('#mail-provider-list button').evaluateAll(items => items.map(item => item.getBoundingClientRect().height));
      assert.ok(sizes.every(height => height >= 44), 'Provider controls must be comfortable to tap.');
      await page.screenshot({ path: path.join(root, '.runtime', `mail-providers-${width}.png`), fullPage: true });
      await page.getByRole('button', { name: 'Collega la casella', exact: true }).click();
      await page.waitForSelector('#imap-dialog[open]');
      assert.equal(await page.locator('#imap-host').isVisible(), true);
      assert.equal(await page.locator('#imap-host').getAttribute('required'), '');
      await page.locator('#imap-password').fill('secret-cleared-on-close');
      await page.locator('#imap-dialog [data-action="close-imap"]').first().click();
      assert.equal(await page.locator('#imap-password').inputValue(), '');
    }
    await page.setViewportSize({ width: 390, height: 1050 });
    await page.getByRole('button', { name: 'Collega iCloud Mail', exact: true }).click();
    await page.locator('#imap-email').fill('mail-ui@example.com'); await page.locator('#imap-password').fill('secret-cleared-on-escape'); await page.keyboard.press('Escape');
    assert.equal(await page.locator('#imap-password').inputValue(), '');
    await page.getByRole('button', { name: 'Collega iCloud Mail', exact: true }).click();
    assert.equal(await page.locator('#imap-host').isVisible(), false);
    assert.ok(await page.locator('#imap-help-link').isVisible());
    await page.locator('#imap-email').fill('mail-ui@example.com'); await page.locator('#imap-password').fill('synthetic-mail-app-password');
    await page.locator('#imap-form button[type="submit"]').click();
    await page.waitForSelector('#imap-error:not([hidden])');
    assert.equal(await page.locator('#imap-password').inputValue(), '');
    assert.ok(await page.locator('#imap-dialog').isVisible());
    const stored = await page.evaluate(() => [...Object.values(localStorage), ...Object.values(sessionStorage)].join('\n'));
    assert.doesNotMatch(stored, /synthetic-mail-app-password|secret-cleared/);
    imapFailure = false;
    await page.locator('#imap-password').fill('synthetic-mail-app-password'); await page.locator('#imap-form button[type="submit"]').click();
    await page.waitForSelector('#imap-dialog:not([open])', { state: 'attached' });
    await page.waitForFunction(() => document.querySelector('#mailbox-connection').textContent.includes('mail-ui@example.com'));
    assert.match(await page.locator('#mailbox-connection').innerText(), /iCloud Mail/);
    assert.equal(await page.locator('#imap-password').inputValue(), '');
    await page.getByRole('button', { name: 'Collega Yahoo Mail', exact: true }).click();
    await page.waitForSelector('#mail-confirm-dialog[open]');
    assert.match(await page.locator('#mail-confirm-description').innerText(), /autorizzare di nuovo/);
    await page.locator('#mail-confirm-dialog [data-action="close-mail-confirm"]').first().click();
    assert.equal(mailMutations.length, 2, 'Cancelling a switch must not alter the mailbox.');
    await page.getByRole('button', { name: 'Scollega la casella', exact: true }).click();
    await page.locator('#mail-confirm-button').click();
    await page.waitForFunction(() => document.querySelector('#mailbox-connection').textContent.includes('Nessuna casella collegata'));
    assert.equal(mailMutations.at(-1), 'POST /api/mail/disconnect');
    member = true;
    await page.reload(); await page.waitForSelector('#app-content:not([hidden])');
    await page.locator('#mail-provider-list .provider-card').filter({ has: page.getByRole('heading', { name: 'Gmail', exact: true }) }).getByRole('button').click();
    assert.doesNotMatch(await page.locator('#mail-provider-setup').innerText(), /FILO_GOOGLE|Railway|Google Cloud/);
    assert.match(await page.locator('#mail-provider-setup').innerText(), /amministratore/);
    member = false; presets[0].configured = true; presets[1].configured = true;
    await page.reload(); await page.waitForSelector('#app-content:not([hidden])');
    await page.getByRole('button', { name: 'Collega Gmail', exact: true }).click();
    await page.waitForURL('https://accounts.google.com/mock-authorize');
    connection = { provider: 'gmail', status: 'connected', label: 'oauth-ui@example.com' };
    await page.goto(`${origin}/app?gmail=connected`); await page.waitForSelector('#app-content:not([hidden])');
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('Gmail collegata'));
    assert.equal(new URL(page.url()).search, '');
    assert.equal(new URL(page.url()).hash, '#connections');
    await page.getByRole('button', { name: 'Collega Outlook', exact: true }).click();
    await page.waitForSelector('#mail-confirm-dialog[open]'); await page.locator('#mail-confirm-button').click();
    await page.waitForURL('https://login.microsoftonline.com/mock-authorize');
    connection = { provider: 'outlook', status: 'connected', label: 'outlook-ui@example.com' };
    await page.goto(`${origin}/app?provider=outlook&connected=1`); await page.waitForSelector('#app-content:not([hidden])');
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('Microsoft 365 collegata'));
    assert.match(await page.locator('#mailbox-connection').innerText(), /Outlook \/ Microsoft 365/);
    assert.equal(new URL(page.url()).search, '');
    assert.deepEqual(oauthNavigations, ['accounts.google.com', 'login.microsoftonline.com']);
    assert.deepEqual(errors, []); assert.deepEqual(popups, []); assert.deepEqual(externalRequests, []);
    console.log('Browser PASS: seven providers, owner/member setup guides, 1440/390/320px layouts, secure IMAP form, password cleared on close/Escape/failure/success, switching confirmation, generic disconnect, same-tab Gmail/Microsoft OAuth and callback recovery. All mail/provider routes mocked; no provider calls or real data.');
  } finally {
    if (browser) await browser.close(); await probe.dispose();
    server.kill('SIGTERM'); await Promise.race([new Promise(resolve => server.once('exit', resolve)), wait(3000)]);
    if (server.exitCode === null) server.kill('SIGKILL');
    await fs.rm(runtime, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });
