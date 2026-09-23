/* Баллы за участие и узкий стенд (vault: decisions/2026-09-21-participation-points).
 *
 * Две вещи, обе общие для всех страниц, поэтому один файл:
 *   1) бейдж «вклад» в шапке — человек должен видеть свой счёт там же, где
 *      работает, а не только в кабинете;
 *   2) гашение разделов по /api/config.hidden — ссылка с data-section="stats"
 *      исчезает, если стенд этот раздел не показывает.
 *
 * Гасим на фронте, а не удаляем код: см. комментарий у HIDDEN_SECTIONS в main.py.
 * Это не защита (эндпоинт по-прежнему отвечает) — это то, что видит тестер.
 */
(function () {
  const api = (p, o) => fetch(p, Object.assign({credentials: 'same-origin'}, o || {}));

  async function hideSections() {
    try {
      const cfg = await (await api('/api/config')).json();
      const hidden = new Set(cfg.hidden || []);
      if (!hidden.size) return;
      // Прячем, а не удаляем из документа: страницы ищут свои узлы по id
      // (кабинет — #votesCard, дерево — вкладки), и вырезанный элемент
      // превращает «закрытый раздел» в сломанную страницу. Inline-стиль —
      // потому что атрибут hidden перебивается любым правилом с display.
      const hide = (el) => {
        if (el.dataset.section.split(/\s+/).some(k => hidden.has(k))) {
          el.hidden = true;
          el.style.display = 'none';
        }
      };
      document.querySelectorAll('[data-section]').forEach(hide);
      window.NOOSPHERE_HIDDEN = hidden;
      // Половина интерфейса рисуется скриптом и уже после этого места
      // (панель подсказок, карточки в дереве), а конфиг приходит асинхронно —
      // разовый проход по документу ловит только то, что лежало в HTML.
      new MutationObserver((muts) => {
        for (const m of muts) {
          for (const node of m.addedNodes) {
            if (node.nodeType !== 1) continue;
            if (node.dataset && node.dataset.section) hide(node);
            if (node.querySelectorAll)
              node.querySelectorAll('[data-section]').forEach(hide);
          }
        }
      }).observe(document.documentElement, {childList: true, subtree: true});
    } catch (e) { /* конфиг не ответил — показываем всё, это не защита */ }
  }

  async function badge() {
    const slot = document.getElementById('pointsBadge');
    if (!slot) return;
    if (window.NOOSPHERE_HIDDEN && window.NOOSPHERE_HIDDEN.has('points')) return;
    try {
      const r = await api('/api/points/me');
      if (!r.ok) { slot.style.display = 'none'; return; }   // не вошёл — бейджа нет
      const p = await r.json();
      slot.textContent = `вклад ${p.total}`;
      slot.title = p.players > 1
        ? `${p.total} баллов · ${p.rank}-й из ${p.players} участников`
        : `${p.total} баллов за участие`;
      slot.style.display = '';
      slot.onclick = () => { location.href = '/profile.html#points'; };
      slot.style.cursor = 'pointer';
    } catch (e) { /* молча: бейдж — украшение, а не функция */ }
  }

  // Бейдж перечитывается сам, а не по событию из дерева: вход на главной
  // ничего не перезагружает (ME меняется на месте), а после публикации счёт
  // должен подрасти на глазах — иначе человек видит своё число только после
  // F5 и решает, что баллы не начислились.
  function watch() {
    setInterval(badge, 20000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) badge();
    });
    window.addEventListener('focus', badge);
  }

  // Сначала разделы, потом бейдж: иначе бейдж успеет показать себя на стенде,
  // где раздел «вклад» закрыт.
  async function start() { await hideSections(); badge(); watch(); }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', start);
  else start();

  window.refreshPointsBadge = badge;
})();
