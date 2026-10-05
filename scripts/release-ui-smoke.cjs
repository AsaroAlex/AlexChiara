/* Browser regressions for the release review of the interface.
 * Run: node scripts/release-ui-smoke.cjs (starts/stops its own isolated server).
 * Uses a temporary data folder, a fictional owner and no external provider.
 */
'use strict';

const { chromium, request } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const port = Number(process.env.FILO_PORT || 8044);
assert.ok(Number.isInteger(port) && port >= 1024 && port <= 65535, 'Use a valid local test port.');
const origin = `http://127.0.0.1:${port}`;
const ownerPassword = 'release-ui-owner-secret';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function login(page) {
  await page.goto(`${origin}/login`);
  await page.waitForSelector('#auth-form:not([hidden])');
  await page.fill('#auth-email', 'filo');
  await page.fill('#auth-password', ownerPassword);
  await page.click('#auth-submit');
  await page.waitForURL(`${origin}/app`);
  await page.waitForSelector('#app-content:not([hidden])');
}

(async () => {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'spazelia-release-ui-'));
  const env = { ...process.env, ALEXCHIARA_DATA_DIR: data, FILO_ACCESS_PASSWORD: ownerPassword, FILO_ACCESS_USERNAME: 'filo', FILO_AI_ENABLED: '' };
  delete env.FILO_REAL_DATA_ONLY;
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
    for (const [route, type] of [['/favicon.ico', 'image/x-icon'], ['/apple-touch-icon.png', 'image/png'], ['/robots.txt', 'text/plain']]) {
      const response = await probe.get(route);
      assert.equal(response.status(), 200, route);
      assert.ok(response.headers()['content-type'].startsWith(type), route);
    }

    browser = await chromium.launch({ executablePath: process.env.FILO_CHROMIUM_PATH || (require('node:fs').existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined), headless: true, args: ['--no-sandbox'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'it-IT', timezoneId: 'Europe/Rome' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => (new URL(route.request().url()).origin === origin ? route.continue() : route.abort()));

    // Registration with a name made only of spaces explains which field to fix.
    await page.goto(`${origin}/register`);
    await page.waitForSelector('#auth-form:not([hidden])');
    await page.fill('#auth-name', '   ');
    await page.fill('#auth-email', 'spazi@example.test');
    await page.fill('#auth-password', 'una-password-lunga-12');
    await page.click('#auth-submit');
    await page.waitForSelector('#auth-error:not([hidden])');
    assert.equal((await page.textContent('#auth-error')).trim(), 'Inserisci il tuo nome.');

    // Unknown pages render the Italian 404 page.
    const missing = await page.goto(`${origin}/pagina-inesistente`);
    assert.equal(missing.status(), 404);
    assert.match(await page.textContent('h1'), /Questa pagina/);

    await login(page);

    // Signed-in visitors of the homepage find no sign-up or sign-in links left.
    await page.goto(`${origin}/`);
    await page.waitForFunction(() => document.querySelector('#hero-start')?.getAttribute('href') === '/app');
    assert.equal(await page.locator('a[href="/register"], a[href="/login"]').count(), 0);

    // The skip link moves focus to the content without leaving the current page.
    await page.goto(`${origin}/app#services`);
    await page.waitForSelector('#page-services:not([hidden])');
    await page.evaluate(() => { location.hash = 'main-content'; });
    await pause(300);
    assert.equal(await page.isVisible('#page-services'), true, 'The skip link must not switch to the overview.');

    // A busy button keeps its icon after the operation.
    await page.goto(`${origin}/app#company`);
    await page.waitForSelector('#page-company:not([hidden])');
    await page.fill('#company-name', 'Studio Prova Interfaccia');
    const save = page.locator('#company-form button[type="submit"]');
    assert.equal(await save.locator('svg').count(), 1);
    await save.click();
    await page.waitForFunction(() => !document.querySelector('#company-form button[type="submit"]').disabled);
    assert.equal(await save.locator('svg').count(), 1, 'The save button lost its icon.');

    // Server validation messages are Italian.
    await page.fill('#company-name', '   ');
    await page.evaluate(() => document.querySelector('#company-name').removeAttribute('required'));
    await save.click();
    await page.waitForFunction(() => /Compila|almeno/.test(document.querySelector('#company-save-status')?.textContent || '') || document.querySelector('#toast:not([hidden])'));
    const shown = await page.evaluate(() => `${document.querySelector('#company-save-status')?.textContent || ''} ${document.querySelector('#toast')?.textContent || ''}`);
    assert.doesNotMatch(shown, /String should|Field required|Input should/, shown);

    // Selecting text inside a dialog and releasing outside keeps it open.
    await page.goto(`${origin}/app#services`);
    await page.waitForSelector('#page-services:not([hidden])');
    await page.locator('[data-service-id="quotes"] [data-action="open-business"]').click();
    await page.locator('#business-workspace [data-action="business-new"]').first().click();
    await page.waitForSelector('#business-form-dialog[open]');
    const title = page.locator('#business-form-dialog input').first();
    await title.fill('Testo da non perdere');
    const box = await title.boundingBox();
    await page.mouse.move(box.x + box.width - 4, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(5, 5, { steps: 8 });
    await page.mouse.up();
    await pause(200);
    assert.equal(await page.isVisible('#business-form-dialog[open]'), true, 'Dragging a selection outside closed the dialog.');
    assert.equal(await title.inputValue(), 'Testo da non perdere');
    // A press that starts on the backdrop still closes it.
    await page.mouse.click(5, 5);
    await pause(200);
    assert.equal(await page.locator('#business-form-dialog[open]').count(), 0, 'Backdrop click must close the dialog.');

    // A failed provider return shows a readable message on Collegamenti.
    await page.goto(`${origin}/app?mail_error=gmail#connections`);
    await page.waitForSelector('#toast:not([hidden])');
    assert.match(await page.textContent('#toast'), /Gmail/);
    assert.equal(new URL(page.url()).search, '', 'The error marker is removed from the address.');

    assert.deepEqual(errors, []);
    console.log('Release UI PASS: Italian name and validation messages, 404 page, public icons and robots, signed-in homepage links, skip link stays on page, busy buttons keep icons, text selection keeps dialogs open, backdrop closes them, readable OAuth failure notice; no JS errors.');
  } finally {
    if (browser) await browser.close();
    await probe.dispose();
    server.kill('SIGTERM');
    await fs.rm(data, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });
