// Вынесено из reset.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = (s) => document.querySelector(s);
const token = new URLSearchParams(location.search).get("token");

function show(kind, text) {
  const m = $("#msg");
  m.className = "msg " + kind;
  m.textContent = text;
  m.classList.remove("hidden");
}

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let data = null;
  try { data = await r.json(); } catch (_) { /* пустой ответ */ }
  if (!r.ok) throw new Error((data && data.detail) || ("ошибка " + r.status));
  return data;
}

if (token) {
  $("#setForm").classList.remove("hidden");
  $("#setBtn").onclick = async () => {
    const a = $("#pw1").value, b = $("#pw2").value;
    if (a.length < 8) return show("err", "пароль: минимум 8 символов");
    if (a !== b) return show("err", "пароли не совпадают");
    $("#setBtn").disabled = true;
    try {
      await post("/api/auth/reset", { token, password: a });
      $("#setForm").classList.add("hidden");
      show("ok", "Пароль изменён. Теперь можно войти с новым паролем.");
    } catch (e) {
      show("err", e.message);
      $("#setBtn").disabled = false;
    }
  };
} else {
  $("#askForm").classList.remove("hidden");
  $("#askBtn").onclick = async () => {
    $("#askBtn").disabled = true;
    try {
      const d = await post("/api/auth/forgot", { email: $("#email").value });
      show("ok", d.detail);
    } catch (e) {
      show("err", e.message);
    } finally {
      $("#askBtn").disabled = false;
    }
  };
}
