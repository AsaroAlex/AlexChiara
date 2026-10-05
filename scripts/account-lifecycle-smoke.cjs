/* Browser smoke for password recovery, password change and account deletion.
 * Run: node scripts/account-lifecycle-smoke.cjs (starts/stops its own isolated server).
 * Emails are written to a temporary outbox folder; no provider or Stripe call is made.
 */
'use strict';

const { chromium, request } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const port = Number(process.env.FILO_PORT || 8046);
assert.ok(Number.isInteger(port) && port >= 1024 && port <= 65535, 'Use a valid local test port.');
const origin = `http://127.0.0.1:${port}`;
const ownerPassword = 'lifecycle-owner-secret';
const password = 'password-iniziale-12';
const recovered = 'password-recuperata-34';
const changed = 'password-cambiata-56';
const email = 'ciclo@example.test';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function latestLink(outbox) {
  for (let attempt = 0; attempt < 50; attempt++) {
    const files = (await fs.readdir(outbox).catch(() => [])).filter(name => name.endsWith('.eml'));
    if (files.length) {
      const stats = await Promise.all(files.map(async name => ({ name, time: (await fs.stat(path.join(outbox, name))).mtimeMs })));
      const newest = stats.sort((a, b) => b.time - a.time)[0].name;
      const raw = await fs.readFile(path.join(outbox, newest), 'utf8');
      const decoded = raw.replace(/=\r?\n/g, '').replace(/=3D/g, '=');
      const match = decoded.match(/http:\/\/127\.0\.0\.1:\d+\/reset-password\?token=([A-Za-z0-9_-]{43})/);
      if (match) return match[0];
    }
    await pause(100);
  }
  throw new Error('No reset email reached the outbox.');
}

async function noOverflow(page, width, label) {
  const sizes = await page.evaluate(() => ({ viewport: innerWidth, scroll: document.documentElement.scrollWidth }));
  assert.equal(sizes.viewport, width);
  assert.ok(sizes.scroll <= width + 1, `${label} overflows at ${width}px`);
}

async function login(page, identifier, secret) {
  await page.goto(`${origin}/login`);
  await page.waitForSelector('#auth-form:not([hidden])');
  await page.fill('#auth-email', identifier);
  await page.fill('#auth-password', secret);
  await page.click('#auth-submit');
}

(async () => {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'spazelia-lifecycle-'));
  const outbox = path.join(data, 'outbox');
  const env = { ...process.env, ALEXCHIARA_DATA_DIR: data, FILO_ACCESS_PASSWORD: ownerPassword, FILO_ACCESS_USERNAME: 'filo', FILO_MAIL_FROM: 'Spazelia <noreply@spazelia.test>', FILO_MAIL_OUTBOX_DIR: outbox, FILO_PUBLIC_URL: origin };
  for (const key of ['FILO_RESEND_API_KEY', 'FILO_SMTP_HOST', 'STRIPE_SECRET_KEY', 'FILO_REAL_DATA_ONLY', 'RAILWAY_PUBLIC_DOMAIN']) delete env[key];
  const server = spawn(path.join(root, '.venv', 'bin', 'python'), ['-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port)], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = '';
  for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => { logs = (logs + chunk).slice(-4000); });
  const probe = await request.newContext({ baseURL: origin });
  let browser;
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100 && !ready; attempt++) {
      if (server.exitCode !== null) throw new Error(`Isolated server exited: ${logs}`);
      if (logs.includes('Uvicorn running on')) { try { ready = (await probe.get('/api/health', { timeout: 1000 })).ok(); } catch (_) { /* Starting. */ } }
      if (!ready) await pause(100);
    }
    assert.ok(ready, `Isolated server did not become ready: ${logs}`);
    browser = await chromium.launch({ executablePath: process.env.FILO_CHROMIUM_PATH || (require('node:fs').existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined), headless: true, args: ['--no-sandbox'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'it-IT', timezoneId: 'Europe/Rome' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => (new URL(route.request().url()).origin === origin ? route.continue() : route.abort()));

    // Register, then sign out.
    await page.goto(`${origin}/register`);
    await page.waitForSelector('#auth-form:not([hidden])');
    await page.fill('#auth-name', 'Ciclo Prova');
    await page.fill('#auth-email', email);
    await page.fill('#auth-password', password);
    await page.click('#auth-submit');
    await page.waitForURL(`${origin}/app`);
    await page.goto(`${origin}/account`);
    await page.waitForSelector('#account-content:not([hidden])');
    await page.click('#account-logout');
    await page.waitForURL(`${origin}/`);

    // Forgot password from the login page: same answer, link by email.
    await page.goto(`${origin}/login`);
    await page.waitForSelector('#auth-form:not([hidden])');
    await page.click('#auth-forgot-link');
    await page.waitForURL(`${origin}/forgot-password`);
    await page.waitForSelector('#auth-form:not([hidden])');
    assert.equal(await page.isVisible('#auth-password'), false);
    for (const width of [1440, 390]) { await page.setViewportSize({ width, height: 900 }); await noOverflow(page, width, 'forgot'); }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.fill('#auth-email', email);
    await page.click('#auth-submit');
    await page.waitForSelector('#auth-success:not([hidden])');
    assert.match(await page.textContent('#auth-success'), /riceverai a breve un'email/);

    // The emailed link opens the reset page; the token leaves the address bar.
    const link = await latestLink(outbox);
    await page.goto(link);
    await page.waitForSelector('#auth-form:not([hidden])');
    assert.equal(new URL(page.url()).search, '', 'The token must be removed from the address bar.');
    assert.equal(await page.isVisible('#auth-email'), false);
    await page.fill('#auth-password', 'corta');
    await page.click('#auth-submit');
    // The browser's own minimum-length check stops a short password before sending.
    assert.equal(await page.$eval('#auth-password', element => element.validity.tooShort), true);
    assert.equal(new URL(page.url()).pathname, '/reset-password');
    await page.fill('#auth-password', recovered);
    await page.click('#auth-submit');
    await page.waitForURL(`${origin}/app`);

    // The same link cannot be used twice.
    const reuse = await context.newPage();
    await reuse.goto(link);
    await reuse.waitForSelector('#auth-form:not([hidden])');
    await reuse.fill('#auth-password', 'ennesima-password-78');
    await reuse.click('#auth-submit');
    await reuse.waitForSelector('#auth-error:not([hidden])');
    assert.match(await reuse.textContent('#auth-error'), /non è valido o è scaduto/);
    await reuse.close();

    // Change the password from the account page.
    await page.goto(`${origin}/account`);
    await page.waitForSelector('#account-content:not([hidden])');
    for (const width of [1440, 390]) { await page.setViewportSize({ width, height: 900 }); await noOverflow(page, width, 'account'); }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.fill('#current-password', 'non-e-quella-giusta');
    await page.fill('#new-password', changed);
    await page.click('#password-submit');
    await page.waitForSelector('#password-error:not([hidden])');
    assert.match(await page.textContent('#password-error'), /attuale non è corretta/);
    await page.fill('#current-password', recovered);
    await page.click('#password-submit');
    await page.waitForSelector('#password-success:not([hidden])');

    // Delete the account: wrong confirmation first, then the real deletion.
    await page.fill('#delete-password', changed);
    await page.fill('#delete-confirmation', 'cancella');
    await page.click('#delete-submit');
    await page.waitForSelector('#delete-error:not([hidden])');
    assert.match(await page.textContent('#delete-error'), /ELIMINA/);
    await page.fill('#delete-confirmation', 'ELIMINA');
    await page.click('#delete-submit');
    await page.waitForURL(`${origin}/`);
    await page.waitForSelector('.deleted-notice');
    assert.match(await page.textContent('.deleted-notice'), /eliminati/);
    await login(page, email, changed);
    await page.waitForSelector('#auth-error:not([hidden])');
    assert.match(await page.textContent('#auth-error'), /non corrette/);

    // The owner sees why recovery and deletion are not available here.
    await login(page, 'filo', ownerPassword);
    await page.waitForURL(`${origin}/app`);
    await page.goto(`${origin}/account`);
    await page.waitForSelector('#account-content:not([hidden])');
    assert.equal(await page.isVisible('#security-owner-note'), true);
    assert.equal(await page.isVisible('#delete-owner-note'), true);
    assert.equal(await page.isVisible('#delete-form'), false);

    assert.deepEqual(errors, []);
    console.log('Account lifecycle PASS: forgot-password link from login, identical answer, emailed single-use link, token removed from the address bar, short password refused, reset signs in, password change with wrong and right current password, typed confirmation, deletion with landing notice and no further login, owner notes, 1440/390px without overflow; no JS errors.');
  } finally {
    if (browser) await browser.close();
    await probe.dispose();
    server.kill('SIGTERM');
    await fs.rm(data, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });
