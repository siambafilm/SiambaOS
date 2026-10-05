/* ================================================================
   SIamba OS — логика рабочего стола
   ================================================================ */
(() => {
  'use strict';

  /* ------------------------------------------------------------
     1. Часы в menu bar — обновление раз в секунду
     ------------------------------------------------------------ */
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
  updateClock();
  setInterval(updateClock, 1000);

  /* ------------------------------------------------------------
     2. Индикатор батареи (заглушка)
        В реальной ОС сюда придёт значение из SystemAPI.
     ------------------------------------------------------------ */
  function setBattery(percent) {
    const INNER_WIDTH = 18;   // доступная ширина внутри корпуса
    const fill = document.querySelector('.battery-fill');
    fill.setAttribute('width', (INNER_WIDTH * percent / 100).toFixed(1));
    document.getElementById('battery-text').textContent = percent + '%';
  }
  setBattery(78);

  /* ------------------------------------------------------------
     3. Dock Magnification («рыбий глаз»)

     - ближайшая к курсору иконка ищется по её layout-координатам
       (offsetLeft / offsetWidth не реагируют на CSS transform,
        поэтому позиции не «плывут» во время анимации);
     - scale: центр = 1.35, соседи = 1.15, остальные = 1;
     - плавность обеспечивает CSS transition — JS только
       выставляет конечное значение.
     ------------------------------------------------------------ */
  const dock  = document.getElementById('dock');
  const items = Array.from(dock.querySelectorAll('.dock-item'));
  const icons = items.map(it => it.querySelector('.dock-icon'));

  const SCALE_CENTER   = 1.35;
  const SCALE_NEIGHBOR = 1.15;

  function updateDock(clientX) {
    const dockRect = dock.getBoundingClientRect();
    const mouseX   = clientX - dockRect.left;

    // Ищем ближайшую иконку
    let nearest = -1;
    let minDist = Infinity;
    for (let i = 0; i < items.length; i++) {
      const center = items[i].offsetLeft + items[i].offsetWidth / 2;
      const d = Math.abs(mouseX - center);
      if (d < minDist) { minDist = d; nearest = i; }
    }

    // Применяем масштабы + z-index по дистанции
    for (let i = 0; i < icons.length; i++) {
      const dist  = Math.abs(i - nearest);
      const scale = dist === 0 ? SCALE_CENTER
                  : dist === 1 ? SCALE_NEIGHBOR
                  : 1;

      icons[i].style.transform = `scale(${scale})`;
      items[i].style.zIndex    = String(100 - dist);
    }
  }

  function resetDock() {
    icons.forEach(icon => { icon.style.transform = 'scale(1)'; });
    items.forEach(item => { item.style.zIndex = '1'; });
  }

  dock.addEventListener('mousemove', (e) => updateDock(e.clientX));
  dock.addEventListener('mouseleave', resetDock);

  /* ------------------------------------------------------------
     4. Клики по иконкам дока

     Если приложение открыто внутри pywebview — вызываем
     SystemAPI.test_launch; иначе просто логируем в консоль.
     ------------------------------------------------------------ */
  function launchApp(appId) {
    if (window.pywebview && window.pywebview.api &&
        typeof window.pywebview.api.test_launch === 'function') {
      window.pywebview.api.test_launch(appId)
        .then(res => console.log('[SIamba OS] launch result:', res))
        .catch(err => console.error('[SIamba OS] launch error:', err));
    } else {
      console.log('[SIamba OS] launch →', appId);
    }
  }

  items.forEach(item => {
    item.addEventListener('click', () => launchApp(item.dataset.app));
  });

  /* ------------------------------------------------------------
     5. Кнопки меню (заглушки, хуки для SystemAPI)
     ------------------------------------------------------------ */
  document.querySelectorAll('.menu button').forEach(btn => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.action;
      console.log('[SIamba OS] menu action →', action);
      // При интеграции с pywebview:
      // window.pywebview.api.system_action(action);
    });
  });

  /* ------------------------------------------------------------
     6. Обновление метрик из бэкенда (если доступен)
     ------------------------------------------------------------ */
  window.addEventListener('pywebviewready', async () => {
    try {
      const stats = await window.pywebview.api.get_system_stats();
      console.log('[SIamba OS] system stats:', stats);
    } catch (e) {
      console.warn('[SIamba OS] stats unavailable:', e);
    }
  });
})();