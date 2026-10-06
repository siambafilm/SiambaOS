/* ================================================================
   SIamba OS — логика рабочего стола.
   Секции:
     1.  Утилиты и DOM
     2.  Часы
     3.  Батарея
     4.  Активное имя
     5.  Поллинг состояния приложений
     6.  Реестр иконок
     7.  Рендер дока
     8.  Magnification
     9.  Запуск/фокус
     10. Popovers
     11. Меню SIambaOS
     12. Wi-Fi
     13. Volume
     14. Spotlight
     15. Launchpad
     16. Context menu
     17. Инициализация
   ================================================================ */
(() => {
  'use strict';

  const DEFAULT_TITLE  = 'SIamba OS';
  const POLL_MS        = 1500;
  const CONFIRM_MS     = 3000;
  const SPOT_MAX       = 12;
  const LAUNCHPAD_LIMIT = 200;

  const api = () => (window.pywebview && window.pywebview.api) || null;

  /* ================================================================
     1. DOM + утилиты
     ================================================================ */
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
    launchpad:     document.getElementById('launchpad-overlay'),
    launchpadGrid: document.getElementById('launchpad-grid'),
    ctxMenu:       document.getElementById('context-menu'),
    ctxItems:      document.getElementById('context-menu-items'),
  };

  const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const escapeAttr = (s) => String(s).replace(/"/g, '&quot;').replace(/</g, '&lt;');

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
     Общее состояние
     ================================================================ */
  const state = {
    running:  new Set(),
    active:   null,
    allApps:  [],       // из scan_linux_apps()
    dock:     [],       // список bin из конфига
    appsReady: false,
  };

  let dockItems = [];   // .dock-item[data-app]
  let dockIcons = [];   // их .dock-icon

  /* ================================================================
     2. Часы
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
     3. Батарея
     ================================================================ */
  function setBattery(percent) {
    const INNER_WIDTH = 18;
    const fill = document.querySelector('.battery-fill');
    if (fill) fill.setAttribute('width', (INNER_WIDTH * percent / 100).toFixed(1));
    const t = document.getElementById('battery-text');
    if (t) t.textContent = percent + '%';
  }

  /* ================================================================
     4. Активное имя в menu bar
     ================================================================ */
  function displayNameFor(bin) {
    if (DOCK_REGISTRY[bin]) return DOCK_REGISTRY[bin].name;
    const app = state.allApps.find(a => a.bin === bin);
    return app ? app.name : bin;
  }

  function refreshActiveName() {
    if (state.active && state.running.has(state.active)) {
      el.activeName.textContent = displayNameFor(state.active);
    } else {
      el.activeName.textContent = DEFAULT_TITLE;
    }
  }

  /* ================================================================
     5. Состояние приложений (поллинг)
     ================================================================ */
  function applyRunningSet(newSet) {
    dockItems.forEach(it => {
      const aid = it.dataset.app;
      it.dataset.running = newSet.has(aid) ? 'true' : 'false';
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
    } catch {}
    finally { pollInFlight = false; }
  }

  /* ================================================================
     6. Реестр иконок
     Приложения, для которых есть кастомный SVG + градиент.
     Всё остальное рендерится через icon_path из .desktop.
     ================================================================ */
  const DOCK_REGISTRY = {
    "xed": {
      name: "Text Editor",
      gradient: "linear-gradient(180deg, #4fa8f0, #1a5fc4)",
      svg: '<svg viewBox="0 0 100 100" fill="none"><ellipse cx="35" cy="42" rx="4" ry="9" fill="#fff"/><ellipse cx="65" cy="42" rx="4" ry="9" fill="#fff"/><path d="M28 66 Q50 84 72 66" stroke="#fff" stroke-width="5" fill="none" stroke-linecap="round"/></svg>',
    },
    "gnome-terminal": {
      name: "Terminal",
      gradient: "linear-gradient(180deg, #2b2b33, #101015)",
      svg: '<svg viewBox="0 0 24 24" fill="none" stroke="#7ee787" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 17 11 11 5 5"/><line x1="13" y1="19" x2="21" y2="19"/></svg>',
    },
    "firefox": {
      name: "Firefox",
      gradient: "linear-gradient(180deg, #58c4ff, #1a7fd4)",
      svg: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
    },
    "thunderbird": {
      name: "Thunderbird",
      gradient: "linear-gradient(180deg, #5ec8ff, #1a7fd4)",
      svg: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="3"/><path d="m22 6-10 7L2 6"/></svg>',
    },
    "rhythmbox": {
      name: "Rhythmbox",
      gradient: "linear-gradient(180deg, #ff5e8a, #c11a55)",
      svg: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
    },
    "eog": {
      name: "Image Viewer",
      gradient: "linear-gradient(135deg, #ffb84d 0%, #ff5e8a 50%, #a855f7 100%)",
      svg: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>',
    },
    "gnome-calendar": {
      name: "Calendar",
      gradient: "#ffffff",
      svg: '<svg viewBox="0 0 24 24" fill="none" stroke="#e11d48" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="3"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
    },
    "gnome-control-center": {
      name: "System Settings",
      gradient: "linear-gradient(180deg, #9a9aa5, #4a4a55)",
      svg: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    },
    "nautilus": {
      name: "Files",
      gradient: "linear-gradient(180deg, #c8ccd4, #7a7f8a)",
      svg: '<svg viewBox="0 0 24 24" fill="none" stroke="#2a2a30" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>',
    },
    // Launchpad — «специальная» иконка, отдельная от данных приложений
    "__launchpad__": {
      name: "Все приложения",
      gradient: "linear-gradient(180deg, #7a7f8a, #3a3a44)",
      svg: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
    },
  };

  /* ================================================================
     7. Рендер дока
     ================================================================ */
  function buildDockIconContent(bin) {
    const reg = DOCK_REGISTRY[bin];
    if (reg) {
      return {
        style: `background: ${reg.gradient};`,
        inner: reg.svg,
      };
    }
    // Fallback — иконка из .desktop
    const app = state.allApps.find(a => a.bin === bin);
    if (app && app.icon_path) {
      const url = 'file://' + app.icon_path;
      return {
        style: 'background: rgba(255,255,255,0.06);',
        inner: `<img src="${escapeAttr(url)}" alt="">`,
      };
    }
    // Совсем нет иконки — буква
    const letter = (app ? app.name : bin).trim().charAt(0).toUpperCase();
    return {
      style: 'background: linear-gradient(135deg, #7dd3fc, #c084fc); color: #0a0a14; font-weight:700; font-size:22px;',
      inner: letter,
    };
  }

  function createDockItem(bin) {
    const item = document.createElement('div');
    item.className = 'dock-item';
    item.dataset.app = bin;
    item.dataset.running = state.running.has(bin) ? 'true' : 'false';
    item.title = displayNameFor(bin);

    const iconData = buildDockIconContent(bin);
    const icon = document.createElement('div');
    icon.className = 'dock-icon';
    icon.setAttribute('style', iconData.style);
    icon.innerHTML = iconData.inner;

    const dot = document.createElement('span');
    dot.className = 'dot';

    item.appendChild(icon);
    item.appendChild(dot);

    item.addEventListener('click', () => launchOrFocus(bin));
    item.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showContextMenu(e.clientX, e.clientY, [
        {
          label: 'Убрать из дока',
          danger: true,
          action: () => removeFromDock(bin),
        },
      ]);
    });

    return item;
  }

  function createLaunchpadButton() {
    const item = document.createElement('div');
    item.className = 'dock-item special';
    item.dataset.special = 'launchpad';
    item.title = 'Все приложения';

    const reg = DOCK_REGISTRY['__launchpad__'];
    const icon = document.createElement('div');
    icon.className = 'dock-icon';
    icon.setAttribute('style', `background: ${reg.gradient};`);
    icon.innerHTML = reg.svg;

    item.appendChild(icon);
    item.addEventListener('click', () => openLaunchpad());

    return item;
  }

  function renderDock() {
    el.dock.innerHTML = '';

    for (const bin of state.dock) {
      el.dock.appendChild(createDockItem(bin));
    }

    if (state.dock.length) {
      const divider = document.createElement('div');
      divider.className = 'dock-divider';
      el.dock.appendChild(divider);
    }
    el.dock.appendChild(createLaunchpadButton());

    refreshDockRefs();
    el.dock.classList.add('dock-ready');
  }

  function refreshDockRefs() {
    dockItems = Array.from(el.dock.querySelectorAll('.dock-item[data-app]'));
    dockIcons = Array.from(el.dock.querySelectorAll('.dock-item .dock-icon'));
  }

  async function saveDock(newList) {
    state.dock = newList;
    renderDock();
    applyRunningSet(state.running);  // восстановить точки у новых иконок

    const a = api();
    if (a && typeof a.set_dock_config === 'function') {
      try { await a.set_dock_config(newList); } catch {}
    }
  }

  async function addToDock(bin) {
    if (state.dock.includes(bin)) return;
    await saveDock([...state.dock, bin]);
    showToast(`«${displayNameFor(bin)}» добавлено в док`);
  }

  async function removeFromDock(bin) {
    if (!state.dock.includes(bin)) return;
    await saveDock(state.dock.filter(x => x !== bin));
    showToast(`«${displayNameFor(bin)}» убрано из дока`);
  }

  /* ================================================================
     8. Magnification
     ================================================================ */
  const SCALE_CENTER = 1.35;
  const SCALE_NEIGHBOR = 1.15;

  function updateDock(clientX) {
    if (!dockIcons.length) return;
    const allItems = Array.from(el.dock.querySelectorAll('.dock-item'));
    if (!allItems.length) return;

    const dockRect = el.dock.getBoundingClientRect();
    const mouseX = clientX - dockRect.left;

    let nearest = -1, minDist = Infinity;
    for (let i = 0; i < allItems.length; i++) {
      const center = allItems[i].offsetLeft + allItems[i].offsetWidth / 2;
      const d = Math.abs(mouseX - center);
      if (d < minDist) { minDist = d; nearest = i; }
    }

    const icons = allItems.map(it => it.querySelector('.dock-icon'));
    for (let i = 0; i < icons.length; i++) {
      const dist = Math.abs(i - nearest);
      const scale = dist === 0 ? SCALE_CENTER
                  : dist === 1 ? SCALE_NEIGHBOR : 1;
      icons[i].style.transform = `scale(${scale})`;
      allItems[i].style.zIndex = String(100 - dist);
    }
  }

  function resetDock() {
    el.dock.querySelectorAll('.dock-item').forEach(it => {
      const ic = it.querySelector('.dock-icon');
      if (ic) ic.style.transform = 'scale(1)';
      it.style.zIndex = '1';
    });
  }

  /* ================================================================
     9. Запуск / фокус
     ================================================================ */
  async function launchOrFocus(bin, execCmd) {
    if (state.running.has(bin)) {
      state.active = bin;
      refreshActiveName();
      return;
    }

    const a = api();
    if (a && typeof a.launch_app === 'function') {
      try {
        const res = await a.launch_app(bin, execCmd);
        if (!res || !res.ok) {
          showToast(`Не удалось запустить ${displayNameFor(bin)}: ${res && res.error || '?'}`);
          return;
        }
        state.active = bin;
        await pollRunning();
      } catch (err) {
        showToast(`Ошибка запуска: ${err}`);
      }
    } else {
      console.log('[SIamba OS] launch →', bin, execCmd);
      const s = new Set(state.running); s.add(bin);
      applyRunningSet(s);
    }
  }

  /* ================================================================
     10. Popovers
     ================================================================ */
  const allPopovers = [el.brandMenu, el.wifiPop, el.volPop];

  function closeAllPopovers(except) {
    allPopovers.forEach(p => { if (p && p !== except) p.hidden = true; });
  }

  function positionPopover(pop, anchor) {
    pop.hidden = false;
    const r = anchor.getBoundingClientRect();
    const pw = pop.offsetWidth;
    const ph = pop.offsetHeight;
    let left = r.left;
    if (left + pw > window.innerWidth - 8) left = window.innerWidth - pw - 8;
    if (left < 8) left = 8;
    let top = r.bottom + 6;
    if (top + ph > window.innerHeight - 8) top = r.top - ph - 6;
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
  }

  el.brandBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = el.brandMenu.hidden;
    closeAllPopovers(el.brandMenu);
    if (willOpen) positionPopover(el.brandMenu, el.brandBtn);
    else el.brandMenu.hidden = true;
  });

  el.wifiBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = el.wifiPop.hidden;
    closeAllPopovers(el.wifiPop);
    if (willOpen) { positionPopover(el.wifiPop, el.wifiBtn); startWifiLoop(); }
    else { el.wifiPop.hidden = true; stopWifiLoop(); }
  });

  el.volBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = el.volPop.hidden;
    closeAllPopovers(el.volPop);
    if (willOpen) { positionPopover(el.volPop, el.volBtn); startVolumeLoop(); }
    else { el.volPop.hidden = true; stopVolumeLoop(); }
  });

  document.addEventListener('click', () => {
    closeAllPopovers();
    stopWifiLoop();
    stopVolumeLoop();
    hideContextMenu();
  });
  allPopovers.forEach(p => p && p.addEventListener('click', e => e.stopPropagation()));

  /* ================================================================
     11. Меню SIambaOS
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
    } catch (e) { showToast(`${action}: ${e}`); }
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
      if (action === 'about') showToast('SIamba OS · pre-alpha');
      else if (action === 'settings') launchOrFocus('gnome-control-center');
    });
  });

  /* ================================================================
     12. Wi-Fi
     ================================================================ */
  let wifiTimer = null;
  function startWifiLoop() { refreshWifi(); stopWifiLoop(); wifiTimer = setInterval(refreshWifi, 5000); }
  function stopWifiLoop()  { if (wifiTimer) { clearInterval(wifiTimer); wifiTimer = null; } }

  async function refreshWifi() {
    const a = api();
    if (!a || !a.get_wifi_state) {
      el.wifiStatus.textContent = 'Бэкенд недоступен';
      el.wifiList.innerHTML = '';
      return;
    }
    let st;
    try { st = await a.get_wifi_state(); }
    catch { el.wifiStatus.textContent = 'Ошибка чтения состояния'; return; }

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
    setTimeout(refreshWifi, 1200);
  });

  /* ================================================================
     13. Volume
     ================================================================ */
  let volumeDebounce = null;
  let volumeTimer    = null;
  let volumeDragging = false;
  let lastFeedbackAt = 0;
  const FEEDBACK_MIN_MS = 150;

  function startVolumeLoop() { refreshVolume(); stopVolumeLoop(); volumeTimer = setInterval(refreshVolume, 2000); }
  function stopVolumeLoop()  { if (volumeTimer) { clearInterval(volumeTimer); volumeTimer = null; } }

  function renderVolumeIcon(muted) {
    el.muteBtn.dataset.muted = muted ? 'true' : 'false';
  }

  async function refreshVolume() {
    if (volumeDragging) return;
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
    if (a && a.play_volume_feedback) a.play_volume_feedback().catch(() => {});
  }

  el.volSlider.addEventListener('pointerdown', () => { volumeDragging = true; });
  el.volSlider.addEventListener('input', () => {
    el.volLabel.textContent = el.volSlider.value + '%';
    clearTimeout(volumeDebounce);
    const a = api();
    if (!a || !a.set_volume) return;
    playVolumeFeedback();
    volumeDebounce = setTimeout(() => {
      a.set_volume(parseInt(el.volSlider.value, 10)).catch(() => {});
    }, 60);
  });
  el.volSlider.addEventListener('change', () => {
    volumeDragging = false;
    setTimeout(refreshVolume, 150);
  });

  el.muteBtn.addEventListener('click', async () => {
    const a = api();
    if (!a || !a.toggle_mute) return;
    const wasMuted = el.muteBtn.dataset.muted === 'true';
    renderVolumeIcon(!wasMuted);
    try { await a.toggle_mute(); } catch {}
    setTimeout(refreshVolume, 120);
  });

  /* ================================================================
     14. Spotlight
     ================================================================ */
  const spot = {
    overlay:  document.getElementById('spotlight-overlay'),
    input:    document.getElementById('spotlight-input'),
    results:  document.getElementById('spotlight-results'),
    filtered: [],
    selected: 0,
    isOpen:   false,
  };

  function openSpotlight() {
    if (spot.isOpen) return;
    spot.isOpen = true;
    spot.overlay.classList.add('show');
    spot.overlay.setAttribute('aria-hidden', 'false');
    spot.input.value = '';
    spot.selected = 0;
    renderSpotlightResults();
    setTimeout(() => spot.input.focus(), 40);
  }

  function closeSpotlight() {
    if (!spot.isOpen) return;
    spot.isOpen = false;
    spot.overlay.classList.remove('show');
    spot.overlay.setAttribute('aria-hidden', 'true');
    spot.input.blur();
  }

  function fuzzyScore(query, text) {
    if (!query) return 0;
    const q = query.toLowerCase();
    const t = text.toLowerCase();
    const idx = t.indexOf(q);
    if (idx === 0) return 1000;
    if (idx > 0) return Math.max(200, 800 - idx);
    let qi = 0, ti = 0, score = 0, lastMatch = -1;
    while (qi < q.length && ti < t.length) {
      if (q[qi] === t[ti]) {
        score += 10;
        if (lastMatch === ti - 1) score += 5;
        if (ti === 0) score += 8;
        lastMatch = ti; qi++;
      }
      ti++;
    }
    return qi === q.length ? score : -1;
  }

  function filterApps(query) {
    const q = query.trim();
    if (!q) return state.allApps.slice(0, SPOT_MAX);
    const scored = [];
    for (const app of state.allApps) {
      const s = Math.max(
        fuzzyScore(q, app.name || ''),
        fuzzyScore(q, app.id || '') * 0.7,
        fuzzyScore(q, app.comment || '') * 0.4,
      );
      if (s >= 0) scored.push({ app, score: s });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, SPOT_MAX).map(s => s.app);
  }

  function renderSpotlightResults() {
    spot.results.innerHTML = '';

    if (!state.appsReady) {
      spot.results.innerHTML = '<div class="spotlight-empty">Загрузка…</div>';
      spot.filtered = [];
      return;
    }

    spot.filtered = filterApps(spot.input.value);
    if (spot.selected >= spot.filtered.length) {
      spot.selected = Math.max(0, spot.filtered.length - 1);
    }
    if (!spot.filtered.length) {
      spot.results.innerHTML = '<div class="spotlight-empty">Ничего не найдено</div>';
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
        iconHtml = `<img class="spotlight-icon" src="${escapeAttr('file://' + app.icon_path)}" alt="">`;
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
      item.addEventListener('click', (e) => { e.stopPropagation(); launchSpotlightApp(app); });
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

  function launchSpotlightApp(app) {
    closeSpotlight();
    launchOrFocus(app.bin || app.id, app.exec);
  }

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey && e.code === 'Space') {
      e.preventDefault();
      spot.isOpen ? closeSpotlight() : openSpotlight();
      return;
    }

    // Escape закрывает всё модальное
    if (e.key === 'Escape') {
      if (!el.ctxMenu.hidden) { hideContextMenu(); e.preventDefault(); return; }
      if (el.launchpad.classList.contains('show')) { closeLaunchpad(); e.preventDefault(); return; }
      if (spot.isOpen) { closeSpotlight(); e.preventDefault(); return; }
      return;
    }

    if (!spot.isOpen) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (spot.filtered.length) { spot.selected = (spot.selected + 1) % spot.filtered.length; updateSpotlightSelection(); }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (spot.filtered.length) { spot.selected = (spot.selected - 1 + spot.filtered.length) % spot.filtered.length; updateSpotlightSelection(); }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const app = spot.filtered[spot.selected];
      if (app) launchSpotlightApp(app);
    } else if (e.key === 'Home') {
      if (spot.filtered.length) { e.preventDefault(); spot.selected = 0; updateSpotlightSelection(); }
    } else if (e.key === 'End') {
      if (spot.filtered.length) { e.preventDefault(); spot.selected = spot.filtered.length - 1; updateSpotlightSelection(); }
    }
  });

  spot.input.addEventListener('input', () => {
    spot.selected = 0;
    renderSpotlightResults();
  });
  spot.overlay.addEventListener('click', (e) => { if (e.target === spot.overlay) closeSpotlight(); });

  /* ================================================================
     15. Launchpad
     ================================================================ */
  function buildLaunchpadIconContent(app) {
    const reg = DOCK_REGISTRY[app.bin];
    if (reg) {
      return `<div class="launchpad-icon" style="background: ${reg.gradient};">${reg.svg}</div>`;
    }
    if (app.icon_path) {
      return `<div class="launchpad-icon"><img src="${escapeAttr('file://' + app.icon_path)}" alt=""></div>`;
    }
    const letter = (app.name || '?').trim().charAt(0).toUpperCase();
    return `<div class="launchpad-icon launchpad-icon-fallback">${escapeHtml(letter)}</div>`;
  }

  function renderLaunchpad() {
    el.launchpadGrid.innerHTML = '';

    const apps = state.allApps.slice(0, LAUNCHPAD_LIMIT);
    if (!apps.length) {
      el.launchpadGrid.innerHTML =
        '<div class="launchpad-empty">Список приложений пуст</div>';
      return;
    }

    const frag = document.createDocumentFragment();
    apps.forEach(app => {
      const bin = app.bin || app.id;
      const inDock = state.dock.includes(bin);

      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'launchpad-app';
      b.title = app.comment || app.name;
      b.innerHTML = `
        ${buildLaunchpadIconContent(app)}
        <span class="launchpad-name">${escapeHtml(app.name)}</span>`;

      b.addEventListener('click', () => {
        closeLaunchpad();
        launchOrFocus(bin, app.exec);
      });
      b.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const items = inDock
          ? [{ label: 'Убрать из дока', danger: true,
               action: () => removeFromDock(bin).then(renderLaunchpad) }]
          : [{ label: 'Добавить в док',
               action: () => addToDock(bin).then(renderLaunchpad) }];
        showContextMenu(e.clientX, e.clientY, items);
      });

      frag.appendChild(b);
    });
    el.launchpadGrid.appendChild(frag);
  }

  function openLaunchpad() {
    renderLaunchpad();
    el.launchpad.classList.add('show');
    el.launchpad.setAttribute('aria-hidden', 'false');
  }

  function closeLaunchpad() {
    el.launchpad.classList.remove('show');
    el.launchpad.setAttribute('aria-hidden', 'true');
  }

  el.launchpad.addEventListener('click', (e) => {
    if (e.target === el.launchpad || e.target === el.launchpadGrid) closeLaunchpad();
  });

  /* ================================================================
     16. Context menu
     ================================================================ */
  function showContextMenu(x, y, items) {
    el.ctxItems.innerHTML = '';

    for (const it of items) {
      if (it.separator) {
        const sep = document.createElement('div');
        sep.className = 'sep';
        el.ctxItems.appendChild(sep);
        continue;
      }
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'menu-item' + (it.danger ? ' danger' : '');
      b.textContent = it.label;
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        hideContextMenu();
        try { it.action(); } catch (err) { console.error(err); }
      });
      el.ctxItems.appendChild(b);
    }

    el.ctxMenu.hidden = false;
    const w = el.ctxMenu.offsetWidth;
    const h = el.ctxMenu.offsetHeight;
    let lx = x, ly = y;
    if (lx + w > window.innerWidth - 8)  lx = window.innerWidth - w - 8;
    if (ly + h > window.innerHeight - 8) ly = window.innerHeight - h - 8;
    if (lx < 8) lx = 8;
    if (ly < 8) ly = 8;
    el.ctxMenu.style.left = lx + 'px';
    el.ctxMenu.style.top  = ly + 'px';

    // Клик по контекстному меню не должен всплывать до document
    el.ctxMenu.onclick = (e) => e.stopPropagation();
  }

  function hideContextMenu() {
    el.ctxMenu.hidden = true;
  }

  // ПКМ по фону окна — не открываем браузерное меню
  document.addEventListener('contextmenu', (e) => {
    // Разрешаем дефолтное поведение в полях ввода (копипаста)
    if (e.target.closest('input, textarea')) return;
    e.preventDefault();
  });

  /* ================================================================
     17. Инициализация
     ================================================================ */
  async function loadAppsAndDock() {
    const a = api();

    // Список приложений
    if (a && typeof a.scan_linux_apps === 'function') {
      try {
        const list = await a.scan_linux_apps();
        state.allApps = Array.isArray(list) ? list : [];
      } catch (e) {
        state.allApps = [];
        showToast('Не удалось получить список приложений');
      }
    } else {
      // Дев-заглушка для открытия index.html без pywebview
      state.allApps = [
        { id: 'firefox',            name: 'Firefox',            exec: 'firefox',              bin: 'firefox',              comment: 'Веб-браузер' },
        { id: 'gnome-terminal',     name: 'Терминал',           exec: 'gnome-terminal',       bin: 'gnome-terminal',       comment: 'Эмулятор терминала' },
        { id: 'xed',                name: 'Текстовый редактор', exec: 'xed',                  bin: 'xed',                  comment: 'Простой редактор' },
        { id: 'nautilus',           name: 'Файлы',              exec: 'nautilus',             bin: 'nautilus',             comment: 'Файловый менеджер' },
        { id: 'gnome-control-center', name: 'Настройки',        exec: 'gnome-control-center', bin: 'gnome-control-center', comment: 'Параметры системы' },
        { id: 'eog',                name: 'Просмотр изображений', exec: 'eog',                bin: 'eog',                  comment: 'Image Viewer' },
      ];
    }
    state.appsReady = true;

    // Конфиг дока
    let dock = null;
    if (a && typeof a.get_dock_config === 'function') {
      try { dock = await a.get_dock_config(); } catch {}
    }
    if (!Array.isArray(dock) || !dock.length) {
      dock = ['xed', 'gnome-terminal', 'firefox', 'thunderbird', 'rhythmbox',
              'eog', 'gnome-calendar', 'gnome-control-center', 'nautilus'];
    }
    state.dock = dock;

    renderDock();
    applyRunningSet(state.running);
    refreshActiveName();

    if (spot.isOpen) renderSpotlightResults();
    if (el.launchpad.classList.contains('show')) renderLaunchpad();
  }

  el.dock.addEventListener('mousemove', e => updateDock(e.clientX));
  el.dock.addEventListener('mouseleave', resetDock);

  updateClock();
  setInterval(updateClock, 1000);
  setBattery(78);

  window.addEventListener('pywebviewready', () => {
    loadAppsAndDock().then(() => {
      pollRunning();
      setInterval(pollRunning, POLL_MS);
    });
  });

  // Dev-режим в браузере — pywebviewready не придёт
  if (!window.pywebview) {
    setTimeout(() => {
      if (!state.appsReady) loadAppsAndDock();
    }, 200);
  }
})();