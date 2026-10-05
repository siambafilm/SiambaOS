/* ================================================================
   SIamba OS — логика рабочего стола.
   Состояние приложений синхронизируется поллингом (без push из тредов).
   ================================================================ */
(() => {
  'use strict';

  const DEFAULT_TITLE = 'SIamba OS';
  const POLL_MS       = 1500;
  const CONFIRM_MS    = 3000;

  const api = () => (window.pywebview && window.pywebview.api) || null;

  /* ---------------- DOM ---------------- */
  const el = {
    activeName: document.getElementById('active-app-name'),
    dock:       document.getElementById('dock'),
    brandBtn:   document.getElementById('brand-btn'),
    brandMenu:  document.getElementById('brand-menu'),
    wifiBtn:    document.getElementById('wifi-btn'),
    wifiPop:    document.getElementById('wifi-popover'),
    volBtn:     document.getElementById('vol-btn'),
    volPop:     document.getElementById('vol-popover'),
    wifiToggle: document.getElementById('wifi-toggle'),
    wifiList:   document.getElementById('wifi-list'),
    wifiStatus: document.getElementById('wifi-status'),
    volSlider:  document.getElementById('volume-slider'),
    volLabel:   document.getElementById('volume-label'),
    muteBtn:    document.getElementById('mute-btn'),
  };

  /* ---------------- Состояние ---------------- */
  const state = {
    running: new Set(),
    active:  null,
    names:   {},
  };

  const dockItems = Array.from(el.dock.querySelectorAll('.dock-item'));
  const dockIcons = dockItems.map(it => it.querySelector('.dock-icon'));

  dockItems.forEach(it => {
    state.names[it.dataset.app] = it.dataset.name || it.dataset.app;
  });

  /* ---------------- Утилиты ---------------- */
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function showToast(msg) {
    let t = document.getElementById('toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'toast';
      t.className = 'toast';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.remove('show'), 2200);
  }

  /* ================================================================
     1. Часы
     ================================================================ */
  const clockTime = document.getElementById('clock-time');
  const clockDate = document.getElementById('clock-date');

  function updateClock() {
    const now = new Date();
    clockTime.textContent = now.toLocaleTimeString('ru-RU', {
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
    clockDate.textContent = now.toLocaleDateString('ru-RU', {
      weekday: 'short', day: 'numeric', month: 'short'
    });
  }

  /* ================================================================
     2. Батарея
     ================================================================ */
  function setBattery(percent) {
    const INNER_WIDTH = 18;
    document.querySelector('.battery-fill')
            .setAttribute('width', (INNER_WIDTH * percent / 100).toFixed(1));
    document.getElementById('battery-text').textContent = percent + '%';
  }

  /* ================================================================
     3. Активное имя
     ================================================================ */
  function refreshActiveName() {
    if (state.active && state.running.has(state.active)) {
      el.activeName.textContent = state.names[state.active] || state.active;
    } else {
      el.activeName.textContent = DEFAULT_TITLE;
    }
  }

  /* ================================================================
     4. Поллинг состояния приложений
     ================================================================ */
  function applyRunningSet(newSet) {
    dockItems.forEach(it => {
      it.dataset.running = newSet.has(it.dataset.app) ? 'true' : 'false';
    });
    if (!state.active || !newSet.has(state.active)) {
      state.active = newSet.values().next().value || null;
    }
    state.running = newSet;
    refreshActiveName();
  }

  let pollInFlight = false;

  async function pollRunning() {
    const a = api();
    if (!a || typeof a.get_running_apps !== 'function') return;
    if (pollInFlight) return;
    pollInFlight = true;
    try {
      const list = await a.get_running_apps();
      applyRunningSet(new Set(Array.isArray(list) ? list : []));
    } catch { /* noop */ }
    finally { pollInFlight = false; }
  }

  /* ================================================================
     5. Dock Magnification
     ================================================================ */
  const SCALE_CENTER   = 1.35;
  const SCALE_NEIGHBOR = 1.15;

  function updateDock(clientX) {
    const dockRect = el.dock.getBoundingClientRect();
    const mouseX   = clientX - dockRect.left;

    let nearest = -1, minDist = Infinity;
    for (let i = 0; i < dockItems.length; i++) {
      const center = dockItems[i].offsetLeft + dockItems[i].offsetWidth / 2;
      const d = Math.abs(mouseX - center);
      if (d < minDist) { minDist = d; nearest = i; }
    }
    for (let i = 0; i < dockIcons.length; i++) {
      const dist  = Math.abs(i - nearest);
      const scale = dist === 0 ? SCALE_CENTER
                  : dist === 1 ? SCALE_NEIGHBOR : 1;
      dockIcons[i].style.transform = `scale(${scale})`;
      dockItems[i].style.zIndex    = String(100 - dist);
    }
  }

  function resetDock() {
    dockIcons.forEach(i => { i.style.transform = 'scale(1)'; });
    dockItems.forEach(i => { i.style.zIndex = '1'; });
  }

  /* ================================================================
     6. Запуск / фокус приложений
     ================================================================ */
  async function launchOrFocus(appId) {
    if (state.running.has(appId)) {
      state.active = appId;
      refreshActiveName();
      return;
    }
    const a = api();
    if (a && typeof a.launch_app === 'function') {
      try {
        const res = await a.launch_app(appId);
        if (!res || !res.ok) {
          showToast(`Не удалось запустить ${appId}: ${res && res.error || '?'}`);
          return;
        }
        state.active = appId;
        await pollRunning();
      } catch (err) {
        showToast(`Ошибка запуска: ${err}`);
      }
    } else {
      console.log('[SIamba OS] launch →', appId);
      const s = new Set(state.running); s.add(appId);
      applyRunningSet(s);
    }
  }

  /* ================================================================
     7. Popovers
     ================================================================ */
  const allPopovers = [el.brandMenu, el.wifiPop, el.volPop];

  function closeAllPopovers(except) {
    allPopovers.forEach(p => { if (p && p !== except) p.hidden = true; });
  }

  function positionPopover(pop, anchor) {
    pop.hidden = false;
    const r  = anchor.getBoundingClientRect();
    const pw = pop.offsetWidth;
    const ph = pop.offsetHeight;

    let left = r.left;
    if (left + pw > window.innerWidth - 8) left = window.innerWidth - pw - 8;
    if (left < 8) left = 8;

    let top = r.bottom + 6;
    if (top + ph > window.innerHeight - 8) top = r.top - ph - 6;

    pop.style.left = left + 'px';
    pop.style.top  = top  + 'px';
  }

  el.brandBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = el.brandMenu.hidden;
    closeAllPopovers(el.brandMenu);
    if (willOpen) positionPopover(el.brandMenu, el.brandBtn);
    else          el.brandMenu.hidden = true;
  });

  el.wifiBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = el.wifiPop.hidden;
    closeAllPopovers(el.wifiPop);
    if (willOpen) {
      positionPopover(el.wifiPop, el.wifiBtn);
      startWifiLoop();
    } else {
      el.wifiPop.hidden = true;
      stopWifiLoop();
    }
  });

  el.volBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = el.volPop.hidden;
    closeAllPopovers(el.volPop);
    if (willOpen) {
      positionPopover(el.volPop, el.volBtn);
      startVolumeLoop();
    } else {
      el.volPop.hidden = true;
      stopVolumeLoop();
    }
  });

  document.addEventListener('click', () => {
    closeAllPopovers();
    stopWifiLoop();
    stopVolumeLoop();
  });

  allPopovers.forEach(p => p && p.addEventListener('click', e => e.stopPropagation()));

  /* ================================================================
     8. Меню SIambaOS (двухшаговое подтверждение)
     ================================================================ */
  const pendingConfirm = new Map();

  async function runPowerAction(action) {
    const a = api();
    if (!a || typeof a[action] !== 'function') {
      showToast(`${action}: бэкенд недоступен`);
      return;
    }
    try {
      const res = await a[action]();
      if (res && res.dev) {
        showToast(action === 'shutdown'
          ? 'DEV: выключение (симуляция)'
          : 'DEV: перезагрузка (симуляция)');
      } else if (res && res.ok) {
        showToast(action === 'shutdown' ? 'Выключение…' : 'Перезагрузка…');
      } else {
        showToast(`${action}: ${res && res.error || 'ошибка'}`);
      }
    } catch (e) {
      showToast(`${action}: ${e}`);
    }
  }

  el.brandMenu.querySelectorAll('button[data-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.action;

      if (action === 'shutdown' || action === 'reboot') {
        const original = btn.dataset.label || btn.textContent;
        btn.dataset.label = original;

        if (!pendingConfirm.has(btn)) {
          btn.textContent = action === 'shutdown'
            ? 'Нажмите ещё раз: выключить'
            : 'Нажмите ещё раз: перезагрузить';
          const t = setTimeout(() => {
            btn.textContent = original;
            pendingConfirm.delete(btn);
          }, CONFIRM_MS);
          pendingConfirm.set(btn, t);
          return;
        }
        clearTimeout(pendingConfirm.get(btn));
        pendingConfirm.delete(btn);
        btn.textContent = original;
        closeAllPopovers();
        await runPowerAction(action);
        return;
      }

      closeAllPopovers();
      if (action === 'about') {
        showToast('SIamba OS · pre-alpha');
      } else if (action === 'settings') {
        launchOrFocus('gnome-control-center');
      }
    });
  });

  /* ================================================================
     9. Wi-Fi popover
     ================================================================ */
  let wifiTimer = null;

  function startWifiLoop() {
    refreshWifi();
    stopWifiLoop();
    wifiTimer = setInterval(refreshWifi, 5000);
  }
  function stopWifiLoop() {
    if (wifiTimer) { clearInterval(wifiTimer); wifiTimer = null; }
  }

  async function refreshWifi() {
    const a = api();
    if (!a || !a.get_wifi_state) {
      el.wifiStatus.textContent = 'Бэкенд недоступен';
      el.wifiList.innerHTML = '';
      return;
    }
    let st;
    try { st = await a.get_wifi_state(); }
    catch (e) { el.wifiStatus.textContent = 'Ошибка чтения состояния'; return; }

    el.wifiToggle.checked = !!st.enabled;

    if (!st.available)     el.wifiStatus.textContent = 'nmcli не найден';
    else if (!st.enabled)  el.wifiStatus.textContent = 'Wi-Fi выключен';
    else if (st.connected) el.wifiStatus.textContent = `Подключено: ${st.connected}`;
    else                   el.wifiStatus.textContent = 'Не подключено';

    el.wifiList.innerHTML = '';
    const networks = st.networks || [];
    if (!networks.length) {
      el.wifiList.innerHTML = st.enabled
        ? '<div class="wifi-empty">Сети не найдены…</div>'
        : '<div class="wifi-empty">Включите Wi-Fi</div>';
      return;
    }
    networks.forEach(n => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'wifi-item';
      b.innerHTML = `<span class="wifi-ssid">${escapeHtml(n.ssid)}</span>
                     <span class="wifi-sig">${n.signal}%</span>`;
      b.addEventListener('click', async () => {
        const res = await a.connect_wifi(n.ssid);
        if (!res || !res.ok) showToast('Wi-Fi: ' + (res && res.error || 'ошибка'));
        setTimeout(refreshWifi, 800);
      });
      el.wifiList.appendChild(b);
    });
  }

  el.wifiToggle.addEventListener('change', async () => {
    const a = api();
    if (!a || !a.toggle_wifi) return;
    el.wifiStatus.textContent = 'Переключение…';
    try { await a.toggle_wifi(); } catch {}
    // Даём NetworkManager время применить состояние и просканировать
    setTimeout(refreshWifi, 1200);
  });

  /* ================================================================
     10. Volume popover
     ================================================================ */
  let volumeDebounce   = null;
  let volumeTimer      = null;
  let volumeDragging   = false;
  let lastFeedbackAt   = 0;
  const FEEDBACK_MIN_MS = 150;

  function startVolumeLoop() {
    refreshVolume();
    stopVolumeLoop();
    volumeTimer = setInterval(refreshVolume, 2000);
  }
  function stopVolumeLoop() {
    if (volumeTimer) { clearInterval(volumeTimer); volumeTimer = null; }
  }

  function renderVolumeIcon(muted) {
    el.muteBtn.dataset.muted = muted ? 'true' : 'false';
  }

  async function refreshVolume() {
    if (volumeDragging) return; // не перебиваем пользователя во время драга
    const a = api();
    if (!a || !a.get_volume) return;
    try {
      const v = await a.get_volume();
      el.volSlider.value = v.volume;
      el.volLabel.textContent = v.volume + '%';
      renderVolumeIcon(v.muted);
    } catch {}
  }

  async function playVolumeFeedback() {
    const now = performance.now();
    if (now - lastFeedbackAt < FEEDBACK_MIN_MS) return;
    lastFeedbackAt = now;
    const a = api();
    if (a && a.play_volume_feedback) {
      a.play_volume_feedback().catch(() => {});
    }
  }

  el.volSlider.addEventListener('pointerdown', () => { volumeDragging = true; });

  el.volSlider.addEventListener('input', () => {
    el.volLabel.textContent = el.volSlider.value + '%';

    clearTimeout(volumeDebounce);
    const a = api();
    if (!a || !a.set_volume) return;

    // Звук — отдельно, с собственным троттлингом
    playVolumeFeedback();

    volumeDebounce = setTimeout(() => {
      a.set_volume(parseInt(el.volSlider.value, 10)).catch(() => {});
    }, 60);
  });

  el.volSlider.addEventListener('change', () => {
    // Отпустили слайдер — синхронизируемся с бэкендом
    volumeDragging = false;
    setTimeout(refreshVolume, 150);
  });

  el.muteBtn.addEventListener('click', async () => {
    const a = api();
    if (!a || !a.toggle_mute) return;

    // Оптимистичный флип — иконка реагирует мгновенно
    const wasMuted = el.muteBtn.dataset.muted === 'true';
    renderVolumeIcon(!wasMuted);

    try { await a.toggle_mute(); } catch {}
    // Подтверждаем реальным состоянием
    setTimeout(refreshVolume, 120);
  });

  /* ================================================================
     11. Инициализация
     ================================================================ */
  dockItems.forEach(item => {
    item.addEventListener('click', () => launchOrFocus(item.dataset.app));
  });

  el.dock.addEventListener('mousemove', e => updateDock(e.clientX));
  el.dock.addEventListener('mouseleave', resetDock);

  updateClock();
  setInterval(updateClock, 1000);
  setBattery(78);
  refreshActiveName();

  window.addEventListener('pywebviewready', () => {
    pollRunning();
    setInterval(pollRunning, POLL_MS);
  });
  /* ================================================================
     12. Spotlight (Ctrl+Space)
     ================================================================ */
  const spot = {
    overlay:  document.getElementById('spotlight-overlay'),
    input:    document.getElementById('spotlight-input'),
    results:  document.getElementById('spotlight-results'),
    apps:     null,     // кэш приложений
    filtered: [],
    selected: 0,
    isOpen:   false,
    loading:  false,
  };

  const SPOT_MAX_RESULTS = 12;

  function escapeAttr(s) {
    return String(s).replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  /* ---------- открытие / закрытие ---------- */
  function openSpotlight() {
    if (spot.isOpen) return;
    spot.isOpen = true;
    spot.overlay.classList.add('show');
    spot.overlay.setAttribute('aria-hidden', 'false');
    spot.input.value = '';
    spot.selected = 0;
    renderSpotlightResults();

    // WebKitGTK не фокусирует элементы, пока overlay ещё visibility:hidden.
    setTimeout(() => spot.input.focus(), 40);

    if (!spot.apps && !spot.loading) loadApps();
  }

  function closeSpotlight() {
    if (!spot.isOpen) return;
    spot.isOpen = false;
    spot.overlay.classList.remove('show');
    spot.overlay.setAttribute('aria-hidden', 'true');
    spot.input.blur();
  }

  function toggleSpotlight() {
    spot.isOpen ? closeSpotlight() : openSpotlight();
  }

  /* ---------- загрузка списка ---------- */
  async function loadApps() {
    spot.loading = true;
    renderSpotlightResults();

    const a = api();
    if (a && typeof a.scan_linux_apps === 'function') {
      try {
        const list = await a.scan_linux_apps();
        spot.apps = Array.isArray(list) ? list : [];
      } catch (e) {
        spot.apps = [];
        showToast('Не удалось получить список приложений');
      }
    } else {
      // Dev-заглушка при открытии index.html в браузере
      spot.apps = [
        { id: 'firefox',           name: 'Firefox',         exec: 'firefox',                        bin: 'firefox',           comment: 'Веб-браузер' },
        { id: 'gnome-terminal',    name: 'Терминал',        exec: 'gnome-terminal',                 bin: 'gnome-terminal',    comment: 'Эмулятор терминала' },
        { id: 'xed',               name: 'Текстовый редактор', exec: 'xed',                         bin: 'xed',               comment: 'Простой редактор' },
        { id: 'nautilus',          name: 'Файлы',           exec: 'nautilus',                       bin: 'nautilus',          comment: 'Файловый менеджер' },
        { id: 'gnome-control-center', name: 'Настройки',    exec: 'gnome-control-center',           bin: 'gnome-control-center', comment: 'Параметры системы' },
        { id: 'eog',               name: 'Просмотр изображений', exec: 'eog',                       bin: 'eog',               comment: 'Image Viewer' },
      ];
    }

    spot.loading = false;
    renderSpotlightResults();
  }

  /* ---------- fuzzy-скоринг ---------- */
  function fuzzyScore(query, text) {
    if (!query) return 0;
    const q = query.toLowerCase();
    const t = text.toLowerCase();

    const idx = t.indexOf(q);
    if (idx === 0)  return 1000;
    if (idx > 0)    return Math.max(200, 800 - idx);

    // Subsequence — все символы query идут по порядку
    let qi = 0, ti = 0, score = 0, lastMatch = -1;
    while (qi < q.length && ti < t.length) {
      if (q[qi] === t[ti]) {
        score += 10;
        if (lastMatch === ti - 1) score += 5;   // бонус за непрерывность
        if (ti === 0) score += 8;               // бонус за старт
        lastMatch = ti;
        qi++;
      }
      ti++;
    }
    return qi === q.length ? score : -1;
  }

  function filterApps(query) {
    if (!spot.apps) return [];
    const q = query.trim();

    if (!q) return spot.apps.slice(0, SPOT_MAX_RESULTS);

    const scored = [];
    for (const app of spot.apps) {
      const nameScore    = fuzzyScore(q, app.name || '');
      const idScore      = fuzzyScore(q, app.id   || '');
      const commentScore = fuzzyScore(q, app.comment || '');
      const best = Math.max(
        nameScore,
        idScore      * 0.7,
        commentScore * 0.4
      );
      if (best >= 0) scored.push({ app, score: best });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, SPOT_MAX_RESULTS).map(s => s.app);
  }

  /* ---------- рендер ---------- */
  function renderSpotlightResults() {
    spot.results.innerHTML = '';

    if (spot.loading) {
      spot.results.innerHTML = '<div class="spotlight-empty">Загрузка…</div>';
      spot.filtered = [];
      return;
    }
    if (!spot.apps) {
      spot.filtered = [];
      return;
    }

    spot.filtered = filterApps(spot.input.value);
    if (spot.selected >= spot.filtered.length) {
      spot.selected = Math.max(0, spot.filtered.length - 1);
    }

    if (!spot.filtered.length) {
      spot.results.innerHTML =
        '<div class="spotlight-empty">Ничего не найдено</div>';
      return;
    }

    const frag = document.createDocumentFragment();
    spot.filtered.forEach((app, i) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'spotlight-item' + (i === spot.selected ? ' selected' : '');
      item.dataset.index = String(i);

      let iconHtml;
      if (app.icon_path) {
        const url = 'file://' + app.icon_path;
        iconHtml = `<img class="spotlight-icon" src="${escapeAttr(url)}" alt="">`;
      } else {
        const letter = (app.name || '?').trim().charAt(0).toUpperCase();
        iconHtml = `<span class="spotlight-icon spotlight-icon-fallback">${escapeHtml(letter)}</span>`;
      }

      item.innerHTML = `
        ${iconHtml}
        <span class="spotlight-item-text">
          <span class="spotlight-item-name">${escapeHtml(app.name)}</span>
          ${app.comment ? `<span class="spotlight-item-comment">${escapeHtml(app.comment)}</span>` : ''}
        </span>`;

      item.addEventListener('click', (e) => {
        e.stopPropagation();
        launchSpotlightApp(app);
      });
      item.addEventListener('mouseenter', () => {
        spot.selected = i;
        updateSpotlightSelection();
      });

      frag.appendChild(item);
    });
    spot.results.appendChild(frag);
  }

  function updateSpotlightSelection() {
    const items = spot.results.querySelectorAll('.spotlight-item');
    items.forEach((it, i) => it.classList.toggle('selected', i === spot.selected));
    const sel = items[spot.selected];
    if (sel) sel.scrollIntoView({ block: 'nearest' });
  }

  /* ---------- запуск ---------- */
  async function launchSpotlightApp(app) {
    closeSpotlight();

    const a = api();
    const bin = app.bin || app.id;

    if (a && typeof a.launch_app === 'function') {
      try {
        const res = await a.launch_app(bin, app.exec);
        if (!res || !res.ok) {
          showToast(`Не удалось запустить ${app.name}: ${res && res.error || '?'}`);
          return;
        }
        // Помечаем активным и подтягиваем состояние — точка в доке
        // загорится сразу, если иконка с таким bin есть.
        state.active = bin;
        refreshActiveName();
        await pollRunning();
      } catch (e) {
        showToast(`Ошибка запуска: ${e}`);
      }
    } else {
      console.log('[SIamba OS] spotlight launch →', app.name, app.exec);
    }
  }

  /* ---------- горячие клавиши ---------- */
  document.addEventListener('keydown', (e) => {
    // Ctrl+Space — открыть/закрыть (Ctrl без Shift/Alt/Meta)
    if (e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey
        && e.code === 'Space') {
      e.preventDefault();
      toggleSpotlight();
      return;
    }

    if (!spot.isOpen) return;

    switch (e.key) {
      case 'Escape':
        e.preventDefault();
        closeSpotlight();
        break;

      case 'ArrowDown':
        e.preventDefault();
        if (spot.filtered.length) {
          spot.selected = (spot.selected + 1) % spot.filtered.length;
          updateSpotlightSelection();
        }
        break;

      case 'ArrowUp':
        e.preventDefault();
        if (spot.filtered.length) {
          spot.selected =
            (spot.selected - 1 + spot.filtered.length) % spot.filtered.length;
          updateSpotlightSelection();
        }
        break;

      case 'Enter': {
        e.preventDefault();
        const app = spot.filtered[spot.selected];
        if (app) launchSpotlightApp(app);
        break;
      }

      case 'Home':
        if (spot.filtered.length) {
          e.preventDefault();
          spot.selected = 0;
          updateSpotlightSelection();
        }
        break;

      case 'End':
        if (spot.filtered.length) {
          e.preventDefault();
          spot.selected = spot.filtered.length - 1;
          updateSpotlightSelection();
        }
        break;
    }
  });

  /* ---------- ввод и клик снаружи ---------- */
  spot.input.addEventListener('input', () => {
    spot.selected = 0;
    renderSpotlightResults();
  });

  spot.overlay.addEventListener('click', (e) => {
    if (e.target === spot.overlay) closeSpotlight();
  });
})();