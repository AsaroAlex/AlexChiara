/* Read-only browser check for a fresh FILO_REAL_DATA_ONLY=1 local instance.
 * Requires Playwright/Chromium, FILO_ACCESS_PASSWORD=real-mode-test and no Google credentials.
 * Run: FILO_PORT=8014 node scripts/real-mode-smoke.cjs
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

const origin = `http://127.0.0.1:${process.env.FILO_PORT || '8014'}`;
const sampleText = /Studio Riva|Giulia Conti|Marco Bianchi|Sara Rossi|@example\.com/;

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.FILO_CHROMIUM_PATH || (require('node:fs').existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined),
    headless: true,
    args: ['--no-sandbox'],
  });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const guest = await (await page.request.get(`${origin}/api/auth/session`)).json();
      const login = await page.request.post(`${origin}/api/auth/login`, {
        headers: { 'X-CSRF-Token': guest.csrf_token },
        data: { email: 'filo', password: process.env.FILO_TEST_OWNER_PASSWORD || 'real-mode-test' },
      });
      assert.equal(login.status(), 200, 'Use the isolated real-mode-test owner account.');
      const errors = [];
      const mutations = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        const request = route.request();
        if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
          mutations.push(`${request.method()} ${new URL(request.url()).pathname}`);
          return route.abort();
        }
        return route.continue();
      });
      await page.goto(`${origin}/app`);
      await page.waitForSelector('#app-content:not([hidden])');
      const state = () => page.evaluate(async () => (await fetch('/api/bootstrap')).json());
      const before = await state();
      assert.equal(before.real_data_only, true);
      assert.equal(before.company.needs_setup, true, 'Use a fresh real-mode instance.');
      assert.equal(before.company.name, '');
      assert.equal(before.service.status, 'inactive');
      for (const values of [before.service.priority_contacts, before.runs, before.approvals,
        before.actions, before.briefing.contacts, before.briefing.priorities,
        before.watches.items, before.agenda.items]) assert.deepEqual(values, []);
      assert.equal(before.connection.status, 'disconnected');
      assert.equal(before.briefing.last_checked_at, null);
      assert.equal(before.briefing_example, null);
      assert.doesNotMatch(await page.locator('body').innerText(), sampleText);

      await page.getByRole('button', { name: 'Collega la posta', exact: true }).first().click();
      await page.waitForSelector('#wizard-dialog[open]');
      assert.equal(await page.locator('[data-action="connect-demo"]').count(), 0);
      await page.getByRole('button', { name: 'Prepara il collegamento Gmail', exact: true }).click();
      const guide = page.locator('#wizard-body .boundary-note');
      const gmail = await page.evaluate(async () => (await fetch('/api/gmail/status')).json());
      assert.equal(gmail.configured, false, 'This smoke must not use Google credentials.');
      const callback = guide.locator('code');
      assert.equal(await callback.innerText(), gmail.redirect_uri);
      assert.ok(await callback.isVisible());
      const google = guide.getByRole('link', { name: 'Apri Google Cloud' });
      const railway = guide.getByRole('link', { name: 'Apri le variabili Railway' });
      assert.equal(new URL(await google.getAttribute('href')).hostname, 'console.cloud.google.com');
      assert.equal(new URL(await railway.getAttribute('href')).hostname, 'railway.com');
      const checked = page.waitForResponse(response =>
        response.url() === `${origin}/api/mail/providers` && response.request().method() === 'GET');
      await guide.getByRole('button', { name: 'Verifica configurazione' }).click();
      assert.equal((await checked).status(), 200);
      await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('richiede ancora la configurazione'));
      const dimensions = await page.evaluate(() => {
        const dialog = document.querySelector('#wizard-dialog');
        return {
          page: document.documentElement.scrollWidth, viewport: innerWidth,
          dialog: dialog.scrollWidth, content: dialog.clientWidth,
        };
      });
      assert.ok(dimensions.page <= dimensions.viewport + 1, `Page overflow at ${width}px: ${JSON.stringify(dimensions)}`);
      assert.ok(dimensions.dialog <= dimensions.content + 1, `Guide overflow at ${width}px: ${JSON.stringify(dimensions)}`);
      assert.doesNotMatch(await page.locator('body').innerText(), sampleText);
      await page.locator('[data-action="close-wizard"]:visible').first().click();
      await page.locator('[data-page="company"]').click();
      for (const field of ['name', 'sector', 'description', 'signature']) {
        assert.equal(await page.locator(`#company-${field}`).inputValue(), '');
      }
      const after = await state();
      assert.deepEqual(after.company, before.company);
      assert.deepEqual(after.service, before.service);
      assert.deepEqual(after.runs, before.runs);
      assert.deepEqual(errors, [], `JS errors at ${width}px`);
      assert.deepEqual(mutations, [], 'The smoke must only make read requests.');
      await page.close();
    }
    console.log('Browser PASS: fresh real workspace, no samples/demo button, Gmail setup guide and links, configuration check, blank profile, no overflow at 390px, no JS errors or mutations.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
