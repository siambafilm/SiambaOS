/* ================================================================
   SIamba OS — блокировка нежелательных горячих клавиш.

   Стратегия:
     • Пропускаем всё, что не содержит Ctrl / Cmd / Alt.
       (обычный ввод, стрелки, Tab, Shift+Tab, Escape, Backspace …)
     • Пропускаем один нажатый модификатор (Control/Shift/Alt/Meta)
       — иначе ломается отслеживание состояния.
     • F1…F12 — блокируем всегда.
     • Alt + … — блокируем всегда.
     • Ctrl/Cmd + … — только явный whitelist.

   Alt+F4 здесь не ловится — это уровень оконного менеджера.
   Его блокирует Python через events.closing (см. main.py).
   ================================================================ */
(() => {
  'use strict';

  // Ctrl+… (или Cmd+… на mac)
  const CTRL_WHITELIST = new Set([
    // буфер обмена и история
    'c', 'v', 'x', 'a', 'z', 'y',
    // удаление / навигация по словам в текстовых полях
    'Backspace', 'Delete',
    'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown',
    'Home', 'End', 'PageUp', 'PageDown',
    // Ctrl+Space — наш Spotlight
    ' ',
  ]);

  // Ctrl+Shift+… — совсем мало (paste-plain, redo)
  const CTRL_SHIFT_WHITELIST = new Set(['v', 'z']);

  const MODIFIER_KEYS = new Set([
    'Control', 'Shift', 'Alt', 'Meta', 'AltGraph',
    'CapsLock', 'NumLock', 'ScrollLock',
  ]);

  function isTypingTarget(el) {
    if (!el) return false;
    const tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable;
  }

  function isAllowed(e) {
    const k = e.key;

    // Одиночный модификатор — пропускаем.
    if (MODIFIER_KEYS.has(k)) return true;

    // Все F-клавиши — режем.
    if (/^F\d+$/.test(k)) return false;

    const hasCtrl = e.ctrlKey || e.metaKey;
    const hasAlt  = e.altKey;

    // Без Ctrl/Cmd/Alt — пропускаем (ввод, стрелки, Tab, Shift+Tab, Escape).
    if (!hasCtrl && !hasAlt) return true;

    // Любой Alt+… — режем (Alt+F4, Alt+Tab обработает WM сам,
    //  Alt+Left/Right — история браузера, и т.п.).
    if (hasAlt) return false;

    // Ctrl/Cmd+… — только whitelist.
    const low = (k.length === 1) ? k.toLowerCase() : k;

    if (e.shiftKey) {
      return CTRL_SHIFT_WHITELIST.has(low);
    }
    return CTRL_WHITELIST.has(low);
  }

  // capture-фаза: перехватываем до любых других обработчиков.
  // preventDefault, но НЕ stopPropagation — наши собственные
  // обработчики (Ctrl+Space → Spotlight, Escape → закрыть модалку)
  // должны продолжать работать.
  document.addEventListener('keydown', (e) => {
    if (!isAllowed(e)) {
      e.preventDefault();
    }
  }, true);

  // Ctrl+колесо → зум страницы. Глушим.
  document.addEventListener('wheel', (e) => {
    if (e.ctrlKey || e.metaKey) e.preventDefault();
  }, { passive: false });

  // На login-экране, в полях ввода и contenteditable оставляем
  // штатное контекстное меню (копипаста). В остальных местах — глушим.
  document.addEventListener('contextmenu', (e) => {
    if (isTypingTarget(e.target)) return;
    e.preventDefault();
  });
})();