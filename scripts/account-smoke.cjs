/* Isolated browser smoke for the public site, form login and private accounts.
 * Run: node scripts/account-smoke.cjs (starts/stops its own local test server).
 * This never uses Railway data, native HTTP Basic credentials or real providers.
 */
'use strict';

const { chromium, request } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const port = Number(process.env.FILO_PORT || 8024);
assert.ok(Number.isInteger(port) && port >= 1024 && port <= 65535, 'Use a valid local test port.');
const origin = `http://127.0.0.1:${port}`;
const password = 'synthetic-account-password-12';
const ownerPassword = 'owner-browser-secret';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function api(page, endpoint, method = 'GET', body) {
  return page.evaluate(async ({ endpoint, method, body }) => {
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') {
      const session = await (await fetch('/api/auth/session')).json();
      headers['X-CSRF-Token'] = session.csrf_token;
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(endpoint, {
      method, headers, credentials: 'same-origin',
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return {
      status: response.status,
      challenge: response.headers.get('www-authenticate'),
      body: await response.json(),
    };
  }, { endpoint, method, body });
}

async function inspect(page, width, label) {
  const measurements = await page.evaluate(() => ({
    viewport: innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    smallControls: [...document.querySelectorAll('button,input:not([type="checkbox"]):not([type="hidden"]),a.button,summary')]
      .filter(element => {
        const bounds = element.getBoundingClientRect();
        return bounds.width && bounds.height && getComputedStyle(element).visibility !== 'hidden';
      })
      .map(element => ({
        control: element.id || element.textContent.trim().slice(0, 40),
        height: element.getBoundingClientRect().height,
      })).filter(element => element.height < 43.5),
  }));
  await page.screenshot({ path: path.join(root, '.runtime', `account-${label}-${width}.png`), fullPage: true });
  assert.equal(measurements.viewport, width);
  assert.ok(measurements.scrollWidth <= width + 1, `${label} overflows at ${width}px: ${JSON.stringify(measurements)}`);
  assert.deepEqual(measurements.smallControls, [], `${label}: controls must be at least 44px high.`);
}

async function openLogin(page, next = '/app') {
  await page.goto(`${origin}/login?next=${encodeURIComponent(next)}`);
  await page.waitForSelector('#auth-form:not([hidden])');
}

async function login(page, email, secret = password, next = '/app') {
  await openLogin(page, next);
  await page.locator('#auth-email').fill(email);
  await page.locator('#auth-password').fill(secret);
  await page.locator('#auth-submit').click();
  await page.waitForURL(`${origin}${next}`);
  await page.waitForSelector(next === '/app' ? '#app-content:not([hidden])' : '#account-content:not([hidden])');
}

async function register(page, email, name) {
  await page.goto(`${origin}/register`);
  await page.waitForSelector('#auth-form:not([hidden])');
  await page.locator('#auth-name').fill(name);
  await page.locator('#auth-email').fill(email);
  await page.locator('#auth-password').fill(password);
  await page.locator('#auth-submit').click();
  await page.waitForURL(`${origin}/app`);
  try {
    await page.waitForSelector('#app-content:not([hidden])');
  } catch (error) {
    const message = await page.locator('#load-error').innerText().catch(() => 'No app error notice.');
    throw new Error(`Private app did not load after signup: ${message}`, { cause: error });
  }
  const session = await api(page, '/api/auth/session');
  assert.equal(session.body.authenticated, true);
  assert.equal(session.body.user.email, email);
  assert.equal(session.body.user.role, 'member');
  assert.notEqual(session.body.user.id, 'owner');
  return session.body.user;
}

async function logout(page) {
  await page.goto(`${origin}/account`);
  await page.waitForSelector('#account-content:not([hidden])');
  await page.locator('#account-logout').click();
  await page.waitForURL(`${origin}/`);
  assert.equal((await api(page, '/api/auth/session')).body.authenticated, false);
}

(async () => {
  const runtime = await fs.mkdtemp(path.join(os.tmpdir(), 'filo-account-smoke-'));
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (/^(STRIPE_|FILO_GOOGLE_|FILO_MICROSOFT_|FILO_AI_|OPENAI_|FILO_PLAN_|FILO_PUBLIC_URL$)/.test(key)) delete env[key];
  }
  Object.assign(env, {
    ALEXCHIARA_DATA_DIR: runtime,
    ALEXCHIARA_ALLOWED_HOSTS: '127.0.0.1,localhost',
    FILO_ACCESS_USERNAME: 'filo',
    FILO_ACCESS_PASSWORD: ownerPassword,
    FILO_REAL_DATA_ONLY: '1',
    FILO_AI_ENABLED: '0',
  });
  const server = spawn(path.join(root, '.venv', 'bin', 'python'), [
    '-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port),
  ], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = '';
  let serverError;
  let browser;
  server.on('error', error => { serverError = error; });
  for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => { logs = (logs + chunk).slice(-4000); });
  const probe = await request.newContext({ baseURL: origin });
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      if (serverError) throw serverError;
      if (server.exitCode !== null) throw new Error(`Isolated server exited: ${logs}`);
      // Probe only after our child has bound the port. An unrelated server on
      // the requested port must never receive registration or workspace writes.
      if (!logs.includes('Uvicorn running on')) { await pause(100); continue; }
      try { ready = (await probe.get('/api/health', { timeout: 1000 })).ok(); } catch (_) { /* Server is starting. */ }
      if (ready) break;
      await pause(100);
    }
    assert.ok(ready, `Isolated server did not become ready: ${logs}`);
    await fs.mkdir(path.join(root, '.runtime'), { recursive: true });
    for (const route of ['/api/bootstrap', '/api/agenda/export', '/api/billing/status']) {
      const response = await probe.get(route);
      assert.equal(response.status(), 401, `${route} must require login.`);
      assert.equal(response.headers()['www-authenticate'], undefined, 'Never invoke the native browser login popup.');
    }
    const redirect = await probe.get('/app', { maxRedirects: 0 });
    assert.equal(redirect.status(), 303);
    assert.match(redirect.headers().location, /^\/login\?next=/);
    assert.equal(redirect.headers()['www-authenticate'], undefined);

    browser = await chromium.launch({
      executablePath: process.env.FILO_CHROMIUM_PATH || '/usr/bin/chromium',
      headless: true, args: ['--no-sandbox'],
    });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'it-IT', timezoneId: 'America/Los_Angeles' });
    const page = await context.newPage();
    const errors = [], externalRequests = [], mutations = [], nativeChallenges = [], popups = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('popup', popup => { popups.push(popup.url()); popup.close(); });
    page.on('response', response => {
      if (response.headers()['www-authenticate']) nativeChallenges.push(response.url());
    });
    await page.route('**/*', route => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.origin !== origin) {
        externalRequests.push(req.url());
        return route.abort();
      }
      if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method())) mutations.push(`${req.method()} ${url.pathname}`);
      return route.continue();
    });

    // Public homepage and an ordinary login form stay open across reloads.
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const home = await page.goto(origin);
      assert.equal(home.status(), 200);
      assert.equal(await page.locator('h1').count(), 1);
      assert.equal(await page.locator('#landing-login').getAttribute('href'), '/login');
      await inspect(page, width, 'home');
      await page.locator('#landing-login').click();
      await page.waitForSelector('#auth-form:not([hidden])');
      await page.reload();
      await page.waitForSelector('#auth-form:not([hidden])');
      assert.equal(await page.locator('#auth-password').getAttribute('type'), 'password');
      await inspect(page, width, 'login');
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#auth-email').fill('not-a-real-account@example.com');
    await page.locator('#auth-password').fill('invalid-browser-secret');
    await page.locator('#auth-submit').click();
    await page.waitForSelector('#auth-error:not([hidden])');
    assert.equal(await page.locator('#auth-error').innerText(), 'Email o password non corrette.');
    assert.doesNotMatch(await page.locator('#auth-error').innerText(), /invalid-browser-secret|traceback|scrypt/i);
    assert.equal(await page.locator('#auth-submit').isEnabled(), true);
    await page.locator('#password-toggle').click();
    assert.equal(await page.locator('#auth-password').getAttribute('type'), 'text');
    await page.locator('#password-toggle').click();
    assert.equal(await page.locator('#auth-password').getAttribute('type'), 'password');

    await page.goto(`${origin}/register`);
    await page.waitForSelector('#auth-form:not([hidden])');
    assert.equal(await page.locator('#auth-password').getAttribute('minlength'), '12');
    assert.equal(await page.locator('#auth-name').getAttribute('required'), '');
    await page.locator('#auth-name').fill('Account Browser A');
    await page.locator('#auth-email').fill('browser-a@example.com');
    await page.locator('#auth-password').fill('short');
    const registrationAttempts = mutations.filter(item => item === 'POST /api/auth/register').length;
    await page.locator('#auth-submit').click();
    assert.equal(await page.locator('#auth-password').evaluate(input => input.validity.tooShort), true);
    assert.equal(mutations.filter(item => item === 'POST /api/auth/register').length, registrationAttempts);
    await inspect(page, 1440, 'register');
    await page.setViewportSize({ width: 390, height: 900 });
    await inspect(page, 390, 'register');
    await page.setViewportSize({ width: 1440, height: 1000 });

    const userA = await register(page, 'browser-a@example.com', 'Account Browser A');
    const emptyA = (await api(page, '/api/bootstrap')).body;
    assert.equal(emptyA.real_data_only, true);
    assert.equal(emptyA.company.name, '');
    assert.deepEqual(emptyA.agenda.items, []);
    assert.equal(emptyA.connection.status, 'disconnected');
    await page.locator('#agenda-add > summary').click();
    await page.locator('#agenda-event-title').fill('Appuntamento privato Browser A');
    await page.locator('#agenda-event-date').fill(emptyA.agenda.date);
    await page.locator('#agenda-event-time').fill('09:30');
    await page.locator('#agenda-form button[type="submit"]').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.agenda-event h3')].some(node => node.textContent === 'Appuntamento privato Browser A'));
    const eventA = (await api(page, '/api/bootstrap')).body.agenda.items[0];
    await page.reload();
    await page.waitForSelector('#app-content:not([hidden])');
    assert.equal((await api(page, '/api/auth/session')).body.user.id, userA.id, 'Cookie login must survive refresh.');
    assert.equal(await page.locator('.agenda-event h3').first().innerText(), 'Appuntamento privato Browser A');
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await inspect(page, width, 'app');
      await page.goto(`${origin}/account?billing=success`);
      await page.waitForSelector('#account-content:not([hidden])');
      await page.waitForSelector('#billing-unavailable:not([hidden])');
      assert.equal(await page.locator('#profile-email').innerText(), 'browser-a@example.com');
      assert.match(await page.locator('#plan-price').innerText(), /definizione|configurazione/i);
      assert.equal(await page.locator('#billing-checkout').isVisible(), false);
      assert.equal(await page.locator('#billing-portal').isVisible(), false);
      assert.equal(await page.locator('#billing-return-notice').isVisible(), true);
      assert.equal(await page.locator('#subscription-status').innerText(), 'Nessun abbonamento');
      const billing = await api(page, '/api/billing/status');
      assert.equal(billing.body.configured, false);
      assert.equal(billing.body.subscription_status, 'free');
      await page.locator('#billing-refresh').click();
      await page.waitForFunction(() => !document.querySelector('#billing-refresh').disabled);
      await inspect(page, width, 'account');
      await page.goto(`${origin}/app`);
      await page.waitForSelector('#app-content:not([hidden])');
    }
    const unavailable = await api(page, '/api/billing/checkout', 'POST', {});
    assert.equal(unavailable.status, 503);
    assert.equal(unavailable.challenge, null);
    assert.match(unavailable.body.detail, /pagamenti non sono ancora attivi/i);

    await logout(page);
    const protectedPage = await page.goto(`${origin}/app`);
    assert.equal(protectedPage.status(), 200, 'Protected navigation should reach the form login.');
    assert.match(page.url(), /\/login\?next=/);
    await page.waitForSelector('#auth-form:not([hidden])');
    await page.reload();
    await page.waitForSelector('#auth-form:not([hidden])');
    const loggedOut = await api(page, '/api/bootstrap');
    assert.equal(loggedOut.status, 401);
    assert.equal(loggedOut.challenge, null);

    const userB = await register(page, 'browser-b@example.com', 'Account Browser B');
    assert.notEqual(userB.id, userA.id);
    const emptyB = (await api(page, '/api/bootstrap')).body;
    assert.deepEqual(emptyB.agenda.items, [], 'A new account cannot see another account’s appointments.');
    assert.deepEqual(emptyB.watches.items, []);
    assert.deepEqual(emptyB.runs, []);
    assert.deepEqual(emptyB.approvals, []);
    assert.equal(emptyB.company.name, '');
    assert.doesNotMatch(await page.locator('body').innerText(), /Appuntamento privato Browser A/);
    const crossUser = await api(page, `/api/agenda/${eventA.id}`, 'DELETE');
    assert.equal(crossUser.status, 404);
    assert.equal(crossUser.challenge, null);
    await logout(page);

    await login(page, 'browser-a@example.com');
    assert.equal((await api(page, '/api/bootstrap')).body.agenda.items[0].id, eventA.id, 'A cross-account delete must leave the original appointment intact.');
    await logout(page);
    await login(page, 'filo', ownerPassword, '/account');
    assert.equal((await api(page, '/api/auth/session')).body.user.id, 'owner');
    assert.equal(await page.locator('#profile-email').innerText(), 'filo');
    await page.reload();
    await page.waitForSelector('#account-content:not([hidden])');
    assert.equal((await api(page, '/api/auth/session')).body.authenticated, true);
    await logout(page);
    await context.close();
    assert.deepEqual(errors, [], 'No JavaScript errors.');
    assert.deepEqual(externalRequests, [], 'No real Gmail, AI or Stripe requests.');
    assert.deepEqual(nativeChallenges, [], 'No native HTTP Basic login popup.');
    assert.deepEqual(popups, [], 'Form login never opens a popup.');
    console.log('Browser PASS: public homepage, persistent form login, friendly failure, 12-character signup, private app/account, cookie reload, logout redirects, isolated workspaces and cross-user 404, existing owner login, inactive Stripe and truthful checkout return, 1440/390px without overflow or small controls; no external requests, native popup or JS errors.');
  } finally {
    await probe.dispose();
    if (browser) await browser.close();
    if (server.exitCode === null) {
      server.kill('SIGTERM');
      await Promise.race([new Promise(resolve => server.once('exit', resolve)), pause(3000)]);
      if (server.exitCode === null) server.kill('SIGKILL');
    }
    await fs.rm(runtime, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
