// Вынесено из create-vote.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = (s) => document.querySelector(s);

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
  return r.json();
}

function err(m) {
  const e = $("#err");
  e.textContent = m;
  e.classList.remove("hidden");
  window.scrollTo(0, 0);
  setTimeout(() => e.classList.add("hidden"), 7000);
}

// ---- Варианты (строки ввода) ----
function addOptionRow(value = "") {
  const row = document.createElement("div");
  row.className = "opt-row";
  const inp = document.createElement("input");
  inp.type = "text";
  inp.placeholder = "Текст варианта";
  inp.value = value;
  const x = document.createElement("button");
  x.className = "opt-x";
  x.type = "button";
  x.textContent = "×";
  x.title = "Убрать вариант";
  x.onclick = () => { row.remove(); ensureMinRows(); };
  row.append(inp, x);
  $("#options").append(row);
}
function ensureMinRows() {
  while ($("#options").children.length < 2) addOptionRow();
}
function optionLabels() {
  return [...document.querySelectorAll("#options input")]
    .map((i) => i.value.trim()).filter(Boolean);
}

// ---- Инициализация ----
async function boot() {
  addOptionRow(); addOptionRow();
  $("#add-opt").onclick = () => addOptionRow();
  $("#submit").onclick = submit;

  let me = null;
  try { me = await api("/api/auth/me"); } catch (_) {}
  if (me && me.name) {
    $("#user").textContent = me.name;
  } else {
    $("#auth-msg").classList.remove("hidden");
    $("#submit").disabled = true;
  }

  try {
    const topics = await api("/api/topics");
    const sel = $("#topic");
    sel.innerHTML = '<option value="">— выбери обсуждение —</option>';
    for (const t of topics) {
      const o = document.createElement("option");
      o.value = t.id;
      const name = (t.title && t.title.trim()) || (t.text || "").trim();
      o.textContent = name.length > 80 ? name.slice(0, 80) + "…" : name;
      sel.append(o);
    }
  } catch (e) {
    $("#topic").innerHTML = '<option value="">— не удалось загрузить список —</option>';
    err("Обсуждения не загрузились: " + e.message);
  }
}

// ---- Создание: decision -> options -> open ----
async function submit() {
  const topicId = Number($("#topic").value);
  const question = $("#question").value.trim();
  const labels = optionLabels();

  if (!topicId) return err("Выбери обсуждение.");
  if (!question) return err("Впиши вопрос голосования.");
  if (labels.length < 2) return err("Нужно минимум два непустых варианта.");

  const btn = $("#submit");
  btn.disabled = true;
  const restore = btn.textContent;
  btn.textContent = "Создаю…";

  try {
    const dec = await api("/api/decisions", { topic_root_id: topicId, question });
    for (const label of labels) {
      await api(`/api/decisions/${dec.id}/options`, { label, origin: "initial" });
    }
    await api(`/api/decisions/${dec.id}/open`, {}); // {} → POST (у /open нет тела, но метод POST)
    const view = await api(`/api/decisions/${dec.id}`);
    showResult(view);
    $("#form").classList.add("hidden");
  } catch (e) {
    err("Не получилось: " + e.message);
    btn.disabled = false;
    btn.textContent = restore;
  }
}

function showResult(view) {
  const d = view.decision || {};
  const opts = view.options || [];
  const box = $("#result");
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="ok-title">✓ Голосование открыто</div>
    <div><b>${escapeHtml(d.question || "")}</b></div>
    <div class="tally">id <code>${+d.id}</code> · статус ${escapeHtml(d.status || "open")} ·
      вариантов: ${opts.length}</div>
    <div class="note" style="margin-top:14px">
      Голосование открыто. Участники разбирают вопрос с ИИ и голосуют на
      <a href="/vote.html?id=${+d.id}">странице голосования</a>; результат виден
      по головам и по весу.
    </div>
    <a class="btn-ghost" href="/vote.html?id=${+d.id}" style="text-decoration:none">Перейти к голосованию →</a>
    <button class="btn-ghost" id="again">Создать ещё одно</button>`;
  box.querySelector("#again").onclick = () => location.reload();
  window.scrollTo(0, 0);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

boot();
