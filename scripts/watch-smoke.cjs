/* Run only against the disposable scripts/watch_smoke_server.py fixture.
 * Worker and provider calls are disabled there; no Google account is used.
 * Start: .venv/bin/python scripts/watch_smoke_server.py
 * Then: FILO_PORT=8016 node scripts/watch-smoke.cjs
 */
const { chromium, request } = require('playwright');
const assert = require('node:assert/strict');

const origin = `http://127.0.0.1:${process.env.FILO_PORT || '8016'}`;

(async () => {
  const client = await request.newContext({ baseURL: origin });
  const marker = await client.get('/__fixture__/status');
  assert.equal(marker.status(), 200, 'Use the isolated fixture server only.');
  assert.equal((await marker.json()).fixture, 'Filo Watch Smoke Fixture');
  assert.equal((await marker.json()).worker, 'disabled');
  const browser = await chromium.launch({
    executablePath: process.env.FILO_CHROMIUM_PATH || '/usr/bin/chromium',
    headless: true,
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'it-IT', timezoneId: 'America/Los_Angeles' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.waitForSelector('#app-content:not([hidden])');
    const state = () => page.evaluate(async () => (await fetch('/api/bootstrap')).json());
    let data = await state();
    assert.equal(data.real_data_only, true);
    assert.equal(data.company.name, 'Filo Watch Smoke Fixture');
    assert.equal(data.watches.items.length, 0, 'Use a fresh fixture server.');
    const today = data.agenda.date;
    const tomorrow = new Date(new Date(`${today}T12:00:00Z`).valueOf() + 86400000).toISOString().slice(0, 10);
    const addToday = async () => {
      await page.locator('#reply-watch-form-panel > summary').click();
      await page.locator('#watch-contact').selectOption('elena@watch-smoke.test');
      await page.locator('#watch-day').fill(today);
      await page.locator('#reply-watch-form button[type=submit]').click();
      await page.waitForFunction(day => [...document.querySelectorAll('.reply-watch')].some(node => node.textContent.includes('Elena Prova')) && !document.querySelector('#reply-watch-form-panel').open, today);
      const current = await state();
      const item = current.watches.items.find(item => item.email === 'elena@watch-smoke.test' && item.day === today);
      assert.ok(item);
      assert.equal(item.status, 'waiting');
      return item;
    };
    const first = await addToday();
    assert.ok(await page.locator(`[data-watch-id="${first.id}"]`).isVisible());
    assert.match(await page.locator(`[data-watch-id="${first.id}"]`).innerText(), /Prossimo controllo|Controllo in corso/);
    console.log('PASS: person/day form creates a persisted waiting reminder using Rome today.');

    await page.locator('[data-page="services"]').click();
    await page.locator('#chat-input').fill('Domani se risponde Elena Prova dimmelo subito');
    await page.locator('#chat-form button[type=submit]').click();
    await page.getByRole('button', { name: 'Segui questa risposta', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Segui questa risposta', exact: true }).click();
    await page.waitForFunction(async day => (await (await fetch('/api/bootstrap')).json()).watches.items.some(item => item.day === day && item.name === 'Elena Prova'), tomorrow);
    data = await state();
    assert.equal(data.watches.items.length, 2);
    assert.equal(data.service.mandate.send, false);
    const future = data.watches.items.find(item => item.day === tomorrow);
    assert.equal(future.status, 'waiting');
    console.log('PASS: natural-language proposal requires confirmation and persists a reminder for tomorrow.');

    await page.locator('[data-page="overview"]').click();
    await page.locator(`[data-watch-id="${first.id}"] [data-action="dismiss-watch"]`).click();
    await page.waitForSelector(`[data-watch-id="${first.id}"]`, { state: 'detached' });
    assert.equal((await state()).watches.items.length, 1);
    console.log('PASS: closing a waiting reminder removes it while keeping the other saved reminder.');

    const watched = await addToday();
    assert.notEqual(watched.id, first.id);
    const seeded = await client.post('/__fixture__/incoming');
    assert.equal(seeded.status(), 200);
    const observed = await seeded.json();
    await page.reload();
    await page.waitForSelector('#app-content:not([hidden])');
    await page.waitForSelector('.reply-watch--matched');
    data = await state();
    assert.equal(data.watches.items[0].id, watched.id);
    assert.equal(data.watches.items[0].status, 'matched');
    assert.equal(data.watches.items[0].match.source_id, observed.source_id);
    const alert = page.locator('.reply-watch--matched');
    assert.match(await alert.innerText(), /Elena Prova ha scritto/);
    assert.equal(await page.locator('.priority-item').count(), 1, 'Seeded urgent draft should remain below the personal alert.');
    const beforePriorities = await page.evaluate(() => {
      const matched = document.querySelector('.reply-watch--matched');
      const priority = document.querySelector('.priority-item');
      return Boolean(matched.compareDocumentPosition(priority) & Node.DOCUMENT_POSITION_FOLLOWING)
        && matched.getBoundingClientRect().top < priority.getBoundingClientRect().top;
    });
    assert.equal(beforePriorities, true);
    const gmail = alert.getByRole('link', { name: 'Apri Gmail', exact: true });
    const url = new URL(await gmail.getAttribute('href'));
    assert.equal(url.origin, 'https://mail.google.com');
    assert.equal(url.pathname, '/mail/');
    assert.equal(url.searchParams.get('authuser'), 'casella@watch-smoke.test');
    assert.equal(url.hash, `#all/${encodeURIComponent(observed.thread_id)}`);
    assert.equal(await gmail.getAttribute('target'), '_blank');
    assert.match(await gmail.getAttribute('rel'), /noopener/);
    await page.screenshot({ path: '.runtime/watch-matched-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile must not overflow.');
    await page.screenshot({ path: '.runtime/watch-matched-mobile.png', fullPage: true });
    console.log('PASS: newly observed incoming message is highlighted before urgent mail; Gmail link selects the right account and encoded thread; mobile fits.');

    await alert.getByRole('button', { name: 'Segna come vista', exact: true }).click();
    await page.waitForSelector('.reply-watch--matched', { state: 'detached' });
    await page.reload();
    await page.waitForSelector('#app-content:not([hidden])');
    assert.equal(await page.locator('.reply-watch--matched').count(), 0);
    assert.deepEqual((await state()).watches.items.map(item => item.id), [future.id]);
    assert.deepEqual(errors, []);
    assert.deepEqual((await (await client.get('/__fixture__/status')).json()).provider_calls, []);
    assert.equal((await (await client.get('/api/health')).json()).scheduler, 'disabled');
    console.log('Browser PASS: waiting form, chat proposal/confirmation, persistent tomorrow reminder, waiting dismissal, source-only alert ordering, account-aware Gmail link, matched dismissal/persistence, desktop/mobile; no JS errors, provider calls or outgoing emails.');
  } finally {
    await browser.close();
    await client.dispose();
  }
})().catch(error => { console.error(error); process.exit(1); });
