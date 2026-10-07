/* ================================================================
   Settings app — UI для виртуальных пользователей SIamba
   ================================================================ */
(() => {
  'use strict';

  const bridge = () => (window.pywebview && window.pywebview.api) || null;

  // Обёртка над bridge.call(method, params) → Promise
  function call(method, params = {}) {
    const b = bridge();
    if (!b || typeof b.call !== 'function') {
      return Promise.reject(new Error('backend not ready'));
    }
    return b.call(method, params);
  }

  /* ---------------------------------------------------------------
     DOM
     --------------------------------------------------------------- */
  const el = {
    body:         document.body,
    titlebar:     document.getElementById('titlebar'),
    btnClose:     document.getElementById('btn-close'),
    btnMin:       document.getElementById('btn-min'),

    nav:          document.getElementById('nav'),
    search:       document.getElementById('settings-search'),
    content:      document.getElementById('content'),

    accountsEmpty: document.getElementById('accounts-empty'),
    accountsMain:  document.getElementById('accounts-main'),
    userList:      document.getElementById('user-list'),
    emptyCreateBtn:document.getElementById('empty-create-btn'),
    addUserBtn:    document.getElementById('add-user-btn'),

    networkStatus: document.getElementById('network-status'),

    modalOverlay: document.getElementById('modal-overlay'),
    modalTitle:   document.getElementById('modal-title'),
    modalBody:    document.getElementById('modal-body'),
    modalFooter:  document.getElementById('modal-footer'),
    modalClose:   document.getElementById('modal-close'),

    toast:        document.getElementById('toast'),
  };

  function showToast(msg) {
    el.toast.textContent = msg;
    el.toast.classList.add('show');
    clearTimeout(el.toast._t);
    el.toast._t = setTimeout(() => el.toast.classList.remove('show'), 2200);
  }

  const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  /* ---------------------------------------------------------------
     Навигация
     --------------------------------------------------------------- */
  let currentPage = 'accounts';

  function navigateTo(page) {
    currentPage = page;
    el.nav.querySelectorAll('.nav-item').forEach(it => {
      it.classList.toggle('selected', it.dataset.page === page);
    });
    el.content.querySelectorAll('.page').forEach(p => {
      p.hidden = (p.dataset.page !== page);
    });
    if (page === 'accounts') refreshUsers();
    if (page === 'appearance') refreshAppearance();
    if (page === 'network') refreshNetwork();
  }

  el.nav.addEventListener('click', (e) => {
    const item = e.target.closest('.nav-item');
    if (!item) return;
    navigateTo(item.dataset.page);
  });

  /* ---------------------------------------------------------------
     Пользователи
     --------------------------------------------------------------- */
    async function refreshUsers() {
    let res;
    try { res = await call('users.list'); }
    catch (e) {
      console.error('[settings] users.list threw:', e);
      showToast('Ошибка связи с бэкендом: ' + e.message);
      return;
    }

    console.log('[settings] users.list response:', res);

    // Если бэкенд явно вернул ошибку — показываем её, не прячем.
    if (!res || res.ok === false) {
      const err = (res && res.error) || 'unknown error';
      console.error('[settings] users.list failed:', err);
      showToast('Не удалось получить пользователей: ' + err);
      el.accountsEmpty.hidden = false;
      el.accountsMain.hidden  = true;
      return;
    }

    const users = Array.isArray(res.users) ? res.users : [];
    console.log('[settings] user count:', users.length);

    if (!users.length) {
      el.accountsEmpty.hidden = false;
      el.accountsMain.hidden  = true;
      return;
    }
    el.accountsEmpty.hidden = true;
    el.accountsMain.hidden  = false;

    el.userList.innerHTML = '';
    users.forEach(u => el.userList.appendChild(renderUserRow(u)));
  }

  function renderUserRow(u) {
    const row = document.createElement('div');
    row.className = 'user-row';

    const letter = (u.display_name || u.username || '?')
                     .trim().charAt(0).toUpperCase();

    row.innerHTML = `
      <div class="user-avatar">${escapeHtml(letter)}</div>
      <div class="user-info">
        <div class="user-name">${escapeHtml(u.display_name || u.username)}</div>
        <div class="user-meta">@${escapeHtml(u.username)}
          · создан ${escapeHtml(u.created || '—')}</div>
      </div>
      <div class="user-actions">
        <button class="btn" data-act="edit">Изменить</button>
        <button class="btn danger" data-act="delete">Удалить</button>
      </div>
    `;
    row.querySelector('[data-act="edit"]').addEventListener('click',
      () => openEditUserModal(u));
    row.querySelector('[data-act="delete"]').addEventListener('click',
      () => confirmDeleteUser(u));
    return row;
  }

  /* ---------------------------------------------------------------
     Модалка: создать / изменить пользователя
     --------------------------------------------------------------- */
  function openModal({ title, bodyHTML, actions }) {
    el.modalTitle.textContent = title;
    el.modalBody.innerHTML = bodyHTML;
    el.modalFooter.innerHTML = '';

    for (const a of actions) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn' + (a.variant ? ' ' + a.variant : '');
      b.textContent = a.label;
      b.addEventListener('click', () => a.onClick());
      el.modalFooter.appendChild(b);
    }
    el.modalOverlay.hidden = false;
  }

  function closeModal() {
    el.modalOverlay.hidden = true;
    el.modalBody.innerHTML = '';
    el.modalFooter.innerHTML = '';
  }

  el.modalClose.addEventListener('click', closeModal);
  el.modalOverlay.addEventListener('click', (e) => {
    if (e.target === el.modalOverlay) closeModal();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !el.modalOverlay.hidden) closeModal();
  });

      function openCreateUserModal() {
    openModal({
      title: 'Новый пользователь',
      bodyHTML: `
        <div class="field">
          <label>Имя пользователя (логин)</label>
          <input id="m-username" type="text" autocomplete="off"
                 placeholder="например, alice">
        </div>
        <div class="field">
          <label>Отображаемое имя</label>
          <input id="m-display" type="text" autocomplete="off"
                 placeholder="Alice Ivanova">
        </div>
        <label class="check-row">
          <input type="checkbox" id="m-require" checked>
          <span>Запрашивать пароль при входе</span>
        </label>
        <div class="field" id="m-pwd-field">
          <label>Пароль</label>
          <input id="m-password" type="password" autocomplete="new-password"
                 placeholder="минимум 4 символа">
        </div>
      `,
      actions: [
        { label: 'Отмена', onClick: closeModal },
        {
          label: 'Создать', variant: 'primary',
          onClick: async () => {
            try {
              const username = el.modalBody.querySelector('#m-username').value.trim();
              const display  = el.modalBody.querySelector('#m-display').value.trim();
              const require  = el.modalBody.querySelector('#m-require').checked;
              const password = require
                ? el.modalBody.querySelector('#m-password').value
                : '';

              if (!username) { showToast('Введите логин'); return; }
              if (require && password.length < 4) {
                showToast('Пароль минимум 4 символа'); return;
              }

              let res;
              try {
                res = await call('users.create', {
                  username,
                  password,
                  display_name: display || username,
                  require_password: require,
                });
              } catch (e) {
                console.error('[settings] users.create threw:', e);
                showToast('Ошибка связи с бэкендом: ' + e.message);
                return;
              }

              console.log('[settings] users.create response:', res);

              if (!res || !res.ok) {
                showToast('Не удалось создать: ' + ((res && res.error) || '?'));
                return;
              }
              closeModal();
              showToast(require
                ? `Пользователь «${username}» создан`
                : `Пользователь «${username}» создан без пароля`);
              refreshUsers();
            } catch (err) {
              console.error('[settings] create handler crashed:', err);
              showToast('Внутренняя ошибка: ' + err.message);
            }
          },
        },
      ],
    });

    // Показ/скрытие поля пароля по галочке.
    const chk = el.modalBody.querySelector('#m-require');
    const pwdField = el.modalBody.querySelector('#m-pwd-field');
    chk.addEventListener('change', () => {
      pwdField.classList.toggle('hidden', !chk.checked);
      if (chk.checked) {
        setTimeout(() => el.modalBody.querySelector('#m-password').focus(), 40);
      }
    });

    setTimeout(() => el.modalBody.querySelector('#m-username').focus(), 40);
  }

    function openEditUserModal(u) {
    const requireInitial = u.require_password !== false;

    openModal({
      title: 'Изменить пользователя',
      bodyHTML: `
        <div class="field">
          <label>Логин (изменить нельзя)</label>
          <input type="text" value="${escapeHtml(u.username)}" disabled>
        </div>
        <div class="field">
          <label>Отображаемое имя</label>
          <input id="m-display" type="text"
                 value="${escapeHtml(u.display_name || u.username)}">
        </div>
        <label class="check-row">
          <input type="checkbox" id="m-require" ${requireInitial ? 'checked' : ''}>
          <span>Запрашивать пароль при входе</span>
        </label>
        <div class="field${requireInitial ? '' : ' hidden'}" id="m-pwd-field">
          <label>Новый пароль (оставьте пустым, чтобы не менять)</label>
          <input id="m-password" type="password" autocomplete="new-password">
        </div>
      `,
      actions: [
        { label: 'Отмена', onClick: closeModal },
        {
          label: 'Сохранить', variant: 'primary',
          onClick: async () => {
            try {
              const display  = el.modalBody.querySelector('#m-display').value.trim();
              const require  = el.modalBody.querySelector('#m-require').checked;
              const password = require
                ? el.modalBody.querySelector('#m-password').value
                : '';

              const params = { username: u.username };

              if (display && display !== (u.display_name || '')) {
                params.display_name = display;
              }
              if (password) {
                if (password.length < 4) {
                  showToast('Пароль минимум 4 символа'); return;
                }
                params.password = password;
              }
              if (require !== requireInitial) {
                params.require_password = require;
              }

              if (Object.keys(params).length === 1) {
                closeModal(); return;
              }

              let res;
              try {
                res = await call('users.update', params);
              } catch (e) {
                console.error('[settings] users.update threw:', e);
                showToast('Ошибка связи с бэкендом: ' + e.message);
                return;
              }

              console.log('[settings] users.update response:', res);

              if (!res || !res.ok) {
                showToast('Не удалось сохранить: ' + ((res && res.error) || '?'));
                return;
              }
              closeModal();
              showToast('Изменения сохранены');
              refreshUsers();
            } catch (err) {
              console.error('[settings] update handler crashed:', err);
              showToast('Внутренняя ошибка: ' + err.message);
            }
          },
        },
      ],
    });

    const chk = el.modalBody.querySelector('#m-require');
    const pwdField = el.modalBody.querySelector('#m-pwd-field');
    chk.addEventListener('change', () => {
      pwdField.classList.toggle('hidden', !chk.checked);
    });
  }

    function confirmDeleteUser(u) {
    openModal({
      title: 'Удалить пользователя?',
      bodyHTML: `<p>Пользователь <b>${escapeHtml(u.display_name || u.username)}</b>
                 (@${escapeHtml(u.username)}) будет удалён вместе со всей
                 его иерархией папок. Это действие необратимо.</p>`,
      actions: [
        { label: 'Отмена', onClick: closeModal },
        {
          label: 'Удалить', variant: 'danger',
          onClick: async () => {
            try {
              let res;
              try {
                res = await call('users.delete', { username: u.username });
              } catch (e) {
                console.error('[settings] users.delete threw:', e);
                showToast('Ошибка связи с бэкендом: ' + e.message);
                return;
              }

              console.log('[settings] users.delete response:', res);

              if (!res || !res.ok) {
                showToast('Не удалось удалить: ' + ((res && res.error) || '?'));
                return;
              }
              closeModal();
              showToast('Пользователь удалён');
              refreshUsers();
            } catch (err) {
              console.error('[settings] delete handler crashed:', err);
              showToast('Внутренняя ошибка: ' + err.message);
            }
          },
        },
      ],
    });
  }

  el.emptyCreateBtn.addEventListener('click', openCreateUserModal);
  el.addUserBtn.addEventListener('click', openCreateUserModal);

  /* ---------------------------------------------------------------
     Сеть (заглушка на будущее)
     --------------------------------------------------------------- */
  async function refreshNetwork() {
    el.networkStatus.textContent = 'См. меню-бар';
  }

  /* ---------------------------------------------------------------
     Оформление
     --------------------------------------------------------------- */
  async function refreshAppearance() {
    let res;
    try { res = await call('theme.get'); }
    catch { return; }

    const curTheme = (res && res.theme) || 'dark';
    const curWp    = (res && res.wallpaper) || 'aurora';

    el.body.dataset.theme = curTheme;

    document.querySelectorAll('.theme-card').forEach(c => {
      c.classList.toggle('selected', c.dataset.theme === curTheme);
    });
    document.querySelectorAll('.wp-card').forEach(c => {
      c.classList.toggle('selected', c.dataset.wallpaper === curWp);
    });
  }

  document.querySelectorAll('.theme-card').forEach(c => {
    c.addEventListener('click', async () => {
      const theme = c.dataset.theme;
      el.body.dataset.theme = theme;
      document.querySelectorAll('.theme-card').forEach(x =>
        x.classList.toggle('selected', x === c));
      try { await call('theme.set', { theme }); showToast('Тема: ' + theme); }
      catch (e) { showToast('Ошибка: ' + e.message); }
    });
  });

  document.querySelectorAll('.wp-card').forEach(c => {
    c.addEventListener('click', async () => {
      const wallpaper = c.dataset.wallpaper;
      document.querySelectorAll('.wp-card').forEach(x =>
        x.classList.toggle('selected', x === c));
      try { await call('theme.set', { wallpaper }); showToast('Обои: ' + wallpaper); }
      catch (e) { showToast('Ошибка: ' + e.message); }
    });
  });

  /* ---------------------------------------------------------------
     Кнопки управления окном (frameless)
     --------------------------------------------------------------- */
  el.btnClose.addEventListener('click', async () => {
    const b = bridge();
    if (b && b.window_close) await b.window_close();
  });
  el.btnMin.addEventListener('click', async () => {
    const b = bridge();
    if (b && b.window_minimize) await b.window_minimize();
  });

  // Перетаскивание окна за titlebar.
  // pywebview не даёт «-webkit-app-region: drag» на GTK, поэтому
  // эмулируем вручную через window_move.
  let dragState = null;
  el.titlebar.addEventListener('mousedown', async (e) => {
    if (e.target.closest('.tb-dot')) return;
    const b = bridge();
    if (!b || !b.window_get_position) return;

    const pos = await b.window_get_position();
    dragState = {
      startX: e.screenX,
      startY: e.screenY,
      winX:   pos.x | 0,
      winY:   pos.y | 0,
      moved:  false,
    };
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!dragState) return;
    const b = bridge();
    if (!b || !b.window_move) return;

    const dx = e.screenX - dragState.startX;
    const dy = e.screenY - dragState.startY;
    if (Math.abs(dx) + Math.abs(dy) > 2) dragState.moved = true;
    b.window_move(dragState.winX + dx, dragState.winY + dy);
  });

  document.addEventListener('mouseup', () => { dragState = null; });

  /* ---------------------------------------------------------------
     Поиск по сайдбару (простой фильтр по label)
     --------------------------------------------------------------- */
  el.search.addEventListener('input', () => {
    const q = el.search.value.trim().toLowerCase();
    el.nav.querySelectorAll('.nav-item').forEach(it => {
      const label = it.querySelector('.nav-label').textContent.toLowerCase();
      it.style.display = !q || label.includes(q) ? '' : 'none';
    });
  });

  /* ---------------------------------------------------------------
     Bootstrap
     --------------------------------------------------------------- */
  async function boot() {
    // Сначала узнаём тему — чтобы не мигало белым/чёрным.
    try {
      const res = await call('theme.get');
      if (res && res.theme) el.body.dataset.theme = res.theme;
    } catch { /* остаёмся на dark по умолчанию */ }

    navigateTo('accounts');
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