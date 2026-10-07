/* ================================================================
   Login screen — вход в SIamba OS / создание первого пользователя.
   ================================================================ */
(() => {
  'use strict';

  const api = () => (window.pywebview && window.pywebview.api) || null;

  /* ---------- DOM ---------- */
  const el = {
    body:          document.body,
    time:          document.getElementById('login-time'),
    date:          document.getElementById('login-date'),

    screenLogin:   document.getElementById('screen-login'),
    screenFirst:   document.getElementById('screen-first'),

    userPicker:    document.getElementById('user-picker'),
    loginPanel:    document.getElementById('login-panel'),
    loginAvatar:   document.getElementById('login-avatar'),
    loginName:     document.getElementById('login-name'),
    loginForm:     document.getElementById('login-form'),
    passwordRow:   document.getElementById('password-row'),
    passwordInput: document.getElementById('login-password'),
    loginSubmit:   document.getElementById('login-submit'),
    loginError:    document.getElementById('login-error'),
    loginBack:     document.getElementById('login-back'),

    firstForm:     document.getElementById('first-form'),
    firstUsername: document.getElementById('first-username'),
    firstDisplay:  document.getElementById('first-display'),
    firstRequire:  document.getElementById('first-require-pwd'),
    firstPwdField: document.getElementById('first-pwd-field'),
    firstPassword: document.getElementById('first-password'),
    firstError:    document.getElementById('first-error'),

    pwrReboot:     document.getElementById('pwr-reboot'),
    pwrShutdown:   document.getElementById('pwr-shutdown'),
  };

  const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  /* ================================================================
     Часы
     ================================================================ */
  function tick() {
    const now = new Date();
    el.time.textContent = now.toLocaleTimeString('ru-RU', {
      hour: '2-digit', minute: '2-digit',
    });
    el.date.textContent = now.toLocaleDateString('ru-RU', {
      weekday: 'long', day: 'numeric', month: 'long',
    });
  }
  tick();
  setInterval(tick, 1000);

  /* ================================================================
     Тема — подтягиваем из system theme.json
     ================================================================ */
  async function applyTheme() {
    const a = api();
    if (!a || typeof a.get_theme !== 'function') return;
    try {
      const res = await a.get_theme();
      if (res && res.theme) el.body.dataset.theme = res.theme;
    } catch {}
  }

  /* ================================================================
     Состояние
     ================================================================ */
  let users = [];
  let selectedUser = null;

  function showScreen(which) {
    el.screenLogin.hidden = (which !== 'login');
    el.screenFirst.hidden = (which !== 'first');
  }

  /* ================================================================
     Загрузка списка пользователей
     ================================================================ */
  async function loadUsers() {
    const a = api();
    if (!a || typeof a.get_users !== 'function') {
      showScreen('first');
      return;
    }

    try {
      users = (await a.get_users()) || [];
    } catch { users = []; }

    if (!users.length) {
      showScreen('first');
      return;
    }

    showScreen('login');
    renderUserPicker();
    el.loginPanel.hidden = true;
  }

  function renderUserPicker() {
    el.userPicker.innerHTML = '';
    users.forEach(u => {
      const tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'user-tile';

      const letter = (u.display_name || u.username || '?')
                       .trim().charAt(0).toUpperCase();
      tile.innerHTML = `
        <span class="user-tile-avatar">${escapeHtml(letter)}</span>
        <span class="user-tile-name">${escapeHtml(u.display_name || u.username)}</span>
      `;
      tile.addEventListener('click', () => selectUser(u));
      el.userPicker.appendChild(tile);
    });
  }

  /* ================================================================
     Экран ввода пароля
     ================================================================ */
  function selectUser(u) {
    selectedUser = u;
    el.userPicker.parentElement.querySelector('.login-clock').style.display = 'none';
    el.userPicker.hidden = true;
    el.loginPanel.hidden = false;

    const letter = (u.display_name || u.username || '?')
                     .trim().charAt(0).toUpperCase();
    el.loginAvatar.textContent = letter;
    el.loginName.textContent = u.display_name || u.username;

    el.loginError.hidden = true;
    el.loginError.textContent = '';

    if (u.require_password) {
      el.passwordRow.hidden = false;
      setTimeout(() => el.passwordInput.focus(), 60);
    } else {
      el.passwordRow.hidden = true;
      // сразу входим
      doLogin('');
    }
  }

  function backToPicker() {
    selectedUser = null;
    el.loginPanel.hidden = true;
    el.userPicker.hidden = false;
    el.userPicker.parentElement.querySelector('.login-clock').style.display = '';
    el.passwordInput.value = '';
    el.loginError.hidden = true;
  }

  el.loginBack.addEventListener('click', backToPicker);

  /* ================================================================
     Аутентификация + вход
     ================================================================ */
  async function doLogin(password) {
    const a = api();
    if (!a || !selectedUser) return;

    el.loginSubmit.disabled = true;
    el.loginError.hidden = true;

    let res;
    try {
      res = await a.authenticate_user(selectedUser.username, password);
    } catch (e) {
      res = { ok: false, error: String(e) };
    }

    if (!res || !res.ok) {
      el.loginSubmit.disabled = false;
      el.loginError.textContent = (res && res.error) || 'Не удалось войти';
      el.loginError.hidden = false;
      el.passwordInput.value = '';
      el.passwordInput.focus();
      return;
    }

    // Записываем сессию
    try { await a.set_current_session(selectedUser.username); } catch {}

    // Просим бэкенд перевести окно на рабочий стол
    try { await a.goto_desktop(); } catch {}
  }

  el.loginForm.addEventListener('submit', (e) => {
    e.preventDefault();
    doLogin(el.passwordInput.value);
  });

  /* ================================================================
     Создание первого пользователя
     ================================================================ */
  el.firstRequire.addEventListener('change', () => {
    el.firstPwdField.classList.toggle('hidden', !el.firstRequire.checked);
    if (el.firstRequire.checked) {
      setTimeout(() => el.firstPassword.focus(), 40);
    }
  });

  el.firstForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const a = api();
    if (!a) return;

    const username = el.firstUsername.value.trim();
    const display  = el.firstDisplay.value.trim();
    const require  = el.firstRequire.checked;
    const password = require ? el.firstPassword.value : '';

    el.firstError.hidden = true;

    if (!username) {
      el.firstError.textContent = 'Введите логин';
      el.firstError.hidden = false;
      return;
    }
    if (require && password.length < 4) {
      el.firstError.textContent = 'Пароль минимум 4 символа';
      el.firstError.hidden = false;
      return;
    }

    let res;
    try {
      res = await a.create_user(username, password, display || username, require);
    } catch (err) {
      res = { ok: false, error: String(err) };
    }

    if (!res || !res.ok) {
      el.firstError.textContent = (res && res.error) || 'Не удалось создать';
      el.firstError.hidden = false;
      return;
    }

    // Сессия + переход на рабочий стол
    try { await a.set_current_session(username); } catch {}
    try { await a.goto_desktop(); } catch {}
  });

  /* ================================================================
     Кнопки питания
     ================================================================ */
  el.pwrShutdown.addEventListener('click', async () => {
    const a = api();
    if (a && a.shutdown) await a.shutdown();
  });
  el.pwrReboot.addEventListener('click', async () => {
    const a = api();
    if (a && a.reboot) await a.reboot();
  });

  /* ================================================================
     Bootstrap
     ================================================================ */
  async function boot() {
    await applyTheme();
    await loadUsers();
  }

  let booted = false;
  function once() { if (booted) return; booted = true; boot(); }

  window.addEventListener('pywebviewready', once);
  if (window.pywebview && window.pywebview.api) once();
  else {
    const iv = setInterval(() => {
      if (window.pywebview && window.pywebview.api) { clearInterval(iv); once(); }
    }, 100);
    setTimeout(() => { clearInterval(iv); once(); }, 1500);
  }
})();