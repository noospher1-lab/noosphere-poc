// Переключатель «Антиспам» (app/spam.py). По умолчанию включён: помеченное
// как спам не видно нигде — дерево, граф, каталог, сведения, карта позиций.
// Выключенный — кука noo_spam=show: сервер читает её сам, поэтому ни один
// запрос страниц переделывать не пришлось. Своё автор видит всегда.
(function () {
  const COOKIE = "noo_spam";
  const off = document.cookie.split("; ").some((c) => c === COOKIE + "=show");

  function set(show) {
    document.cookie = show
      ? COOKIE + "=show; path=/; max-age=31536000; samesite=lax"
      : COOKIE + "=; path=/; max-age=0; samesite=lax";
    location.reload();
  }

  // метка у помеченного: видна, когда антиспам выключен, и автору у своего
  function tag(reason) {
    const t = document.createElement("span");
    t.className = "spam-tag";
    t.textContent = "спам";
    t.title = "Помечено как спам: " + (reason || "без причины") +
      ". Видно, потому что антиспам выключен или это ваше.";
    return t;
  }

  // кнопка администратора: пометить / снять пометку
  function adminButton(kind, id, isSpam, done) {
    const b = document.createElement("button");
    b.className = "mini spam-admin";
    b.textContent = isSpam ? "не спам" : "спам";
    b.title = isSpam ? "Снять пометку — ИИ повторно её не поставит"
                     : "Пометить как спам — скроется у всех с включённым антиспамом";
    b.onclick = async (e) => {
      e.stopPropagation();
      let reason = null;
      if (!isSpam) {
        reason = prompt("Причина (увидят те, кто выключил антиспам):", "спам");
        if (reason === null) return;
      }
      b.disabled = true;
      const r = await fetch(`/api/admin/spam/${kind}/${id}`, {
        method: "POST", headers: { "content-type": "application/json" },
        credentials: "same-origin", body: JSON.stringify({ spam: !isSpam, reason }),
      });
      b.disabled = false;
      if (r.ok && done) done();
      else if (!r.ok) alert("Не получилось: " + r.status);
    };
    return b;
  }

  function mount() {
    const header = document.querySelector("header");
    if (!header || document.getElementById("antispamBtn")) return;
    const b = document.createElement("button");
    b.id = "antispamBtn";
    b.type = "button";
    b.className = "antispam-btn" + (off ? " is-off" : "");
    b.textContent = off ? "Антиспам: выкл" : "Антиспам: вкл";
    b.setAttribute("aria-pressed", off ? "false" : "true");
    b.title = off
      ? "Сейчас видно и то, что ИИ или администратор пометили как спам (с меткой «спам»). Нажмите, чтобы скрыть."
      : "Спам скрыт везде: в дереве, графе, каталоге, сведениях. Нажмите, чтобы показать его с меткой.";
    b.onclick = () => set(!off);
    const anchor = document.getElementById("hintsBtn") || header.querySelector("nav");
    if (anchor && anchor.id === "hintsBtn") anchor.after(b);
    else if (anchor) anchor.before(b);
    else header.appendChild(b);
  }

  const css = document.createElement("style");
  css.textContent = `
    .antispam-btn { font: inherit; font-size: 12px; padding: 3px 9px; border-radius: 999px;
      border: 1px solid var(--line2, #333a49); background: transparent;
      color: var(--muted, #9aa3b5); cursor: pointer; white-space: nowrap; }
    .antispam-btn:hover { color: var(--text, #e8ebf2); }
    .antispam-btn.is-off { border-color: #b8893c; color: #d8af6e; }
    .spam-tag { display: inline-block; font-size: 11px; line-height: 1.4; padding: 0 6px;
      margin-left: 6px; border-radius: 4px; background: rgba(226, 91, 86, .15);
      color: #e8837f; border: 1px solid rgba(226, 91, 86, .4); vertical-align: middle; }
    .spam-admin { margin-left: 6px; }
  `;
  document.head.appendChild(css);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();

  window.NooSpam = { off, tag, adminButton };
})();
