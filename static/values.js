// Вынесено из values.html: CSP script-src 'self' не допускает встроенных скриптов.
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  function renderList(d) {
    $("#list-title").textContent = "Список, версия " + d.version;
    const box = $("#list");
    box.textContent = "";
    box.className = "";
    for (const v of d.values) {
      const row = el("div", "vrow" + (v.nodes ? "" : " zero"));
      row.append(el("div", "n", v.name), el("div", "m", v.meaning),
                 el("div", "c", v.nodes ? `доводов ${v.nodes} · людей ${v.people}` : "пока ни одного"));
      box.appendChild(row);
    }
  }

  function renderCands(d) {
    const t = d.thresholds;
    $("#cand-lead").textContent = "Похожие по смыслу формулировки, которым нет дома в списке: "
      + "они попали в «другое» или расходятся по разным пунктам. Кандидат появляется, когда "
      + `такие формулировки набирают хотя бы ${t.min_people} человек в ${t.min_topics} и более обсуждениях. `
      + "Имён здесь нет.";
    const box = $("#cands");
    box.textContent = "";
    box.className = "";
    if (!d.embed) {
      box.appendChild(el("div", "note",
        "На этом сервере поиск кандидатов выключен: нужна локальная модель, которая сравнивает "
        + "формулировки по смыслу. Формулировки при этом копятся — кандидаты появятся, когда модель включат."));
      return;
    }
    if (!d.candidates.length) {
      box.appendChild(el("div", "note", "Пока кандидатов нет — список покрывает то, на что опираются доводы."));
      return;
    }
    for (const c of d.candidates) {
      const card = el("div", "cand");
      const ph = el("div", "ph");
      for (const p of c.phrases) ph.appendChild(el("span", null, "«" + p + "»"));
      card.appendChild(ph);
      card.appendChild(el("div", "meta",
        `людей ${c.people} · обсуждений ${c.topics} · доводов ${c.nodes}`));
      const why = el("div", "why");
      why.textContent = c.reason === "other"
        ? "ИИ не нашёл этой ценности места в списке и отметил «другое»."
        : "Раскладывается по разным пунктам — ни один не держит её уверенно: "
          + c.values.map((v) => v.name + " " + v.count).join(", ") + ".";
      card.appendChild(why);
      const links = el("div", "meta");
      links.append("доводы: ");
      c.node_ids.forEach((id, i) => {
        if (i) links.append(", ");
        const a = el("a", null, "#" + id);
        a.href = "/n/" + id;
        links.appendChild(a);
      });
      card.appendChild(links);
      box.appendChild(card);
    }
  }

  fetch("/api/values/overview")
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((d) => { renderList(d); renderCands(d); })
    .catch((e) => {
      $("#list").textContent = "Не загрузилось: " + e.message;
      $("#cands").textContent = "";
    });
})();
