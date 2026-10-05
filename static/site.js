'use strict';

(() => {
  const page = document.body.dataset.page;
  const byId = (id) => document.getElementById(id);
  let csrfToken = '';

  function buttonLabel(element, label) {
    const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    arrow.setAttribute('class', 'arrow-icon');
    arrow.setAttribute('viewBox', '0 0 24 24');
    arrow.setAttribute('aria-hidden', 'true');
    arrow.setAttribute('focusable', 'false');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'M6 18 18 6M6 6h12v12');
    arrow.appendChild(path);
    element.replaceChildren(document.createTextNode(label), arrow);
  }

  const fieldMessages = {
    name: 'Inserisci il tuo nome.',
    email: 'Inserisci un indirizzo email valido.',
    password: 'La password deve contenere da 12 a 128 caratteri.',
  };

  function detailMessage(detail, fallback) {
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const field = detail.map(item => Array.isArray(item?.loc) ? item.loc[item.loc.length - 1] : null).find(name => fieldMessages[name]);
      return field ? fieldMessages[field] : 'Controlla i dati inseriti e riprova.';
    }
    return fallback;
  }

  async function request(path, options = {}) {
    const method = options.method || 'GET';
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRF-Token'] = csrfToken;
    }
    let response;
    try {
      response = await fetch(path, { method, credentials: 'same-origin', headers, ...(method !== 'GET' ? { body: JSON.stringify(options.body || {}) } : {}) });
    } catch (_) {
      throw new Error('Non riusciamo a raggiungere Spazelia. Controlla la connessione e riprova.');
    }
    let data = {};
    try { data = await response.json(); } catch (_) { /* Some server errors have no JSON body. */ }
    if (!response.ok) {
      const error = new Error(detailMessage(data.detail, 'Qualcosa non è andato a buon fine. Riprova tra poco.'));
      error.status = response.status;
      throw error;
    }
    if (data.csrf_token) csrfToken = data.csrf_token;
    return data;
  }

  async function session() {
    const data = await request('/api/auth/session');
    csrfToken = data.csrf_token || '';
    return data;
  }

  function showError(id, error) {
    const element = byId(id);
    element.textContent = error.message || 'Qualcosa non è andato a buon fine. Riprova.';
    element.hidden = false;
  }

  function localDestination() {
    const next = new URLSearchParams(window.location.search).get('next');
    return next === '/account' ? '/account' : '/app';
  }

  function goToLogin() {
    window.location.replace(`/login?next=${encodeURIComponent('/account')}`);
  }

  function clearPendingWizard() {
    try { sessionStorage.removeItem('filo-pending-wizard'); } catch (_) { /* The app also works when browser storage is disabled. */ }
  }

  async function initLanding() {
    if (new URLSearchParams(window.location.search).get('account') === 'deleted') {
      const notice = document.createElement('div');
      notice.className = 'notice success content-width deleted-notice';
      notice.setAttribute('role', 'status');
      notice.textContent = 'Il tuo account e i dati del tuo spazio sono stati eliminati. Grazie per aver usato Spazelia.';
      document.querySelector('main')?.prepend(notice);
      history.replaceState(null, '', '/');
    }
    try {
      const data = await session();
      if (!data.authenticated) return;
      byId('landing-login').href = '/account';
      byId('landing-login').textContent = 'Il tuo account';
      byId('landing-register').href = '/app';
      buttonLabel(byId('landing-register'), 'Apri il tuo spazio');
      byId('hero-start').href = '/app';
      buttonLabel(byId('hero-start'), 'Apri la tua giornata');
      // Every other sign-up or sign-in link leads straight to the private space.
      document.querySelectorAll('a[href="/register"], a[href="/login"]').forEach((link) => {
        link.href = '/app';
        if (link.classList.contains('button')) buttonLabel(link, 'Apri il tuo spazio');
        else if (link.firstChild?.nodeType === Node.TEXT_NODE) link.firstChild.textContent = 'Apri il tuo spazio ';
      });
    } catch (_) { /* The public homepage stays usable when account services are unavailable. */ }
  }

  async function postWithFreshSession(endpoint, body) {
    try {
      return await request(endpoint, { method: 'POST', body });
    } catch (error) {
      // A form left open past the one-hour guest session gets a fresh
      // session and CSRF token, then the same submission is retried once.
      if (error.status !== 403) throw error;
      const current = await session();
      if (current.authenticated && !endpoint.startsWith('/api/auth/password/')) return { alreadySignedIn: true };
      return request(endpoint, { method: 'POST', body });
    }
  }

  function showSuccess(id, message) {
    const element = byId(id);
    element.textContent = message;
    element.hidden = false;
  }

  function hideField(id, input) {
    byId(id).hidden = true;
    input.required = false;
  }

  async function initAuth() {
    const path = window.location.pathname;
    const mode = path === '/register' ? 'register' : path === '/forgot-password' ? 'forgot' : path === '/reset-password' ? 'reset' : 'login';
    const register = mode === 'register';
    const form = byId('auth-form');
    const submit = byId('auth-submit');
    const password = byId('auth-password');
    const email = byId('auth-email');
    const name = byId('auth-name');
    // The reset token leaves the address bar at once: it never stays in history.
    const resetToken = mode === 'reset' ? new URLSearchParams(window.location.search).get('token') : null;
    if (mode === 'reset') history.replaceState(null, '', '/reset-password');
    if (register) {
      document.title = 'Crea il tuo account · Spazelia';
      byId('auth-title').textContent = 'Cominciamo da te.';
      byId('auth-description').textContent = 'Crea il tuo account Spazelia.';
      byId('auth-name-field').hidden = false;
      name.required = true;
      byId('auth-email-label').textContent = 'Email';
      email.type = 'email';
      email.autocomplete = 'email';
      password.autocomplete = 'new-password';
      password.minLength = 12;
      byId('auth-password-hint').textContent = 'Almeno 12 caratteri. Scegli una password che non usi altrove.';
      byId('register-payment-note').hidden = false;
      byId('auth-legacy-note').hidden = true;
      byId('auth-forgot').hidden = true;
      buttonLabel(submit, 'Crea il tuo account');
      byId('auth-switch-copy').textContent = 'Hai già un account?';
      byId('auth-switch-link').href = '/login';
      byId('auth-switch-link').textContent = 'Accedi';
    } else if (mode === 'forgot') {
      document.title = 'Recupera l’accesso · Spazelia';
      byId('auth-title').textContent = 'Recupera l’accesso.';
      byId('auth-description').textContent = 'Scrivi l’email del tuo account: ti mandiamo un link per scegliere una nuova password.';
      byId('auth-email-label').textContent = 'Email';
      email.type = 'email';
      email.autocomplete = 'email';
      hideField('auth-password-field', password);
      byId('auth-legacy-note').textContent = 'L’account del gestore usa la password impostata nel servizio Railway: non si recupera via email.';
      byId('auth-forgot').hidden = true;
      buttonLabel(submit, 'Invia il link');
      byId('auth-switch-copy').textContent = 'Ti è tornata in mente?';
      byId('auth-switch-link').href = '/login';
      byId('auth-switch-link').textContent = 'Accedi';
    } else if (mode === 'reset') {
      document.title = 'Nuova password · Spazelia';
      byId('auth-title').textContent = 'Scegli una nuova password.';
      byId('auth-description').textContent = 'Dopo il salvataggio entri nel tuo spazio; gli altri dispositivi dovranno accedere di nuovo.';
      hideField('auth-email-field', email);
      byId('auth-password-label').textContent = 'Nuova password';
      password.autocomplete = 'new-password';
      password.minLength = 12;
      byId('auth-password-hint').textContent = 'Almeno 12 caratteri. Scegli una password che non usi altrove.';
      byId('auth-legacy-note').hidden = true;
      byId('auth-forgot').hidden = true;
      buttonLabel(submit, 'Salva la nuova password');
      byId('auth-switch-copy').textContent = 'Il link è scaduto?';
      byId('auth-switch-link').href = '/forgot-password';
      byId('auth-switch-link').textContent = 'Chiedine uno nuovo';
    }
    const destination = localDestination();
    if (destination === '/account' && (mode === 'login' || register)) byId('auth-switch-link').href += '?next=%2Faccount';
    byId('password-toggle').addEventListener('click', () => {
      const visible = password.type === 'password';
      password.type = visible ? 'text' : 'password';
      byId('password-toggle').textContent = visible ? 'Nascondi' : 'Mostra';
      byId('password-toggle').setAttribute('aria-label', visible ? 'Nascondi password' : 'Mostra password');
      byId('password-toggle').setAttribute('aria-pressed', String(visible));
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      byId('auth-error').hidden = true;
      byId('auth-success').hidden = true;
      if (register && !name.value.trim()) {
        // The browser accepts a name made only of spaces; the account would not.
        showError('auth-error', new Error(fieldMessages.name));
        name.focus();
        return;
      }
      if (mode === 'forgot' && !email.value.trim()) {
        showError('auth-error', new Error(fieldMessages.email));
        email.focus();
        return;
      }
      if ((mode === 'reset' || register) && password.value.length < 12) {
        showError('auth-error', new Error(fieldMessages.password));
        password.focus();
        return;
      }
      submit.disabled = true;
      byId('auth-progress').textContent = { register: 'Creiamo il tuo account…', forgot: 'Prepariamo il link…', reset: 'Salviamo la nuova password…' }[mode] || 'Accesso in corso…';
      try {
        if (mode === 'forgot') {
          const result = await postWithFreshSession('/api/auth/password/forgot', { email: email.value.trim() });
          showSuccess('auth-success', result.message);
          byId('auth-progress').textContent = '';
          submit.disabled = false;
          return;
        }
        if (mode === 'reset') {
          await postWithFreshSession('/api/auth/password/reset', { token: resetToken, password: password.value });
          password.value = '';
          clearPendingWizard();
          window.location.assign('/app');
          return;
        }
        const body = { email: email.value.trim(), password: password.value };
        if (register) body.name = name.value.trim();
        await postWithFreshSession(register ? '/api/auth/register' : '/api/auth/login', body);
        clearPendingWizard();
        password.value = '';
        window.location.assign(destination);
      } catch (error) {
        showError('auth-error', error);
        byId('auth-progress').textContent = '';
        submit.disabled = false;
      }
    });
    try {
      const data = await session();
      if (data.authenticated && mode !== 'reset') {
        window.location.replace(mode === 'forgot' ? '/account' : destination);
        return;
      }
      byId('auth-loading').hidden = true;
      if (mode === 'reset' && !/^[A-Za-z0-9_-]{43}$/.test(resetToken || '')) {
        showError('auth-error', new Error('Il link non è completo o non è valido. Chiedi un nuovo link per reimpostare la password.'));
        return;
      }
      if (mode === 'forgot' && !data.password_reset_available) {
        showError('auth-error', new Error('Il recupero della password via email non è ancora attivo. Contatta l’assistenza di Spazelia.'));
        submit.disabled = true;
      }
      form.hidden = false;
    } catch (error) {
      byId('auth-loading').hidden = true;
      showError('auth-error', error);
      // A fresh navigation retries the session and obtains a valid CSRF token.
      const retry = document.createElement('a');
      retry.className = 'button secondary full-width';
      retry.href = `${window.location.pathname}${window.location.search}`;
      retry.textContent = 'Riprova';
      byId('auth-loading').insertAdjacentElement('afterend', retry);
    }
  }

  const subscriptionLabels = {
    active: 'Attivo', trialing: 'Periodo di prova', past_due: 'Pagamento da aggiornare',
    canceled: 'Disdetto', unpaid: 'Pagamento in sospeso', incomplete: 'Da completare',
    incomplete_expired: 'Non completato', paused: 'In pausa', none: 'Nessun abbonamento', free: 'Nessun abbonamento',
  };

  function renderBilling(data) {
    const subscription = data.subscription || {};
    const plan = data.plan || {};
    const status = data.subscription_status || subscription.status || 'free';
    const active = ['active', 'trialing'].includes(status);
    const hasSubscription = !['free', 'none'].includes(status);
    byId('plan-name').textContent = plan.name || 'Spazelia';
    byId('plan-price').textContent = plan.price_label || (data.configured ? 'Il prezzo sarà indicato nel checkout.' : 'Prezzo in definizione');
    byId('subscription-status').textContent = subscriptionLabels[status] || 'Stato da verificare';
    byId('subscription-status').classList.toggle('active', active);
    byId('subscription-description').textContent = active
      ? (subscription.cancel_at_period_end ? 'Il tuo abbonamento resta attivo fino alla fine del periodo già pagato.' : 'Il tuo abbonamento è attivo. Puoi consultare pagamenti e impostazioni dal portale.')
      : status === 'past_due' || status === 'unpaid'
        ? 'Aggiorna il metodo di pagamento dal portale per regolarizzare l’abbonamento.'
        : hasSubscription && status !== 'canceled'
          ? 'Controlla i dettagli dal portale per completare o gestire il tuo abbonamento.'
          : 'Puoi scegliere il piano quando sei pronta o pronto. Il pagamento richiede la tua conferma.';
    byId('plan-features').replaceChildren();
    const features = Array.isArray(plan.features) ? plan.features : [];
    for (const feature of features) {
      if (typeof feature !== 'string') continue;
      const item = document.createElement('li');
      item.textContent = feature;
      byId('plan-features').appendChild(item);
    }
    byId('plan-features').hidden = features.length === 0;
    byId('billing-unavailable').hidden = Boolean(data.configured);
    byId('billing-checkout').hidden = !data.configured || !['free', 'none', 'canceled', 'incomplete_expired'].includes(status);
    byId('billing-portal').hidden = !data.portal_available;
    byId('billing-payment-note').hidden = byId('billing-checkout').hidden;
  }

  async function loadBilling() {
    byId('billing-error').hidden = true;
    byId('billing-refresh').disabled = true;
    byId('billing-progress').textContent = 'Verifichiamo lo stato…';
    try {
      renderBilling(await request('/api/billing/status'));
      byId('billing-progress').textContent = '';
    } catch (error) {
      if (error.status === 401) { goToLogin(); return; }
      showError('billing-error', error);
      byId('billing-progress').textContent = '';
    } finally {
      byId('billing-refresh').disabled = false;
    }
  }

  async function openBilling(path, button, progress) {
    byId('billing-error').hidden = true;
    button.disabled = true;
    byId('billing-progress').textContent = progress;
    try {
      const data = await request(path, { method: 'POST' });
      const destination = new URL(data.url);
      if (destination.protocol !== 'https:') throw new Error('Il collegamento al pagamento non è valido. Riprova tra poco.');
      window.location.assign(destination.href);
    } catch (error) {
      if (error.status === 401) { goToLogin(); return; }
      showError('billing-error', error);
      byId('billing-progress').textContent = '';
      button.disabled = false;
    }
  }

  function initSecurity(user) {
    if (user.role === 'owner') {
      byId('password-form').hidden = true;
      byId('security-owner-note').hidden = false;
      byId('delete-section').hidden = true;
      byId('delete-owner-note').hidden = false;
      return;
    }
    const newPassword = byId('new-password');
    byId('new-password-toggle').addEventListener('click', () => {
      const visible = newPassword.type === 'password';
      newPassword.type = visible ? 'text' : 'password';
      byId('new-password-toggle').textContent = visible ? 'Nascondi' : 'Mostra';
      byId('new-password-toggle').setAttribute('aria-label', visible ? 'Nascondi password' : 'Mostra password');
      byId('new-password-toggle').setAttribute('aria-pressed', String(visible));
    });
    byId('password-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      byId('password-error').hidden = true;
      byId('password-success').hidden = true;
      const current = byId('current-password');
      if (!current.value) { showError('password-error', new Error('Inserisci la password attuale.')); current.focus(); return; }
      if (newPassword.value.length < 12) { showError('password-error', new Error(fieldMessages.password)); newPassword.focus(); return; }
      byId('password-submit').disabled = true;
      try {
        const result = await request('/api/account/password', { method: 'POST', body: { current_password: current.value, new_password: newPassword.value } });
        current.value = '';
        newPassword.value = '';
        showSuccess('password-success', result.message || 'Password aggiornata.');
      } catch (error) {
        if (error.status === 401 && error.message.startsWith('Accedi')) { goToLogin(); return; }
        showError('password-error', error);
      } finally {
        byId('password-submit').disabled = false;
      }
    });
    byId('delete-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      byId('delete-error').hidden = true;
      const secret = byId('delete-password');
      const confirmation = byId('delete-confirmation');
      if (!secret.value) { showError('delete-error', new Error('Inserisci la tua password per confermare.')); secret.focus(); return; }
      if (confirmation.value.trim().toUpperCase() !== 'ELIMINA') { showError('delete-error', new Error('Scrivi ELIMINA per confermare la cancellazione.')); confirmation.focus(); return; }
      byId('delete-submit').disabled = true;
      try {
        await request('/api/account/delete', { method: 'POST', body: { password: secret.value, confirmation: confirmation.value.trim() } });
        secret.value = '';
        clearPendingWizard();
        window.location.assign('/?account=deleted');
      } catch (error) {
        if (error.status === 401 && error.message.startsWith('Accedi')) { goToLogin(); return; }
        showError('delete-error', error);
        byId('delete-submit').disabled = false;
      }
    });
  }

  async function initAccount() {
    byId('billing-refresh').addEventListener('click', loadBilling);
    byId('billing-checkout').addEventListener('click', () => openBilling('/api/billing/checkout', byId('billing-checkout'), 'Apriamo il checkout…'));
    byId('billing-portal').addEventListener('click', () => openBilling('/api/billing/portal', byId('billing-portal'), 'Apriamo la gestione dell’abbonamento…'));
    byId('account-logout').addEventListener('click', async () => {
      byId('account-logout').disabled = true;
      try {
        await request('/api/auth/logout', { method: 'POST' });
        clearPendingWizard();
        window.location.assign('/');
      } catch (error) {
        showError('account-error', error);
        byId('account-logout').disabled = false;
      }
    });
    const checkout = new URLSearchParams(window.location.search).get('billing');
    if (checkout === 'success' || checkout === 'cancelled') {
      byId('billing-return-notice').textContent = checkout === 'success'
        ? 'Se hai completato il pagamento, lo stato si aggiornerà dopo la conferma del servizio di pagamento. Puoi usare “Aggiorna stato”.'
        : 'Hai chiuso il checkout. Puoi riprendere il pagamento quando vuoi.';
      byId('billing-return-notice').hidden = false;
    }
    try {
      const data = await session();
      if (!data.authenticated) { goToLogin(); return; }
      const user = data.user || {};
      byId('profile-name').textContent = user.name || 'Account Spazelia';
      byId('profile-email').textContent = user.email || '—';
      byId('profile-role').textContent = user.role === 'owner' ? 'Titolare dello spazio' : 'Account Spazelia';
      byId('profile-avatar').textContent = (user.name || user.email || 'S').trim().charAt(0).toUpperCase();
      initSecurity(user);
      byId('account-loading').hidden = true;
      byId('account-content').hidden = false;
      byId('account-logout').disabled = false;
      await loadBilling();
    } catch (error) {
      byId('account-loading').hidden = true;
      if (error.status === 401) { goToLogin(); return; }
      showError('account-error', error);
      const retry = document.createElement('a');
      retry.className = 'button secondary';
      retry.href = '/account';
      retry.textContent = 'Riprova';
      byId('account-error').insertAdjacentElement('afterend', retry);
    }
  }

  if (page === 'landing') initLanding();
  if (page === 'auth') initAuth();
  if (page === 'account') initAccount();
})();
