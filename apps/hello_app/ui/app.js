const api = () => (window.pywebview && window.pywebview.api) || null;

const greetingEl = document.getElementById('greeting');
const statsEl    = document.getElementById('stats');
const nameEl     = document.getElementById('name');

async function refreshStats() {
  statsEl.textContent = 'Загрузка…';
  const a = api();
  if (!a || !a.call) { statsEl.textContent = 'нет соединения'; return; }
  const res = await a.call('get_stats', {});
  if (res && res.ok) statsEl.textContent = JSON.stringify(res.stats, null, 2);
  else               statsEl.textContent = 'Ошибка: ' + (res && res.error || '?');
}

async function greet() {
  const a = api();
  if (!a || !a.call) return;
  const res = await a.call('greet', { name: nameEl.value || 'мир' });
  if (res && res.ok) greetingEl.textContent = res.text;
}

document.getElementById('refresh').addEventListener('click', refreshStats);
document.getElementById('greet').addEventListener('click', greet);

/* --- Управление frameless-окном --- */
(function initTitlebar() {
  const tb = document.getElementById('titlebar');
  const a = () => (window.pywebview && window.pywebview.api) || null;

  document.querySelectorAll('.traffic-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const act = btn.dataset.act;
      const apiRef = a(); if (!apiRef) return;
      if (act === 'close')    await apiRef.window_close();
      if (act === 'minimize') await apiRef.window_minimize();
      if (act === 'zoom')     await apiRef.window_move(0, 0); // простой пример
    });
  });

  let dragStart = null, winStart = null;
  tb.addEventListener('mousedown', async (e) => {
    if (e.target.closest('.traffic-btn')) return;
    const apiRef = a(); if (!apiRef) return;
    const pos = await apiRef.window_get_position();
    dragStart = { x: e.screenX, y: e.screenY };
    winStart  = { x: pos.x, y: pos.y };
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragStart) return;
    const a2 = a(); if (!a2) return;
    const dx = e.screenX - dragStart.x;
    const dy = e.screenY - dragStart.y;
    a2.window_move(winStart.x + dx, winStart.y + dy);
  });
  window.addEventListener('mouseup', () => { dragStart = null; winStart = null; });
})();

window.addEventListener('pywebviewready', async () => {
  const a = api();
  if (a && a.get_app_info) {
    try { const info = await a.get_app_info();
          document.getElementById('app-title').textContent = info.name; } catch {}
  }
  greet();
  refreshStats();
});