const api = () => (window.pywebview && window.pywebview.api) || null;
const notesEl = document.getElementById('notes');
const inputEl = document.getElementById('new-note');

function render(notes) {
  notesEl.innerHTML = '';
  if (!notes || !notes.length) {
    notesEl.innerHTML = '<div class="empty">Пока пусто</div>';
    return;
  }
  const frag = document.createDocumentFragment();
  for (const n of notes) {
    const li = document.createElement('li');
    li.className = 'note';
    const span = document.createElement('span');
    span.className = 'note-text';
    span.textContent = n.text;
    const del = document.createElement('button');
    del.className = 'note-del';
    del.textContent = '×';
    del.title = 'Удалить';
    del.addEventListener('click', async () => {
      const a = api(); if (!a) return;
      const res = await a.call('delete', { note_id: n.id });
      if (res && res.ok) render(res.notes);
    });
    li.appendChild(span); li.appendChild(del);
    frag.appendChild(li);
  }
  notesEl.appendChild(frag);
}

async function refresh() {
  const a = api(); if (!a) return;
  const res = await a.call('list', {});
  if (res && res.ok) render(res.notes);
}

async function addNote() {
  const text = (inputEl.value || '').trim();
  if (!text) return;
  const a = api(); if (!a) return;
  const res = await a.call('add', { text });
  if (res && res.ok) { inputEl.value = ''; render(res.notes); }
}

document.getElementById('add').addEventListener('click', addNote);
inputEl.addEventListener('keydown', e => { if (e.key === 'Enter') addNote(); });

/* Titlebar */
(function initTitlebar() {
  const a = () => (window.pywebview && window.pywebview.api) || null;
  document.querySelectorAll('.traffic-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const apiRef = a(); if (!apiRef) return;
      const act = btn.dataset.act;
      if (act === 'close')    await apiRef.window_close();
      if (act === 'minimize') await apiRef.window_minimize();
      if (act === 'zoom')     await apiRef.window_move(0, 0);
    });
  });
  const tb = document.getElementById('titlebar');
  let ds = null, ws = null;
  tb.addEventListener('mousedown', async (e) => {
    if (e.target.closest('.traffic-btn')) return;
    const apiRef = a(); if (!apiRef) return;
    const pos = await apiRef.window_get_position();
    ds = { x: e.screenX, y: e.screenY };
    ws = { x: pos.x, y: pos.y };
    e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!ds) return;
    const apiRef = a(); if (!apiRef) return;
    apiRef.window_move(ws.x + (e.screenX - ds.x), ws.y + (e.screenY - ds.y));
  });
  window.addEventListener('mouseup', () => { ds = null; ws = null; });
})();

window.addEventListener('pywebviewready', refresh);