// Вынесено из dialogue.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
  return r.json();
}
function err(m) { const e = $("#err"); e.textContent = m; e.classList.remove("hidden");
  setTimeout(() => e.classList.add("hidden"), 6000); }
function show(id) {
  for (const s of ["s-intro","s-pre","s-dlg","s-post","s-scoring","s-results"])
    document.getElementById(s).classList.toggle("hidden", s !== id);
  window.scrollTo(0, 0);
}

let D = null;   // dialogue state from the server
let preVote = null, preConf = null, postVote = null, postConf = null;
let topics = null, selectedTopicId = null;

async function renderTopicPicker() {
  if (topics === null) {
    try { topics = await api("/api/dialogue/topics"); } catch (e) { err(e.message); topics = []; }
  }
  const box = $("#topicList");
  box.innerHTML = "";
  for (const t of topics) {
    const b = document.createElement("button");
    b.textContent = t.title;
    if (t.id === selectedTopicId) b.classList.add("sel");
    b.onclick = () => {
      selectedTopicId = t.id;
      for (const c of box.children) c.classList.remove("sel");
      b.classList.add("sel");
      $("#startBtn").disabled = false;
    };
    box.appendChild(b);
  }
}

const CRIT_LABELS = {
  problem_framing: ["Постановка проблемы", 20],
  information_integration: ["Интеграция информации", 20],
  reasoning_under_revision: ["Мышление под давлением", 20],
  handling_disagreement: ["Работа с несогласием", 15],
  cognitive_patterns: ["Когнитивные паттерны", 15],
  decision_quality: ["Качество решения", 10],
};

function route() {
  if (!D) { renderTopicPicker(); show("s-intro"); return; }
  $("#preTopic").textContent = D.topic;
  $("#dlgTopic").textContent = D.topic;
  if (D.phase === "pre") show("s-pre");
  else if (D.phase === "dialogue") { renderDlg(); show("s-dlg"); }
  else if (D.phase === "post") { show("s-post"); }
  else if (D.phase === "results") { renderResults(); show("s-results"); }
}

// ---- dialogue rendering
function renderDlg() {
  $("#dlgPre").textContent =
    `твой голос: ${D.pre.vote === "yes" ? "ДА" : "НЕТ"} (${D.pre.confidence})`;
  const chat = $("#chat");
  chat.innerHTML = "";
  for (const t of D.turns) {
    if (t.content.startsWith("[")) continue;         // service messages
    if (t.meta === "inform_offer_pending") {
      const o = document.createElement("div");
      o.className = "offer";
      o.innerHTML = '<div class="who">💡 есть информация по теме</div><div class="tx"></div>' +
        '<div class="row"><button data-a="1">да, поделись</button><button data-a="0">нет, продолжим</button></div>';
      o.querySelector(".tx").textContent = t.content;
      o.querySelectorAll("button").forEach(b =>
        b.onclick = () => inform(b.dataset.a === "1"));
      chat.appendChild(o);
      continue;
    }
    const d = document.createElement("div");
    d.className = "turn " + (t.role === "ai" ? "ai" : "user");
    d.innerHTML = '<div class="who"></div><div class="tx"></div>';
    d.querySelector(".who").textContent = t.role === "ai" ? "собеседник" : "ты";
    d.querySelector(".tx").textContent = t.content;
    chat.appendChild(d);
  }
  const n = D.user_turns;
  $("#turnCounter").textContent = n >= D.min_turns
    ? `ход ${n} из ${D.max_turns}` : `ход ${n} · итог доступен с ${D.min_turns}`;
  $("#finalizeBtn").classList.toggle("hidden", n < D.min_turns);
  const pending = D.turns.length && D.turns[D.turns.length - 1].meta === "inform_offer_pending";
  $("#sendBtn").disabled = pending || n >= D.max_turns;
  window.scrollTo(0, document.body.scrollHeight);
}

async function send() {
  const text = $("#msgText").value.trim();
  if (!text) return;
  $("#sendBtn").disabled = true; $("#think").classList.remove("hidden");
  try {
    D = await api("/api/dialogue/message", { text });
    $("#msgText").value = "";
    renderDlg();
  } catch (e) { err(e.message); }
  finally { $("#think").classList.add("hidden"); $("#sendBtn").disabled = false; }
}

async function inform(accept) {
  $("#think").classList.remove("hidden");
  try { D = await api("/api/dialogue/inform", { accept }); renderDlg(); }
  catch (e) { err(e.message); }
  finally { $("#think").classList.add("hidden"); }
}

// ---- results
function renderResults() {
  const s = D.scores || {};
  $("#totalScore").textContent = s.total ?? "—";
  $("#overall").textContent = s.overall_summary || "";
  const box = $("#criteria");
  box.innerHTML = "";
  for (const [key, [label, max]] of Object.entries(CRIT_LABELS)) {
    const c = (s.criteria || {})[key];
    if (!c) continue;
    const d = document.createElement("div");
    d.className = "crit card";
    d.innerHTML = `<b>${escHtml(label)}</b> <span class="muted">${escHtml(c.score)} / ${max}</span>
      <div class="bar"><span style="width:${+((c.score / max) * 100) || 0}%"></span></div>
      <div class="why"></div>`;
    d.querySelector(".why").textContent = c.why || "";
    for (const q of c.evidence || []) {
      const ev = document.createElement("div");
      ev.className = "ev"; ev.textContent = "«" + q + "»";
      d.appendChild(ev);
    }
    if (c.what_would_raise_score) {
      const w = document.createElement("div");
      w.className = "muted"; w.style.marginTop = "6px";
      w.textContent = "↑ что поднимет балл: " + c.what_would_raise_score;
      d.appendChild(w);
    }
    box.appendChild(d);
  }
  const meta = s.meta_patterns || [];
  $("#metaBox").classList.toggle("hidden", !meta.length);
  $("#metaList").innerHTML = meta.map(() => "<div>· <span></span></div>").join("");
  $$("#metaList span").forEach((el, i) => el.textContent = meta[i]);
  const left = D.max_turns - D.user_turns;
  $("#continueBtn").style.display = left > 0 ? "" : "none";
  $("#budgetNote").textContent = left > 0
    ? `осталось ${left} ходов бюджета — продолжение накапливает транскрипт, итог пересчитается`
    : "бюджет ходов на этот вопрос исчерпан — дальше тренируйся в самих обсуждениях";
}

// ---- wiring
function bindChoice(sel, set) {
  $$(sel).forEach(b => b.onclick = () => {
    $$(sel).forEach(x => x.classList.remove("sel"));
    b.classList.add("sel");
    set(b.dataset.v || b.dataset.c);
  });
}
function counter(taSel, countSel, min, check) {
  $(taSel).addEventListener("input", () => {
    const n = $(taSel).value.trim().length;
    $(countSel).textContent = `${n} / ${min}`;
    $(countSel).style.color = n >= min ? "var(--green)" : "var(--dim)";
    check();
  });
}
const checkPre = () => $("#lockBtn").disabled =
  !(preVote && preConf && $("#preText").value.trim().length >= 100);
const checkPost = () => $("#submitPostBtn").disabled =
  !(postVote && postConf && $("#postText").value.trim().length >= 100 &&
    $("#reflText").value.trim().length >= 50);

bindChoice(".vote", v => { preVote = v; checkPre(); });
bindChoice(".conf", c => { preConf = c; checkPre(); });
bindChoice(".pvote", v => { postVote = v; checkPost(); });
bindChoice(".pconf", c => { postConf = c; checkPost(); });
counter("#preText", "#preCount", 100, checkPre);
counter("#postText", "#postCount", 100, checkPost);
counter("#reflText", "#reflCount", 50, checkPost);

$("#startBtn").onclick = async () => {
  if (!selectedTopicId) return;
  try { D = await api("/api/dialogue/start", { topic_id: selectedTopicId }); route(); }
  catch (e) { err(e.message); }
};
$("#lockBtn").onclick = async () => {
  if (!confirm("Зафиксировать стартовую позицию? Изменить её будет нельзя.")) return;
  $("#lockBtn").disabled = true;
  try {
    D = await api("/api/dialogue/pre",
      { vote: preVote, reasoning: $("#preText").value.trim(), confidence: preConf });
    route();
  } catch (e) { err(e.message); $("#lockBtn").disabled = false; }
};
$("#sendBtn").onclick = send;
$("#msgText").addEventListener("keydown", e => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send();
});
$("#finalizeBtn").onclick = () => show("s-post");
$("#backToDlgBtn").onclick = () => { renderDlg(); show("s-dlg"); };
$("#submitPostBtn").onclick = async () => {
  show("s-scoring");
  try {
    D = await api("/api/dialogue/finalize", {
      vote: postVote, reasoning: $("#postText").value.trim(),
      confidence: postConf, reflection: $("#reflText").value.trim(),
    });
    route();
  } catch (e) { err(e.message); show("s-post"); }
};
$("#continueBtn").onclick = async () => {
  try { D = await api("/api/dialogue/continue", {}); route(); } catch (e) { err(e.message); }
};

(async function boot() {
  let me = null;
  try { me = await api("/api/auth/me"); } catch (_) {}
  if (!me) {
    $("#user").textContent = "не авторизован";
    $("#wrap").innerHTML = '<div class="card">Нужен аккаунт: <a href="/">войди на главной</a> и возвращайся.</div>';
    return;
  }
  $("#user").textContent = me.name + (me.dialogue_poi ? ` · PoI диалога ${me.dialogue_poi}` : "");
  try { D = await api("/api/dialogue"); } catch (_) { D = null; }
  route();
})();
